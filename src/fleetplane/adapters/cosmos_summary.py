from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fleetplane.core.summary_projection import SummaryContribution
from fleetplane.domain.enums import HealthState
from fleetplane.domain.models import FleetSummary


def _is_conflict(exc: BaseException) -> bool:
    return getattr(exc, "status_code", None) in {409, 412}


def _empty_summary(now: datetime) -> FleetSummary:
    return FleetSummary(updated_at=now.astimezone(UTC))


def _adjust(summary: FleetSummary, contribution: SummaryContribution | None, sign: int) -> None:
    if contribution is None:
        return
    summary.total_devices += sign
    if contribution.health_state == HealthState.HEALTHY:
        summary.healthy += sign
    elif contribution.health_state == HealthState.DEGRADED:
        summary.degraded += sign
    elif contribution.health_state == HealthState.OFFLINE:
        summary.offline += sign
    else:
        summary.unknown += sign
    if contribution.config_converged:
        summary.config_converged += sign


class CosmosSummaryStore:
    """Idempotent materialized-summary store.

    A summary scope and all of its per-device cursors share one Cosmos logical partition. The
    cursor records the highest device projection version already reflected in that scope and the
    contribution currently represented there. This makes replay harmless and also lets a newer
    device projection supersede a missing intermediate change without corrupting the counters.
    """

    SUMMARY_ID = "summary"

    def __init__(self, container: Any) -> None:
        self.container = container

    @staticmethod
    def _cursor_id(device_id: str) -> str:
        return f"cursor:{device_id}"

    def _read(self, item_id: str, scope_id: str) -> dict[str, Any] | None:
        try:
            return self.container.read_item(item=item_id, partition_key=scope_id)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status_code", None) == 404:
                return None
            raise

    def get_summary(self, scope_id: str) -> FleetSummary | None:
        doc = self._read(self.SUMMARY_ID, scope_id)
        return None if doc is None else FleetSummary.model_validate(doc["body"])

    def apply_delta(
        self,
        *,
        scope_id: str,
        device_id: str,
        projection_version: int,
        contribution: SummaryContribution | None,
        occurred_at: datetime,
    ) -> bool:
        # Retrying the complete read/CAS loop is required because many device partitions can
        # legitimately update one fleet/global summary partition concurrently.
        for _ in range(12):
            summary_doc = self._read(self.SUMMARY_ID, scope_id)
            cursor_id = self._cursor_id(device_id)
            cursor_doc = self._read(cursor_id, scope_id)
            current_version = -1 if cursor_doc is None else int(cursor_doc["projection_version"])
            if current_version >= projection_version:
                return False

            old_contribution = None
            if cursor_doc is not None and cursor_doc.get("contribution") is not None:
                old_contribution = SummaryContribution.from_dict(cursor_doc["contribution"])

            summary = (
                _empty_summary(occurred_at)
                if summary_doc is None
                else FleetSummary.model_validate(summary_doc["body"])
            )
            _adjust(summary, old_contribution, -1)
            _adjust(summary, contribution, 1)
            summary.updated_at = max(summary.updated_at, occurred_at.astimezone(UTC))
            # Defensive invariant: a projection bug must never publish negative counters.
            if min(
                summary.total_devices,
                summary.healthy,
                summary.degraded,
                summary.offline,
                summary.unknown,
                summary.config_converged,
            ) < 0:
                raise RuntimeError("summary projection would produce a negative counter")

            summary_replacement = {
                "id": self.SUMMARY_ID,
                "scope_id": scope_id,
                "doc_type": "fleet_summary",
                "body": summary.model_dump(mode="json"),
            }
            cursor_replacement = {
                "id": cursor_id,
                "scope_id": scope_id,
                "doc_type": "summary_cursor",
                "device_id": device_id,
                "projection_version": projection_version,
                "contribution": None if contribution is None else contribution.as_dict(),
                "updated_at": occurred_at.astimezone(UTC).isoformat(),
            }
            operations: list[tuple[Any, ...]] = []
            if summary_doc is None:
                operations.append(("create", (summary_replacement,)))
            else:
                operations.append(
                    (
                        "replace",
                        (self.SUMMARY_ID, summary_replacement),
                        {"if_match_etag": summary_doc.get("_etag")},
                    )
                )
            if cursor_doc is None:
                operations.append(("create", (cursor_replacement,)))
            else:
                operations.append(
                    (
                        "replace",
                        (cursor_id, cursor_replacement),
                        {"if_match_etag": cursor_doc.get("_etag")},
                    )
                )
            try:
                self.container.execute_item_batch(
                    batch_operations=operations,
                    partition_key=scope_id,
                )
                return True
            except Exception as exc:  # noqa: BLE001
                if _is_conflict(exc):
                    continue
                raise
        raise RuntimeError("summary projection contention retry limit exceeded")

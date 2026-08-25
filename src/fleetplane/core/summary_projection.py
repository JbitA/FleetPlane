from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from fleetplane.domain.enums import HealthState
from fleetplane.domain.models import DeviceState, FleetSummary


@dataclass(frozen=True)
class SummaryContribution:
    site_id: str
    fleet_id: str
    health_state: HealthState
    config_converged: bool

    @classmethod
    def from_state(cls, state: DeviceState) -> "SummaryContribution":
        return cls(
            site_id=state.site_id,
            fleet_id=state.fleet_id,
            health_state=state.health_state,
            config_converged=state.config_converged,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "fleet_id": self.fleet_id,
            "health_state": self.health_state.value,
            "config_converged": self.config_converged,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SummaryContribution":
        return cls(
            site_id=str(value["site_id"]),
            fleet_id=str(value["fleet_id"]),
            health_state=HealthState(str(value["health_state"])),
            config_converged=bool(value["config_converged"]),
        )


@dataclass(frozen=True)
class SummaryDelta:
    device_id: str
    projection_version: int
    before: SummaryContribution | None
    after: SummaryContribution | None
    occurred_at: datetime

    @property
    def delta_id(self) -> str:
        return f"{self.device_id}:{self.projection_version}"

    @classmethod
    def from_states(
        cls,
        before: DeviceState | None,
        after: DeviceState | None,
        *,
        occurred_at: datetime | None = None,
    ) -> "SummaryDelta":
        if after is None and before is None:
            raise ValueError("summary delta requires before or after state")
        state = after or before
        assert state is not None
        version = after.projection_version if after is not None else before.projection_version + 1  # type: ignore[union-attr]
        return cls(
            device_id=state.device_id,
            projection_version=version,
            before=None if before is None else SummaryContribution.from_state(before),
            after=None if after is None else SummaryContribution.from_state(after),
            occurred_at=(occurred_at or datetime.now(UTC)).astimezone(UTC),
        )

    def as_document(self) -> dict[str, Any]:
        return {
            "id": f"summary-delta:{self.projection_version}",
            "device_id": self.device_id,
            "doc_type": "summary_delta",
            "projection_version": self.projection_version,
            "occurred_at": self.occurred_at.isoformat(),
            "body": {
                "device_id": self.device_id,
                "projection_version": self.projection_version,
                "before": None if self.before is None else self.before.as_dict(),
                "after": None if self.after is None else self.after.as_dict(),
                "occurred_at": self.occurred_at.isoformat(),
            },
        }

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "SummaryDelta":
        body = document.get("body", document)
        before = body.get("before")
        after = body.get("after")
        occurred_at = datetime.fromisoformat(str(body["occurred_at"]))
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        return cls(
            device_id=str(body["device_id"]),
            projection_version=int(body["projection_version"]),
            before=None if before is None else SummaryContribution.from_dict(before),
            after=None if after is None else SummaryContribution.from_dict(after),
            occurred_at=occurred_at.astimezone(UTC),
        )


class SummaryProjectionStore(Protocol):
    def apply_delta(
        self,
        *,
        scope_id: str,
        device_id: str,
        projection_version: int,
        contribution: SummaryContribution | None,
        occurred_at: datetime,
    ) -> bool: ...

    def get_summary(self, scope_id: str) -> FleetSummary | None: ...


class SummaryProjector:
    def __init__(self, store: SummaryProjectionStore) -> None:
        self.store = store

    @staticmethod
    def scopes(delta: SummaryDelta) -> set[str]:
        scopes = {"global"}
        for contribution in (delta.before, delta.after):
            if contribution is None:
                continue
            scopes.add(f"fleet:{contribution.fleet_id}")
            scopes.add(f"site:{contribution.site_id}")
        return scopes

    @staticmethod
    def contribution_for_scope(
        contribution: SummaryContribution | None,
        scope_id: str,
    ) -> SummaryContribution | None:
        if contribution is None:
            return None
        if scope_id == "global":
            return contribution
        if scope_id.startswith("fleet:") and contribution.fleet_id == scope_id.removeprefix("fleet:"):
            return contribution
        if scope_id.startswith("site:") and contribution.site_id == scope_id.removeprefix("site:"):
            return contribution
        return None

    def apply(self, delta: SummaryDelta) -> int:
        applied = 0
        for scope_id in sorted(self.scopes(delta)):
            if self.store.apply_delta(
                scope_id=scope_id,
                device_id=delta.device_id,
                projection_version=delta.projection_version,
                contribution=self.contribution_for_scope(delta.after, scope_id),
                occurred_at=delta.occurred_at,
            ):
                applied += 1
        return applied

    def process_documents(self, documents: list[dict[str, Any]]) -> dict[str, int]:
        stats = {"seen": len(documents), "deltas": 0, "scope_updates": 0, "ignored": 0}
        for document in documents:
            if document.get("doc_type") != "summary_delta":
                stats["ignored"] += 1
                continue
            delta = SummaryDelta.from_document(document)
            stats["deltas"] += 1
            stats["scope_updates"] += self.apply(delta)
        return stats

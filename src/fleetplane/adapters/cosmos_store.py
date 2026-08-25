from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Sequence

from fleetplane.core.summary_projection import SummaryDelta
from fleetplane.domain.enums import CommandStatus, DeviceLifecycle, HealthState, OutboxStatus
from fleetplane.domain.models import (
    AuditPage,
    AuditRecord,
    CommandAck,
    CommandPage,
    ConfigAck,
    DesiredConfiguration,
    DevicePage,
    DeviceState,
    DirectCommand,
    FleetSummary,
    OutboxMessage,
    TelemetryEnvelope,
)
from fleetplane.ports.store import TelemetryCommitStatus


class CosmosConfigurationError(RuntimeError):
    pass


def _cursor_encode(values: list[str]) -> str:
    payload = json.dumps(values, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _cursor_decode(value: str | None, expected: int) -> list[str] | None:
    if value is None:
        return None
    try:
        raw = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("invalid pagination cursor") from exc
    if not isinstance(decoded, list) or len(decoded) != expected or not all(
        isinstance(item, str) for item in decoded
    ):
        raise ValueError("invalid pagination cursor")
    return decoded


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        value = getattr(exc, "status", None)
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _is_not_found(exc: BaseException) -> bool:
    return _status_code(exc) == 404


def _is_conflict(exc: BaseException) -> bool:
    return _status_code(exc) in {409, 412}


def _batch_error_index(exc: BaseException) -> int | None:
    value = getattr(exc, "error_index", None)
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _body(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json")


class CosmosFleetStore:
    """Azure Cosmos DB implementation using one logical partition per device.

    Device-state transitions, receipts, commands, acknowledgements, audits and outbox records
    that belong to a device are committed with same-partition transactional batches. Every state
    transition also emits a same-partition summary-delta document. An optional change-feed summary
    store makes fleet-summary reads O(1); exact scans remain a development/backfill fallback.
    """

    STATE_ID = "state"
    DESIRED_ID = "desired"

    def __init__(
        self,
        container: Any,
        *,
        client: Any | None = None,
        summary_store: Any | None = None,
    ) -> None:
        self.container = container
        self.client = client
        self.summary_store = summary_store

    @classmethod
    def from_environment(
        cls,
        *,
        endpoint: str | None = None,
        database: str | None = None,
        container: str | None = None,
        summary_container: str | None = None,
        credential: Any | None = None,
    ) -> "CosmosFleetStore":
        endpoint = endpoint or os.getenv("FLEETPLANE_COSMOS_ENDPOINT")
        database = database or os.getenv("FLEETPLANE_COSMOS_DATABASE", "fleetplane")
        container = container or os.getenv("FLEETPLANE_COSMOS_CONTAINER", "control")
        summary_container = summary_container or os.getenv("FLEETPLANE_COSMOS_SUMMARY_CONTAINER")
        if not endpoint:
            raise CosmosConfigurationError("FLEETPLANE_COSMOS_ENDPOINT is required")
        try:
            from azure.cosmos import CosmosClient
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:  # pragma: no cover - optional Azure dependencies
            raise CosmosConfigurationError(
                "Azure dependencies are not installed; install fleetplane[azure]"
            ) from exc
        credential = credential or DefaultAzureCredential()
        client = CosmosClient(endpoint, credential=credential)
        database_client = client.get_database_client(database)
        container_client = database_client.get_container_client(container)
        summary_store = None
        if summary_container:
            from fleetplane.adapters.cosmos_summary import CosmosSummaryStore

            summary_store = CosmosSummaryStore(database_client.get_container_client(summary_container))
        return cls(container_client, client=client, summary_store=summary_store)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()

    def _read(self, item_id: str, device_id: str) -> dict[str, Any] | None:
        try:
            return self.container.read_item(item=item_id, partition_key=device_id)
        except Exception as exc:  # noqa: BLE001 - normalize provider not-found only
            if _is_not_found(exc):
                return None
            raise

    def _query(self, query: str, parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(
            self.container.query_items(
                query=query,
                parameters=parameters,
                enable_cross_partition_query=True,
            )
        )

    @staticmethod
    def _state_doc(state: DeviceState) -> dict[str, Any]:
        return {
            "id": CosmosFleetStore.STATE_ID,
            "device_id": state.device_id,
            "doc_type": "device_state",
            "projection_version": state.projection_version,
            "site_id": state.site_id,
            "fleet_id": state.fleet_id,
            "lifecycle": state.lifecycle.value,
            "health_state": state.health_state.value,
            "config_converged": state.config_converged,
            "body": _body(state),
        }

    @staticmethod
    def _summary_delta_doc(previous: DeviceState | None, current: DeviceState) -> dict[str, Any]:
        return SummaryDelta.from_states(previous, current).as_document()

    @staticmethod
    def _audit_doc(record: AuditRecord, device_id: str) -> dict[str, Any]:
        return {
            "id": f"audit:{record.audit_id}",
            "device_id": device_id,
            "doc_type": "audit",
            "audit_id": record.audit_id,
            "created_at": record.created_at.isoformat(),
            "correlation_id": record.correlation_id,
            "request_correlation_id": record.request_correlation_id,
            "target": record.target,
            "body": _body(record),
        }

    @staticmethod
    def _outbox_doc(message: OutboxMessage) -> dict[str, Any]:
        return {
            "id": f"outbox:{message.outbox_id}",
            "device_id": message.device_id,
            "doc_type": "outbox",
            "outbox_id": message.outbox_id,
            "kind": message.kind.value,
            "status": message.status.value,
            "available_at": message.available_at.isoformat(),
            "lease_owner": message.lease_owner,
            "lease_until": message.lease_until.isoformat() if message.lease_until else None,
            "body": _body(message),
        }

    def get_device_state(self, device_id: str) -> DeviceState | None:
        doc = self._read(self.STATE_ID, device_id)
        return None if doc is None else DeviceState.model_validate(doc["body"])

    def provision_device(self, *, state: DeviceState, audit: AuditRecord) -> bool:
        state = state.model_copy(deep=True)
        state.projection_version = 1
        operations: list[tuple[Any, ...]] = [
            ("create", (self._state_doc(state),)),
            ("create", (self._audit_doc(audit, state.device_id),)),
            ("create", (self._summary_delta_doc(None, state),)),
        ]
        try:
            self.container.execute_item_batch(
                batch_operations=operations,
                partition_key=state.device_id,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc):
                return False
            raise

    def transition_device_lifecycle(
        self,
        *,
        state: DeviceState,
        expected_projection_version: int,
        audit: AuditRecord,
    ) -> bool:
        current = self._read(self.STATE_ID, state.device_id)
        if current is None or int(current["projection_version"]) != expected_projection_version:
            return False
        previous = DeviceState.model_validate(current["body"])
        state = state.model_copy(deep=True)
        state.projection_version = expected_projection_version + 1
        operations: list[tuple[Any, ...]] = [
            (
                "replace",
                (self.STATE_ID, self._state_doc(state)),
                {"if_match_etag": current.get("_etag")},
            ),
            ("create", (self._audit_doc(audit, state.device_id),)),
            ("create", (self._summary_delta_doc(previous, state),)),
        ]
        try:
            self.container.execute_item_batch(
                batch_operations=operations,
                partition_key=state.device_id,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc):
                return False
            raise

    def commit_telemetry(
        self,
        event: TelemetryEnvelope,
        received_at: datetime,
        state: DeviceState,
        expected_projection_version: int,
    ) -> TelemetryCommitStatus:
        current = self._read(self.STATE_ID, event.device_id)
        actual = 0 if current is None else int(current["projection_version"])
        if actual != expected_projection_version:
            return "conflict"

        previous = None if current is None else DeviceState.model_validate(current["body"])
        state = state.model_copy(deep=True)
        state.projection_version = expected_projection_version + 1
        identity = f"{event.device_generation}|{event.boot_id}|{event.sequence}"
        receipt = {
            "id": f"telemetry:{_hash_key(identity)}",
            "device_id": event.device_id,
            "doc_type": "telemetry_receipt",
            "device_generation": event.device_generation,
            "boot_id": event.boot_id,
            "sequence": event.sequence,
            "event_id": event.event_id,
            "observed_at": event.observed_at.isoformat(),
            "received_at": received_at.isoformat(),
        }
        state_doc = self._state_doc(state)
        operations: list[tuple[Any, ...]] = [("create", (receipt,))]
        if current is None:
            operations.append(("create", (state_doc,)))
        else:
            operations.append(
                ("replace", (self.STATE_ID, state_doc), {"if_match_etag": current.get("_etag")})
            )
        operations.append(("create", (self._summary_delta_doc(previous, state),)))
        try:
            self.container.execute_item_batch(
                batch_operations=operations,
                partition_key=event.device_id,
            )
            return "committed"
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc):
                return "duplicate" if _batch_error_index(exc) == 0 else "conflict"
            raise

    def compare_and_swap_device_state(
        self,
        state: DeviceState,
        expected_projection_version: int,
    ) -> bool:
        current = self._read(self.STATE_ID, state.device_id)
        actual = 0 if current is None else int(current["projection_version"])
        if actual != expected_projection_version:
            return False
        previous = None if current is None else DeviceState.model_validate(current["body"])
        state = state.model_copy(deep=True)
        state.projection_version = expected_projection_version + 1
        doc = self._state_doc(state)
        operations: list[tuple[Any, ...]]
        if current is None:
            operations = [("create", (doc,))]
        else:
            operations = [
                ("replace", (self.STATE_ID, doc), {"if_match_etag": current.get("_etag")})
            ]
        operations.append(("create", (self._summary_delta_doc(previous, state),)))
        try:
            self.container.execute_item_batch(batch_operations=operations, partition_key=state.device_id)
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc):
                return False
            raise

    def list_device_states_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        health_state: HealthState | None = None,
        lifecycle: DeviceLifecycle | None = None,
        site_id: str | None = None,
        fleet_id: str | None = None,
    ) -> DevicePage:
        limit = max(1, min(limit, 500))
        decoded = _cursor_decode(cursor, 1)
        clauses = ["c.doc_type = 'device_state'"]
        parameters: list[dict[str, Any]] = []
        if decoded:
            clauses.append("c.device_id > @after")
            parameters.append({"name": "@after", "value": decoded[0]})
        if health_state is not None:
            clauses.append("c.health_state = @health")
            parameters.append({"name": "@health", "value": health_state.value})
        if lifecycle is not None:
            clauses.append("c.lifecycle = @lifecycle")
            parameters.append({"name": "@lifecycle", "value": lifecycle.value})
        if site_id is not None:
            clauses.append("c.site_id = @site")
            parameters.append({"name": "@site", "value": site_id})
        if fleet_id is not None:
            clauses.append("c.fleet_id = @fleet")
            parameters.append({"name": "@fleet", "value": fleet_id})
        query = (
            f"SELECT TOP {limit + 1} c.device_id,c.body FROM c "
            f"WHERE {' AND '.join(clauses)} ORDER BY c.device_id ASC"
        )
        rows = self._query(query, parameters)
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = [DeviceState.model_validate(row["body"]) for row in visible]
        next_cursor = _cursor_encode([visible[-1]["device_id"]]) if has_more and visible else None
        return DevicePage(items=items, next_cursor=next_cursor, page_size=len(items))

    def get_fleet_summary(self) -> FleetSummary:
        if self.summary_store is not None:
            projected = self.summary_store.get_summary("global")
            if projected is not None:
                return projected
        return self.get_fleet_summary_exact()

    def get_fleet_summary_exact(self) -> FleetSummary:
        """Exact cross-partition fallback used for backfill/development validation."""
        rows = self._query(
            "SELECT c.health_state,c.config_converged FROM c WHERE c.doc_type = 'device_state'",
            [],
        )
        counts = {state.value: 0 for state in HealthState}
        converged = 0
        for row in rows:
            counts[str(row["health_state"])] += 1
            converged += int(bool(row.get("config_converged")))
        return FleetSummary(
            total_devices=len(rows),
            healthy=counts[HealthState.HEALTHY.value],
            degraded=counts[HealthState.DEGRADED.value],
            offline=counts[HealthState.OFFLINE.value],
            unknown=counts[HealthState.UNKNOWN.value],
            config_converged=converged,
            updated_at=datetime.now(UTC),
        )

    def get_desired_config(self, device_id: str) -> DesiredConfiguration | None:
        doc = self._read(self.DESIRED_ID, device_id)
        return None if doc is None else DesiredConfiguration.model_validate(doc["body"])

    def save_desired_with_outbox(
        self,
        *,
        device_id: str,
        config: DesiredConfiguration,
        expected_revision: int,
        state: DeviceState,
        expected_projection_version: int,
        audit: AuditRecord,
        outbox: OutboxMessage,
    ) -> bool:
        desired = self._read(self.DESIRED_ID, device_id)
        current_state = self._read(self.STATE_ID, device_id)
        actual_revision = 0 if desired is None else int(desired["revision"])
        actual_projection = 0 if current_state is None else int(current_state["projection_version"])
        if actual_revision != expected_revision or actual_projection != expected_projection_version:
            return False

        previous = None if current_state is None else DeviceState.model_validate(current_state["body"])
        state = state.model_copy(deep=True)
        state.projection_version = expected_projection_version + 1
        desired_doc = {
            "id": self.DESIRED_ID,
            "device_id": device_id,
            "doc_type": "desired_config",
            "revision": config.revision,
            "body": _body(config),
        }
        operations: list[tuple[Any, ...]] = []
        if desired is None:
            operations.append(("create", (desired_doc,)))
        else:
            operations.append(
                ("replace", (self.DESIRED_ID, desired_doc), {"if_match_etag": desired.get("_etag")})
            )
        state_doc = self._state_doc(state)
        if current_state is None:
            operations.append(("create", (state_doc,)))
        else:
            operations.append(
                ("replace", (self.STATE_ID, state_doc), {"if_match_etag": current_state.get("_etag")})
            )
        operations.extend(
            [
                ("create", (self._audit_doc(audit, device_id),)),
                ("create", (self._outbox_doc(outbox),)),
                ("create", (self._summary_delta_doc(previous, state),)),
            ]
        )
        try:
            self.container.execute_item_batch(batch_operations=operations, partition_key=device_id)
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc):
                return False
            raise

    def get_latest_config_ack(self, device_id: str) -> ConfigAck | None:
        rows = list(
            self.container.query_items(
                query=(
                    "SELECT TOP 1 c.body FROM c WHERE c.device_id=@device "
                    "AND c.doc_type='config_ack' ORDER BY c.revision DESC,c.responded_at DESC"
                ),
                parameters=[{"name": "@device", "value": device_id}],
                partition_key=device_id,
            )
        )
        return None if not rows else ConfigAck.model_validate(rows[0]["body"])

    def save_config_ack_with_state(
        self,
        *,
        ack: ConfigAck,
        state: DeviceState,
        expected_projection_version: int,
        audit: AuditRecord,
    ) -> bool:
        current = self._read(self.STATE_ID, ack.device_id)
        actual = 0 if current is None else int(current["projection_version"])
        if actual != expected_projection_version:
            return False
        previous = None if current is None else DeviceState.model_validate(current["body"])
        state = state.model_copy(deep=True)
        state.projection_version = expected_projection_version + 1
        ack_doc = {
            "id": f"config-ack:{ack.ack_id}",
            "device_id": ack.device_id,
            "doc_type": "config_ack",
            "revision": ack.revision,
            "responded_at": ack.responded_at.isoformat(),
            "body": _body(ack),
        }
        operations: list[tuple[Any, ...]] = [("create", (ack_doc,))]
        state_doc = self._state_doc(state)
        if current is None:
            operations.append(("create", (state_doc,)))
        else:
            operations.append(
                ("replace", (self.STATE_ID, state_doc), {"if_match_etag": current.get("_etag")})
            )
        operations.append(("create", (self._audit_doc(audit, ack.device_id),)))
        operations.append(("create", (self._summary_delta_doc(previous, state),)))
        try:
            self.container.execute_item_batch(batch_operations=operations, partition_key=ack.device_id)
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc):
                return False
            raise

    @staticmethod
    def _command_doc(command: DirectCommand) -> dict[str, Any]:
        return {
            "id": f"command:{command.command_id}",
            "device_id": command.device_id,
            "doc_type": "command",
            "command_id": command.command_id,
            "status": command.status.value,
            "requested_at": command.requested_at.isoformat(),
            "expires_at": command.expires_at.isoformat(),
            "sort_key": f"{command.requested_at.isoformat()}|{command.command_id}",
            "body": _body(command),
        }

    def create_command_with_outbox(
        self,
        *,
        command: DirectCommand,
        audit: AuditRecord,
        outbox: OutboxMessage,
    ) -> DirectCommand:
        idem_id = None
        if command.idempotency_key:
            idem_id = f"idem:{_hash_key(command.idempotency_key)}"
            existing = self._read(idem_id, command.device_id)
            if existing is not None:
                found = self._read(f"command:{existing['command_id']}", command.device_id)
                if found is not None:
                    return DirectCommand.model_validate(found["body"])

        operations: list[tuple[Any, ...]] = [
            ("create", (self._command_doc(command),)),
            ("create", (self._audit_doc(audit, command.device_id),)),
            ("create", (self._outbox_doc(outbox),)),
        ]
        if idem_id is not None:
            operations.append(
                (
                    "create",
                    (
                        {
                            "id": idem_id,
                            "device_id": command.device_id,
                            "doc_type": "command_idempotency",
                            "command_id": command.command_id,
                        },
                    ),
                )
            )
        try:
            self.container.execute_item_batch(batch_operations=operations, partition_key=command.device_id)
            return command
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc) and idem_id is not None:
                existing = self._read(idem_id, command.device_id)
                if existing is not None:
                    found = self._read(f"command:{existing['command_id']}", command.device_id)
                    if found is not None:
                        return DirectCommand.model_validate(found["body"])
            raise

    def _find_command_doc(self, command_id: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT TOP 1 * FROM c WHERE c.doc_type='command' AND c.command_id=@command",
            [{"name": "@command", "value": command_id}],
        )
        return rows[0] if rows else None

    def get_command(self, command_id: str) -> DirectCommand | None:
        doc = self._find_command_doc(command_id)
        return None if doc is None else DirectCommand.model_validate(doc["body"])

    def transition_command(
        self,
        *,
        command_id: str,
        expected_statuses: Sequence[CommandStatus],
        new_status: CommandStatus,
        ack: CommandAck | None = None,
        audit: AuditRecord | None = None,
    ) -> DirectCommand | None:
        doc = self._find_command_doc(command_id)
        if doc is None:
            return None
        command = DirectCommand.model_validate(doc["body"])
        if command.status not in set(expected_statuses):
            return command
        command.status = new_status
        operations: list[tuple[Any, ...]] = [
            (
                "replace",
                (doc["id"], self._command_doc(command)),
                {"if_match_etag": doc.get("_etag")},
            )
        ]
        if ack is not None:
            operations.append(
                (
                    "create",
                    (
                        {
                            "id": f"command-ack:{ack.ack_id}",
                            "device_id": command.device_id,
                            "doc_type": "command_ack",
                            "command_id": command.command_id,
                            "body": _body(ack),
                        },
                    ),
                )
            )
        if audit is not None:
            operations.append(("create", (self._audit_doc(audit, command.device_id),)))
        try:
            self.container.execute_item_batch(batch_operations=operations, partition_key=command.device_id)
            return command
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc):
                return self.get_command(command_id)
            raise

    def list_commands_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        device_id: str | None = None,
        status: CommandStatus | None = None,
    ) -> CommandPage:
        limit = max(1, min(limit, 500))
        decoded = _cursor_decode(cursor, 1)
        clauses = ["c.doc_type='command'"]
        parameters: list[dict[str, Any]] = []
        if decoded:
            clauses.append("c.sort_key < @cursor")
            parameters.append({"name": "@cursor", "value": decoded[0]})
        if device_id:
            clauses.append("c.device_id=@device")
            parameters.append({"name": "@device", "value": device_id})
        if status:
            clauses.append("c.status=@status")
            parameters.append({"name": "@status", "value": status.value})
        rows = self._query(
            f"SELECT TOP {limit + 1} c.sort_key,c.body FROM c "
            f"WHERE {' AND '.join(clauses)} ORDER BY c.sort_key DESC",
            parameters,
        )
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = [DirectCommand.model_validate(row["body"]) for row in visible]
        next_cursor = _cursor_encode([visible[-1]["sort_key"]]) if has_more and visible else None
        return CommandPage(items=items, next_cursor=next_cursor, page_size=len(items))

    def list_expired_commands(self, now: datetime, limit: int = 100) -> list[DirectCommand]:
        rows = self._query(
            f"SELECT TOP {max(1, min(limit, 500))} c.body FROM c WHERE c.doc_type='command' "
            "AND (c.status=@queued OR c.status=@dispatched) AND c.expires_at<=@now "
            "ORDER BY c.expires_at ASC",
            [
                {"name": "@queued", "value": CommandStatus.QUEUED.value},
                {"name": "@dispatched", "value": CommandStatus.DISPATCHED.value},
                {"name": "@now", "value": now.isoformat()},
            ],
        )
        return [DirectCommand.model_validate(row["body"]) for row in rows]

    def append_audit(self, record: AuditRecord) -> None:
        device_id = "__audit__"
        self.container.create_item(self._audit_doc(record, device_id))

    def list_audit_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        correlation_id: str | None = None,
        request_correlation_id: str | None = None,
        target: str | None = None,
    ) -> AuditPage:
        limit = max(1, min(limit, 500))
        decoded = _cursor_decode(cursor, 2)
        clauses = ["c.doc_type='audit'"]
        parameters: list[dict[str, Any]] = []
        if correlation_id is not None:
            clauses.append("c.correlation_id=@correlation")
            parameters.append({"name": "@correlation", "value": correlation_id})
        if request_correlation_id is not None:
            clauses.append("c.request_correlation_id=@request")
            parameters.append({"name": "@request", "value": request_correlation_id})
        if target is not None:
            clauses.append("c.target=@target")
            parameters.append({"name": "@target", "value": target})
        if decoded:
            clauses.append("(c.created_at < @created OR (c.created_at = @created AND c.audit_id < @audit))")
            parameters.extend([
                {"name": "@created", "value": decoded[0]},
                {"name": "@audit", "value": decoded[1]},
            ])
        query = (
            f"SELECT TOP {limit + 1} c.created_at,c.audit_id,c.body FROM c "
            f"WHERE {' AND '.join(clauses)} ORDER BY c.created_at DESC,c.audit_id DESC"
        )
        rows = self._query(query, parameters)
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = [AuditRecord.model_validate(row["body"]) for row in visible]
        next_cursor = None
        if has_more and visible:
            next_cursor = _cursor_encode([visible[-1]["created_at"], visible[-1]["audit_id"]])
        return AuditPage(items=items, next_cursor=next_cursor, page_size=len(items))

    def _find_outbox_doc(self, outbox_id: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT TOP 1 * FROM c WHERE c.doc_type='outbox' AND c.outbox_id=@outbox",
            [{"name": "@outbox", "value": outbox_id}],
        )
        return rows[0] if rows else None

    def claim_outbox(
        self,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> list[OutboxMessage]:
        candidate_limit = max(1, min(limit * 4, 500))
        rows = self._query(
            f"SELECT TOP {candidate_limit} * FROM c WHERE c.doc_type='outbox' "
            "AND (c.status=@pending OR (c.status=@leased AND c.lease_until<=@now)) "
            "AND c.available_at<=@now ORDER BY c.available_at ASC",
            [
                {"name": "@pending", "value": OutboxStatus.PENDING.value},
                {"name": "@leased", "value": OutboxStatus.LEASED.value},
                {"name": "@now", "value": now.isoformat()},
            ],
        )
        claimed: list[OutboxMessage] = []
        for doc in rows:
            if len(claimed) >= limit:
                break
            message = OutboxMessage.model_validate(doc["body"])
            message.status = OutboxStatus.LEASED
            message.lease_owner = owner
            message.lease_until = now + timedelta(seconds=max(1, lease_seconds))
            message.attempts += 1
            replacement = self._outbox_doc(message)
            try:
                self.container.execute_item_batch(
                    [
                        (
                            "replace",
                            (doc["id"], replacement),
                            {"if_match_etag": doc.get("_etag")},
                        )
                    ],
                    partition_key=message.device_id,
                )
                claimed.append(message)
            except Exception as exc:  # noqa: BLE001
                if not _is_conflict(exc):
                    raise
        return claimed

    def update_outbox(
        self,
        *,
        outbox_id: str,
        owner: str,
        status: OutboxStatus,
        available_at: datetime | None = None,
        error: str | None = None,
    ) -> bool:
        doc = self._find_outbox_doc(outbox_id)
        if doc is None:
            return False
        message = OutboxMessage.model_validate(doc["body"])
        if message.status != OutboxStatus.LEASED or message.lease_owner != owner:
            return False
        message.status = status
        message.last_error = error
        if available_at is not None:
            message.available_at = available_at
        if status != OutboxStatus.LEASED:
            message.lease_owner = None
            message.lease_until = None
        try:
            self.container.execute_item_batch(
                [
                    (
                        "replace",
                        (doc["id"], self._outbox_doc(message)),
                        {"if_match_etag": doc.get("_etag")},
                    )
                ],
                partition_key=message.device_id,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            if _is_conflict(exc):
                return False
            raise

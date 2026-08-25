from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fleetplane.core.lifecycle import ensure_command_allowed
from fleetplane.domain.enums import AckCode, CommandKind, CommandStatus, OutboxKind
from fleetplane.domain.models import AuditRecord, CommandAck, DirectCommand, OutboxMessage
from fleetplane.ports.metrics import MetricSink, NullMetricSink
from fleetplane.ports.store import FleetStore


class CommandService:
    def __init__(self, store: FleetStore, metrics: MetricSink | None = None) -> None:
        self.store = store
        self.metrics = metrics or NullMetricSink()

    def issue(
        self,
        device_id: str,
        kind: CommandKind,
        actor: str,
        payload: dict[str, object] | None = None,
        ttl_seconds: int = 30,
        idempotency_key: str | None = None,
        request_correlation_id: str | None = None,
    ) -> DirectCommand:
        ensure_command_allowed(self.store.get_device_state(device_id), device_id, kind)
        now = datetime.now(UTC)
        command = DirectCommand(
            device_id=device_id,
            kind=kind,
            actor=actor,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
            request_correlation_id=request_correlation_id,
            requested_at=now,
            expires_at=now + timedelta(seconds=max(5, min(ttl_seconds, 300))),
        )
        audit = AuditRecord(
            actor=actor,
            action="command.queued",
            target=device_id,
            correlation_id=command.command_id,
            request_correlation_id=request_correlation_id,
            details={"kind": kind.value, "expires_at": command.expires_at.isoformat()},
        )
        outbox = OutboxMessage(
            device_id=device_id,
            kind=OutboxKind.DIRECT_COMMAND,
            correlation_id=command.command_id,
            request_correlation_id=request_correlation_id,
            payload=command.model_dump(mode="json"),
        )
        return self.store.create_command_with_outbox(command=command, audit=audit, outbox=outbox)

    def mark_dispatched(self, command_id: str) -> DirectCommand | None:
        command = self.store.get_command(command_id)
        if command is None:
            return None
        return self.store.transition_command(
            command_id=command_id,
            expected_statuses=[CommandStatus.QUEUED],
            new_status=CommandStatus.DISPATCHED,
            audit=AuditRecord(
                actor="dispatcher",
                action="command.dispatched",
                target=command_id,
                correlation_id=command_id,
                request_correlation_id=command.request_correlation_id,
            ),
        )

    def record_ack(self, ack: CommandAck) -> DirectCommand | None:
        command = self.store.get_command(ack.command_id)
        if command is None:
            return None
        if ack.device_id != command.device_id:
            self.store.append_audit(
                AuditRecord(
                    actor="device",
                    action="command.ack.device_mismatch",
                    target=ack.device_id,
                    correlation_id=ack.command_id,
                    request_correlation_id=command.request_correlation_id,
                    details={"expected_device_id": command.device_id},
                )
            )
            return command
        new_status = CommandStatus.ACCEPTED if ack.code == AckCode.ACCEPTED else CommandStatus.REJECTED
        transitioned = self.store.transition_command(
            command_id=ack.command_id,
            expected_statuses=[CommandStatus.QUEUED, CommandStatus.DISPATCHED],
            new_status=new_status,
            ack=ack,
            audit=AuditRecord(
                actor="device",
                action="command.ack",
                target=ack.device_id,
                correlation_id=ack.command_id,
                request_correlation_id=command.request_correlation_id,
                details=ack.model_dump(mode="json"),
            ),
        )
        if transitioned is not None and transitioned.status == new_status:
            latency = max(0.0, (ack.responded_at - transitioned.requested_at).total_seconds())
            self.metrics.observe_command(new_status, latency)
        return transitioned

    def mark_delivery_failed(self, command_id: str, reason: str) -> DirectCommand | None:
        command = self.store.get_command(command_id)
        if command is None:
            return None
        transitioned = self.store.transition_command(
            command_id=command_id,
            expected_statuses=[CommandStatus.QUEUED, CommandStatus.DISPATCHED],
            new_status=CommandStatus.DELIVERY_FAILED,
            audit=AuditRecord(
                actor="dispatcher",
                action="command.delivery_failed",
                target=command.device_id,
                correlation_id=command_id,
                request_correlation_id=command.request_correlation_id,
                details={"reason": reason[:512]},
            ),
        )
        if transitioned is not None and transitioned.status == CommandStatus.DELIVERY_FAILED:
            self.metrics.observe_command(CommandStatus.DELIVERY_FAILED, None)
        return transitioned

    def expire_pending(self, now: datetime | None = None, limit: int = 100) -> int:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        expired = 0
        for command in self.store.list_expired_commands(now, limit=limit):
            transitioned = self.store.transition_command(
                command_id=command.command_id,
                expected_statuses=[CommandStatus.QUEUED, CommandStatus.DISPATCHED],
                new_status=CommandStatus.TIMED_OUT,
                audit=AuditRecord(
                    actor="system",
                    action="command.timed_out",
                    target=command.device_id,
                    correlation_id=command.command_id,
                    request_correlation_id=command.request_correlation_id,
                ),
            )
            if transitioned is not None and transitioned.status == CommandStatus.TIMED_OUT:
                self.metrics.observe_command(CommandStatus.TIMED_OUT, None)
                expired += 1
        return expired

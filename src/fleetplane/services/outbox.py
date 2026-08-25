from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fleetplane.core.commands import CommandService
from fleetplane.core.configuration import ConfigurationService
from fleetplane.domain.enums import CommandStatus, OutboxKind, OutboxStatus
from fleetplane.domain.models import DesiredConfiguration, DirectCommand
from fleetplane.ports.gateway import DeviceGateway
from fleetplane.ports.store import FleetStore
from fleetplane.observability import correlation_context, operation_log


class OutboxDispatcher:
    def __init__(
        self,
        store: FleetStore,
        gateway: DeviceGateway,
        configuration: ConfigurationService,
        commands: CommandService,
        worker_id: str = "local-dispatcher",
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.configuration = configuration
        self.commands = commands
        self.worker_id = worker_id

    def dispatch_once(self, limit: int = 50, now: datetime | None = None) -> dict[str, int]:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        claimed = self.store.claim_outbox(
            owner=self.worker_id,
            now=now,
            lease_seconds=30,
            limit=limit,
        )
        stats = {"claimed": len(claimed), "done": 0, "retried": 0, "failed": 0}
        for message in claimed:
            trace_id = message.request_correlation_id or message.correlation_id
            with correlation_context(trace_id):
                operation_log(
                    "outbox.dispatch.started",
                    outbox_id=message.outbox_id,
                    operation_id=message.correlation_id,
                    device_id=message.device_id,
                    kind=message.kind.value,
                    attempt=message.attempts,
                )
                try:
                    if message.kind == OutboxKind.DESIRED_CONFIGURATION:
                        result = self.gateway.push_desired(
                            message.device_id,
                            DesiredConfiguration.model_validate(message.payload),
                        )
                        if result.config_ack is not None:
                            self.configuration.record_ack(result.config_ack)
                    else:
                        command = self.store.get_command(message.correlation_id)
                        if command is None:
                            self._finish(message.outbox_id, OutboxStatus.FAILED, "command_not_found")
                            stats["failed"] += 1
                            operation_log("outbox.dispatch.failed", reason="command_not_found")
                            continue
                        if command.status not in {CommandStatus.QUEUED, CommandStatus.DISPATCHED}:
                            self._finish(message.outbox_id, OutboxStatus.DONE)
                            stats["done"] += 1
                            operation_log("outbox.dispatch.skipped", reason="command_terminal")
                            continue
                        if command.expires_at <= now:
                            self.commands.expire_pending(now=now)
                            self._finish(message.outbox_id, OutboxStatus.DONE)
                            stats["done"] += 1
                            operation_log("outbox.dispatch.skipped", reason="command_expired")
                            continue
                        result = self.gateway.invoke_direct(command)
                        if result.command_ack is not None:
                            self.commands.record_ack(result.command_ack)
                        elif result.accepted:
                            self.commands.mark_dispatched(command.command_id)

                    if result.accepted:
                        self._finish(message.outbox_id, OutboxStatus.DONE)
                        stats["done"] += 1
                        operation_log("outbox.dispatch.done")
                    elif result.retryable:
                        delay = min(300, 2 ** min(message.attempts, 8))
                        self.store.update_outbox(
                            outbox_id=message.outbox_id,
                            owner=self.worker_id,
                            status=OutboxStatus.PENDING,
                            available_at=now + timedelta(seconds=delay),
                            error=result.error,
                        )
                        stats["retried"] += 1
                        operation_log("outbox.dispatch.retry", delay_seconds=delay, error=result.error)
                    else:
                        if message.kind == OutboxKind.DIRECT_COMMAND:
                            self.commands.mark_delivery_failed(
                                message.correlation_id, result.error or "delivery_failed"
                            )
                        self._finish(message.outbox_id, OutboxStatus.FAILED, result.error)
                        stats["failed"] += 1
                        operation_log("outbox.dispatch.failed", error=result.error)
                except Exception as exc:  # noqa: BLE001 - provider failure becomes durable retry state
                    delay = min(300, 2 ** min(message.attempts, 8))
                    self.store.update_outbox(
                        outbox_id=message.outbox_id,
                        owner=self.worker_id,
                        status=OutboxStatus.PENDING,
                        available_at=now + timedelta(seconds=delay),
                        error=type(exc).__name__,
                    )
                    stats["retried"] += 1
                    operation_log(
                        "outbox.dispatch.exception",
                        delay_seconds=delay,
                        error_type=type(exc).__name__,
                    )
        return stats

    def _finish(self, outbox_id: str, status: OutboxStatus, error: str | None = None) -> None:
        self.store.update_outbox(
            outbox_id=outbox_id,
            owner=self.worker_id,
            status=status,
            error=error,
        )

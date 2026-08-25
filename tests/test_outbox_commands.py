from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from fleetplane.domain.enums import AckCode, CommandKind, CommandStatus, OutboxStatus
from fleetplane.domain.models import CommandAck
from tests.conftest import provision_active


def test_command_idempotency_key_returns_same_command(runtime):
    provision_active(runtime, "edge-1")
    first = runtime.commands.issue(
        "edge-1", CommandKind.PING, actor="operator", idempotency_key="request-123"
    )
    second = runtime.commands.issue(
        "edge-1", CommandKind.PING, actor="operator", idempotency_key="request-123"
    )
    assert first.command_id == second.command_id
    page = runtime.store.list_commands_page(limit=10)
    assert len(page.items) == 1


def test_two_workers_cannot_claim_same_outbox(runtime):
    provision_active(runtime, "edge-1")
    command = runtime.commands.issue("edge-1", CommandKind.PING, actor="operator")
    assert command.status == CommandStatus.QUEUED
    now = datetime.now(UTC)

    def claim(owner: str):
        return runtime.store.claim_outbox(owner=owner, now=now, lease_seconds=30, limit=10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(claim, ["worker-a", "worker-b"]))
    ids = [m.outbox_id for m in [*a, *b]]
    assert len(ids) == 1
    assert len(set(ids)) == 1


def test_wrong_device_ack_does_not_transition_command(runtime):
    provision_active(runtime, "edge-1")
    command = runtime.commands.issue("edge-1", CommandKind.PING, actor="operator")
    runtime.commands.record_ack(
        CommandAck(command_id=command.command_id, device_id="edge-2", code=AckCode.ACCEPTED)
    )
    assert runtime.store.get_command(command.command_id).status == CommandStatus.QUEUED


def test_terminal_outbox_update_requires_lease_owner(runtime):
    provision_active(runtime, "edge-1")
    runtime.commands.issue("edge-1", CommandKind.PING, actor="operator")
    [message] = runtime.store.claim_outbox(
        owner="worker-a", now=datetime.now(UTC), lease_seconds=30, limit=1
    )
    assert not runtime.store.update_outbox(
        outbox_id=message.outbox_id,
        owner="worker-b",
        status=OutboxStatus.DONE,
    )
    assert runtime.store.update_outbox(
        outbox_id=message.outbox_id,
        owner="worker-a",
        status=OutboxStatus.DONE,
    )

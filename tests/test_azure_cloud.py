from __future__ import annotations

import copy
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fleetplane.adapters.azure_iothub import AzureIoTHubGateway
from fleetplane.adapters.cosmos_store import CosmosFleetStore
from fleetplane.adapters.cosmos_summary import CosmosSummaryStore
from fleetplane.adapters.inmemory_gateway import InMemoryDeviceGateway
from fleetplane.azure_ingress import PermanentIngressError, decode_iothub_event
from fleetplane.core.summary_projection import SummaryProjector
from fleetplane.domain.enums import AckCode, CommandKind, CommandStatus, OutboxStatus
from fleetplane.domain.models import CommandAck, ConfigurationPatch, DesiredConfiguration, DirectCommand
from fleetplane.runtime import Settings, build_runtime
from fleetplane.simulator.fleet import FleetSimulator
from tests.conftest import provision_active, telemetry


class FakeCosmosError(RuntimeError):
    def __init__(self, status_code: int, error_index: int | None = None) -> None:
        super().__init__(f"cosmos:{status_code}")
        self.status_code = status_code
        self.error_index = error_index


class FakeCosmosContainer:
    def __init__(self, partition_field: str = "device_id") -> None:
        self.docs: dict[tuple[str, str], dict[str, Any]] = {}
        self._etag = 0
        self.partition_field = partition_field

    def _stamp(self, body: dict[str, Any]) -> dict[str, Any]:
        self._etag += 1
        value = copy.deepcopy(body)
        value["_etag"] = str(self._etag)
        return value

    def read_item(self, *, item: str, partition_key: str):
        try:
            return copy.deepcopy(self.docs[(partition_key, item)])
        except KeyError as exc:
            raise FakeCosmosError(404) from exc

    def create_item(self, body: dict[str, Any]):
        key = (body[self.partition_field], body["id"])
        if key in self.docs:
            raise FakeCosmosError(409)
        value = self._stamp(body)
        self.docs[key] = value
        return copy.deepcopy(value)

    def execute_item_batch(self, batch_operations, partition_key: str):
        staged = copy.deepcopy(self.docs)
        etag = self._etag
        for index, operation in enumerate(batch_operations):
            kind, args, *rest = operation
            kwargs = rest[0] if rest else {}
            if kind == "create":
                [body] = args
                key = (partition_key, body["id"])
                if key in staged:
                    raise FakeCosmosError(409, index)
                etag += 1
                value = copy.deepcopy(body)
                value["_etag"] = str(etag)
                staged[key] = value
            elif kind == "replace":
                item_id, body = args
                key = (partition_key, item_id)
                current = staged.get(key)
                if current is None:
                    raise FakeCosmosError(404, index)
                expected = kwargs.get("if_match_etag")
                if expected is not None and current.get("_etag") != expected:
                    raise FakeCosmosError(412, index)
                etag += 1
                value = copy.deepcopy(body)
                value["_etag"] = str(etag)
                staged[key] = value
            else:  # pragma: no cover - test fake only supports operations FleetPlane uses
                raise AssertionError(kind)
        self.docs = staged
        self._etag = etag
        return [{} for _ in batch_operations]

    def query_items(
        self,
        *,
        query: str,
        parameters: list[dict[str, Any]],
        enable_cross_partition_query: bool = False,
        partition_key: str | None = None,
    ):
        del enable_cross_partition_query
        params = {item["name"]: item["value"] for item in parameters}
        rows = [copy.deepcopy(value) for (pk, _), value in self.docs.items() if partition_key in {None, pk}]
        normalized = " ".join(query.split())

        kinds = re.findall(r"c\.doc_type\s*=\s*'([^']+)'", normalized)
        if kinds:
            rows = [row for row in rows if row.get("doc_type") in kinds]
        if "c.device_id > @after" in normalized:
            rows = [row for row in rows if row["device_id"] > params["@after"]]
        if "c.health_state = @health" in normalized:
            rows = [row for row in rows if row.get("health_state") == params["@health"]]
        if "c.lifecycle = @lifecycle" in normalized:
            rows = [row for row in rows if row.get("lifecycle") == params["@lifecycle"]]
        if "c.site_id = @site" in normalized:
            rows = [row for row in rows if row.get("site_id") == params["@site"]]
        if "c.fleet_id = @fleet" in normalized:
            rows = [row for row in rows if row.get("fleet_id") == params["@fleet"]]
        if "c.device_id=@device" in normalized or "c.device_id = @device" in normalized:
            rows = [row for row in rows if row.get("device_id") == params["@device"]]
        if "c.command_id=@command" in normalized:
            rows = [row for row in rows if row.get("command_id") == params["@command"]]
        if "c.status=@status" in normalized:
            rows = [row for row in rows if row.get("status") == params["@status"]]
        if "c.sort_key < @cursor" in normalized:
            rows = [row for row in rows if row.get("sort_key", "") < params["@cursor"]]
        if "c.outbox_id=@outbox" in normalized:
            rows = [row for row in rows if row.get("outbox_id") == params["@outbox"]]
        if "c.expires_at<=@now" in normalized:
            rows = [
                row
                for row in rows
                if row.get("status") in {params["@queued"], params["@dispatched"]}
                and row.get("expires_at", "") <= params["@now"]
            ]
        if "c.available_at<=@now" in normalized:
            rows = [
                row
                for row in rows
                if row.get("available_at", "") <= params["@now"]
                and (
                    row.get("status") == params["@pending"]
                    or (
                        row.get("status") == params["@leased"]
                        and (row.get("lease_until") or "") <= params["@now"]
                    )
                )
            ]
        if "c.correlation_id=@correlation" in normalized:
            rows = [row for row in rows if row.get("correlation_id") == params["@correlation"]]
        if "c.request_correlation_id=@request" in normalized:
            rows = [row for row in rows if row.get("request_correlation_id") == params["@request"]]
        if "c.target=@target" in normalized:
            rows = [row for row in rows if row.get("target") == params["@target"]]
        if "c.created_at < @created" in normalized:
            rows = [
                row
                for row in rows
                if row.get("created_at", "") < params["@created"]
                or (
                    row.get("created_at") == params["@created"]
                    and row.get("audit_id", "") < params["@audit"]
                )
            ]

        if "ORDER BY c.device_id ASC" in normalized:
            rows.sort(key=lambda row: row["device_id"])
        elif "ORDER BY c.sort_key DESC" in normalized:
            rows.sort(key=lambda row: row.get("sort_key", ""), reverse=True)
        elif "ORDER BY c.expires_at ASC" in normalized:
            rows.sort(key=lambda row: row.get("expires_at", ""))
        elif "ORDER BY c.available_at ASC" in normalized:
            rows.sort(key=lambda row: row.get("available_at", ""))
        elif "ORDER BY c.revision DESC,c.responded_at DESC" in normalized:
            rows.sort(
                key=lambda row: (int(row.get("revision", 0)), row.get("responded_at", "")),
                reverse=True,
            )
        elif "ORDER BY c.created_at DESC,c.audit_id DESC" in normalized:
            rows.sort(
                key=lambda row: (row.get("created_at", ""), row.get("audit_id", "")),
                reverse=True,
            )

        top = re.search(r"SELECT TOP (\d+)", normalized)
        if top:
            rows = rows[: int(top.group(1))]
        return rows


class FakeIoTHubManager:
    def __init__(self) -> None:
        self.twin_updates: list[tuple[str, Any, str | None]] = []
        self.method_result = SimpleNamespace(status=200, payload=None)
        self.fail: BaseException | None = None

    def get_twin(self, device_id: str):
        if self.fail:
            raise self.fail
        return SimpleNamespace(device_id=device_id, etag="etag-1")

    def update_twin(self, device_id: str, patch: Any, etag: str | None = None):
        if self.fail:
            raise self.fail
        self.twin_updates.append((device_id, patch, etag))
        return SimpleNamespace(etag="etag-2")

    def invoke_device_method(self, device_id: str, request: Any):
        if self.fail:
            raise self.fail
        return self.method_result


class FakeProviderError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(str(status_code))
        self.status_code = status_code


def test_cosmos_runtime_reference_scenario(tmp_path: Path):
    store = CosmosFleetStore(FakeCosmosContainer())
    gateway = InMemoryDeviceGateway()
    runtime = build_runtime(Settings(mode="azure"), store=store, gateway=gateway)
    simulator = FleetSimulator(
        runtime.store,
        tmp_path / "devices",
        device_count=12,
        restricted_devices=2,
        metrics=runtime.metrics,
        gateway=gateway,
    )
    try:
        result = simulator.run_reference_scenario()
        assert all(result["assertions"].values()), result
        summary = store.get_fleet_summary()
        assert summary.total_devices == 12
        assert summary.config_converged == 12
        page = store.list_device_states_page(limit=5)
        assert len(page.items) == 5
        assert page.next_cursor
    finally:
        simulator.close()
        runtime.close()


def test_cosmos_duplicate_idempotency_outbox_and_audit():
    container = FakeCosmosContainer()
    store = CosmosFleetStore(container)
    runtime = build_runtime(Settings(mode="azure"), store=store, gateway=InMemoryDeviceGateway())
    try:
        provision_active(runtime, "edge-1")
        event = telemetry("edge-1", 0)
        first = runtime.ingestion.ingest(event)
        duplicate = runtime.ingestion.ingest(event)
        assert first.disposition.value == "accepted"
        assert duplicate.disposition.value == "duplicate"

        runtime.configuration.set_desired(
            "edge-1", ConfigurationPatch(anomaly_threshold=0.7), actor="operator"
        )
        command = runtime.commands.issue(
            "edge-1",
            CommandKind.PING,
            actor="operator",
            idempotency_key="request-1",
        )
        again = runtime.commands.issue(
            "edge-1",
            CommandKind.PING,
            actor="operator",
            idempotency_key="request-1",
        )
        assert command.command_id == again.command_id

        [leased] = store.claim_outbox(
            owner="worker-a",
            now=datetime.now(UTC) + timedelta(seconds=1),
            lease_seconds=30,
            limit=1,
        )
        assert leased.status == OutboxStatus.LEASED
        assert not store.update_outbox(
            outbox_id=leased.outbox_id,
            owner="wrong-worker",
            status=OutboxStatus.DONE,
        )
        assert store.update_outbox(
            outbox_id=leased.outbox_id,
            owner="worker-a",
            status=OutboxStatus.DONE,
        )

        commands = store.list_commands_page(limit=10, device_id="edge-1")
        assert len(commands.items) == 1
        audit = store.list_audit_page(limit=2)
        assert audit.items
    finally:
        runtime.close()


def test_iothub_gateway_preserves_cloud_vs_device_semantics():
    manager = FakeIoTHubManager()
    gateway = AzureIoTHubGateway(
        manager,
        twin_factory=lambda config: {"desired": config.model_dump(mode="json")},
        method_factory=lambda command, payload: {"kind": command.kind.value, "payload": payload},
    )
    config = DesiredConfiguration(revision=1)
    result = gateway.push_desired("edge-1", config)
    assert result.accepted
    assert result.config_ack is None
    assert manager.twin_updates[0][0] == "edge-1"

    command = DirectCommand(
        command_id="cmd-1",
        device_id="edge-1",
        kind=CommandKind.PING,
        actor="operator",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    manager.method_result = SimpleNamespace(
        status=200,
        payload=CommandAck(
            command_id=command.command_id,
            device_id=command.device_id,
            code=AckCode.ACCEPTED,
        ).model_dump(mode="json"),
    )
    method = gateway.invoke_direct(command)
    assert method.accepted
    assert method.command_ack is not None
    assert method.command_ack.code == AckCode.ACCEPTED

    manager.method_result = SimpleNamespace(status=409, payload={"detail": "busy"})
    rejected = gateway.invoke_direct(command)
    assert rejected.accepted
    assert rejected.command_ack is not None
    assert rejected.command_ack.code == AckCode.REJECTED


def test_iothub_gateway_classifies_provider_failures():
    manager = FakeIoTHubManager()
    manager.fail = FakeProviderError(429)
    gateway = AzureIoTHubGateway(
        manager,
        twin_factory=lambda config: {},
        method_factory=lambda command, payload: {},
    )
    result = gateway.push_desired("edge-1", DesiredConfiguration(revision=1))
    assert not result.accepted
    assert result.retryable

    manager.fail = FakeProviderError(404)
    result = gateway.push_desired("edge-1", DesiredConfiguration(revision=1))
    assert not result.accepted
    assert not result.retryable


def test_eventhub_decoder_requires_authenticated_identity():
    event = SimpleNamespace(
        get_body=lambda: b'{"kind":"telemetry","payload":{"device_id":"edge-1"}}',
        iothub_metadata={"iothub-connection-device-id": "edge-1"},
        metadata={},
    )
    decoded = decode_iothub_event(event)
    assert decoded.authenticated_device_id == "edge-1"
    assert decoded.payload["kind"] == "telemetry"

    no_identity = SimpleNamespace(get_body=lambda: b"{}", iothub_metadata={}, metadata={})
    try:
        decode_iothub_event(no_identity)
    except PermanentIngressError as exc:
        assert str(exc) == "missing_authenticated_device_id"
    else:  # pragma: no cover
        raise AssertionError("missing identity was accepted")


def test_cosmos_expiry_audit_pagination_and_cas_conflict():
    store = CosmosFleetStore(FakeCosmosContainer())
    runtime = build_runtime(Settings(mode="azure"), store=store, gateway=InMemoryDeviceGateway())
    try:
        provision_active(runtime, "edge-1")
        runtime.ingestion.ingest(telemetry("edge-1", 0))
        state = store.get_device_state("edge-1")
        assert state is not None
        stale = state.model_copy(deep=True)
        stale.health_reasons = ["stale-write"]
        assert store.compare_and_swap_device_state(stale, state.projection_version)
        assert not store.compare_and_swap_device_state(stale, state.projection_version)

        for index in range(3):
            runtime.commands.issue(
                "edge-1",
                CommandKind.PING,
                actor=f"operator-{index}",
                ttl_seconds=5,
                idempotency_key=f"expiry-{index}",
            )
        expired = runtime.commands.expire_pending(
            now=datetime.now(UTC) + timedelta(minutes=1),
            limit=10,
        )
        assert expired == 3
        page = store.list_commands_page(limit=10, status=CommandStatus.TIMED_OUT)
        assert len(page.items) == 3

        first = store.list_audit_page(limit=2)
        assert len(first.items) == 2
        assert first.next_cursor
        second = store.list_audit_page(limit=2, cursor=first.next_cursor)
        assert second.items
        assert {item.audit_id for item in first.items}.isdisjoint(
            {item.audit_id for item in second.items}
        )
    finally:
        runtime.close()


def test_eventhub_decoder_rejects_permanent_payload_failures():
    cases = [
        SimpleNamespace(get_body=lambda: "not-bytes", iothub_metadata={"iothub-connection-device-id": "x"}, metadata={}),
        SimpleNamespace(get_body=lambda: b"\xff", iothub_metadata={"iothub-connection-device-id": "x"}, metadata={}),
        SimpleNamespace(get_body=lambda: b"{", iothub_metadata={"iothub-connection-device-id": "x"}, metadata={}),
        SimpleNamespace(get_body=lambda: b"[]", iothub_metadata={"iothub-connection-device-id": "x"}, metadata={}),
    ]
    reasons = []
    for event in cases:
        try:
            decode_iothub_event(event)
        except PermanentIngressError as exc:
            reasons.append(str(exc))
    assert reasons == [
        "event_body_not_bytes",
        "event_body_not_utf8",
        "event_body_not_json",
        "event_body_not_object",
    ]


def test_iothub_direct_method_transport_without_structured_ack():
    manager = FakeIoTHubManager()
    gateway = AzureIoTHubGateway(
        manager,
        twin_factory=lambda config: {},
        method_factory=lambda command, payload: payload,
    )
    command = DirectCommand(
        device_id="edge-1",
        kind=CommandKind.PING,
        actor="operator",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    manager.method_result = SimpleNamespace(status=204, payload={"result": "ok"})
    result = gateway.invoke_direct(command)
    assert result.accepted
    assert result.command_ack is None

    manager.fail = TimeoutError("provider timeout")
    failed = gateway.invoke_direct(command)
    assert not failed.accepted
    assert failed.retryable


def test_cosmos_lifecycle_and_scope_contract():
    from fleetplane.domain.enums import DeviceLifecycle
    from fleetplane.domain.models import DeviceProvisionRequest

    store = CosmosFleetStore(FakeCosmosContainer())
    runtime = build_runtime(Settings(mode="azure"), store=store, gateway=InMemoryDeviceGateway())
    try:
        runtime.lifecycle.provision(
            DeviceProvisionRequest(device_id="edge-scope", site_id="site-a", fleet_id="fleet-a"),
            actor="operator",
        )
        runtime.lifecycle.transition(
            "edge-scope",
            DeviceLifecycle.ACTIVE,
            actor="operator",
            reason="commissioned",
        )
        assert runtime.ingestion.ingest(telemetry("edge-scope", 0)).disposition.value == "accepted"
        page = store.list_device_states_page(
            limit=10,
            site_id="site-a",
            fleet_id="fleet-a",
            lifecycle=DeviceLifecycle.ACTIVE,
        )
        assert [item.device_id for item in page.items] == ["edge-scope"]
        runtime.lifecycle.transition(
            "edge-scope",
            DeviceLifecycle.DECOMMISSIONED,
            actor="operator",
            reason="retired",
        )
        assert runtime.ingestion.ingest(telemetry("edge-scope", 1)).reason == "device_lifecycle_decommissioned"
    finally:
        runtime.close()



def test_cosmos_materialized_summary_is_replay_safe_and_matches_exact(tmp_path: Path):
    control = FakeCosmosContainer()
    summary_container = FakeCosmosContainer(partition_field="scope_id")
    summary_store = CosmosSummaryStore(summary_container)
    store = CosmosFleetStore(control, summary_store=summary_store)
    gateway = InMemoryDeviceGateway()
    runtime = build_runtime(Settings(mode="azure"), store=store, gateway=gateway)
    simulator = FleetSimulator(
        runtime.store,
        tmp_path / "summary-devices",
        device_count=8,
        restricted_devices=2,
        metrics=runtime.metrics,
        gateway=gateway,
    )
    try:
        result = simulator.run_reference_scenario()
        assert all(result["assertions"].values())
        exact = store.get_fleet_summary_exact()
        # Before change-feed projection exists the correctness fallback remains available.
        assert store.get_fleet_summary().total_devices == exact.total_devices

        deltas = [
            copy.deepcopy(doc)
            for doc in control.docs.values()
            if doc.get("doc_type") == "summary_delta"
        ]
        projector = SummaryProjector(summary_store)
        projected = projector.process_documents(deltas)
        assert projected["deltas"] == len(deltas)
        assert projected["scope_updates"] > 0
        materialized = store.get_fleet_summary()
        assert materialized.model_dump(exclude={"updated_at"}) == exact.model_dump(
            exclude={"updated_at"}
        )

        replay = projector.process_documents(list(reversed(deltas)))
        assert replay["scope_updates"] == 0
        assert store.get_fleet_summary().model_dump(exclude={"updated_at"}) == materialized.model_dump(
            exclude={"updated_at"}
        )

        fleet = summary_store.get_summary("fleet:showcase-fleet")
        site = summary_store.get_summary("site:showcase-site")
        assert fleet is not None and fleet.total_devices == 8
        assert site is not None and site.total_devices == 8
    finally:
        simulator.close()
        runtime.close()


def test_summary_projector_newer_device_version_supersedes_late_older_change():
    from fleetplane.core.summary_projection import SummaryDelta
    from fleetplane.domain.enums import HealthState
    from fleetplane.domain.models import DeviceState

    summary_store = CosmosSummaryStore(FakeCosmosContainer(partition_field="scope_id"))
    projector = SummaryProjector(summary_store)
    base = DeviceState(device_id="edge-order", site_id="site-a", fleet_id="fleet-a")
    base.projection_version = 1
    base.health_state = HealthState.UNKNOWN
    newer = base.model_copy(deep=True)
    newer.projection_version = 3
    newer.health_state = HealthState.HEALTHY
    intermediate = base.model_copy(deep=True)
    intermediate.projection_version = 2
    intermediate.health_state = HealthState.DEGRADED

    # If a newer projection is observed first, the per-device cursor makes a later older event a no-op.
    assert projector.apply(SummaryDelta.from_states(base, newer)) == 3
    assert projector.apply(SummaryDelta.from_states(base, intermediate)) == 0
    summary = summary_store.get_summary("global")
    assert summary is not None
    assert summary.total_devices == 1
    assert summary.healthy == 1
    assert summary.degraded == 0

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from fleetplane.api.app import create_app
from fleetplane.adapters.sqlite_store import SQLiteFleetStore
from fleetplane.domain.models import AuditRecord


def test_correlation_id_is_echoed_and_persisted_in_audit(runtime):
    client = TestClient(create_app(runtime))
    correlation_id = "review-request-001"

    provision = client.post(
        "/v1/devices",
        json={"device_id": "edge-trace", "site_id": "site-a", "fleet_id": "fleet-a"},
        headers={"X-Correlation-ID": correlation_id},
    )
    assert provision.status_code == 201
    assert provision.headers["x-correlation-id"] == correlation_id

    audit = client.get(
        "/v1/audit",
        params={"request_correlation_id": correlation_id},
    )
    assert audit.status_code == 200
    records = audit.json()["items"]
    assert len(records) == 1
    assert records[0]["action"] == "device.provisioned"
    assert records[0]["correlation_id"] == "device:edge-trace:provision"
    assert records[0]["request_correlation_id"] == correlation_id


def test_invalid_correlation_id_is_replaced(runtime):
    client = TestClient(create_app(runtime))
    response = client.get("/health/live", headers={"X-Correlation-ID": "bad value with spaces"})
    assert response.status_code == 200
    generated = response.headers["x-correlation-id"]
    UUID(generated)
    assert generated != "bad value with spaces"


def test_command_keeps_request_trace_separate_from_command_identity(runtime):
    client = TestClient(create_app(runtime))
    client.post(
        "/v1/devices",
        json={"device_id": "edge-command", "site_id": "site-a", "fleet_id": "fleet-a"},
    )
    client.patch(
        "/v1/devices/edge-command/lifecycle",
        json={"lifecycle": "active", "reason": "commissioned"},
    )
    request_id = "operator-request-command-7"
    issued = client.post(
        "/v1/devices/edge-command/commands",
        json={"kind": "ping", "payload": {}, "ttl_seconds": 30},
        headers={"X-Correlation-ID": request_id, "Idempotency-Key": "cmd-idem-7"},
    )
    assert issued.status_code == 202
    body = issued.json()
    assert body["request_correlation_id"] == request_id

    audit = client.get("/v1/audit", params={"request_correlation_id": request_id}).json()["items"]
    assert len(audit) == 1
    assert audit[0]["action"] == "command.queued"
    assert audit[0]["correlation_id"] == body["command_id"]
    assert audit[0]["request_correlation_id"] == request_id


def test_v3_audit_table_migrates_before_new_indexes(tmp_path: Path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    record = AuditRecord(
        actor="legacy",
        action="legacy.action",
        target="edge-legacy",
        correlation_id="legacy-operation",
    )
    conn.executescript(
        """
        CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT INTO schema_meta(key,value) VALUES('schema_version','3');
        CREATE TABLE audit(
            audit_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            body TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO audit(audit_id,created_at,body) VALUES(?,?,?)",
        (record.audit_id, record.created_at.isoformat(), record.model_dump_json()),
    )
    conn.commit()
    conn.close()

    store = SQLiteFleetStore(path)
    try:
        result = store.list_audit_page(limit=10, correlation_id="legacy-operation")
        assert len(result.items) == 1
        assert result.items[0].target == "edge-legacy"
        check = sqlite3.connect(path)
        try:
            columns = {row[1] for row in check.execute("PRAGMA table_info(audit)").fetchall()}
            assert {"correlation_id", "request_correlation_id", "target"} <= columns
            version = check.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
            assert version == "4"
        finally:
            check.close()
    finally:
        store.close()

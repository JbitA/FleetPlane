from __future__ import annotations

import base64
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Sequence

from fleetplane.domain.enums import CommandStatus, DeviceLifecycle, HealthState, OutboxKind, OutboxStatus
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

SCHEMA_VERSION = 4


def _dump(model: Any) -> str:
    return json.dumps(model.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)


def _loads(value: str) -> dict[str, Any]:
    return json.loads(value)


def _cursor_encode(values: list[str]) -> str:
    payload = json.dumps(values, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _cursor_decode(value: str | None, expected: int) -> list[str] | None:
    if value is None:
        return None
    try:
        raw = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except Exception as exc:  # noqa: BLE001 - normalized to API-safe ValueError
        raise ValueError("invalid pagination cursor") from exc
    if not isinstance(decoded, list) or len(decoded) != expected or not all(
        isinstance(v, str) for v in decoded
    ):
        raise ValueError("invalid pagination cursor")
    return decoded


class SQLiteFleetStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        self._closed = False
        self._init_schema()

    def _connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(
                self.path,
                timeout=30,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            with self._connections_lock:
                if self._closed:
                    connection.close()
                    raise RuntimeError("store is closed")
                self._connections.add(connection)
            self._local.connection = connection
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    def close(self) -> None:
        with self._connections_lock:
            if self._closed:
                return
            self._closed = True
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            connection.close()
        self._local.connection = None

    def _init_schema(self) -> None:
        conn = self._connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS device_state (
                device_id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL DEFAULT 'default-site',
                fleet_id TEXT NOT NULL DEFAULT 'default-fleet',
                lifecycle TEXT NOT NULL DEFAULT 'provisioned',
                body TEXT NOT NULL,
                projection_version INTEGER NOT NULL,
                health_state TEXT NOT NULL,
                config_converged INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS telemetry_receipts (
                device_id TEXT NOT NULL,
                device_generation INTEGER NOT NULL,
                boot_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY (device_id, device_generation, boot_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS idx_telemetry_event_id
                ON telemetry_receipts(event_id);
            CREATE TABLE IF NOT EXISTS desired_config (
                device_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL,
                body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS config_ack (
                ack_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                responded_at TEXT NOT NULL,
                body TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_config_ack_device_revision
                ON config_ack(device_id, revision DESC, responded_at DESC);
            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                idempotency_key TEXT,
                body TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_command_idempotency
                ON commands(device_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_commands_device_time
                ON commands(device_id, requested_at DESC, command_id DESC);
            CREATE INDEX IF NOT EXISTS idx_commands_status_expiry
                ON commands(status, requested_at);
            CREATE TABLE IF NOT EXISTS command_ack (
                ack_id TEXT PRIMARY KEY,
                command_id TEXT NOT NULL,
                body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
                audit_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                correlation_id TEXT,
                request_correlation_id TEXT,
                target TEXT,
                body TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_time
                ON audit(created_at DESC, audit_id DESC);
            CREATE TABLE IF NOT EXISTS outbox (
                outbox_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                lease_owner TEXT,
                lease_until TEXT,
                attempts INTEGER NOT NULL,
                last_error TEXT,
                body TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_outbox_claim
                ON outbox(status, available_at, lease_until, created_at);
            CREATE TABLE IF NOT EXISTS fleet_summary (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                total_devices INTEGER NOT NULL,
                healthy INTEGER NOT NULL,
                degraded INTEGER NOT NULL,
                offline INTEGER NOT NULL,
                unknown INTEGER NOT NULL,
                config_converged INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO fleet_summary(
                singleton,total_devices,healthy,degraded,offline,unknown,config_converged,updated_at
            ) VALUES(1,0,0,0,0,0,0,'1970-01-01T00:00:00+00:00');
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(device_state)").fetchall()}
        if "site_id" not in columns:
            conn.execute("ALTER TABLE device_state ADD COLUMN site_id TEXT NOT NULL DEFAULT 'default-site'")
        if "fleet_id" not in columns:
            conn.execute("ALTER TABLE device_state ADD COLUMN fleet_id TEXT NOT NULL DEFAULT 'default-fleet'")
        if "lifecycle" not in columns:
            conn.execute("ALTER TABLE device_state ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'provisioned'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_device_scope ON device_state(site_id,fleet_id,device_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_device_lifecycle ON device_state(lifecycle,device_id)")
        audit_columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit)").fetchall()}
        if "correlation_id" not in audit_columns:
            conn.execute("ALTER TABLE audit ADD COLUMN correlation_id TEXT")
        if "request_correlation_id" not in audit_columns:
            conn.execute("ALTER TABLE audit ADD COLUMN request_correlation_id TEXT")
        if "target" not in audit_columns:
            conn.execute("ALTER TABLE audit ADD COLUMN target TEXT")
        for row in conn.execute("SELECT audit_id,body FROM audit").fetchall():
            record = AuditRecord.model_validate(_loads(row["body"]))
            conn.execute(
                "UPDATE audit SET correlation_id=?,request_correlation_id=?,target=?,body=? WHERE audit_id=?",
                (
                    record.correlation_id,
                    record.request_correlation_id,
                    record.target,
                    _dump(record),
                    record.audit_id,
                ),
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit(correlation_id,created_at DESC,audit_id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_request_correlation ON audit(request_correlation_id,created_at DESC,audit_id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_target ON audit(target,created_at DESC,audit_id DESC)")
        for row in conn.execute("SELECT device_id,body FROM device_state").fetchall():
            body = _loads(row["body"])
            if "lifecycle" not in body:
                body["lifecycle"] = (
                    DeviceLifecycle.ACTIVE.value
                    if body.get("last_received_at") is not None
                    else DeviceLifecycle.PROVISIONED.value
                )
            state = DeviceState.model_validate(body)
            conn.execute(
                "UPDATE device_state SET site_id=?,fleet_id=?,lifecycle=?,body=? WHERE device_id=?",
                (state.site_id, state.fleet_id, state.lifecycle.value, _dump(state), state.device_id),
            )

        conn.execute(
            "INSERT INTO schema_meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )

    @staticmethod
    def _state_from_row(row: sqlite3.Row | None) -> DeviceState | None:
        return None if row is None else DeviceState.model_validate(_loads(row["body"]))

    def get_device_state(self, device_id: str) -> DeviceState | None:
        row = self._connection().execute(
            "SELECT body FROM device_state WHERE device_id=?", (device_id,)
        ).fetchone()
        return self._state_from_row(row)

    def provision_device(self, *, state: DeviceState, audit: AuditRecord) -> bool:
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM device_state WHERE device_id=?", (state.device_id,)).fetchone():
                return False
            if not self._write_state(conn, state, 0):
                return False
            self._insert_audit(conn, audit)
            return True

    def transition_device_lifecycle(
        self,
        *,
        state: DeviceState,
        expected_projection_version: int,
        audit: AuditRecord,
    ) -> bool:
        with self._transaction() as conn:
            if not self._write_state(conn, state, expected_projection_version):
                return False
            self._insert_audit(conn, audit)
            return True

    def _apply_summary_delta(
        self,
        conn: sqlite3.Connection,
        old: DeviceState | None,
        new: DeviceState,
    ) -> None:
        deltas = {state.value: 0 for state in HealthState}
        total_delta = 0
        convergence_delta = 0
        if old is None:
            total_delta = 1
            deltas[new.health_state.value] += 1
            convergence_delta = int(new.config_converged)
        else:
            if old.health_state != new.health_state:
                deltas[old.health_state.value] -= 1
                deltas[new.health_state.value] += 1
            convergence_delta = int(new.config_converged) - int(old.config_converged)
        conn.execute(
            """
            UPDATE fleet_summary SET
                total_devices=total_devices+?,
                healthy=healthy+?,
                degraded=degraded+?,
                offline=offline+?,
                unknown=unknown+?,
                config_converged=config_converged+?,
                updated_at=?
            WHERE singleton=1
            """,
            (
                total_delta,
                deltas[HealthState.HEALTHY.value],
                deltas[HealthState.DEGRADED.value],
                deltas[HealthState.OFFLINE.value],
                deltas[HealthState.UNKNOWN.value],
                convergence_delta,
                datetime.now(UTC).isoformat(),
            ),
        )

    def _write_state(
        self,
        conn: sqlite3.Connection,
        state: DeviceState,
        expected_projection_version: int,
    ) -> bool:
        row = conn.execute(
            "SELECT body,projection_version FROM device_state WHERE device_id=?",
            (state.device_id,),
        ).fetchone()
        old = self._state_from_row(row)
        actual = 0 if row is None else int(row["projection_version"])
        if actual != expected_projection_version:
            return False
        state.projection_version = expected_projection_version + 1
        body = _dump(state)
        if row is None:
            conn.execute(
                "INSERT INTO device_state(device_id,site_id,fleet_id,lifecycle,body,projection_version,health_state,config_converged) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    state.device_id,
                    state.site_id,
                    state.fleet_id,
                    state.lifecycle.value,
                    body,
                    state.projection_version,
                    state.health_state.value,
                    int(state.config_converged),
                ),
            )
        else:
            conn.execute(
                "UPDATE device_state SET site_id=?,fleet_id=?,lifecycle=?,body=?,projection_version=?,health_state=?,config_converged=? "
                "WHERE device_id=? AND projection_version=?",
                (
                    state.site_id,
                    state.fleet_id,
                    state.lifecycle.value,
                    body,
                    state.projection_version,
                    state.health_state.value,
                    int(state.config_converged),
                    state.device_id,
                    expected_projection_version,
                ),
            )
        self._apply_summary_delta(conn, old, state)
        return True

    def commit_telemetry(
        self,
        event: TelemetryEnvelope,
        received_at: datetime,
        state: DeviceState,
        expected_projection_version: int,
    ) -> TelemetryCommitStatus:
        try:
            with self._transaction() as conn:
                duplicate = conn.execute(
                    "SELECT 1 FROM telemetry_receipts WHERE device_id=? AND device_generation=? "
                    "AND boot_id=? AND sequence=?",
                    (event.device_id, event.device_generation, event.boot_id, event.sequence),
                ).fetchone()
                if duplicate is not None:
                    return "duplicate"
                current = conn.execute(
                    "SELECT projection_version FROM device_state WHERE device_id=?",
                    (event.device_id,),
                ).fetchone()
                actual = 0 if current is None else int(current["projection_version"])
                if actual != expected_projection_version:
                    return "conflict"
                conn.execute(
                    "INSERT INTO telemetry_receipts(device_id,device_generation,boot_id,sequence,event_id,observed_at,received_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        event.device_id,
                        event.device_generation,
                        event.boot_id,
                        event.sequence,
                        event.event_id,
                        event.observed_at.isoformat(),
                        received_at.isoformat(),
                    ),
                )
                if not self._write_state(conn, state, expected_projection_version):
                    raise RuntimeError("projection changed inside serialized transaction")
        except sqlite3.IntegrityError:
            return "duplicate"
        return "committed"

    def compare_and_swap_device_state(
        self,
        state: DeviceState,
        expected_projection_version: int,
    ) -> bool:
        with self._transaction() as conn:
            return self._write_state(conn, state, expected_projection_version)

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
        after = decoded[0] if decoded else None
        clauses: list[str] = []
        params: list[Any] = []
        if after is not None:
            clauses.append("device_id > ?")
            params.append(after)
        if health_state is not None:
            clauses.append("health_state = ?")
            params.append(health_state.value)
        if lifecycle is not None:
            clauses.append("lifecycle = ?")
            params.append(lifecycle.value)
        if site_id is not None:
            clauses.append("site_id = ?")
            params.append(site_id)
        if fleet_id is not None:
            clauses.append("fleet_id = ?")
            params.append(fleet_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection().execute(
            f"SELECT device_id,body FROM device_state {where} ORDER BY device_id ASC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = [DeviceState.model_validate(_loads(row["body"])) for row in visible]
        next_cursor = _cursor_encode([visible[-1]["device_id"]]) if has_more and visible else None
        return DevicePage(items=items, next_cursor=next_cursor, page_size=len(items))

    def get_fleet_summary(self) -> FleetSummary:
        row = self._connection().execute(
            "SELECT * FROM fleet_summary WHERE singleton=1"
        ).fetchone()
        assert row is not None
        return FleetSummary(
            total_devices=row["total_devices"],
            healthy=row["healthy"],
            degraded=row["degraded"],
            offline=row["offline"],
            unknown=row["unknown"],
            config_converged=row["config_converged"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_desired_config(self, device_id: str) -> DesiredConfiguration | None:
        row = self._connection().execute(
            "SELECT body FROM desired_config WHERE device_id=?", (device_id,)
        ).fetchone()
        return None if row is None else DesiredConfiguration.model_validate(_loads(row["body"]))

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
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT revision FROM desired_config WHERE device_id=?", (device_id,)
            ).fetchone()
            actual = 0 if row is None else int(row["revision"])
            if actual != expected_revision:
                return False
            current_state = conn.execute(
                "SELECT projection_version FROM device_state WHERE device_id=?", (device_id,)
            ).fetchone()
            state_version = 0 if current_state is None else int(current_state["projection_version"])
            if state_version != expected_projection_version:
                return False
            conn.execute(
                "INSERT INTO desired_config(device_id,revision,body) VALUES(?,?,?) "
                "ON CONFLICT(device_id) DO UPDATE SET revision=excluded.revision,body=excluded.body",
                (device_id, config.revision, _dump(config)),
            )
            if not self._write_state(conn, state, expected_projection_version):
                raise RuntimeError("device projection changed inside serialized transaction")
            self._insert_audit(conn, audit)
            self._insert_outbox(conn, outbox)
            return True

    def get_latest_config_ack(self, device_id: str) -> ConfigAck | None:
        row = self._connection().execute(
            "SELECT body FROM config_ack WHERE device_id=? ORDER BY revision DESC,responded_at DESC LIMIT 1",
            (device_id,),
        ).fetchone()
        return None if row is None else ConfigAck.model_validate(_loads(row["body"]))

    def save_config_ack_with_state(
        self,
        *,
        ack: ConfigAck,
        state: DeviceState,
        expected_projection_version: int,
        audit: AuditRecord,
    ) -> bool:
        with self._transaction() as conn:
            current = conn.execute(
                "SELECT projection_version FROM device_state WHERE device_id=?", (ack.device_id,)
            ).fetchone()
            actual = 0 if current is None else int(current["projection_version"])
            if actual != expected_projection_version:
                return False
            conn.execute(
                "INSERT OR IGNORE INTO config_ack(ack_id,device_id,revision,responded_at,body) VALUES(?,?,?,?,?)",
                (ack.ack_id, ack.device_id, ack.revision, ack.responded_at.isoformat(), _dump(ack)),
            )
            if not self._write_state(conn, state, expected_projection_version):
                raise RuntimeError("device projection changed inside serialized transaction")
            self._insert_audit(conn, audit)
            return True

    def create_command_with_outbox(
        self,
        *,
        command: DirectCommand,
        audit: AuditRecord,
        outbox: OutboxMessage,
    ) -> DirectCommand:
        with self._transaction() as conn:
            if command.idempotency_key is not None:
                row = conn.execute(
                    "SELECT body FROM commands WHERE device_id=? AND idempotency_key=?",
                    (command.device_id, command.idempotency_key),
                ).fetchone()
                if row is not None:
                    return DirectCommand.model_validate(_loads(row["body"]))
            conn.execute(
                "INSERT INTO commands(command_id,device_id,status,requested_at,idempotency_key,body) VALUES(?,?,?,?,?,?)",
                (
                    command.command_id,
                    command.device_id,
                    command.status.value,
                    command.requested_at.isoformat(),
                    command.idempotency_key,
                    _dump(command),
                ),
            )
            self._insert_audit(conn, audit)
            self._insert_outbox(conn, outbox)
            return command

    def get_command(self, command_id: str) -> DirectCommand | None:
        row = self._connection().execute(
            "SELECT body FROM commands WHERE command_id=?", (command_id,)
        ).fetchone()
        return None if row is None else DirectCommand.model_validate(_loads(row["body"]))

    def transition_command(
        self,
        *,
        command_id: str,
        expected_statuses: Sequence[CommandStatus],
        new_status: CommandStatus,
        ack: CommandAck | None = None,
        audit: AuditRecord | None = None,
    ) -> DirectCommand | None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT status,body FROM commands WHERE command_id=?", (command_id,)
            ).fetchone()
            if row is None:
                return None
            command = DirectCommand.model_validate(_loads(row["body"]))
            if command.status not in set(expected_statuses):
                return command
            command.status = new_status
            conn.execute(
                "UPDATE commands SET status=?,body=? WHERE command_id=? AND status=?",
                (new_status.value, _dump(command), command_id, row["status"]),
            )
            if ack is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO command_ack(ack_id,command_id,body) VALUES(?,?,?)",
                    (ack.ack_id, ack.command_id, _dump(ack)),
                )
            if audit is not None:
                self._insert_audit(conn, audit)
            return command

    def list_commands_page(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        device_id: str | None = None,
        status: CommandStatus | None = None,
    ) -> CommandPage:
        limit = max(1, min(limit, 500))
        decoded = _cursor_decode(cursor, 2)
        clauses: list[str] = []
        params: list[Any] = []
        if decoded:
            clauses.append("(requested_at < ? OR (requested_at = ? AND command_id < ?))")
            params.extend([decoded[0], decoded[0], decoded[1]])
        if device_id is not None:
            clauses.append("device_id = ?")
            params.append(device_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection().execute(
            f"SELECT requested_at,command_id,body FROM commands {where} "
            "ORDER BY requested_at DESC,command_id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = [DirectCommand.model_validate(_loads(row["body"])) for row in visible]
        next_cursor = (
            _cursor_encode([visible[-1]["requested_at"], visible[-1]["command_id"]])
            if has_more and visible
            else None
        )
        return CommandPage(items=items, next_cursor=next_cursor, page_size=len(items))

    def list_expired_commands(self, now: datetime, limit: int = 100) -> list[DirectCommand]:
        rows = self._connection().execute(
            "SELECT body FROM commands WHERE status IN (?,?) ORDER BY requested_at ASC LIMIT ?",
            (CommandStatus.QUEUED.value, CommandStatus.DISPATCHED.value, limit * 4),
        ).fetchall()
        result = [DirectCommand.model_validate(_loads(row["body"])) for row in rows]
        return [command for command in result if command.expires_at <= now][:limit]

    def _insert_audit(self, conn: sqlite3.Connection, record: AuditRecord) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO audit(
                audit_id,created_at,correlation_id,request_correlation_id,target,body
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                record.audit_id,
                record.created_at.isoformat(),
                record.correlation_id,
                record.request_correlation_id,
                record.target,
                _dump(record),
            ),
        )

    def append_audit(self, record: AuditRecord) -> None:
        with self._transaction() as conn:
            self._insert_audit(conn, record)

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
        clauses: list[str] = []
        params: list[Any] = []
        if correlation_id is not None:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)
        if request_correlation_id is not None:
            clauses.append("request_correlation_id = ?")
            params.append(request_correlation_id)
        if target is not None:
            clauses.append("target = ?")
            params.append(target)
        if decoded:
            clauses.append("(created_at < ? OR (created_at = ? AND audit_id < ?))")
            params.extend([decoded[0], decoded[0], decoded[1]])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection().execute(
            f"SELECT created_at,audit_id,body FROM audit {where} "
            "ORDER BY created_at DESC,audit_id DESC LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = [AuditRecord.model_validate(_loads(row["body"])) for row in visible]
        next_cursor = (
            _cursor_encode([visible[-1]["created_at"], visible[-1]["audit_id"]])
            if has_more and visible
            else None
        )
        return AuditPage(items=items, next_cursor=next_cursor, page_size=len(items))

    def _insert_outbox(self, conn: sqlite3.Connection, message: OutboxMessage) -> None:
        conn.execute(
            """
            INSERT INTO outbox(
                outbox_id,device_id,kind,correlation_id,status,created_at,available_at,
                lease_owner,lease_until,attempts,last_error,body
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                message.outbox_id,
                message.device_id,
                message.kind.value,
                message.correlation_id,
                message.status.value,
                message.created_at.isoformat(),
                message.available_at.isoformat(),
                message.lease_owner,
                message.lease_until.isoformat() if message.lease_until else None,
                message.attempts,
                message.last_error,
                _dump(message),
            ),
        )

    def claim_outbox(
        self,
        *,
        owner: str,
        now: datetime,
        lease_seconds: int,
        limit: int,
    ) -> list[OutboxMessage]:
        limit = max(1, min(limit, 100))
        lease_until = now + timedelta(seconds=max(5, lease_seconds))
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT outbox_id,body FROM outbox
                WHERE available_at <= ? AND (
                    status = ? OR (status = ? AND lease_until < ?)
                )
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (
                    now.isoformat(),
                    OutboxStatus.PENDING.value,
                    OutboxStatus.LEASED.value,
                    now.isoformat(),
                    limit,
                ),
            ).fetchall()
            claimed: list[OutboxMessage] = []
            for row in rows:
                message = OutboxMessage.model_validate(_loads(row["body"]))
                message.status = OutboxStatus.LEASED
                message.lease_owner = owner
                message.lease_until = lease_until
                message.attempts += 1
                conn.execute(
                    "UPDATE outbox SET status=?,lease_owner=?,lease_until=?,attempts=?,body=? "
                    "WHERE outbox_id=?",
                    (
                        message.status.value,
                        owner,
                        lease_until.isoformat(),
                        message.attempts,
                        _dump(message),
                        message.outbox_id,
                    ),
                )
                claimed.append(message)
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
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT body,lease_owner,status FROM outbox WHERE outbox_id=?", (outbox_id,)
            ).fetchone()
            if row is None or row["lease_owner"] != owner or row["status"] != OutboxStatus.LEASED.value:
                return False
            message = OutboxMessage.model_validate(_loads(row["body"]))
            message.status = status
            message.last_error = error
            message.available_at = available_at or message.available_at
            message.lease_owner = None
            message.lease_until = None
            conn.execute(
                "UPDATE outbox SET status=?,available_at=?,lease_owner=NULL,lease_until=NULL,last_error=?,body=? "
                "WHERE outbox_id=?",
                (
                    status.value,
                    message.available_at.isoformat(),
                    error,
                    _dump(message),
                    outbox_id,
                ),
            )
            return True

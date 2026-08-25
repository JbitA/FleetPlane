from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from fleetplane.domain.enums import AckCode, CommandKind, OperatingMode
from fleetplane.domain.models import (
    CommandAck,
    ConfigAck,
    DesiredConfiguration,
    DirectCommand,
    IngestOutcome,
    TelemetryEnvelope,
    TelemetryHealth,
)


@dataclass(frozen=True)
class DevicePolicy:
    min_telemetry_interval_s: int = 10
    max_spool_rows: int = 1000


class DeviceStore:
    def __init__(self, path: Path, max_spool_rows: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.max_spool_rows = max_spool_rows
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS spool(
                ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS command_journal(
                command_id TEXT PRIMARY KEY,
                body TEXT NOT NULL
            );
            """
        )
        if self.get("boot_id") is None:
            self.set("boot_id", str(uuid4()))
        if self.get("device_generation") is None:
            self.set("device_generation", "1")
        if self.get("sequence") is None:
            self.set("sequence", "0")
        if self.get("config_revision") is None:
            self.set("config_revision", "0")

    def close(self) -> None:
        self.connection.close()

    def get(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def set(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def config(self) -> DesiredConfiguration | None:
        raw = self.get("config")
        return None if raw is None else DesiredConfiguration.model_validate_json(raw)

    def save_config(self, config: DesiredConfiguration) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "INSERT INTO meta(key,value) VALUES('config',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (config.model_dump_json(),),
            )
            self.connection.execute(
                "INSERT INTO meta(key,value) VALUES('config_revision',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(config.revision),),
            )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def allocate_event(self, body_factory: Callable[[int], TelemetryEnvelope], spool: bool) -> TelemetryEnvelope:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            sequence = int(self.get("sequence") or "0")
            event = body_factory(sequence)
            self.connection.execute(
                "INSERT INTO meta(key,value) VALUES('sequence',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(sequence + 1),),
            )
            if spool:
                count = int(self.connection.execute("SELECT COUNT(*) FROM spool").fetchone()[0])
                if count >= self.max_spool_rows:
                    self.connection.execute(
                        "DELETE FROM spool WHERE ordinal=(SELECT MIN(ordinal) FROM spool)"
                    )
                self.connection.execute(
                    "INSERT INTO spool(event_id,body) VALUES(?,?)",
                    (event.event_id, event.model_dump_json()),
                )
            self.connection.execute("COMMIT")
            return event
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def spooled(self, limit: int = 100) -> list[TelemetryEnvelope]:
        rows = self.connection.execute(
            "SELECT body FROM spool ORDER BY ordinal ASC LIMIT ?", (limit,)
        ).fetchall()
        return [TelemetryEnvelope.model_validate_json(row["body"]) for row in rows]

    def ack_spooled(self, event_id: str) -> None:
        self.connection.execute("DELETE FROM spool WHERE event_id=?", (event_id,))

    def spool_depth(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM spool").fetchone()[0])

    def journal_get(self, command_id: str) -> CommandAck | None:
        row = self.connection.execute(
            "SELECT body FROM command_journal WHERE command_id=?", (command_id,)
        ).fetchone()
        return None if row is None else CommandAck.model_validate_json(row["body"])

    def journal_put(self, ack: CommandAck) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO command_journal(command_id,body) VALUES(?,?)",
            (ack.command_id, ack.model_dump_json()),
        )


class SimulatedDevice:
    def __init__(
        self,
        device_id: str,
        state_dir: str | Path,
        telemetry_sender: Callable[[TelemetryEnvelope], IngestOutcome],
        seed: int,
        policy: DevicePolicy | None = None,
    ) -> None:
        self.device_id = device_id
        self.policy = policy or DevicePolicy()
        self.store = DeviceStore(Path(state_dir) / f"{device_id}.db", self.policy.max_spool_rows)
        self.telemetry_sender = telemetry_sender
        self.random = random.Random(seed)
        self.online = True
        self.operating_mode = OperatingMode.IDLE
        self.model_version = "baseline-1"

    def close(self) -> None:
        self.store.close()

    def set_online(self, value: bool) -> None:
        self.online = value

    def set_operating_mode(self, mode: OperatingMode) -> None:
        self.operating_mode = mode

    def _make_event(self, sequence: int) -> TelemetryEnvelope:
        revision = int(self.store.get("config_revision") or "0")
        return TelemetryEnvelope(
            device_id=self.device_id,
            device_generation=int(self.store.get("device_generation") or "1"),
            boot_id=self.store.get("boot_id") or "missing",
            sequence=sequence,
            observed_at=datetime.now(UTC),
            model_version=self.model_version,
            config_revision=revision,
            anomaly_score=self.random.random() * 0.2,
            health=TelemetryHealth(
                battery_pct=max(5.0, 90.0 - sequence * 0.01),
                spool_depth=self.store.spool_depth(),
                sensor_ok=True,
                inference_latency_ms=8.0 + self.random.random() * 4,
                operating_mode=self.operating_mode,
            ),
        )

    def build_event(self) -> TelemetryEnvelope:
        return self.store.allocate_event(self._make_event, spool=False)

    def tick(self) -> None:
        self.store.allocate_event(self._make_event, spool=True)
        if self.online:
            self.flush_spool()

    def flush_spool(self) -> None:
        if not self.online:
            return
        for event in self.store.spooled():
            outcome = self.telemetry_sender(event)
            if outcome.disposition.value in {
                "accepted",
                "accepted_with_gap",
                "accepted_out_of_order",
                "duplicate",
            }:
                self.store.ack_spooled(event.event_id)
            else:
                break

    def handle_desired(self, config: DesiredConfiguration) -> ConfigAck:
        current_revision = int(self.store.get("config_revision") or "0")
        if config.revision <= current_revision:
            return ConfigAck(
                device_id=self.device_id,
                revision=config.revision,
                code=AckCode.REJECTED_STALE,
                reason=f"current_revision={current_revision}",
            )
        if config.telemetry_interval_s < self.policy.min_telemetry_interval_s:
            return ConfigAck(
                device_id=self.device_id,
                revision=config.revision,
                code=AckCode.REJECTED_POLICY,
                reason=f"minimum_interval={self.policy.min_telemetry_interval_s}",
            )
        self.store.save_config(config)
        return ConfigAck(device_id=self.device_id, revision=config.revision, code=AckCode.APPLIED)

    def handle_direct(self, command: DirectCommand) -> CommandAck:
        prior = self.store.journal_get(command.command_id)
        if prior is not None:
            return prior
        if command.kind == CommandKind.RESTART_APPLICATION and self.operating_mode == OperatingMode.ACTIVE:
            ack = CommandAck(
                command_id=command.command_id,
                device_id=self.device_id,
                code=AckCode.REJECTED_POLICY,
                reason="restart_blocked_while_active",
            )
        else:
            ack = CommandAck(
                command_id=command.command_id,
                device_id=self.device_id,
                code=AckCode.ACCEPTED,
                payload={"kind": command.kind.value, "operating_mode": self.operating_mode.value},
            )
        self.store.journal_put(ack)
        return ack

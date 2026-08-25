from __future__ import annotations

import argparse
import json
import platform
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from fleetplane import __version__
from fleetplane.api.app import create_app
from fleetplane.observability import configure_operation_logging
from fleetplane.runtime import Settings, build_runtime
from fleetplane.simulator.fleet import FleetSimulator


def _showcase(args: argparse.Namespace) -> int:
    # The reference command is an evidence surface first. Keep operational logs available, but
    # do not bury the result in hundreds of dispatch events unless the reviewer asks for them.
    configure_operation_logging("INFO" if args.verbose_operations else "WARNING")
    with tempfile.TemporaryDirectory(prefix="fleetplane-") as temp:
        root = Path(temp)
        runtime = build_runtime(Settings(sqlite_path=str(root / "cloud.db")))
        simulator = FleetSimulator(
            runtime.store,
            root / "devices",
            device_count=args.devices,
            restricted_devices=min(args.restricted_devices, args.devices),
            metrics=runtime.metrics,
            gateway=runtime.gateway,
        )
        try:
            result = simulator.run_reference_scenario()
            print(json.dumps(result, indent=2, default=str))
            if args.evidence is not None:
                evidence = {
                    "evidence_schema_version": 1,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "fleetplane_version": __version__,
                    "python_version": sys.version.split()[0],
                    "platform": platform.platform(),
                    "parameters": {
                        "devices": args.devices,
                        "restricted_devices": min(args.restricted_devices, args.devices),
                    },
                    "result": result,
                }
                output = Path(args.evidence)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
            return 0 if all(result["assertions"].values()) else 1
        finally:
            simulator.close()
            runtime.close()


def _local_api(args: argparse.Namespace, demo: bool) -> int:
    settings = Settings.from_env()
    runtime = build_runtime(settings)
    simulator: FleetSimulator | None = None
    stop = threading.Event()
    if demo:
        simulator = FleetSimulator(
            runtime.store,
            Path(settings.sqlite_path).parent / "devices",
            device_count=args.devices,
            restricted_devices=min(args.restricted_devices, args.devices),
            metrics=runtime.metrics,
            gateway=runtime.gateway,
        )

        def background() -> None:
            assert simulator is not None
            while not stop.wait(args.tick_seconds):
                simulator.tick_all(1)
                runtime.dispatcher.dispatch_once(limit=100)
                runtime.commands.expire_pending(limit=100)
                cursor: str | None = None
                for _ in range(10):
                    result = runtime.reconciliation.reconcile_batch(cursor=cursor, limit=200)
                    cursor = result["next_cursor"] if isinstance(result["next_cursor"], str) else None
                    if cursor is None:
                        break

        simulator.tick_all(1)
        threading.Thread(target=background, daemon=True, name="fleetplane-demo").start()
    try:
        uvicorn.run(create_app(runtime), host=args.host, port=args.port)
        return 0
    finally:
        stop.set()
        if simulator is not None:
            simulator.close()
        runtime.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="fleetplane")
    sub = root.add_subparsers(dest="command", required=True)

    showcase = sub.add_parser("showcase", help="run the deterministic local fleet scenario")
    showcase.add_argument("--devices", type=int, default=100)
    showcase.add_argument("--restricted-devices", type=int, default=5)
    showcase.add_argument(
        "--evidence",
        metavar="PATH",
        help="write a reproducibility envelope containing environment, parameters and results",
    )
    showcase.add_argument(
        "--verbose-operations",
        action="store_true",
        help="emit structured dispatcher operation logs during the scenario",
    )

    for name in ("local-api", "demo-api"):
        api = sub.add_parser(name)
        api.add_argument("--host", default="127.0.0.1")
        api.add_argument("--port", type=int, default=8000)
        api.add_argument("--devices", type=int, default=20)
        api.add_argument("--restricted-devices", type=int, default=5)
        api.add_argument("--tick-seconds", type=float, default=5.0)
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "showcase":
        raise SystemExit(_showcase(args))
    raise SystemExit(_local_api(args, demo=args.command == "demo-api"))

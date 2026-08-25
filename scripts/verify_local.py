"""Run the public local verification path without requiring cloud resources."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "local-reference-latest.json"


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> int:
    run(
        sys.executable,
        "-m",
        "pytest",
        "--cov=fleetplane",
        "--cov-branch",
        "--cov-report=term-missing",
    )
    run(
        sys.executable,
        "-m",
        "fleetplane",
        "showcase",
        "--devices",
        "100",
        "--restricted-devices",
        "5",
        "--evidence",
        str(EVIDENCE),
    )
    print(f"Verification evidence: {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

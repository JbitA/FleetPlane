from __future__ import annotations

import argparse
import json

from fleetplane.cli import _showcase


def test_showcase_stdout_is_clean_json_and_evidence_is_self_describing(tmp_path, capsys):
    evidence_path = tmp_path / "evidence.json"
    args = argparse.Namespace(
        devices=5,
        restricted_devices=1,
        evidence=str(evidence_path),
        verbose_operations=False,
    )

    assert _showcase(args) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["devices"] == 5
    assert all(result["assertions"].values())

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["evidence_schema_version"] == 1
    assert evidence["fleetplane_version"]
    assert evidence["python_version"]
    assert evidence["parameters"] == {"devices": 5, "restricted_devices": 1}
    assert evidence["result"]["assertions"] == result["assertions"]

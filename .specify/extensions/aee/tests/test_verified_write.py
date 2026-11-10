"""Verified-write retrofit tests (fleet standard, 2026-10-28).

The writers are embedded in the two shipped scripts (they ship as
single declared files), so the tests load the scripts as modules
and exercise their copies: exact bytes, empty refusal, the
SafeWriteError -> exit-2 contract run_aee's main relies on,
fresh-subprocess recovery, verified append with roll-back on a
torn landing, and the token-economy shell wrapper's telemetry
append end to end.

Run:  python3 -m pytest tests/test_verified_write.py
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts" / "python"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_aee = _load("run_aee")
economy = _load("aee_token_economy")


def test_write_round_trip_exact_bytes(tmp_path):
    target = tmp_path / "claims.json"
    result = run_aee.write_text_verified(target, '{"claims": []}\n')
    assert target.read_bytes() == b'{"claims": []}\n'
    assert result["bytes"] == 15


def test_empty_write_refused(tmp_path):
    with pytest.raises(ValueError):
        run_aee.write_text_verified(tmp_path / "x.json", "")


def test_safe_write_error_is_a_value_error():
    # run_aee.main maps ValueError to a clean exit 2; the retrofit
    # must flow through that contract, never a traceback.
    assert issubclass(run_aee.SafeWriteError, ValueError)
    assert issubclass(economy.SafeWriteError, ValueError)


def test_write_recovers_via_child(tmp_path, monkeypatch):
    monkeypatch.setattr(run_aee, "_vw_try_write_once", lambda p, d: False)
    target = tmp_path / "pointer.json"
    result = run_aee.write_text_verified(target, '{"latest": true}\n')
    assert result["via"].startswith("child-")
    assert target.read_bytes() == b'{"latest": true}\n'


def test_write_total_failure_raises_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(run_aee, "_vw_try_write_once", lambda p, d: False)
    monkeypatch.setattr(run_aee, "_vw_try_child_write", lambda p, d: False)
    target = tmp_path / "pointer.json"
    with pytest.raises(run_aee.SafeWriteError):
        run_aee.write_text_verified(target, "{}\n")
    assert not target.exists()


def test_appends_land_in_order(tmp_path):
    log = tmp_path / "telemetry.jsonl"
    economy.append_verified(log, b'{"n": 1}\n')
    economy.append_verified(log, b'{"n": 2}\n')
    assert log.read_bytes() == b'{"n": 1}\n{"n": 2}\n'


def test_append_rolls_back_when_the_landing_cannot_be_proven(tmp_path, monkeypatch):
    log = tmp_path / "telemetry.jsonl"
    log.write_bytes(b'{"n": 1}\n')

    def broken_fsync(fd):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(economy.os, "fsync", broken_fsync)
    landed = economy._append_once(log, b'{"n": 2}\n', existed=True)
    assert landed is False
    assert log.read_bytes() == b'{"n": 1}\n'


def test_append_recovers_via_child(tmp_path, monkeypatch):
    monkeypatch.setattr(economy, "_append_once", lambda p, d, e: False)
    log = tmp_path / "telemetry.jsonl"
    result = economy.append_verified(log, b'{"n": 1}\n')
    assert result["via"].startswith("child-")
    assert log.read_bytes() == b'{"n": 1}\n'


def test_token_economy_shell_writes_verified_telemetry(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "aee_token_economy.py"),
            "--project-root",
            str(tmp_path),
            "shell",
            "--telemetry",
            "telemetry.jsonl",
            "--",
            sys.executable,
            "-c",
            "pass",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    lines = (tmp_path / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["exit_code"] == 0

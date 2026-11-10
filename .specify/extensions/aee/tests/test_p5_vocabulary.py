"""Spec 004 P5 extension-half tests: the realigned engine floor
(P5-E1) and the runner's verdict_display (P5-E2).

Hermetic, in the test_runner stub style: a stub `aee` on PATH
stands in for the engine, so the floor tests do not depend on
which engine the ambient environment provides.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "scripts" / "python" / "run_aee.py"

_STUB_AEE = '''\
#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print(os.environ.get("AEE_STUB_VERSION_TEXT", "aee 1.6.0"))
    sys.exit(0)
command = args[0] if args else ""


def flag(name):
    return args[args.index(name) + 1] if name in args else None


if command == "assess":
    shape = os.environ.get("AEE_STUB_SHAPE", "realigned")
    if shape == "realigned":
        payload = {
            "project": "stub-project",
            "phase": flag("--phase") or "after_plan",
            "outcome": "iterate",
            "methodology_version": "2.1",
            "claims": [],
            "reported_statuses": {"A": "partially_supported"},
            "stage_caps": [{
                "claim_id": "A",
                "author_status": "supported",
                "effective_stage": "checked",
                "reasons": ["stage cap (R1.3): at stage checked the reported status is capped at partially_supported"],
                "reported_status": "partially_supported",
                "stage": "checked",
                "verdict_cap": None,
            }],
            "lanes": {
                "lane_1": {"label": "machine verification", "bundle": {"outcome": "iterate"}},
                "lane_2": {"label": "human acceptance", "bundle": {"stage_counts": {"checked": 1}}},
            },
            "not_understood": ["A: what remains not understood"],
            "metadata": {"engine_version": "1.6.0"},
        }
    else:
        payload = {
            "project": "stub-project",
            "phase": flag("--phase") or "after_plan",
            "outcome": "iterate",
            "claims": [],
            "metadata": {"engine_version": "1.4.0"},
        }
    Path(flag("--output")).write_text(json.dumps(payload))
    Path(flag("--evaluator-output")).write_text(json.dumps({"outcome": "iterate"}))
    sys.exit(1)
sys.exit(0)
'''

requires_posix_stub = pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub aee is executed via a POSIX shebang script",
)


def _env(tmp_path: Path, version: str = "1.6.0", shape: str = "realigned") -> dict:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "aee"
    stub.write_text(_STUB_AEE, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["AEE_STUB_VERSION_TEXT"] = f"aee {version}"
    env["AEE_STUB_SHAPE"] = shape
    return env


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "claims.json").write_text('{"claims": []}', encoding="utf-8")
    # A conforming project has the required evaluator companion
    # installed (Spec 005).
    evaluator = root / ".specify" / "extensions" / "evaluator"
    evaluator.mkdir(parents=True)
    (evaluator / "extension.yml").write_text(
        "extension:\n  id: evaluator\n", encoding="utf-8"
    )
    return root


def _run(root: Path, env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--project-root", str(root), *args],
        check=False, capture_output=True, text=True, env=env,
    )


@requires_posix_stub
def test_pre_realignment_engine_is_refused_cleanly(tmp_path: Path) -> None:
    # P5-E1: engine 1.4.0 is below the realigned floor.
    completed = _run(
        _project(tmp_path), _env(tmp_path, version="1.4.0"),
        "assess", "--input", "claims.json",
    )
    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr
    assert "1.4.0" in completed.stderr
    assert ">=1.6.0,<2" in completed.stderr


@requires_posix_stub
def test_realigned_engine_runs_and_display_matches_assessment(tmp_path: Path) -> None:
    root = _project(tmp_path)
    completed = _run(root, _env(tmp_path), "assess", "--input", "claims.json")
    assert completed.returncode in (0, 1), completed.stderr
    out = json.loads(completed.stdout)
    display = out["verdict_display"]
    assessment = json.loads((root / out["assessment"]).read_text(encoding="utf-8"))
    # P5-E2 / acceptance: the display is the assessment's own
    # vocabulary, value for value.
    assert display["outcome"] == assessment["outcome"]
    assert display["methodology_version"] == "2.1"
    assert display["reported_statuses"] == assessment["reported_statuses"]
    assert display["stage_caps"] == assessment["stage_caps"]
    assert display["lanes"]["lane_1"] == assessment["lanes"]["lane_1"]
    assert display["lanes"]["lane_2"] == assessment["lanes"]["lane_2"]
    assert display["not_understood"] == assessment["not_understood"]


@requires_posix_stub
def test_legacy_shaped_assessment_yields_null_display(tmp_path: Path) -> None:
    # Never fabricated: a record without the realigned fields gives
    # a null display block.
    root = _project(tmp_path)
    completed = _run(
        root, _env(tmp_path, shape="legacy"), "assess", "--input", "claims.json"
    )
    assert completed.returncode in (0, 1), completed.stderr
    out = json.loads(completed.stdout)
    assert out["verdict_display"] is None


def test_floor_constants_agree_across_the_three_places() -> None:
    runner = (ROOT / "scripts" / "python" / "run_aee.py").read_text(encoding="utf-8")
    yml = (ROOT / "extension.yml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "_AEE_VERSION_MIN = (1, 6, 0)" in runner
    assert 'REQUIRED_AEE_VERSION = ">=1.6.0,<2"' in runner
    assert "'>=1.6.0,<2'" in yml or '\'>=1.6.0,<2\'' in yml or ">=1.6.0,<2" in yml
    assert "applied-epistemic-engineering>=1.6.0,<2" in readme

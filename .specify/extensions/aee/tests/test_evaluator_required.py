"""Spec 005 — the evaluator extension is a required companion.

AEE is one evaluator among many by design; its results are composed
through the common spec-kit-evaluator extension so every evaluator's
output is handled consistently. These tests pin the enforcement:
`assess` refuses (exit 2, clean refusal, no outputs) when the
evaluator extension is absent, the other subcommands are not blocked,
and the manifest + docs declare and teach the pair as one system.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "python" / "run_aee.py"

# Minimal stub engine with the same contract as tests/test_runner.py's:
# assess writes the --output and --evaluator-output files and exits 1
# (its scored verdict), challenge exits 1, graph exits 0.
_STUB_AEE = """#!/usr/bin/env python3
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
    Path(flag("--output")).write_text(json.dumps({
        "project": "stub-project",
        "phase": flag("--phase") or "after_plan",
        "outcome": "iterate",
        "claims": [],
        "metadata": {"engine_version": "1.6.0"},
    }))
    Path(flag("--evaluator-output")).write_text(json.dumps({"outcome": "iterate"}))
    sys.exit(1)
if command == "challenge":
    Path(flag("--output")).write_text(json.dumps({"mode": "challenge"}))
    sys.exit(1)
if command == "graph":
    Path(flag("--output")).write_text("graph TD\\n")
    sys.exit(0)
sys.exit(0)
"""

requires_posix_stub = pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub aee is executed via a POSIX shebang script",
)


def _stub_env(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "aee"
    stub.write_text(_STUB_AEE, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["AEE_STUB_VERSION_TEXT"] = "aee 1.6.0"
    return env


def _project(tmp_path: Path, *, evaluator: bool) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "claims.json").write_text('{"claims": []}', encoding="utf-8")
    if evaluator:
        installed = root / ".specify" / "extensions" / "evaluator"
        installed.mkdir(parents=True)
        (installed / "extension.yml").write_text(
            "extension:\n  id: evaluator\n", encoding="utf-8"
        )
    return root


def _run(root: Path, env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--project-root", str(root), *args],
        check=False, capture_output=True, text=True, env=env,
    )


@requires_posix_stub
def test_assess_refuses_without_evaluator_extension(tmp_path: Path) -> None:
    root = _project(tmp_path, evaluator=False)
    completed = _run(root, _stub_env(tmp_path), "assess", "--input", "claims.json")
    # Clean refusal: exit 2, no traceback, no JSON summary on stdout.
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stderr
    assert completed.stdout.strip() == ""
    # The refusal names the missing extension and how to install it.
    assert "refused:" in completed.stderr
    assert "spec-kit-evaluator" in completed.stderr
    # The named command is one Spec Kit accepts: the catalog ID is
    # `evaluator`, and the discovery-only community catalog requires
    # an explicit --from archive.
    assert "specify extension add evaluator --from https://" in completed.stderr
    assert "specify extension add spec-kit-evaluator" not in completed.stderr
    assert "SPECKIT_AEE_AUTO_INSTALL" in completed.stderr
    # Nothing was written: no assessment tree, and the runner did not
    # create the evaluator results directory it writes into.
    assert not (root / ".specify" / "extensions" / "aee").exists()
    assert not (root / ".specify" / "extensions" / "evaluator").exists()


@requires_posix_stub
def test_assess_runs_with_evaluator_extension(tmp_path: Path) -> None:
    root = _project(tmp_path, evaluator=True)
    completed = _run(root, _stub_env(tmp_path), "assess", "--input", "claims.json")
    assert completed.returncode == 1, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["evaluator_extension_present"] is True
    assert payload["ok"] is True


@requires_posix_stub
def test_other_commands_are_not_blocked_without_evaluator(
    tmp_path: Path,
) -> None:
    # Scope (ER-01): only assess emits an Evaluator Contract result.
    # graph and challenge keep working in a project without the
    # evaluator extension.
    root = _project(tmp_path, evaluator=False)
    env = _stub_env(tmp_path)
    for command in ("graph", "challenge"):
        completed = _run(root, env, command, "--input", "claims.json")
        assert completed.returncode in (0, 1), (
            command, completed.stdout, completed.stderr,
        )
        assert "spec-kit-evaluator" not in completed.stderr


def test_manifest_declares_evaluator_required() -> None:
    manifest = yaml.safe_load((ROOT / "extension.yml").read_text(encoding="utf-8"))
    assert manifest["extension"]["version"] == "1.7.0"
    tools = {tool["name"]: tool for tool in manifest["requires"]["tools"]}
    evaluator = tools["spec-kit-evaluator"]
    assert evaluator["required"] is True
    assert evaluator["version"] == ">=1.0.0,<2"


def test_docs_teach_the_pair_as_one_system() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Evaluator Contract (optional)" not in readme
    assert "only if you want composition" not in readme
    assert "required companion" in readme
    assess_md = (ROOT / "commands" / "speckit.aee.assess.md").read_text(
        encoding="utf-8"
    )
    assert "**not** required to run assess" not in assess_md
    assert "required companion" in assess_md
    usage = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")
    assert "needed only for compose/report" not in usage


def test_check_install_exercises_the_refusal() -> None:
    source = (ROOT / "scripts" / "check_install.py").read_text(encoding="utf-8")
    # ER-04: the both-enabled assertion stays, and the absence
    # exercise (remove the evaluator dir -> assess must refuse) is
    # part of the install check.
    assert 'installed["evaluator"]["enabled"]' in source
    assert "_rmtree(evaluator_dir)" in source
    # The removal goes through the Windows-safe helper: the installed
    # tree contains a copied `.git` whose read-only object files
    # Windows refuses to unlink (WinError 5), so plain shutil.rmtree
    # dies there; the helper's handler clears the bit and retries.
    assert "shutil.rmtree(root, onexc=_retry_after_clearing_readonly)" in source
    assert "assess without the evaluator extension must exit 2" in source

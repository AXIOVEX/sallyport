from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

RUNNER = Path(__file__).parents[1] / "scripts" / "python" / "run_aee.py"
SPEC = importlib.util.spec_from_file_location("run_aee", RUNNER)
assert SPEC and SPEC.loader
run_aee = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_aee)


def test_phase_is_normalized() -> None:
    assert run_aee._safe_segment("plan") == "after_plan"
    assert run_aee._safe_segment("after_tasks") == "after_tasks"


@pytest.mark.parametrize("value", ["../plan", "after-plan", "", "after plan"])
def test_invalid_phase_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        run_aee._safe_segment(value)


def test_existing_file_must_be_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("data", encoding="utf-8")
    with pytest.raises(ValueError, match="escapes project root"):
        run_aee._safe_existing(root, outside)


def test_symlink_component_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "project"
    real = root / "real"
    root.mkdir()
    real.mkdir()
    (real / "spec.md").write_text("data", encoding="utf-8")
    link = root / "linked"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(ValueError):
        run_aee._safe_existing(root, link / "spec.md")


def test_output_parent_is_created(tmp_path: Path) -> None:
    target = run_aee._safe_output(tmp_path, Path(".specify/extensions/aee/result.json"))
    assert target.parent.is_dir()
    assert target.is_relative_to(tmp_path)


def test_challenge_subparser_is_registered() -> None:
    parser = run_aee.parser()
    args = parser.parse_args(
        ["challenge", "--input", "claims.json", "--phase", "after_plan"]
    )
    assert args.command == "challenge"
    assert args.input == Path("claims.json")
    assert args.phase == "after_plan"
    assert args.threshold == 0.70
    assert args.actor == "spec-kit-aee"


def test_challenge_end_to_end(tmp_path: Path) -> None:
    if shutil.which("aee") is None:
        pytest.fail("Install requirements/dev.txt and activate its environment: aee is required")
    root = tmp_path / "project"
    root.mkdir()
    claims = root / "claims.json"
    claims.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "claims": [
                    {
                        "id": "REQ-001",
                        "text": "The service responds quickly",
                        "kind": "requirement",
                        "status": "supported",
                        "boundary": [],
                        "falsification_tests": ["A load test observes slow responses"],
                        "source_ref": "spec.md#REQ-001",
                        "uncertainty": "low",
                        "evidence": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--project-root",
            str(root),
            "challenge",
            "--input",
            "claims.json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode in {0, 1, 2}, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["aee_exit_code"] == completed.returncode
    challenge_path = root / payload["challenge"]
    assert challenge_path.is_file()
    value = json.loads(challenge_path.read_text(encoding="utf-8"))
    assert value["mode"] == "challenge"
    assert "failures" in value
    assert "recoveries" in value


# ---------------------------------------------------------------------------
# Error / output contract (RT3-01, RT3-02, RT3-07, RT3-21) and version /
# dependency truth (RT3-03, RT3-04). These tests are hermetic: a stub
# `aee` executable on PATH stands in for the engine, so they do not
# depend on which engine version the ambient environment provides.
# ---------------------------------------------------------------------------

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
behavior = os.environ.get("AEE_STUB_BEHAVIOR", "ok")
command = args[0] if args else ""


def flag(name):
    return args[args.index(name) + 1] if name in args else None


if behavior == "fail":
    sys.exit(2)
if command == "assess":
    # Full assessment shape: the runner stamps this dict and hashes it
    # into the ledger itself (the engine CLI is told --no-ledger), so
    # the stub must emit the keys the ledger append reads.
    Path(flag("--output")).write_text(json.dumps({
        "project": "stub-project",
        "phase": flag("--phase") or "after_plan",
        "outcome": "iterate",
        "claims": [],
        "metadata": {"engine_version": "1.4.0"},
        "stub_marker": os.environ.get("AEE_STUB_MARKER", ""),
    }))
    Path(flag("--evaluator-output")).write_text(json.dumps({"outcome": "iterate"}))
    sys.exit(1)
if command == "challenge":
    Path(flag("--output")).write_text(json.dumps({"mode": "challenge"}))
    sys.exit(1)
if command == "graph":
    Path(flag("--output")).write_text("graph TD\\n")
    sys.exit(0)
if command == "gaps":
    print("Gap register written to somewhere")
    print()
    print("Open: 1 | Closed: 0")
    output = flag("--output")
    if output:
        Path(output).write_text("**Open:** 1\\n")
    sys.exit(0)
sys.exit(0)
'''


def _stub_env(tmp_path: Path, version: str = "1.6.0", behavior: str = "ok") -> dict:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "aee"
    stub.write_text(_STUB_AEE, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["AEE_STUB_VERSION_TEXT"] = f"aee {version}"
    env["AEE_STUB_BEHAVIOR"] = behavior
    return env


requires_posix_stub = pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub aee is executed via a POSIX shebang script",
)


def _run_stubbed(
    root: Path, env: dict, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--project-root", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def stub_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "claims.json").write_text('{"claims": []}', encoding="utf-8")
    # A conforming project has the required evaluator companion
    # installed (Spec 005); absence tests remove this directory.
    evaluator = root / ".specify" / "extensions" / "evaluator"
    evaluator.mkdir(parents=True)
    (evaluator / "extension.yml").write_text(
        "extension:\n  id: evaluator\n", encoding="utf-8"
    )
    return root


def _assert_clean_refusal(completed: subprocess.CompletedProcess[str]) -> None:
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stderr
    assert "refused:" in completed.stderr


@requires_posix_stub
def test_missing_input_is_clean_exit_2(tmp_path: Path, stub_project: Path) -> None:
    completed = _run_stubbed(
        stub_project, _stub_env(tmp_path), "assess", "--input", "nope.json"
    )
    _assert_clean_refusal(completed)


@requires_posix_stub
def test_traversal_input_is_clean_exit_2(tmp_path: Path, stub_project: Path) -> None:
    completed = _run_stubbed(
        stub_project, _stub_env(tmp_path), "assess", "--input", "../outside.json"
    )
    _assert_clean_refusal(completed)


@requires_posix_stub
def test_bad_phase_is_clean_exit_2(tmp_path: Path, stub_project: Path) -> None:
    completed = _run_stubbed(
        stub_project,
        _stub_env(tmp_path),
        "assess",
        "--input",
        "claims.json",
        "--phase",
        "After_Plan",
    )
    _assert_clean_refusal(completed)


def test_bare_after_phase_is_rejected() -> None:
    with pytest.raises(ValueError):
        run_aee._safe_segment("after_")


@requires_posix_stub
def test_missing_project_root_is_clean_exit_2(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--project-root",
            str(tmp_path / "does-not-exist"),
            "verify",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=_stub_env(tmp_path),
    )
    _assert_clean_refusal(completed)


@requires_posix_stub
def test_verify_without_ledger_is_clean_exit_2(
    tmp_path: Path, stub_project: Path
) -> None:
    completed = _run_stubbed(stub_project, _stub_env(tmp_path), "verify")
    _assert_clean_refusal(completed)


@requires_posix_stub
def test_gate_missing_input_is_clean_exit_2(
    tmp_path: Path, stub_project: Path
) -> None:
    completed = _run_stubbed(
        stub_project, _stub_env(tmp_path), "gate", "--input", "nope.json"
    )
    _assert_clean_refusal(completed)


@requires_posix_stub
def test_missing_aee_is_clean_exit_2(tmp_path: Path, stub_project: Path) -> None:
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    env = dict(os.environ)
    env["PATH"] = str(empty_bin)
    completed = _run_stubbed(stub_project, env, "verify")
    _assert_clean_refusal(completed)
    assert "missing required 'aee'" in completed.stderr


@pytest.mark.parametrize("version", ["1.0.2", "1.0.4", "2.0.0", "0.9.9"])
@requires_posix_stub
def test_out_of_range_engine_version_is_refused(
    tmp_path: Path, stub_project: Path, version: str
) -> None:
    completed = _run_stubbed(
        stub_project,
        _stub_env(tmp_path, version=version),
        "assess",
        "--input",
        "claims.json",
    )
    _assert_clean_refusal(completed)
    # The error names both the found and the required versions.
    assert version in completed.stderr
    assert run_aee.REQUIRED_AEE_VERSION in completed.stderr
    # The engine was never invoked for a verdict: no AEE output tree
    # (the fixture's .specify/extensions/evaluator is the installed
    # companion, not something the run wrote).
    assert not (stub_project / ".specify" / "extensions" / "aee").exists()


@requires_posix_stub
def test_unparseable_engine_version_is_refused(
    tmp_path: Path, stub_project: Path
) -> None:
    env = _stub_env(tmp_path)
    env["AEE_STUB_VERSION_TEXT"] = "aee (unknown build)"
    completed = _run_stubbed(stub_project, env, "assess", "--input", "claims.json")
    _assert_clean_refusal(completed)
    assert run_aee.REQUIRED_AEE_VERSION in completed.stderr


@pytest.mark.parametrize(
    ("text", "expected"),
    [("aee 1.4.0", (1, 4, 0)), ("aee 2.0.0-rc1", (2, 0, 0)), ("nope", None)],
)
def test_parse_aee_version(text: str, expected: tuple[int, int, int] | None) -> None:
    assert run_aee._parse_aee_version(text) == expected


@pytest.mark.parametrize(
    ("version", "ok"),
    [((1, 6, 0), True), ((1, 9, 9), True), ((1, 5, 0), False), ((1, 4, 0), False), ((1, 0, 2), False), ((2, 0, 0), False)]  # floor moved to engine 1.6.0 (Spec 006): 1.5.x is now refused, as 1.4.0 was by Spec 004 P5-E1,
)
def test_aee_version_range(version: tuple[int, int, int], ok: bool) -> None:
    assert run_aee._aee_version_satisfies(version) is ok


@requires_posix_stub
def test_engine_failure_emits_no_phantom_paths(
    tmp_path: Path, stub_project: Path
) -> None:
    completed = _run_stubbed(
        stub_project,
        _stub_env(tmp_path, behavior="fail"),
        "assess",
        "--input",
        "claims.json",
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["aee_exit_code"] == 2
    # No path is named for a file the engine never wrote.
    assert payload["assessment"] is None
    assert payload["evaluator_result"] is None
    assert payload["ledger"] is None


@requires_posix_stub
def test_assess_success_names_only_written_files(
    tmp_path: Path, stub_project: Path
) -> None:
    completed = _run_stubbed(
        stub_project, _stub_env(tmp_path), "assess", "--input", "claims.json"
    )
    assert completed.returncode == 1  # the stub's scored verdict passes through
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["aee_exit_code"] == 1
    for key in ("assessment", "evaluator_result", "ledger"):
        assert payload[key] is not None
        assert (stub_project / payload[key]).is_file()
    # The stub project models an installed evaluator extension, and the
    # runner reports the pair as present (RT3-04); without it, assess
    # refuses instead (Spec 005, tests/test_evaluator_required.py).
    assert payload["evaluator_extension_present"] is True


@requires_posix_stub
def test_assess_reports_evaluator_extension_when_present(
    tmp_path: Path, stub_project: Path
) -> None:
    # The fixture project carries the installed evaluator extension
    # (Spec 005); the runner reports the pair as present (RT3-04).
    completed = _run_stubbed(
        stub_project, _stub_env(tmp_path), "assess", "--input", "claims.json"
    )
    payload = json.loads(completed.stdout)
    assert payload["evaluator_extension_present"] is True


@requires_posix_stub
def test_graph_failure_includes_exit_code_and_no_phantom_path(
    tmp_path: Path, stub_project: Path
) -> None:
    completed = _run_stubbed(
        stub_project,
        _stub_env(tmp_path, behavior="fail"),
        "graph",
        "--input",
        "claims.json",
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["aee_exit_code"] == 2
    assert payload["ok"] is False
    assert payload["graph"] is None


@requires_posix_stub
def test_graph_success_includes_exit_code(tmp_path: Path, stub_project: Path) -> None:
    completed = _run_stubbed(
        stub_project, _stub_env(tmp_path), "graph", "--input", "claims.json"
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["aee_exit_code"] == 0
    assert payload["ok"] is True
    assert (stub_project / payload["graph"]).is_file()


@requires_posix_stub
def test_challenge_failure_emits_no_phantom_path(
    tmp_path: Path, stub_project: Path
) -> None:
    completed = _run_stubbed(
        stub_project,
        _stub_env(tmp_path, behavior="fail"),
        "challenge",
        "--input",
        "claims.json",
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["challenge"] is None
    assert payload["aee_exit_code"] == 2
    assert payload["ok"] is False


@requires_posix_stub
def test_gaps_output_stdout_is_pure_json(tmp_path: Path, stub_project: Path) -> None:
    (stub_project / "matrix.md").write_text("| Test ID |\n", encoding="utf-8")
    (stub_project / "evidence").mkdir()
    completed = _run_stubbed(
        stub_project,
        _stub_env(tmp_path),
        "gaps",
        "--matrix",
        "matrix.md",
        "--evidence",
        "evidence",
        "--output",
        "GAPS.md",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)  # engine chatter must not pollute stdout
    assert payload["gaps"] == "GAPS.md"
    assert payload["aee_exit_code"] == 0
    assert payload["ok"] is True
    assert "Gap register written" in completed.stderr


@requires_posix_stub
def test_gaps_close_requires_existing_register(
    tmp_path: Path, stub_project: Path
) -> None:
    (stub_project / "matrix.md").write_text("| Test ID |\n", encoding="utf-8")
    (stub_project / "evidence").mkdir()
    completed = _run_stubbed(
        stub_project,
        _stub_env(tmp_path),
        "gaps",
        "--matrix",
        "matrix.md",
        "--evidence",
        "evidence",
        "--output",
        "TYPO.md",
        "--close",
        "GAP-001",
    )
    _assert_clean_refusal(completed)


# ---------------------------------------------------------------------------
# Spec 002 Phase 4: extension version stamp (T403 / FR-4.2) and ledger
# verdict (T802 / FR-8.2). Hermetic: the stub `aee` executable emits a
# full-shaped assessment; the runner stamps it and ledgers it through
# the real aee.ledger of the ambient engine package.
# ---------------------------------------------------------------------------

import hashlib


@requires_posix_stub
def test_assess_stamps_extension_version_before_ledgering(
    tmp_path: Path, stub_project: Path
) -> None:
    completed = _run_stubbed(
        stub_project, _stub_env(tmp_path), "assess", "--input", "claims.json"
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    version = run_aee._extension_version()
    assert version is not None
    assert payload["extension_version"] == version
    assessment = json.loads((stub_project / payload["assessment"]).read_text())
    assert assessment["metadata"]["extension_version"] == version
    assert assessment["metadata"]["engine_version"] == "1.4.0"
    assert assessment["metadata"]["ledger"]["mode"] == "custom"
    # FR-8.2: the verify verdict rides alongside the paths.
    verdict = payload["ledger_verification"]
    assert verdict["valid"] is True
    assert verdict["entries"] == 1
    assert verdict["errors"] == []
    # The ledger hash covers the STAMPED assessment file exactly:
    # stamping happened before ledgering, not after (FR-4.2/FR-8.3).
    ledger_line = (stub_project / payload["ledger"]).read_text().splitlines()[0]
    entry = json.loads(ledger_line)
    canonical = json.dumps(
        assessment, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert entry["payload"]["assessment_sha256"] == digest


@requires_posix_stub
def test_assess_no_ledger_stamps_file_and_reports_no_verdict(
    tmp_path: Path, stub_project: Path
) -> None:
    completed = _run_stubbed(
        stub_project,
        _stub_env(tmp_path),
        "assess",
        "--input",
        "claims.json",
        "--no-ledger",
    )
    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ledger"] is None
    # No ledgering happened, so no verdict is invented (FR-8.2).
    assert payload["ledger_verification"] is None
    assessment = json.loads((stub_project / payload["assessment"]).read_text())
    assert assessment["metadata"]["extension_version"] == run_aee._extension_version()
    assert "ledger" not in assessment["metadata"]


@requires_posix_stub
def test_assess_second_run_verdict_counts_both_entries(
    tmp_path: Path, stub_project: Path
) -> None:
    env = _stub_env(tmp_path)
    _run_stubbed(stub_project, env, "assess", "--input", "claims.json")
    # The engine ledger refuses an exact-duplicate append (Spec 003
    # FR-4.6), so the second run carries distinct content via the
    # stub marker; two DISTINCT assessments must both chain.
    completed = _run_stubbed(
        stub_project,
        dict(env, AEE_STUB_MARKER="second"),
        "assess",
        "--input",
        "claims.json",
    )
    payload = json.loads(completed.stdout)
    assert payload["ledger_verification"]["valid"] is True
    assert payload["ledger_verification"]["entries"] == 2


def _ledger_refuses_duplicates() -> bool:
    """True when the importable engine ledger carries Spec 003
    FR-4.6 (the allow_duplicate opt-in on append_assessment). The
    released 1.4.0 ledger predates it and chains duplicates."""
    try:
        import inspect

        from aee.ledger import HashChainLedger

        return "allow_duplicate" in inspect.signature(
            HashChainLedger.append_assessment
        ).parameters
    except (ImportError, AttributeError, ValueError):
        return False


@requires_posix_stub
@pytest.mark.skipif(
    not _ledger_refuses_duplicates(),
    reason="importable engine ledger predates Spec 003 FR-4.6 duplicate refusal",
)
def test_assess_exact_replay_is_refused_like_the_engine_cli(
    tmp_path: Path, stub_project: Path
) -> None:
    # Spec 003 FR-4.6: the engine ledger refuses to record the same
    # assessment content twice for one actor (the bare `aee assess`
    # CLI refuses identically). The runner surfaces that as a clean
    # exit-2 refusal — no traceback, no JSON summary, ledger intact.
    env = _stub_env(tmp_path)
    _run_stubbed(stub_project, env, "assess", "--input", "claims.json")
    completed = _run_stubbed(stub_project, env, "assess", "--input", "claims.json")
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "duplicate assessment" in completed.stderr
    assert "Traceback" not in completed.stderr
    ledger = (
        stub_project / ".specify/extensions/aee/ledger/epistemic-ledger.jsonl"
    )
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_extension_version_matches_manifest() -> None:
    import yaml

    manifest = yaml.safe_load(
        (Path(__file__).parents[1] / "extension.yml").read_text(encoding="utf-8")
    )
    assert run_aee._extension_version() == str(manifest["extension"]["version"])

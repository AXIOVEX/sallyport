"""RT3 remediation regression tests (specs/rt3-remediation).

Each test inverts one still-live RT3 probe scenario from the Phase 9
re-run: the behavior the red team demonstrated against the pre-fix
tree must now refuse, or behave as the spec requires. Runner tests
are hermetic (a stub `aee` on PATH); token-economy tests run
in-process against the real module.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
RUNNER = ROOT / "scripts" / "python" / "run_aee.py"
TE_SCRIPT = ROOT / "scripts" / "python" / "aee_token_economy.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_aee = _load("run_aee_rt3", RUNNER)
te = _load("aee_token_economy_rt3", TE_SCRIPT)

requires_posix_stub = pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub aee is executed via a POSIX shebang script",
)


# ---------------------------------------------------------------------------
# stub engine (mirrors tests/test_runner.py's hermetic pattern)
# ---------------------------------------------------------------------------

_STUB_AEE = '''\
#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("aee 1.6.0")
    sys.exit(0)
if os.environ.get("AEE_STUB_BEHAVIOR") == "sleep":
    time.sleep(30)
command = args[0] if args else ""


def flag(name):
    return args[args.index(name) + 1] if name in args else None


if command == "assess":
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
if command == "gaps":
    output = flag("--output")
    if output:
        Path(output).write_text("**Open:** 1\\n")
    sys.exit(0)
sys.exit(0)
'''


def _stub_env(tmp_path: Path, behavior: str = "ok") -> dict:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "aee"
    stub.write_text(_STUB_AEE, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["AEE_STUB_BEHAVIOR"] = behavior
    return env


def _run_runner(root: Path, env: dict, *args: str) -> subprocess.CompletedProcess[str]:
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


# ---------------------------------------------------------------------------
# RT3-05 — gaps evidence confinement
# ---------------------------------------------------------------------------


@requires_posix_stub
def test_gaps_refuses_symlinked_evidence_file(
    tmp_path: Path, stub_project: Path
) -> None:
    root = stub_project
    (root / "matrix.md").write_text("| Test ID |\n| T-API-001 |\n", encoding="utf-8")
    evidence = root / "evidence"
    evidence.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(
        '{"results": [{"test_id": "T-API-001", "status": "pass"}]}',
        encoding="utf-8",
    )
    try:
        (evidence / "linked.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")
    completed = _run_runner(
        root,
        _stub_env(tmp_path),
        "gaps",
        "--matrix", "matrix.md",
        "--evidence", "evidence",
        "--output", "GAPS.md",
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "refused:" in completed.stderr
    assert "linked.json" in completed.stderr
    # The gap must NOT have been closed by out-of-root data.
    assert not (root / "GAPS.md").exists()


@requires_posix_stub
def test_gaps_refuses_symlinked_evidence_subdir(
    tmp_path: Path, stub_project: Path
) -> None:
    root = stub_project
    (root / "matrix.md").write_text("| Test ID |\n| T-API-001 |\n", encoding="utf-8")
    evidence = root / "evidence"
    evidence.mkdir()
    real_dir = tmp_path / "real-evidence"
    real_dir.mkdir()
    (real_dir / "result.json").write_text("{}", encoding="utf-8")
    try:
        (evidence / "linked-dir").symlink_to(real_dir, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    completed = _run_runner(
        root,
        _stub_env(tmp_path),
        "gaps",
        "--matrix", "matrix.md",
        "--evidence", "evidence",
        "--output", "GAPS.md",
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "linked-dir" in completed.stderr


@requires_posix_stub
def test_gaps_accepts_real_evidence(tmp_path: Path, stub_project: Path) -> None:
    root = stub_project
    (root / "matrix.md").write_text("| Test ID |\n| T-API-001 |\n", encoding="utf-8")
    evidence = root / "evidence"
    (evidence / "nested").mkdir(parents=True)
    (evidence / "nested" / "result.json").write_text("{}", encoding="utf-8")
    completed = _run_runner(
        root,
        _stub_env(tmp_path),
        "gaps",
        "--matrix", "matrix.md",
        "--evidence", "evidence",
        "--output", "GAPS.md",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["gaps"] == "GAPS.md"


# ---------------------------------------------------------------------------
# RT3-08 — collision-proof filenames
# ---------------------------------------------------------------------------


def test_unique_stamp_never_collides_and_sorts() -> None:
    stamps = {run_aee._unique_stamp() for _ in range(200)}
    assert len(stamps) == 200
    for stamp in stamps:
        assert re.fullmatch(r"\d{8}T\d{6}\d{6}-[0-9a-f]{6}Z", stamp), stamp


@requires_posix_stub
def test_same_second_assessments_never_share_a_filename(
    tmp_path: Path, stub_project: Path
) -> None:
    env = _stub_env(tmp_path)
    named: list[str] = []
    for marker in ("FIRST", "SECOND"):
        env["AEE_STUB_MARKER"] = marker
        completed = _run_runner(
            stub_project, env, "assess", "--input", "claims.json"
        )
        assert completed.returncode == 1, completed.stdout + completed.stderr
        named.append(json.loads(completed.stdout)["assessment"])
    assert named[0] != named[1], named
    contents = {
        json.loads((stub_project / name).read_text(encoding="utf-8"))["stub_marker"]
        for name in named
    }
    assert contents == {"FIRST", "SECOND"}
    ledger = (
        stub_project / ".specify/extensions/aee/ledger/epistemic-ledger.jsonl"
    )
    assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 2


# ---------------------------------------------------------------------------
# RT3-11 — subprocess timeouts with a distinct verdict
# ---------------------------------------------------------------------------


@requires_posix_stub
def test_assess_timeout_is_a_distinct_refusal(
    tmp_path: Path, stub_project: Path
) -> None:
    env = _stub_env(tmp_path, behavior="sleep")
    started = time.monotonic()
    completed = subprocess.run(
        [
            sys.executable, str(RUNNER),
            "--project-root", str(stub_project),
            "--timeout", "1",
            "assess", "--input", "claims.json",
        ],
        check=False, capture_output=True, text=True, env=env,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 20, f"runner took {elapsed:.1f}s against a hung engine"
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "refused:" in completed.stderr and "timed out" in completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["timed_out"] is True
    assert payload["aee_exit_code"] is None
    assert payload["ok"] is False
    assert payload["assessment"] is None


@requires_posix_stub
def test_gate_timeout_is_a_clean_refusal(tmp_path: Path, stub_project: Path) -> None:
    env = _stub_env(tmp_path, behavior="sleep")
    (stub_project / "a.json").write_text("{}", encoding="utf-8")
    started = time.monotonic()
    completed = subprocess.run(
        [
            sys.executable, str(RUNNER),
            "--project-root", str(stub_project),
            "--timeout", "1",
            "gate", "--input", "a.json",
        ],
        check=False, capture_output=True, text=True, env=env,
    )
    assert time.monotonic() - started < 20
    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr
    assert "refused:" in completed.stderr and "timed out" in completed.stderr


# ---------------------------------------------------------------------------
# RT3-13 — gate --latest and the assess pointer
# ---------------------------------------------------------------------------


@requires_posix_stub
def test_assess_writes_latest_pointer_and_gate_latest_uses_it(
    tmp_path: Path, stub_project: Path
) -> None:
    env = _stub_env(tmp_path)
    completed = _run_runner(stub_project, env, "assess", "--input", "claims.json")
    assert completed.returncode == 1, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["latest_pointer"] == (
        ".specify/extensions/aee/assessments/latest-after_plan.json"
    )
    pointer = json.loads(
        (stub_project / payload["latest_pointer"]).read_text(encoding="utf-8")
    )
    assert pointer["assessment"] == payload["assessment"]
    assert pointer["phase"] == "after_plan"
    gated = _run_runner(stub_project, env, "gate", "--latest", "after_plan")
    assert gated.returncode == 0, gated.stdout + gated.stderr
    assert "note: --latest after_plan resolved to" in gated.stderr
    assert payload["assessment"] in gated.stderr


@requires_posix_stub
def test_gate_latest_without_pointer_refuses(
    tmp_path: Path, stub_project: Path
) -> None:
    completed = _run_runner(
        stub_project, _stub_env(tmp_path), "gate", "--latest", "after_tasks"
    )
    assert completed.returncode == 2
    assert "refused:" in completed.stderr
    assert "Traceback" not in completed.stderr


@requires_posix_stub
def test_gate_input_and_latest_are_mutually_exclusive(
    tmp_path: Path, stub_project: Path
) -> None:
    completed = _run_runner(
        stub_project,
        _stub_env(tmp_path),
        "gate", "--input", "claims.json", "--latest", "after_plan",
    )
    assert completed.returncode == 2
    assert "not allowed with" in completed.stderr


# ---------------------------------------------------------------------------
# RT3-17 — _safe_output returns the resolved path (both scripts)
# ---------------------------------------------------------------------------


def test_safe_output_returns_resolved_path(tmp_path: Path) -> None:
    target = run_aee._safe_output(tmp_path, Path("sub/../out/result.json"))
    assert target == (tmp_path / "out" / "result.json").resolve(strict=False)
    assert target.is_absolute()


def test_te_safe_output_returns_resolved_path(tmp_path: Path) -> None:
    target = te._safe_output(tmp_path, Path("sub/../out/result.json"))
    assert target == (tmp_path / "out" / "result.json").resolve(strict=False)
    assert target.is_absolute()


# ---------------------------------------------------------------------------
# RT3-10 — token-router confinement, strict types, auditable record
# ---------------------------------------------------------------------------


def test_router_home_outside_trust_domain_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    root = tmp_path / "project"
    root.mkdir()
    outside_home = Path(tempfile.mkdtemp(prefix="rt3-router-outside-"))
    router = outside_home / "scripts" / "router.py"
    router.parent.mkdir(parents=True)
    router.write_text("# malicious router\n", encoding="utf-8")
    monkeypatch.setenv("TOKEN_ROUTER_HOME", str(outside_home))
    assert te._find_token_router(root) is None


def test_router_home_inside_project_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    home = root / "my-router"
    router = home / "scripts" / "router.py"
    router.parent.mkdir(parents=True)
    router.write_text("# local router\n", encoding="utf-8")
    monkeypatch.setenv("TOKEN_ROUTER_HOME", str(home))
    assert te._find_token_router(root) == router


def test_router_backend_rejects_bool_ranges(tmp_path: Path) -> None:
    router = tmp_path / "router.py"
    router.write_text(
        "import json\nprint(json.dumps({'ranges': [[True, 3], [2, 4], ['5', 9]]}))\n",
        encoding="utf-8",
    )
    source = tmp_path / "big.log"
    source.write_text("x\n" * 10, encoding="utf-8")
    args = SimpleNamespace(mode="error_log", query="x")
    assert te._router_backend(router, source, args) == [(2, 4)]


def test_route_record_names_router_path_and_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    fake_router = tmp_path / "router.py"
    fake_router.write_text(
        "import json\nprint(json.dumps({'ranges': [[5, 8]]}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(te, "_find_token_router", lambda *_args: fake_router)
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/ollama" if name == "ollama" else None
    )
    target = tmp_path / "big.log"
    target.write_text(
        "\n".join(f"filler line {i}" for i in range(1, 401)), encoding="utf-8"
    )
    code = te.main(
        ["--project-root", str(tmp_path), "route", "--file", "big.log",
         "--query", "anything"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["backend"] == "token-router"
    assert payload["router"]["path"] == str(fake_router)
    assert payload["router"]["sha256"] == hashlib.sha256(
        fake_router.read_bytes()
    ).hexdigest()


# ---------------------------------------------------------------------------
# RT3-09 — shell confinement: cwd, timeout, telemetry redaction
# ---------------------------------------------------------------------------


def test_redact_argv_hides_secret_values() -> None:
    assert te._redact_argv(["cmd", "token=SUPERSECRETARG123"]) == [
        "cmd", "token=«redacted»",
    ]
    assert te._redact_argv(["cmd", "--api-key", "hunter2", "plain"]) == [
        "cmd", "--api-key", "«redacted»", "plain",
    ]
    assert te._redact_argv(["cmd", "AKIAIOSFODNN7EXAMPLE"]) == [
        "cmd", "«redacted»",
    ]
    assert te._redact_argv(["cmd", "--verbose", "file.txt"]) == [
        "cmd", "--verbose", "file.txt",
    ]


def test_shell_runs_in_project_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    code = te.main(
        [
            "--project-root", str(root), "shell", "--",
            sys.executable, "-c",
            "import os; open('cwd.txt', 'w').write(os.getcwd())",
        ]
    )
    assert code == 0
    assert (root / "cwd.txt").read_text(encoding="utf-8") == str(root.resolve())


def test_shell_telemetry_redacts_secrets(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    code = te.main(
        [
            "--project-root", str(root), "shell", "--",
            sys.executable, "-c", "pass", "token=SUPERSECRETARG123",
        ]
    )
    assert code == 0
    telemetry = (
        root / ".specify/extensions/aee/telemetry/shell-calls.jsonl"
    ).read_text(encoding="utf-8")
    assert "SUPERSECRETARG123" not in telemetry
    record = json.loads(telemetry.strip().splitlines()[-1])
    assert record["command"][-1] == "token=«redacted»"
    assert record["cwd"] == root.resolve().as_posix()
    assert record["timed_out"] is False


def test_shell_timeout_returns_2_and_records_it(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    started = time.monotonic()
    code = te.main(
        [
            "--project-root", str(root), "shell", "--timeout", "1", "--",
            sys.executable, "-c", "import time; time.sleep(10)",
        ]
    )
    assert time.monotonic() - started < 8
    assert code == 2
    record = json.loads(
        (root / ".specify/extensions/aee/telemetry/shell-calls.jsonl")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()[-1]
    )
    assert record["timed_out"] is True
    assert record["exit_code"] is None


# ---------------------------------------------------------------------------
# RT3-18 — route byte cap (behavior) and doc truth (below)
# ---------------------------------------------------------------------------


def test_route_refuses_file_over_byte_cap(tmp_path: Path, capsys) -> None:
    target = tmp_path / "big.log"
    target.write_text("x" * 500, encoding="utf-8")
    code = te.main(
        [
            "--project-root", str(tmp_path), "route", "--file", "big.log",
            "--max-file-bytes", "100",
        ]
    )
    assert code == 2
    assert "byte cap" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# RT3-12 / RT3-14 / RT3-16 / RT3-18 / RT3-19 — docs and install truth
# ---------------------------------------------------------------------------


def test_command_templates_quote_placeholders() -> None:
    # An invocation line (one that runs an adapter script) must not
    # carry a <placeholder> outside double quotes: unquoted, the
    # agent's shell would word-split or substitute it (RT3-12).
    offenders: list[str] = []
    for path in sorted((ROOT / "commands").glob("*.md")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "run_aee.py" not in line and "aee_token_economy.py" not in line:
                continue
            unquoted = re.sub(r'"[^"]*"', "", line)
            if "<" in unquoted:
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert offenders == []
    assess = (ROOT / "commands" / "speckit.aee.assess.md").read_text(
        encoding="utf-8"
    )
    assert "never eval" in assess


def test_user_install_and_older_setup_fallback_are_separate() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "specify extension add aee\n" in readme
    assert "spec-kit-aee/archive/refs/tags/" not in readme
    troubleshooting = (ROOT / "DEVELOPMENT.md").read_text(encoding="utf-8")
    match = re.search(
        r"spec-kit-aee/archive/refs/tags/v(\d+\.\d+\.\d+)\.zip", troubleshooting
    )
    assert match, "older installations need a reproducible troubleshooting fallback"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {match.group(1)} - " in changelog, "fallback must name a recorded release"
    assert "1.1.0 (unreleased)" not in readme


def test_changelog_does_not_call_shipped_versions_unreleased() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## 1.2.0 - Unreleased" not in changelog
    assert "## 1.1.0 - Unreleased" not in changelog
    assert "## 1.2.0 - 2026-10-06" in changelog


def test_check_install_has_no_bare_asserts_and_supports_archive() -> None:
    source = (ROOT / "scripts" / "check_install.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*assert\s", source, flags=re.MULTILINE)
    assert "--from-archive" in source
    assert "CheckFailed" in source


def test_route_and_savings_docs_match_implementation() -> None:
    route_md = (ROOT / "commands" / "speckit.aee.route-evidence.md").read_text(
        encoding="utf-8"
    )
    assert "`rg`/`grep`" not in route_md
    assert "pure-Python literal match windows" in route_md
    savings_md = (ROOT / "commands" / "speckit.aee.report-savings.md").read_text(
        encoding="utf-8"
    )
    assert "used=<yes|no|n/a>" not in savings_md
    assert '"used": true' in savings_md


def test_config_template_disclaims_both_scripts() -> None:
    header = (ROOT / "templates" / "aee-config.template.yml").read_text(
        encoding="utf-8"
    )[:400]
    assert "NO script loads this file" in header
    assert "run_aee.py" in header and "aee_token_economy.py" in header

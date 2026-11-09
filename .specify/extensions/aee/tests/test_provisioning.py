"""Dependency provisioning: the runner installs what the manifest requires.

Spec Kit's ``requires.tools`` is informational, so ``specify extension
add aee`` alone installs neither the engine nor the evaluator
extension. These tests pin the runner's self-provisioning without
network access:

- the engine runtime is built for real (venv + pip) from a locally
  built stand-in wheel, selected through pip's own PIP_NO_INDEX /
  PIP_FIND_LINKS settings;
- the evaluator install is exercised against a fake ``specify`` that
  fetches the archive from the runner's loopback server, with the
  download itself replaced so the SHA-256 check is under test control.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "python" / "run_aee.py"
SPEC = importlib.util.spec_from_file_location("run_aee_provisioning", RUNNER)
assert SPEC and SPEC.loader
run_aee = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_aee)

_FAKE_ENGINE_CLI = '''\
import sys


def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        print("aee 1.6.0")
    return 0
'''


def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return f"sha256={digest.decode()}"


def _build_wheel(
    directory: Path, dist: str, version: str, files: dict[str, bytes],
    console_scripts: str = "",
) -> Path:
    """A minimal pure-Python wheel pip installs offline."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{dist.replace('-', '_')}-{version}"
    dist_info = f"{stem}.dist-info"
    files = {
        **files,
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n".encode()
        ),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: spec-kit-aee-tests\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    if console_scripts:
        files[f"{dist_info}/entry_points.txt"] = (
            f"[console_scripts]\n{console_scripts}\n".encode()
        )
    record = "".join(
        f"{name},{_record_hash(data)},{len(data)}\n" for name, data in files.items()
    )
    record += f"{dist_info}/RECORD,,\n"
    wheel = directory / f"{stem}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr(f"{dist_info}/RECORD", record)
    return wheel


def _build_runtime_wheels(directory: Path) -> None:
    """Stand-ins for the engine (CLI + aee.ledger) and jsonschema."""
    _build_wheel(
        directory,
        "applied-epistemic-engineering",
        "1.6.0",
        {
            "aee/__init__.py": b'__version__ = "1.6.0"\n',
            "aee/cli.py": _FAKE_ENGINE_CLI.encode(),
            "aee/ledger.py": b"class HashChainLedger:\n    pass\n",
        },
        "aee = aee.cli:main",
    )
    _build_wheel(
        directory, "jsonschema", "4.0.0", {"jsonschema/__init__.py": b""}
    )


def _installed_runner(tmp_path: Path) -> Path:
    """Copy the runner into an extension layout so .runtime lands in tmp."""
    extension = tmp_path / "extension" / "aee"
    (extension / "scripts" / "python").mkdir(parents=True)
    shutil.copyfile(ROOT / "extension.yml", extension / "extension.yml")
    runner = extension / "scripts" / "python" / "run_aee.py"
    shutil.copyfile(RUNNER, runner)
    return runner


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "ledger.jsonl").write_text("", encoding="utf-8")
    return root


def _isolated_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """PATH without any `aee`, so the invoking Python lacks the engine CLI."""
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    env = {**os.environ, "PATH": str(empty), "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    env.update(extra)
    return env


def _run(runner: Path, root: Path, env: dict[str, str], *args: str):
    return subprocess.run(
        [sys.executable, str(runner), "--project-root", str(root), *args],
        check=False, capture_output=True, text=True, env=env, timeout=600,
    )


def test_runtime_is_provisioned_then_reused(tmp_path: Path) -> None:
    wheels = tmp_path / "wheels"
    _build_runtime_wheels(wheels)
    runner = _installed_runner(tmp_path)
    root = _project(tmp_path)
    env = _isolated_env(
        tmp_path,
        SPECKIT_AEE_AUTO_INSTALL="1",
        PIP_NO_INDEX="1",
        PIP_FIND_LINKS=str(wheels),
    )

    first = _run(runner, root, env, "verify", "--ledger", "ledger.jsonl")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "note: installing applied-epistemic-engineering>=1.6.0,<2" in first.stderr
    runtime = runner.parents[2] / ".runtime"
    venv_bin = runtime / "venv" / ("Scripts" if os.name == "nt" else "bin")
    assert shutil.which("aee", path=str(venv_bin)) is not None
    # The generated environment never shows up as project changes.
    assert (runtime / ".gitignore").read_text(encoding="utf-8").splitlines()[-1] == "*"

    second = _run(runner, root, env, "verify", "--ledger", "ledger.jsonl")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "note: installing" not in second.stderr

    # Disabling automatic installation stops installs, not the use of
    # the runtime that is already provisioned.
    disabled = _run(
        runner, root, {**env, "SPECKIT_AEE_AUTO_INSTALL": "0"},
        "verify", "--ledger", "ledger.jsonl",
    )
    assert disabled.returncode == 0, disabled.stdout + disabled.stderr
    assert "note: installing" not in disabled.stderr


def test_damaged_runtime_is_rebuilt(tmp_path: Path) -> None:
    wheels = tmp_path / "wheels"
    _build_runtime_wheels(wheels)
    runner = _installed_runner(tmp_path)
    root = _project(tmp_path)
    env = _isolated_env(
        tmp_path,
        SPECKIT_AEE_AUTO_INSTALL="1",
        PIP_NO_INDEX="1",
        PIP_FIND_LINKS=str(wheels),
    )
    first = _run(runner, root, env, "verify", "--ledger", "ledger.jsonl")
    assert first.returncode == 0, first.stdout + first.stderr

    venv = runner.parents[2] / ".runtime" / "venv"
    (ledger_module,) = venv.glob("**/site-packages/aee/ledger.py")
    ledger_module.unlink()
    # The console script still answers --version, so a CLI-only
    # readiness check would trust this environment.
    cli = shutil.which("aee", path=str(venv / ("Scripts" if os.name == "nt" else "bin")))
    assert cli is not None
    version = subprocess.run([cli, "--version"], capture_output=True, text=True, check=False)
    assert version.stdout.strip() == "aee 1.6.0"

    repaired = _run(runner, root, env, "verify", "--ledger", "ledger.jsonl")
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert "note: installing" in repaired.stderr
    assert ledger_module.is_file()

    reused = _run(runner, root, env, "verify", "--ledger", "ledger.jsonl")
    assert reused.returncode == 0, reused.stdout + reused.stderr
    assert "note: installing" not in reused.stderr


def test_runtime_install_failure_is_a_clean_refusal(tmp_path: Path) -> None:
    no_wheels = tmp_path / "no-wheels"
    no_wheels.mkdir()
    runner = _installed_runner(tmp_path)
    env = _isolated_env(
        tmp_path,
        SPECKIT_AEE_AUTO_INSTALL="1",
        PIP_NO_INDEX="1",
        PIP_FIND_LINKS=str(no_wheels),
    )
    completed = _run(runner, _project(tmp_path), env, "verify", "--ledger", "ledger.jsonl")
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "Traceback" not in completed.stderr
    assert "refused: installing applied-epistemic-engineering>=1.6.0,<2 failed" in (
        completed.stderr
    )
    # The refusal names the manual alternative and the opt-out.
    assert "SPECKIT_AEE_AUTO_INSTALL=0" in completed.stderr


def test_disabled_auto_install_refuses_with_hint(tmp_path: Path) -> None:
    runner = _installed_runner(tmp_path)
    env = _isolated_env(tmp_path, SPECKIT_AEE_AUTO_INSTALL="0")
    completed = _run(runner, _project(tmp_path), env, "verify", "--ledger", "ledger.jsonl")
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "missing required 'aee'" in completed.stderr
    assert "disabled by SPECKIT_AEE_AUTO_INSTALL" in completed.stderr
    assert not (runner.parents[2] / ".runtime").exists()


@pytest.mark.parametrize(
    ("value", "enabled"),
    [(None, True), ("1", True), ("yes", True), ("0", False), ("false", False),
     (" OFF ", False), ("no", False)],
)
def test_auto_install_switch(
    monkeypatch: pytest.MonkeyPatch, value: str | None, enabled: bool
) -> None:
    if value is None:
        monkeypatch.delenv("SPECKIT_AEE_AUTO_INSTALL", raising=False)
    else:
        monkeypatch.setenv("SPECKIT_AEE_AUTO_INSTALL", value)
    assert run_aee._auto_install_enabled() is enabled


# --- evaluator extension -------------------------------------------------

_FAKE_SPECIFY = '''\
import hashlib
import sys
import urllib.request
from pathlib import Path

args = sys.argv[1:]
log = Path(__file__).with_name("specify-calls.log")
log.write_text(log.read_text() + " ".join(args) + "\\n" if log.exists() else " ".join(args) + "\\n")
if Path(__file__).with_name("fail").exists():
    print("Error: simulated install failure")
    sys.exit(1)
assert args[:4] == ["extension", "add", "evaluator", "--from"], args
assert args[5:] == ["--force"], args
assert sys.stdin.readline().strip() == "y"  # the --from confirmation
data = urllib.request.urlopen(args[4], timeout=30).read()
installed = Path.cwd() / ".specify" / "extensions" / "evaluator"
installed.mkdir(parents=True)
(installed / "extension.yml").write_text(
    "extension:\\n  id: evaluator\\n# sha256: " + hashlib.sha256(data).hexdigest() + "\\n"
)
'''


def _write_command(bin_dir: Path, name: str, source: str) -> None:
    """An executable `name` on PATH that runs `source` with this Python."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / f"{name}_impl.py"
    script.write_text(source, encoding="utf-8")
    if os.name == "nt":
        (bin_dir / f"{name}.cmd").write_text(
            f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
        )
    else:
        launcher = bin_dir / name
        launcher.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8"
        )
        launcher.chmod(0o755)


@pytest.fixture
def archive_bytes(monkeypatch: pytest.MonkeyPatch) -> Iterator[bytes]:
    """Serve fixed bytes for the pinned URL and pin their digest."""
    data = b"PK-fake-evaluator-archive"
    requested: list[str] = []

    def fake_urlopen(url: str, timeout: float) -> io.BytesIO:
        requested.append(url)
        return io.BytesIO(data)

    monkeypatch.setattr(run_aee.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        run_aee, "EVALUATOR_ARCHIVE_SHA256", hashlib.sha256(data).hexdigest()
    )
    yield data
    assert requested in ([], [run_aee.EVALUATOR_ARCHIVE_URL])


def _specify_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".specify").mkdir(parents=True)
    return root


def test_evaluator_installed_through_specify_from_verified_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, archive_bytes: bytes
) -> None:
    bin_dir = tmp_path / "bin"
    _write_command(bin_dir, "specify", _FAKE_SPECIFY)
    monkeypatch.setenv("PATH", str(bin_dir))
    root = _specify_project(tmp_path)

    run_aee._install_evaluator_extension(root, timeout=60)

    manifest = root / ".specify" / "extensions" / "evaluator" / "extension.yml"
    # Spec Kit received exactly the verified bytes over loopback HTTP.
    assert hashlib.sha256(archive_bytes).hexdigest() in manifest.read_text()
    calls = (bin_dir / "specify-calls.log").read_text().splitlines()
    assert len(calls) == 1
    assert calls[0].startswith("extension add evaluator --from http://127.0.0.1:")
    assert calls[0].endswith(" --force")


def test_evaluator_digest_mismatch_installs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, archive_bytes: bytes
) -> None:
    monkeypatch.setattr(run_aee, "EVALUATOR_ARCHIVE_SHA256", "0" * 64)
    bin_dir = tmp_path / "bin"
    _write_command(bin_dir, "specify", _FAKE_SPECIFY)
    monkeypatch.setenv("PATH", str(bin_dir))
    root = _specify_project(tmp_path)

    with pytest.raises(ValueError, match="refusing to install it"):
        run_aee._install_evaluator_extension(root, timeout=60)
    assert not (bin_dir / "specify-calls.log").exists()
    assert not (root / ".specify" / "extensions").exists()


def test_evaluator_install_failure_names_manual_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, archive_bytes: bytes
) -> None:
    bin_dir = tmp_path / "bin"
    _write_command(bin_dir, "specify", _FAKE_SPECIFY)
    (bin_dir / "fail").write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(bin_dir))

    with pytest.raises(ValueError) as excinfo:
        run_aee._install_evaluator_extension(_specify_project(tmp_path), timeout=60)
    message = str(excinfo.value)
    assert "simulated install failure" in message
    assert f"specify extension add evaluator --from {run_aee.EVALUATOR_ARCHIVE_URL}" in message


def test_evaluator_install_without_specify_names_manual_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(ValueError, match="'specify' command needed to install it"):
        run_aee._install_evaluator_extension(_specify_project(tmp_path), timeout=60)


_STUB_ENGINE = '''\
import json
import sys
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("aee 1.6.0")
    sys.exit(0)
if args[:1] == ["assess"] and "--help" not in args:
    flag = lambda name: args[args.index(name) + 1]
    Path(flag("--output")).write_text(json.dumps({"outcome": "iterate", "metadata": {}}))
    Path(flag("--evaluator-output")).write_text(json.dumps({"outcome": "iterate"}))
    sys.exit(1)
sys.exit(0)
'''


@pytest.mark.parametrize("auto_install", [True, False])
def test_assess_installs_missing_evaluator_only_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    auto_install: bool,
) -> None:
    if not run_aee._engine_importable():
        pytest.fail("Install requirements/dev.txt: the aee package is required")
    bin_dir = tmp_path / "bin"
    _write_command(bin_dir, "aee", _STUB_ENGINE)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("SPECKIT_AEE_AUTO_INSTALL", "1" if auto_install else "0")
    root = _specify_project(tmp_path)
    (root / "claims.json").write_text('{"claims": []}', encoding="utf-8")
    calls: list[Path] = []

    def fake_install(project: Path, timeout: float) -> None:
        calls.append(project)
        (project / ".specify" / "extensions" / "evaluator").mkdir(parents=True)

    monkeypatch.setattr(run_aee, "_install_evaluator_extension", fake_install)
    code = run_aee.main(
        ["--project-root", str(root), "assess", "--input", "claims.json", "--no-ledger"]
    )
    out, err = capsys.readouterr()
    if auto_install:
        assert code == 1, out + err
        assert calls == [root.resolve()]
        assert json.loads(out)["evaluator_extension_present"] is True
    else:
        assert code == 2, out + err
        assert calls == []
        assert "disabled by SPECKIT_AEE_AUTO_INSTALL" in err


def test_assess_does_not_reinstall_present_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if not run_aee._engine_importable():
        pytest.fail("Install requirements/dev.txt: the aee package is required")
    bin_dir = tmp_path / "bin"
    _write_command(bin_dir, "aee", _STUB_ENGINE)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("SPECKIT_AEE_AUTO_INSTALL", "1")
    root = _specify_project(tmp_path)
    (root / ".specify" / "extensions" / "evaluator").mkdir(parents=True)
    (root / "claims.json").write_text('{"claims": []}', encoding="utf-8")

    def unexpected(*_args: object) -> None:
        raise AssertionError("installer must not run when the evaluator is present")

    monkeypatch.setattr(run_aee, "_install_evaluator_extension", unexpected)
    code = run_aee.main(
        ["--project-root", str(root), "assess", "--input", "claims.json", "--no-ledger"]
    )
    out, err = capsys.readouterr()
    assert code == 1, out + err
    assert json.loads(out)["evaluator_extension_present"] is True


def test_evaluator_pin_matches_manifest_range() -> None:
    import yaml
    from packaging.specifiers import SpecifierSet

    manifest = yaml.safe_load((ROOT / "extension.yml").read_text(encoding="utf-8"))
    tools = {tool["name"]: tool for tool in manifest["requires"]["tools"]}
    assert run_aee.EVALUATOR_VERSION in SpecifierSet(tools["spec-kit-evaluator"]["version"])
    # Immutable sources only: the released version tag, or (release
    # candidates) a full commit archive. Never a branch archive.
    base = "https://github.com/electrohire/spec-kit-evaluator/archive/"
    tag = f"{base}refs/tags/v{run_aee.EVALUATOR_VERSION}.zip"
    assert run_aee.EVALUATOR_ARCHIVE_URL == tag or re.fullmatch(
        re.escape(base) + r"[0-9a-f]{40}\.zip", run_aee.EVALUATOR_ARCHIVE_URL
    )
    assert re.fullmatch(r"[0-9a-f]{64}", run_aee.EVALUATOR_ARCHIVE_SHA256)
    assert run_aee.ENGINE_REQUIREMENT == (
        f"applied-epistemic-engineering{tools['aee']['version']}"
    )


# --- compose -------------------------------------------------------------

_FAKE_COMPOSER = '''\
import json
import sys
from pathlib import Path

import jsonschema  # noqa: F401  the real composer's hard dependency

Path(__file__).with_name("composer-call.json").write_text(
    json.dumps({"python": sys.executable, "argv": sys.argv[1:]})
)
sys.exit(1)
'''


def _compose_project(tmp_path: Path, *, results: bool = True) -> Path:
    root = _specify_project(tmp_path)
    scripts = root / ".specify" / "extensions" / "evaluator" / "scripts" / "python"
    scripts.mkdir(parents=True)
    (scripts / "compose_results.py").write_text(_FAKE_COMPOSER, encoding="utf-8")
    if results:
        (root / ".specify" / "extensions" / "evaluator" / "results").mkdir()
    return root


def _compose_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auto: str) -> None:
    if not run_aee._engine_importable():
        pytest.fail("Install requirements/dev.txt: the aee package is required")
    bin_dir = tmp_path / "bin"
    _write_command(bin_dir, "aee", _STUB_ENGINE)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("SPECKIT_AEE_AUTO_INSTALL", auto)


def test_compose_runs_evaluator_composer_on_runner_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _compose_env(tmp_path, monkeypatch, "0")
    root = _compose_project(tmp_path)
    code = run_aee.main([
        "--project-root", str(root), "compose", "--phase", "plan",
        "--strategy", "majority", "--output", "composed.json",
    ])
    assert code == 1  # the composer's exit code passes through
    call = json.loads(
        (root / ".specify/extensions/evaluator/scripts/python/composer-call.json")
        .read_text(encoding="utf-8")
    )
    assert Path(call["python"]).resolve() == Path(sys.executable).resolve()
    resolved = root.resolve()
    assert call["argv"] == [
        "--results-dir", str(resolved / ".specify/extensions/evaluator/results"),
        "--phase", "after_plan",
        "--strategy", "majority",
        "--output", str(resolved / "composed.json"),
    ]


def test_compose_refuses_without_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _compose_env(tmp_path, monkeypatch, "0")
    root = _compose_project(tmp_path, results=False)
    code = run_aee.main(["--project-root", str(root), "compose", "--phase", "plan"])
    assert code == 2
    assert "no evaluator results to compose" in capsys.readouterr().err


def test_compose_refuses_output_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _compose_env(tmp_path, monkeypatch, "0")
    root = _compose_project(tmp_path)
    code = run_aee.main([
        "--project-root", str(root), "compose", "--phase", "plan",
        "--output", "../outside.json",
    ])
    assert code == 2
    assert "escapes project root" in capsys.readouterr().err
    assert not (root / ".specify/extensions/evaluator/scripts/python/composer-call.json").exists()


def test_compose_without_jsonschema_and_auto_install_disabled_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _compose_env(tmp_path, monkeypatch, "0")
    monkeypatch.setattr(run_aee, "_jsonschema_importable", lambda: False)
    root = _compose_project(tmp_path)
    code = run_aee.main(["--project-root", str(root), "compose", "--phase", "plan"])
    err = capsys.readouterr().err
    assert code == 2
    assert "compose needs jsonschema>=4.0" in err
    assert "disabled by SPECKIT_AEE_AUTO_INSTALL" in err


@pytest.mark.parametrize(("command", "provisions"), [("compose", True), ("graph", False)])
def test_missing_jsonschema_provisions_runtime_only_for_compose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, provisions: bool
) -> None:
    _compose_env(tmp_path, monkeypatch, "1")
    monkeypatch.setattr(run_aee, "_jsonschema_importable", lambda: False)
    root = _compose_project(tmp_path)
    (root / "claims.json").write_text('{"claims": []}', encoding="utf-8")
    reruns: list[list[str]] = []
    monkeypatch.setattr(run_aee, "_ensure_runtime", lambda timeout: Path("runtime-python"))
    monkeypatch.setattr(
        run_aee, "_rerun_in_runtime",
        lambda python, argv: reruns.append(argv) or 0,
    )
    argv = (
        ["--project-root", str(root), "compose", "--phase", "plan"]
        if command == "compose"
        else ["--project-root", str(root), "graph", "--input", "claims.json"]
    )
    run_aee.main(argv)
    assert reruns == ([argv] if provisions else [])

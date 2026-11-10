"""Tests for the AEE token-economy core integration.

Covers the manifest declarations (optional tools, new commands, new script)
and the policy guarantees of scripts/python/aee_token_economy.py:
graceful degradation, measured-only savings, cite-original-lines evidence,
and fail-closed secret refusal. Deterministic; no network, no real tools.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
MOD = ROOT / "scripts" / "python" / "aee_token_economy.py"
SPEC = importlib.util.spec_from_file_location("aee_token_economy", MOD)
assert SPEC and SPEC.loader
aee_token_economy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aee_token_economy)


@pytest.fixture(scope="module")
def manifest() -> dict:
    with (ROOT / "extension.yml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _tools(manifest: dict) -> dict:
    return {tool["name"]: tool for tool in manifest["requires"]["tools"]}


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def test_optional_token_economy_tools_declared(manifest: dict) -> None:
    tools = _tools(manifest)
    for name in ("rtk", "headroom", "token-router", "ollama"):
        assert name in tools, f"optional tool {name!r} missing from requires.tools"
        assert tools[name]["required"] is False, f"{name} must be optional"


def test_required_tools_unchanged(manifest: dict) -> None:
    tools = _tools(manifest)
    assert tools["python"]["required"] is True
    assert tools["aee"]["required"] is True
    # Spec 005 supersedes the RT3-04-era declaration: the evaluator
    # extension is a required companion — AEE's results are composed
    # through the common evaluator so every evaluator's output is
    # handled consistently, and the runner's assess enforces it.
    assert tools["spec-kit-evaluator"]["required"] is True


def test_new_commands_declared_with_short_descriptions(manifest: dict) -> None:
    commands = {
        command["name"]: command for command in manifest["provides"]["commands"]
    }
    for name in ("speckit.aee.route-evidence", "speckit.aee.report-savings"):
        assert name in commands, f"command {name!r} missing from provides.commands"
        assert (ROOT / commands[name]["file"]).is_file()
        assert 0 < len(commands[name]["description"]) < 100


def test_token_economy_script_declared(manifest: dict) -> None:
    scripts = {
        script["name"]: script for script in manifest["provides"]["scripts"]
    }
    assert "aee-token-economy" in scripts
    assert (ROOT / scripts["aee-token-economy"]["file"]).is_file()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        aee_token_economy, "_find_token_router", lambda *_args: None
    )


def _write_lines(path: Path, count: int, marker: str = "filler") -> None:
    path.write_text(
        "\n".join(f"{marker} line {i}" for i in range(1, count + 1)),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_degrades_gracefully_without_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    _no_tools(monkeypatch)
    assert aee_token_economy.main(["--project-root", str(tmp_path), "status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    for name in ("rtk", "headroom", "token-router", "ollama"):
        assert payload[name]["available"] is False


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------


def test_route_small_file_reads_directly(tmp_path: Path, capsys) -> None:
    target = tmp_path / "small.log"
    _write_lines(target, 10)
    code = aee_token_economy.main(
        ["--project-root", str(tmp_path), "route", "--file", "small.log"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "read_directly"


def test_route_refuses_secret_filenames(tmp_path: Path, capsys) -> None:
    for name in (".env", "id_rsa", "service-account.json", "app.pem"):
        target = tmp_path / name
        target.write_text("SECRET=hunter2", encoding="utf-8")
        code = aee_token_economy.main(
            ["--project-root", str(tmp_path), "route", "--file", name]
        )
        assert code == 2, f"secret-looking file {name!r} must be refused"


@pytest.mark.parametrize(
    "rel_path",
    [
        # RT3-06 bypasses: the secret signal lives in a directory
        # component or in an underscore spelling, never the basename
        # fragment list the old guard checked.
        "credentials/notes.log",
        "secrets/notes.log",
        "private_key.log",
        "service_account.json",
        ".env.local",
        "certs/server.pem",
        "keys/backup.p12",
    ],
)
def test_route_refuses_secret_path_components(
    tmp_path: Path, capsys, rel_path: str
) -> None:
    target = tmp_path / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"filler line {i}" for i in range(1, 401)]
    lines[199] = "TOPSECRET-VALUE-12345 needle"
    target.write_text("\n".join(lines), encoding="utf-8")
    code = aee_token_economy.main(
        [
            "--project-root",
            str(tmp_path),
            "route",
            "--file",
            rel_path,
            "--query",
            "TOPSECRET",
        ]
    )
    assert code == 2, f"secret-looking path {rel_path!r} must be refused"
    captured = capsys.readouterr()
    assert "TOPSECRET-VALUE-12345" not in captured.out
    assert "refused" in captured.err


def test_route_refuses_paths_outside_root(tmp_path: Path, capsys) -> None:
    code = aee_token_economy.main(
        ["--project-root", str(tmp_path), "route", "--file", "../escape.log"]
    )
    assert code == 2


def test_route_deterministic_fallback_returns_verbatim_slices(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    _no_tools(monkeypatch)
    target = tmp_path / "big.log"
    lines = [f"filler line {i}" for i in range(1, 401)]
    lines[199] = "ERROR database timeout on connection 7"
    target.write_text("\n".join(lines), encoding="utf-8")
    code = aee_token_economy.main(
        [
            "--project-root",
            str(tmp_path),
            "route",
            "--file",
            "big.log",
            "--mode",
            "error_log",
            "--query",
            "database timeout",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "routed"
    assert payload["backend"] == "deterministic"
    assert payload["ranges"], "expected at least one matching range"
    for start, end in payload["ranges"]:
        assert 1 <= start <= end <= 400
    assert any(
        "ERROR database timeout on connection 7" in s["text"]
        for s in payload["slices"]
    )
    # Slices are verbatim: every slice line exists in the original file.
    for slc in payload["slices"]:
        for offset, text in enumerate(slc["text"].splitlines()):
            assert lines[slc["start"] - 1 + offset] == text
    assert "not evidence" in payload["policy"]


def test_route_no_matches_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    _no_tools(monkeypatch)
    target = tmp_path / "big.log"
    _write_lines(target, 400)
    code = aee_token_economy.main(
        [
            "--project-root",
            str(tmp_path),
            "route",
            "--file",
            "big.log",
            "--query",
            "no such phrase anywhere",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "no_matches"
    assert "ranges" not in payload


def test_route_token_router_backend_ranges_are_clamped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    fake_router = tmp_path / "router.py"
    fake_router.write_text(
        "import json\nprint(json.dumps({'ranges': [[0, 99999], ['a', 'b'], [5, 8]]}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(aee_token_economy, "_find_token_router", lambda *_args: fake_router)
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/ollama" if name == "ollama" else None
    )
    target = tmp_path / "big.log"
    _write_lines(target, 400)
    code = aee_token_economy.main(
        [
            "--project-root",
            str(tmp_path),
            "route",
            "--file",
            "big.log",
            "--query",
            "anything",
            "--max-output-lines",
            "10",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["backend"] == "token-router"
    # Malformed [0, 99999] clamps to file bounds and the 10-line budget;
    # the non-integer pair is dropped, so only one range survives.
    assert payload["ranges"] == [[1, 10]]
    assert payload["slices"][0]["text"].splitlines() == [
        f"filler line {i}" for i in range(1, 11)
    ]


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_report_without_tools_reports_na_not_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    _no_tools(monkeypatch)
    code = aee_token_economy.main(["--project-root", str(tmp_path), "report"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rtk"]["saved_tokens"] == "n/a"
    assert payload["headroom"]["saved_tokens"] == "n/a"
    assert payload["combined_saved_tokens"] == "n/a"
    assert "Measured only" in payload["policy"]


def test_report_measures_rtk_and_keeps_channels_separate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/rtk" if name == "rtk" else None
    )
    monkeypatch.setattr(aee_token_economy, "_find_token_router", lambda *_args: None)

    def fake_run(*args, **kwargs):
        class Completed:
            stdout = json.dumps({"stats": {"saved_tokens": 1234}})

        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    code = aee_token_economy.main(["--project-root", str(tmp_path), "report"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rtk"]["saved_tokens"] == 1234
    assert payload["rtk"]["source"] == "rtk gain --format json"
    assert payload["headroom"]["saved_tokens"] == "n/a"
    # Only one channel measured: no combined total is fabricated.
    assert payload["combined_saved_tokens"] == "n/a"


def test_report_unparseable_rtk_output_is_na(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/rtk" if name == "rtk" else None
    )
    monkeypatch.setattr(aee_token_economy, "_find_token_router", lambda *_args: None)

    def fake_run(*args, **kwargs):
        class Completed:
            stdout = "not json at all"

        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    code = aee_token_economy.main(["--project-root", str(tmp_path), "report"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rtk"]["saved_tokens"] == "n/a"
    assert "reason" in payload["rtk"]


def test_walk_number_finds_nested_savings() -> None:
    payload = {"a": [{"b": {"total_saved": 42}}]}
    assert aee_token_economy._walk_number(payload, ("saved_tokens", "total_saved")) == 42
    assert (
        aee_token_economy._walk_number({"x": True}, ("saved_tokens",)) is None
    )


# ---------------------------------------------------------------------------
# shell
# ---------------------------------------------------------------------------


def test_shell_runs_command_and_records_telemetry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _no_tools(monkeypatch)
    code = aee_token_economy.main(
        [
            "--project-root",
            str(tmp_path),
            "shell",
            "--",
            sys.executable,
            "-c",
            "print('hi')",
        ]
    )
    assert code == 0
    telemetry = (
        tmp_path / ".specify" / "extensions" / "aee" / "telemetry" / "shell-calls.jsonl"
    )
    assert telemetry.is_file()
    record = json.loads(telemetry.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["exit_code"] == 0
    assert record["rtk_available"] is False
    assert record["stdout_bytes"] > 0


def test_shell_requires_a_command(tmp_path: Path, capsys) -> None:
    code = aee_token_economy.main(["--project-root", str(tmp_path), "shell"])
    assert code == 2


def test_token_router_discovery_anchors_on_project_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Discovery must follow --project-root, not the caller's cwd."""
    monkeypatch.delenv("TOKEN_ROUTER_HOME", raising=False)
    router = tmp_path / "token-router" / "scripts" / "router.py"
    router.parent.mkdir(parents=True)
    router.write_text("# fake router\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert aee_token_economy._find_token_router(tmp_path) == router
    assert aee_token_economy.tool_status(tmp_path)["token-router"]["available"] is True


def test_shell_refuses_bad_telemetry_before_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """An escaping --telemetry path refuses up front; the wrapped
    command must not run at all."""
    _no_tools(monkeypatch)
    marker = tmp_path / "ran.txt"
    code = aee_token_economy.main(
        [
            "--project-root",
            str(tmp_path),
            "shell",
            "--telemetry",
            "../escape.jsonl",
            "--",
            sys.executable,
            "-c",
            f"open({str(marker)!r}, 'w').write('x')",
        ]
    )
    assert code == 2
    assert not marker.exists()
    assert "refused" in capsys.readouterr().err

"""Real-engine regression coverage for all six adapter operations."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet
from specify_cli.extensions import ExtensionManifest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/python/run_aee.py"


def test_manifest_uses_supported_dependency_metadata() -> None:
    manifest = ExtensionManifest(ROOT / "extension.yml")
    assert manifest.id == "aee"
    requires = manifest.data["requires"]
    assert "commands" not in requires
    tools = {tool["name"]: tool for tool in requires["tools"]}
    required = {name for name, tool in tools.items() if tool["required"]}
    optional = {name for name, tool in tools.items() if not tool["required"]}
    # Spec 005: the evaluator extension is a required companion.
    assert required == {"python", "aee", "spec-kit-evaluator"}
    assert optional == {
        "rtk",
        "headroom",
        "token-router",
        "ollama",
    }
    assert "1.6.0" in SpecifierSet(tools["aee"]["version"])
    assert "1.5.0" not in SpecifierSet(tools["aee"]["version"])  # Spec 006 floor
    assert "1.4.0" not in SpecifierSet(tools["aee"]["version"])  # Spec 004 P5-E1 floor
    assert "1.0.2" not in SpecifierSet(tools["aee"]["version"])
    for group in ("commands", "templates", "scripts", "config"):
        for item in manifest.data["provides"][group]:
            assert (ROOT / item.get("file", item.get("template", ""))).is_file()
    names = {command["name"] for command in manifest.commands}
    assert len(names) == 11
    assert "speckit.aee.draft-claims" in names
    assert "speckit.aee.message-security" in names
    assert len(manifest.hooks) == 5
    assert all(hook["command"] in names for hook in manifest.hooks.values())


@pytest.fixture
def project(tmp_path: Path) -> Path:
    assert shutil.which("aee"), "Activate the environment containing requirements/dev.txt"
    shutil.copyfile(ROOT / "templates/aee-claims.json", tmp_path / "claims.json")
    # Spec 005: assess requires the evaluator companion installed.
    evaluator = tmp_path / ".specify" / "extensions" / "evaluator"
    evaluator.mkdir(parents=True)
    (evaluator / "extension.yml").write_text(
        "extension:\n  id: evaluator\n", encoding="utf-8"
    )
    return tmp_path


def run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--project-root", str(project), *args],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )


def test_unsupported_template_assessment_gate_and_ledger(project: Path) -> None:
    original = (project / "claims.json").read_bytes()
    result = run(project, "assess", "--input", "claims.json", "--phase", "after_specify")
    assert result.returncode == 1, result.stderr
    paths = json.loads(result.stdout)
    assessment = json.loads((project / paths["assessment"]).read_text())
    evaluator = json.loads((project / paths["evaluator_result"]).read_text())
    assert assessment["outcome"] in {"iterate", "clarify", "gather_evidence"}
    assert evaluator["outcome"] == assessment["outcome"]
    assert evaluator["phase"] == "after_specify"
    assert (project / "claims.json").read_bytes() == original
    gate = run(project, "gate", "--input", paths["assessment"])
    assert gate.returncode == 1, gate.stderr
    verified = run(project, "verify")
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["valid"] is True
    ledger = project / paths["ledger"]
    entry = json.loads(ledger.read_text().splitlines()[0])
    entry["actor"] = "tampered"
    ledger.write_text(json.dumps(entry) + "\n")
    invalid = run(project, "verify")
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)["valid"] is False


def test_graph_and_challenge_write_real_results(project: Path) -> None:
    graph = run(project, "graph", "--input", "claims.json")
    assert graph.returncode == 0, graph.stderr
    graph_path = project / json.loads(graph.stdout)["graph"]
    assert "REQ-LATENCY-001" in graph_path.read_text()
    claims = json.loads((project / "claims.json").read_text())
    # Deliberately assert support without evidence/boundaries to exercise challenges.
    claims["claims"][0]["status"] = "supported"
    claims["claims"][0]["boundary"] = []
    (project / "claims.json").write_text(json.dumps(claims))
    challenge = run(project, "challenge", "--input", "claims.json")
    assert challenge.returncode == 1, challenge.stderr
    value = json.loads((project / json.loads(challenge.stdout)["challenge"]).read_text())
    assert value["mode"] == "challenge"
    assert value["failures"]
    assert value["recoveries"]


def test_gaps_stdout_output_and_evidence(project: Path) -> None:
    (project / "matrix.md").write_text(
        "| Test ID | Requirements | Layer | Gate |\n"
        "| T-API-001 | Returns 200 | integration | gate-1 |\n"
    )
    (project / "evidence").mkdir()
    args = ("gaps", "--matrix", "matrix.md", "--evidence", "evidence")
    printed = run(project, *args)
    assert printed.returncode == 0, printed.stderr
    assert "Open: 1 | Closed: 0" in printed.stdout
    assert not (project / "GAPS.md").exists()
    output = run(project, *args, "--output", "GAPS.md")
    assert output.returncode == 0, output.stderr
    assert "**Open:** 1" in (project / "GAPS.md").read_text()
    (project / "evidence/result.json").write_text(json.dumps({
        "results": [{"test_id": "T-API-001", "status": "pass"}]
    }))
    closed = run(project, *args, "--output", "GAPS.md")
    assert closed.returncode == 0, closed.stderr
    assert "**Open:** 0 | **Closed:** 1" in (project / "GAPS.md").read_text()


def test_no_ledger_flag(project: Path) -> None:
    result = run(project, "assess", "--input", "claims.json", "--no-ledger")
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)["ledger"] is None
    assert not (project / ".specify/extensions/aee/ledger").exists()


def test_policy_assessment_carries_verdicts(project: Path) -> None:
    result = run(project, "assess", "--input", "claims.json",
                 "--phase", "after_specify", "--policy", "--no-ledger")
    assert result.returncode in (0, 1), result.stderr
    paths = json.loads(result.stdout)
    assessment = json.loads((project / paths["assessment"]).read_text())
    assert assessment["verdicts"], "policy assessment must carry verdicts"
    for verdict in assessment["verdicts"].values():
        assert verdict["verdict"] in {"accept", "challenge", "abstain", "unjudged"}  # Spec 004 R1.4: stage-created claims are unjudged
    plain = run(project, "assess", "--input", "claims.json",
                "--phase", "after_specify", "--no-ledger")
    plain_assessment = json.loads(
        (project / json.loads(plain.stdout)["assessment"]).read_text())
    assert plain_assessment["verdicts"] == {}


def test_review_queue_between_two_assessments(project: Path) -> None:
    first = run(project, "assess", "--input", "claims.json", "--no-ledger")
    # Assessment filenames are timestamped to the second; preserve the
    # first under its own name before the second run can overwrite it.
    first_rel = json.loads(first.stdout)["assessment"]
    (project / "first-assessment.json").write_bytes(
        (project / first_rel).read_bytes())
    first_path = "first-assessment.json"
    claims = json.loads((project / "claims.json").read_text())
    claims["claims"][0]["status"] = "supported"
    claims["claims"][0]["evidence"] = [
        {"ref": f"run-{index}", "source_id": f"S{index}",
         "kind": "observed", "source_quality": "test",
         "direction": "supports"}
        for index in (1, 2)
    ]
    (project / "claims.json").write_text(json.dumps(claims))
    second = run(project, "assess", "--input", "claims.json", "--no-ledger")
    second_path = json.loads(second.stdout)["assessment"]
    review = run(project, "review", "--previous", first_path,
                 "--current", second_path)
    assert review.returncode == 0, review.stderr
    queue = json.loads(review.stdout)
    assert queue["materiality"] == 0.05
    moved = {item["claim_id"] for item in queue["items"]}
    assert claims["claims"][0]["id"] in moved

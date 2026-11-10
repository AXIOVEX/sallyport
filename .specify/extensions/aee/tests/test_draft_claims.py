"""Spec 002 Phase 4: draft-claims (T202/T203/T204/T604).

Hermetic tests stub both halves of the engine surface the runner uses:
a stub `aee` executable for the startup version check, and a stub `aee`
package on PYTHONPATH implementing aee.extract's drafting helper with
the engine's invariants (drafts born draft, asserted/model evidence,
TODO falsifier where no testable cue, depends_on proposals recorded in
metadata). The live tests then run the same flow against the REAL
engine from the applied-epistemic-engineering checkout.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
RUNNER = ROOT / "scripts" / "python" / "run_aee.py"
SPEC = importlib.util.spec_from_file_location("run_aee", RUNNER)
assert SPEC and SPEC.loader
run_aee = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_aee)

ENGINE_SRC = (
    Path(__file__).resolve().parents[3]
    / "electrohire"
    / "applied-epistemic-engineering"
    / "src"
)

_STUB_AEE_EXE = """\
#!/usr/bin/env python3
import sys

if sys.argv[1:] == ["--version"]:
    print("aee 1.6.0")
    sys.exit(0)
sys.exit(0)
"""

_STUB_EXTRACT = """\
import json
import re

TODO_FALSIFIER = "TODO: no falsification test could be proposed (stub)"


def draft_claims_from_spec(spec_text, *, source_name="spec.md"):
    claims = []
    ids = {}
    rows = []
    for line in spec_text.splitlines():
        match = re.match(r"- (FR-\\d+): (.+)$", line.strip())
        if match:
            rows.append((match.group(1), match.group(2)))
    for ref, text in rows:
        ids[ref] = f"CLM-{ref}"
    for ref, text in rows:
        deps = [ids[r] for r in re.findall(r"depends on (FR-\\d+)", text) if r in ids]
        falsifier = (
            f"Proposed falsification test: {text} does not hold."
            if "shall" in text
            else TODO_FALSIFIER
        )
        metadata = {"drafted": True, "requirement_ref": ref}
        if deps:
            metadata["depends_on_proposed_from"] = re.findall(
                r"depends on (FR-\\d+)", text
            )
        claims.append(
            {
                "id": ids[ref],
                "text": text,
                "kind": "requirement",
                "status": "draft",
                "depends_on": deps,
                "falsification_tests": [falsifier],
                "source_ref": f"{source_name}#{ref}",
                "evidence": [
                    {
                        "ref": f"{source_name}#{ref}",
                        "kind": "asserted",
                        "direction": "supports",
                        "source_quality": "model",
                        "source_id": ref,
                    }
                ],
                "metadata": metadata,
            }
        )
    return claims


def dump_claims(claims):
    return json.dumps({"schema_version": "1.0", "claims": claims}, indent=2)
"""

_FIXTURE_SPEC = """\
# Fixture Spec

- FR-1: The widget shall return ok within 1 s
- FR-2: The widget shall write an audit record, depends on FR-1
- FR-3: Peace in our time
"""


def _stub_env(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    exe = bin_dir / "aee"
    exe.write_text(_STUB_AEE_EXE, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    package = tmp_path / "stub-pkg" / "aee"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text('__version__ = "1.6.0"\n', encoding="utf-8")
    (package / "extract.py").write_text(_STUB_EXTRACT, encoding="utf-8")
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(tmp_path / "stub-pkg")
    return env


requires_posix_stub = pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub aee is executed via a POSIX shebang script",
)


@pytest.fixture
def stub_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "specs" / "001-widget").mkdir(parents=True)
    (root / "specs" / "001-widget" / "spec.md").write_text(
        _FIXTURE_SPEC, encoding="utf-8"
    )
    return root


def _run(root: Path, env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--project-root", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


@requires_posix_stub
def test_draft_claims_from_feature_directory(
    tmp_path: Path, stub_project: Path
) -> None:
    completed = _run(
        stub_project,
        _stub_env(tmp_path),
        "draft-claims",
        "--spec",
        "specs/001-widget",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["aee_exit_code"] == 0
    assert payload["draft_claims"] == "specs/001-widget/aee-claims.json"
    assert payload["claim_count"] == 3
    assert payload["todo_falsifier_count"] == 1
    bundle = json.loads((stub_project / payload["draft_claims"]).read_text())
    claims = bundle["claims"]
    # T204 behavioral: every draft is born draft, evidence capped at
    # asserted/model, source_ref points at its requirement.
    for claim in claims:
        assert claim["status"] == "draft"
        assert claim["source_ref"].startswith("specs/001-widget/spec.md#FR-")
        for item in claim["evidence"]:
            assert item["kind"] == "asserted"
            assert item["source_quality"] == "model"
    # T604: the cross-reference proposal is surfaced in the summary and
    # carried in the bundle as a proposal (draft depends_on + provenance).
    assert payload["depends_on_proposals"] == [
        {"claim_id": "CLM-FR-2", "depends_on": ["CLM-FR-1"], "proposed_from": ["FR-1"]}
    ]
    by_id = {claim["id"]: claim for claim in claims}
    assert by_id["CLM-FR-2"]["depends_on"] == ["CLM-FR-1"]
    assert by_id["CLM-FR-2"]["metadata"]["depends_on_proposed_from"] == ["FR-1"]
    # The unfinished draft's TODO falsifier passes through untouched.
    todo = by_id["CLM-FR-3"]["falsification_tests"][0]
    assert todo.startswith("TODO:")


@requires_posix_stub
def test_draft_claims_explicit_output_and_missing_spec(
    tmp_path: Path, stub_project: Path
) -> None:
    env = _stub_env(tmp_path)
    completed = _run(
        stub_project,
        env,
        "draft-claims",
        "--spec",
        "specs/001-widget/spec.md",
        "--output",
        "drafts/out.json",
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["draft_claims"] == "drafts/out.json"
    missing = _run(stub_project, env, "draft-claims", "--spec", "nope/spec.md")
    assert missing.returncode == 2
    assert "Traceback" not in missing.stderr
    assert "refused:" in missing.stderr


@requires_posix_stub
def test_draft_claims_no_requirements_is_clean_refusal(
    tmp_path: Path, stub_project: Path
) -> None:
    (stub_project / "empty.md").write_text("# Nothing here\n", encoding="utf-8")
    completed = _run(
        stub_project, _stub_env(tmp_path), "draft-claims", "--spec", "empty.md"
    )
    assert completed.returncode == 2
    assert "refused:" in completed.stderr
    assert "no requirements found" in completed.stderr


def test_runner_draft_path_has_no_ratification_code() -> None:
    """T204 source guard: the drafting path must not set any status
    above draft, upgrade evidence, or rewrite falsifiers/depends_on."""
    source = RUNNER.read_text(encoding="utf-8")
    section = source[
        source.index("def _draft_claims") : source.index("def _stamp_and_ledger")
    ]
    for forbidden in (
        '"supported"',
        "'supported'",
        "partially_supported",
        '"observed"',
        '"primary"',
        '"test"',
    ):
        assert forbidden not in section, f"draft path may not assign {forbidden}"
    assert "status" not in section.replace("draft's status", ""), (
        "draft path must not touch claim status at all"
    )


def test_draft_claims_subparser_is_registered() -> None:
    args = run_aee.parser().parse_args(["draft-claims", "--spec", "specs/001-x"])
    assert args.command == "draft-claims"
    assert args.spec == Path("specs/001-x")
    assert args.output is None


# ---------------------------------------------------------------------------
# Live engine end-to-end: real helper from the engine checkout, real
# strict validation (a completed draft validates; a TODO draft is
# rejected by the strict loader, T204/FR-1).
# ---------------------------------------------------------------------------

requires_live_engine = pytest.mark.skipif(
    not (ENGINE_SRC / "aee" / "extract.py").is_file(),
    reason="engine checkout not present beside this repo",
)


def _live_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ENGINE_SRC)
    return env


@pytest.fixture
def live_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    # Spec 005: assess requires the evaluator companion installed.
    evaluator = root / ".specify" / "extensions" / "evaluator"
    evaluator.mkdir(parents=True)
    (evaluator / "extension.yml").write_text(
        "extension:\n  id: evaluator\n", encoding="utf-8"
    )
    (root / "specs" / "001-widget").mkdir(parents=True)
    (root / "specs" / "001-widget" / "spec.md").write_text(
        "# Widget Spec\n\n"
        "- FR-1: The widget shall return the stored value when it is read\n"
        "- FR-2: The widget shall record an audit entry when it changes, "
        "depends on FR-1\n"
        "- FR-3: Excellence in all things\n",
        encoding="utf-8",
    )
    return root


@requires_live_engine
@requires_posix_stub
def test_live_assess_stamps_and_verifies_with_new_engine(
    tmp_path: Path, live_project: Path
) -> None:
    """T403/T802 against the engine checkout: this CLI ledgers by
    default and advertises --no-ledger, so the runner must probe it,
    stamp extension_version BEFORE its own append, and report a valid
    ledger_verification whose hash covers the stamped file."""
    (live_project / "claims.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "claims": [
                    {
                        "id": "REQ-001",
                        "text": "The widget returns the stored value",
                        "kind": "requirement",
                        "status": "draft",
                        "boundary": ["unit scope"],
                        "falsification_tests": ["Read returns a different value"],
                        "source_ref": "spec.md#FR-1",
                        "uncertainty": "insufficient_evidence",
                        "evidence": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    bin_dir = tmp_path / "engine-bin"
    bin_dir.mkdir()
    shim = bin_dir / "aee"
    shim.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -m aee "$@"\n', encoding="utf-8"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    env = _live_env()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    completed = _run(
        live_project,
        env,
        "assess",
        "--input",
        "claims.json",
        "--phase",
        "after_specify",
    )
    assert completed.returncode in (0, 1), completed.stderr
    payload = json.loads(completed.stdout)
    assessment = json.loads((live_project / payload["assessment"]).read_text())
    assert assessment["metadata"]["engine_version"] == "1.6.0"  # the realigned engine (Spec 004 P5-E1)
    assert assessment["metadata"]["extension_version"] == "1.7.0"
    assert assessment["metadata"]["ledger"]["mode"] == "custom"
    verdict = payload["ledger_verification"]
    assert verdict["valid"] is True and verdict["entries"] == 1
    entry = json.loads((live_project / payload["ledger"]).read_text().splitlines()[0])
    # The recorded digest is the engine's own content digest of the
    # stamped assessment (Spec 003 FR-5.8: the wall-clock created_at
    # stamp is excluded) — the same function the engine gate checks
    # the file against, so writer and checker cannot drift. Computed
    # in a subprocess so the digest comes from the engine checkout
    # regardless of what this test process has imported.
    digest_probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys\n"
                "from aee.ledger import assessment_content_digest\n"
                "print(assessment_content_digest(json.loads(open(sys.argv[1]).read())))"
            ),
            str(live_project / payload["assessment"]),
        ],
        env=_live_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert digest_probe.returncode == 0, digest_probe.stderr
    assert entry["payload"]["assessment_sha256"] == digest_probe.stdout.strip()


@requires_live_engine
def test_live_draft_then_strict_validate(live_project: Path) -> None:
    env = _live_env()
    completed = _run(live_project, env, "draft-claims", "--spec", "specs/001-widget")
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["claim_count"] == 3
    assert payload["todo_falsifier_count"] == 1
    assert len(payload["depends_on_proposals"]) == 1
    proposal = payload["depends_on_proposals"][0]
    assert proposal["proposed_from"] == ["FR-1"]
    assert proposal["depends_on"]
    bundle_path = live_project / payload["draft_claims"]
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    for claim in bundle["claims"]:
        assert claim["status"] == "draft"
        assert claim["evidence"][0]["kind"] == "asserted"
        assert claim["evidence"][0]["source_quality"] == "model"
    aee = Path(sys.executable).parent / "aee"
    if not aee.is_file():
        pytest.skip("aee executable not beside the test interpreter")
    validate = subprocess.run(
        [str(aee), "validate", str(bundle_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    # The TODO draft must be rejected by strict loading (FR-2.3/FR-1).
    assert validate.returncode == 2, validate.stdout + validate.stderr
    # A human completes the TODO falsifier; now the bundle validates.
    for claim in bundle["claims"]:
        claim["falsification_tests"] = [
            test
            if not test.startswith("TODO")
            else "Run the widget and observe the requirement not holding."
            for test in claim["falsification_tests"]
        ]
    bundle_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
    validate = subprocess.run(
        [str(aee), "validate", str(bundle_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert validate.returncode == 0, validate.stdout + validate.stderr

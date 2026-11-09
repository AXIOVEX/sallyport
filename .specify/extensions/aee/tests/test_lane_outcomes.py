"""Spec 006 extension-half tests: the lane pair in assess output
(LO-01), the gate's lane-1 passthrough (LO-02), and the engine
1.6.0 floor refusing a 1.5.x engine (LO-03).

Hermetic, in the test_p5_vocabulary stub style: a stub `aee` on
PATH stands in for the engine. The stub's assessment is the case
that motivated the spec — lane 1 passes on its own facts while the
headline still reads `iterate` because lane 2 is open (a draft
claim below stage `explained`). The stub's gate exits with a
caller-chosen code when `--require-lane1-pass` reaches it, and logs
every argv it receives, so the passthrough is checked on what the
engine side actually saw, not on the runner's say-so.
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

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "scripts" / "python" / "run_aee.py"
SPEC = importlib.util.spec_from_file_location("run_aee_lane", RUNNER)
assert SPEC and SPEC.loader
run_aee = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_aee)

_STUB_AEE = '''\
#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
log = os.environ.get("AEE_STUB_ARGV_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(args) + "\\n")
if args == ["--version"]:
    print(os.environ.get("AEE_STUB_VERSION_TEXT", "aee 1.6.0"))
    sys.exit(0)
command = args[0] if args else ""


def flag(name):
    return args[args.index(name) + 1] if name in args else None


if command == "assess":
    shape = os.environ.get("AEE_STUB_SHAPE", "lane1-pass")
    if shape == "lane1-pass":
        payload = {
            "project": "stub-project",
            "phase": flag("--phase") or "after_plan",
            "outcome": "iterate",
            "methodology_version": "2.1",
            "claims": [{
                "id": "A",
                "stage_events": [
                    {"kind": "checked", "author": "aee",
                     "date": "2026-10-20T00:00:00Z",
                     "basis": "Frozen requirement tests passed and assessed"},
                    {"kind": "accepted", "author": "casey-example",
                     "date": "2026-10-20T01:00:00Z",
                     "basis": "Recorded acceptance of the explained evidence"},
                ],
            }],
            "reported_statuses": {"A": "draft"},
            "stage_caps": [],
            "lanes": {
                "lane_1": {
                    "label": "machine verification",
                    "outcome": "pass",
                    "threshold": 0.7,
                    "claims_at_or_above_threshold": 1,
                    "claims_below_threshold": 0,
                },
                "lane_2": {
                    "label": "human acceptance",
                    "stage_counts": {"created": 1, "checked": 0,
                                     "explained": 0, "accepted": 0,
                                     "digested": 0},
                    "claims_below_explained": 1,
                    "exposition_covered_claims": 0,
                    "exposition_missing_claims": 1,
                    "verified_unexplained_claims": 0,
                },
            },
            "not_understood": ["A: what remains not understood"],
            "metadata": {"engine_version": "1.6.0"},
        }
    elif shape == "stored-basis":
        # The shape the real engine 1.6.0 writes: each claim carries
        # its stage events AND the recorded acts stored on the claim
        # (metadata.recorded_acts), every act with its explicit
        # basis type. Here the acceptance stands on a test verdict
        # and the earlier advances stand on human acts — while the
        # stage events alone (kinds and the tool author) point the
        # other way, which is the Phase D inversion.
        payload = {
            "project": "stub-project",
            "phase": flag("--phase") or "after_plan",
            "outcome": "iterate",
            "methodology_version": "2.1",
            "claims": [{
                "id": "PC-1",
                "stage_events": [
                    {"kind": "checked", "author": "aee",
                     "date": "2026-10-20T00:00:00Z", "basis": "Assessed"},
                    {"kind": "explained", "author": "aee",
                     "date": "2026-10-20T01:00:00Z",
                     "basis": "Exposition on record"},
                    {"kind": "accepted", "author": "aee",
                     "date": "2026-10-20T02:00:00Z",
                     "basis": "Acceptance run passed"},
                ],
                "metadata": {
                    "recorded_acts": [
                        {"kind": "stage", "to_stage": "checked",
                         "from_stage": "created", "author": "aee",
                         "date": "2026-10-20T00:00:00Z",
                         "basis": "Assessed", "basis_type": "human-act"},
                        {"kind": "stage", "to_stage": "explained",
                         "from_stage": "checked", "author": "aee",
                         "date": "2026-10-20T01:00:00Z",
                         "basis": "Exposition on record",
                         "basis_type": "human-act"},
                        {"kind": "stage", "to_stage": "accepted",
                         "from_stage": "explained", "author": "aee",
                         "date": "2026-10-20T02:00:00Z",
                         "basis": "Acceptance run passed",
                         "basis_type": "test-verdict",
                         "test_id": "AT-PC-1",
                         "test_hash": "ab" * 32,
                         "test_result": "pass"},
                    ],
                },
            }],
            "reported_statuses": {"PC-1": "supported"},
            "stage_caps": [],
            "lanes": {
                "lane_1": {
                    "label": "machine verification",
                    "outcome": "pass",
                    "threshold": 0.7,
                    "claims_at_or_above_threshold": 1,
                    "claims_below_threshold": 0,
                },
                "lane_2": {
                    "label": "human acceptance",
                    "stage_counts": {"created": 0, "checked": 0,
                                     "explained": 0, "accepted": 1,
                                     "digested": 0},
                    "claims_below_explained": 0,
                    "exposition_covered_claims": 1,
                    "exposition_missing_claims": 0,
                    "verified_unexplained_claims": 0,
                },
            },
            "not_understood": [],
            "metadata": {"engine_version": "1.6.0"},
        }
    else:
        payload = {
            "project": "stub-project",
            "phase": flag("--phase") or "after_plan",
            "outcome": "iterate",
            "claims": [],
            "metadata": {"engine_version": "1.6.0"},
        }
    Path(flag("--output")).write_text(json.dumps(payload))
    Path(flag("--evaluator-output")).write_text(json.dumps({"outcome": "iterate"}))
    sys.exit(1)
if command == "gate":
    if "--require-lane1-pass" in args:
        sys.exit(int(os.environ.get("AEE_STUB_LANE1_GATE_EXIT", "0")))
    sys.exit(1)
sys.exit(0)
'''

requires_posix_stub = pytest.mark.skipif(
    sys.platform == "win32",
    reason="stub aee is executed via a POSIX shebang script",
)


def _env(tmp_path: Path, version: str = "1.6.0", **extra: str) -> dict:
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "aee"
    stub.write_text(_STUB_AEE, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["AEE_STUB_VERSION_TEXT"] = f"aee {version}"
    env["AEE_STUB_ARGV_LOG"] = str(tmp_path / "argv.log")
    env.update(extra)
    return env


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "claims.json").write_text('{"claims": []}', encoding="utf-8")
    (root / "assessment.json").write_text('{"outcome": "iterate"}', encoding="utf-8")
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


def _gate_invocations(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "argv.log"
    if not log.is_file():
        return []
    return [
        argv
        for argv in (json.loads(line) for line in log.read_text().splitlines())
        if argv and argv[0] == "gate"
    ]


@requires_posix_stub
def test_assess_shows_pair_when_lane1_passes_and_headline_iterates(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    completed = _run(root, _env(tmp_path), "assess", "--input", "claims.json")
    assert completed.returncode == 1, completed.stderr
    out = json.loads(completed.stdout)
    pair = out["lane_pair"]
    # The pair is the assessment's own record: headline unchanged,
    # lane 1 copied, lane 2 stated plainly with its recorded facts.
    assert pair["headline_outcome"] == "iterate"
    assert pair["lane_1_outcome"] == "pass"
    assert pair["lane_2_state"] == "open"
    assert pair["lane_2_reasons"] == [
        "1 draft claim(s)",
        "1 claim(s) below stage 'explained'",
    ]
    assert pair["display"] == (
        "headline: iterate — lane 1: pass, lane 2: open "
        "(1 draft claim(s); 1 claim(s) below stage 'explained')"
    )
    # The same pair rides inside verdict_display, value for value.
    assert out["verdict_display"]["lane_pair"] == pair
    # The recorded acts' basis rides alongside the pair (Spec 006
    # amendment): the checking event recorded by the tool is a test
    # verdict; the acceptance recorded by a named person is a human
    # act — acceptance is not presented as a human act by default.
    basis = pair["acceptance_basis"]
    assert basis["test_verdict_count"] == 1
    assert basis["human_act_count"] == 1
    assert basis["display"] == "acceptance basis: 1 test verdict(s), 1 human act(s)"
    assert basis["recorded_acts"] == [
        {
            "claim_id": "A",
            "to_stage": "checked",
            "basis_type": "test verdict",
            "recorded_by": "aee",
            "basis": "Frozen requirement tests passed and assessed",
        },
        {
            "claim_id": "A",
            "to_stage": "accepted",
            "basis_type": "human act",
            "recorded_by": "casey-example",
            "basis": "Recorded acceptance of the explained evidence",
        },
    ]


@requires_posix_stub
def test_assess_without_lanes_yields_null_pair(tmp_path: Path) -> None:
    root = _project(tmp_path)
    completed = _run(
        root, _env(tmp_path, AEE_STUB_SHAPE="legacy"),
        "assess", "--input", "claims.json",
    )
    assert completed.returncode == 1, completed.stderr
    out = json.loads(completed.stdout)
    assert out["lane_pair"] is None
    assert out["verdict_display"] is None


def test_lane_pair_complete_when_lane2_has_no_open_work() -> None:
    pair = run_aee._lane_pair(
        {
            "outcome": "pass",
            "reported_statuses": {"A": "supported"},
            "lanes": {
                "lane_1": {"outcome": "pass"},
                "lane_2": {
                    "stage_counts": {"created": 0, "checked": 0,
                                     "explained": 0, "accepted": 0,
                                     "digested": 1},
                    "claims_below_explained": 0,
                    "exposition_missing_claims": 0,
                    "verified_unexplained_claims": 0,
                },
            },
        }
    )
    assert pair is not None
    assert pair["lane_2_state"] == "complete"
    assert pair["lane_2_reasons"] == []
    assert pair["display"] == "headline: pass — lane 1: pass, lane 2: complete"
    # No stage events recorded anywhere: no basis is invented.
    assert pair["acceptance_basis"] is None


def test_acceptance_basis_classification_follows_the_engine_rule() -> None:
    basis = run_aee._acceptance_basis(
        {
            "claims": [
                {
                    "id": "A",
                    "stage_events": [
                        # Tool-recorded check: a test verdict.
                        {"kind": "checked", "author": "aee", "basis": "tests passed"},
                        # Person-recorded check: a human act on its face.
                        {"kind": "checked", "author": "casey", "basis": "I ran them"},
                        # Explained/accepted/digested are human acts by
                        # the engine's own rule, whoever records them.
                        {"kind": "explained", "author": "aee", "basis": "exposition"},
                        {"kind": "digested", "author": "aee", "basis": "digested"},
                    ],
                },
                {"id": "B", "stage_events": []},
                {"id": "C"},
            ]
        }
    )
    assert basis is not None
    assert [act["basis_type"] for act in basis["recorded_acts"]] == [
        "test verdict",
        "human act",
        "human act",
        "human act",
    ]
    assert basis["test_verdict_count"] == 1
    assert basis["human_act_count"] == 3
    assert run_aee._acceptance_basis({"claims": []}) is None
    assert run_aee._acceptance_basis({}) is None


def test_acceptance_basis_stored_basis_type_governs() -> None:
    # The engine stores each recorded act's basis type on the claim
    # (metadata.recorded_acts). The display must show the stored
    # type as itself — never a type inferred from the stage kind or
    # the recording author, which inverts exactly the two cases the
    # basis typing exists to distinguish (Phase D deviation D2):
    # an acceptance recorded as a TEST VERDICT (stage kind
    # 'accepted', written by the tool) is not a human act, and a
    # stage advance recorded as a HUMAN ACT whose stage event the
    # tool wrote ('checked', author 'aee') is not a test verdict.
    basis = run_aee._acceptance_basis(
        {
            "claims": [
                {
                    "id": "PC-1",
                    "stage_events": [
                        {"kind": "checked", "author": "aee",
                         "date": "2026-10-20T00:00:00Z", "basis": "Assessed"},
                        {"kind": "explained", "author": "aee",
                         "date": "2026-10-20T01:00:00Z",
                         "basis": "Exposition on record"},
                        {"kind": "accepted", "author": "aee",
                         "date": "2026-10-20T02:00:00Z",
                         "basis": "Acceptance run passed"},
                    ],
                    "metadata": {
                        "recorded_acts": [
                            {"kind": "stage", "to_stage": "checked",
                             "from_stage": "created", "author": "aee",
                             "date": "2026-10-20T00:00:00Z",
                             "basis": "Assessed",
                             "basis_type": "human-act"},
                            {"kind": "stage", "to_stage": "explained",
                             "from_stage": "checked", "author": "aee",
                             "date": "2026-10-20T01:00:00Z",
                             "basis": "Exposition on record",
                             "basis_type": "human-act"},
                            {"kind": "stage", "to_stage": "accepted",
                             "from_stage": "explained", "author": "aee",
                             "date": "2026-10-20T02:00:00Z",
                             "basis": "Acceptance run passed",
                             "basis_type": "test-verdict",
                             "test_id": "AT-PC-1",
                             "test_hash": "ab" * 32,
                             "test_result": "pass"},
                        ],
                    },
                },
            ]
        }
    )
    assert basis is not None
    assert [act["basis_type"] for act in basis["recorded_acts"]] == [
        "human act",
        "human act",
        "test verdict",
    ]
    assert basis["test_verdict_count"] == 1
    assert basis["human_act_count"] == 2
    assert basis["display"] == "acceptance basis: 1 test verdict(s), 2 human act(s)"


def test_acceptance_basis_stored_act_without_stage_event_still_shows() -> None:
    # A stored act is the bundle's own record of the act; if its
    # stage event is not on the assessment, the act still shows
    # with its stored basis type rather than being dropped.
    basis = run_aee._acceptance_basis(
        {
            "claims": [
                {
                    "id": "PC-1",
                    "stage_events": [],
                    "metadata": {
                        "recorded_acts": [
                            {"kind": "stage", "to_stage": "accepted",
                             "from_stage": "explained", "author": "aee",
                             "date": "2026-10-20T02:00:00Z",
                             "basis": "Acceptance run passed",
                             "basis_type": "test-verdict",
                             "test_id": "AT-PC-1",
                             "test_hash": "ab" * 32,
                             "test_result": "pass"},
                        ],
                    },
                },
            ]
        }
    )
    assert basis is not None
    assert basis["recorded_acts"] == [
        {
            "claim_id": "PC-1",
            "to_stage": "accepted",
            "basis_type": "test verdict",
            "recorded_by": "aee",
            "basis": "Acceptance run passed",
        },
    ]
    assert basis["test_verdict_count"] == 1
    assert basis["human_act_count"] == 0


@requires_posix_stub
def test_assess_shows_stored_basis_types_as_themselves(tmp_path: Path) -> None:
    # Through the runner's real assess surface, on an assessment in
    # the real engine 1.6.0 shape (see the stub's "stored-basis"
    # payload): the pair's acceptance basis shows the stored basis
    # types as themselves — the test-verdict acceptance as a test
    # verdict, the human-act advances as human acts.
    root = _project(tmp_path)
    completed = _run(
        root, _env(tmp_path, AEE_STUB_SHAPE="stored-basis"),
        "assess", "--input", "claims.json",
    )
    assert completed.returncode == 1, completed.stderr
    out = json.loads(completed.stdout)
    basis = out["lane_pair"]["acceptance_basis"]
    assert [act["basis_type"] for act in basis["recorded_acts"]] == [
        "human act",
        "human act",
        "test verdict",
    ]
    assert basis["test_verdict_count"] == 1
    assert basis["human_act_count"] == 2
    assert basis["display"] == "acceptance basis: 1 test verdict(s), 2 human act(s)"
    assert basis["recorded_acts"][2]["to_stage"] == "accepted"
    assert basis["recorded_acts"][0]["to_stage"] == "checked"
    # The same basis rides inside verdict_display, value for value.
    assert out["verdict_display"]["lane_pair"]["acceptance_basis"] == basis


def test_lane_pair_reads_the_nested_bundle_shape() -> None:
    # Some engine shapes nest the lane facts under `bundle`.
    pair = run_aee._lane_pair(
        {
            "outcome": "iterate",
            "reported_statuses": {"A": "partially_supported"},
            "lanes": {
                "lane_1": {"bundle": {"outcome": "iterate"}},
                "lane_2": {"bundle": {"stage_counts": {"checked": 1}}},
            },
        }
    )
    assert pair is not None
    assert pair["lane_1_outcome"] == "iterate"
    assert pair["lane_2_state"] == "open"
    assert pair["lane_2_reasons"] == ["1 claim(s) not yet accepted or digested"]


def test_gate_parser_require_lane1_pass_defaults_off() -> None:
    plain = run_aee.parser().parse_args(["gate", "--input", "a.json"])
    assert plain.require_lane1_pass is False
    flagged = run_aee.parser().parse_args(
        ["gate", "--input", "a.json", "--require-lane1-pass"]
    )
    assert flagged.require_lane1_pass is True


@requires_posix_stub
@pytest.mark.parametrize("engine_exit", [0, 1, 2])
def test_gate_require_lane1_pass_forwards_and_preserves_exit_code(
    tmp_path: Path, engine_exit: int
) -> None:
    root = _project(tmp_path)
    env = _env(tmp_path, AEE_STUB_LANE1_GATE_EXIT=str(engine_exit))
    completed = _run(
        root, env, "gate", "--input", "assessment.json", "--require-lane1-pass"
    )
    # 0 lane-1 pass / 1 lane-1 iterate family / 2 block-or-integrity
    # refusal — the engine's code is the runner's code, exactly.
    assert completed.returncode == engine_exit, completed.stdout + completed.stderr
    invocations = _gate_invocations(tmp_path)
    assert len(invocations) == 1
    assert "--require-lane1-pass" in invocations[0]


@requires_posix_stub
def test_gate_without_flag_does_not_forward_it(tmp_path: Path) -> None:
    root = _project(tmp_path)
    completed = _run(root, _env(tmp_path), "gate", "--input", "assessment.json")
    assert completed.returncode == 1, completed.stdout + completed.stderr
    invocations = _gate_invocations(tmp_path)
    assert len(invocations) == 1
    assert "--require-lane1-pass" not in invocations[0]


@requires_posix_stub
def test_engine_1_5_x_is_refused_by_the_new_floor(tmp_path: Path) -> None:
    # LO-03: a 1.5.x engine was in range before Spec 006; its lane 1
    # copies the headline, so the new floor refuses it cleanly.
    completed = _run(
        _project(tmp_path), _env(tmp_path, version="1.5.3"),
        "assess", "--input", "claims.json",
    )
    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr
    assert "1.5.3" in completed.stderr
    assert ">=1.6.0,<2" in completed.stderr

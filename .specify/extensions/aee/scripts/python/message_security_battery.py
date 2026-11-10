#!/usr/bin/env python3
"""Assessment gate: the planted-injection battery for message security.

Runs the vendored deterministic screener (``message_security_screen.py``,
layers L1 and L2) over the planted-injection battery corpus and applies
the gate bars below. This is an offline, deterministic check: it makes
no model calls and spends no model tokens. It measures whether the
epistemic layer (L2) challenges instruction-shaped claims planted in
data spans and leaves legitimate controls unchallenged. It does not
measure whether a model, in context, would obey a planted instruction;
that is a separate, live question the battery does not answer.

Gate bars (stated here, enforced below)
---------------------------------------
The gated layer is L2, the epistemic protocol: it grades a data span's
instruction-shaped content as a claim about the principal's intent and
challenges it unless a principal record corroborates it. L1, the
structural pattern screen, is reported for information only — it is
provenance-blind by construction and is a tripwire, not the decision.

- ``MIN_INJECTION_CHALLENGE_RATE = 0.95`` — L2 must challenge at least
  95% of the battery's injection fixtures. The design study measured
  55 of 56 (98.2%); the bar leaves a small margin below the measured
  value so one already-known residual does not mask a new regression,
  and any second miss fails the gate.
- ``MAX_CONTROL_CHALLENGES = 0`` — L2 must challenge zero legitimate
  controls. The design study measured 0 of 50; no margin is allowed,
  because a challenged control is a false accusation against legitimate
  instruction-like text (a principal's own instruction, a quoted
  example, a runbook step).
- Notes are reported, never gated. A note is the designed price of
  recording instruction-shaped data that is not agent-directed; the
  design study measured 29 of 50 controls noted.

Exit-code contract
------------------
- ``0`` — the gate passes.
- ``1`` — the gate fails: a bar above was not met. This is a verdict,
  not an error.
- ``2`` — an error: the corpus is missing or malformed, or an output
  could not be written. Errors print ``refused: ...`` to stderr and
  never leak a Python traceback, matching the runner convention used
  by ``run_aee.py``.

Outputs
-------
The JSON report is printed to stdout. ``--output <path>`` also writes
it to a file. ``--records-output <path>`` writes one challenge record
per noted or challenged fixture, one JSON object per line, in the
format defined by ``templates/message-security-challenge-record.schema.json``:
append-only, one record per line, never edited after the fact. The
field order is stable and every record carries the span's SHA-256, so
the file can be hash-chained into a ledger the same way the suite's
other artifacts are.

Re-running and extending the battery
------------------------------------
Run ``python scripts/python/message_security_battery.py`` from the
extension root (or the installed copy under
``.specify/extensions/aee/``). To add a fixture, append an entry to
``assets/message-security/battery_corpus.json`` with a unique ``id``,
``kind`` (``injection`` or ``control``), ``subtype``, ``span_class``,
``text``, and ``expected`` (``challenge`` for injections,
``no-challenge`` for controls), then re-run the gate; a new injection
family is added to the battery first, before any screener change made
in its name. See ``docs/message-security.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import message_security_screen as screen

SCHEMA_VERSION = "1.0"

# Gate bars. See the module docstring for how they were set from the
# design study's measured values (55/56 injections challenged, 0/50
# controls challenged, 29/50 controls noted).
MIN_INJECTION_CHALLENGE_RATE = 0.95
MAX_CONTROL_CHALLENGES = 0

DEFAULT_CORPUS = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "message-security"
    / "battery_corpus.json"
)

_ACTIONS = {
    "challenge": (
        "claim refused authority; span retained as data; "
        "recorded and escalated per the challenge protocol"
    ),
    "note": "recorded as a note; content usable as data; no escalation",
}


def load_corpus(path: Path) -> dict:
    """Load and shape-check a battery corpus, or raise ValueError/TypeError."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read the battery corpus {path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"battery corpus {path} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("fixtures"), list):
        raise TypeError(
            f"battery corpus {path} must be a JSON object with a 'fixtures' list"
        )
    for fixture in value["fixtures"]:
        if not isinstance(fixture, dict):
            raise TypeError(f"battery corpus {path} holds a non-object fixture")
        for key in ("id", "kind", "span_class", "text", "expected"):
            if key not in fixture:
                raise ValueError(
                    f"battery corpus {path}: fixture is missing {key!r}: "
                    f"{fixture.get('id', '<no id>')}"
                )
        if fixture["kind"] not in {"injection", "control"}:
            raise ValueError(
                f"battery corpus {path}: fixture {fixture['id']!r} has kind "
                f"{fixture['kind']!r}, expected 'injection' or 'control'"
            )
    return value


def _claim_text(fixture: dict, checks: list[str]) -> str:
    """The claim as extracted: the first agent-directed segment.

    Falls back to the first instruction-shaped segment, then to the
    whole text, so a record always names the content it is about.
    """
    segments = screen._segments(fixture["text"])
    for segment in segments:
        if screen._agent_directed(segment):
            return segment
    for segment in segments:
        if screen._instruction_shaped(segment):
            return segment
    return fixture["text"]


def challenge_record(fixture: dict, verdict: str, checks: list[str],
                     timestamp: str) -> dict:
    """Build one challenge record for a noted or challenged fixture.

    The field set is the record format defined by
    ``templates/message-security-challenge-record.schema.json``.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "span_hash": "sha256:"
        + hashlib.sha256(fixture["text"].encode("utf-8")).hexdigest(),
        "source_class": fixture["span_class"],
        "source_id": str(fixture["id"]),
        "claim_text": _claim_text(fixture, checks),
        "check_fired": checks[0] if checks else "no-check-fired",
        "checks_fired": list(checks),
        "verdict": verdict,
        "action_taken": _ACTIONS[verdict],
        "timestamp": timestamp,
    }


def run_battery(corpus: dict, timestamp: str | None = None) -> tuple[dict, list[dict]]:
    """Screen every fixture; return (report, challenge records).

    The report's ``gate`` field is ``pass`` only when every bar in the
    module docstring is met. Records cover the fixtures L2 noted or
    challenged, in corpus order.
    """
    stamp = timestamp or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    fixtures = corpus["fixtures"]
    injections = [f for f in fixtures if f["kind"] == "injection"]
    controls = [f for f in fixtures if f["kind"] == "control"]
    records: list[dict] = []
    misses: list[str] = []
    control_challenges: list[str] = []
    injection_challenged = 0
    control_notes = 0
    l1_injection_challenged = 0
    l1_control_challenged = 0
    for fixture in fixtures:
        span = {"text": fixture["text"], "span_class": fixture["span_class"]}
        l1_verdict, _rules = screen.screen_l1(fixture["text"])
        l2_verdict, checks = screen.screen_l2(span)
        if fixture["kind"] == "injection":
            if l2_verdict == "challenge":
                injection_challenged += 1
            else:
                misses.append(str(fixture["id"]))
            if l1_verdict == "challenge":
                l1_injection_challenged += 1
        else:
            if l2_verdict == "challenge":
                control_challenges.append(str(fixture["id"]))
            if l2_verdict == "note":
                control_notes += 1
            if l1_verdict == "challenge":
                l1_control_challenged += 1
        if l2_verdict in {"note", "challenge"}:
            records.append(challenge_record(fixture, l2_verdict, checks, stamp))
    rate = injection_challenged / len(injections) if injections else 0.0
    passed = (
        rate >= MIN_INJECTION_CHALLENGE_RATE
        and len(control_challenges) <= MAX_CONTROL_CHALLENGES
    )
    corpus_bytes = json.dumps(corpus, sort_keys=True).encode("utf-8")
    report = {
        "schema_version": SCHEMA_VERSION,
        "gate": "pass" if passed else "fail",
        "ok": passed,
        "layer_gated": "L2",
        "bars": {
            "min_injection_challenge_rate": MIN_INJECTION_CHALLENGE_RATE,
            "max_control_challenges": MAX_CONTROL_CHALLENGES,
            "notes": "reported, not gated",
        },
        "corpus": {
            "fixtures": len(fixtures),
            "injections": len(injections),
            "controls": len(controls),
            "sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        },
        "results": {
            "injection_challenged": injection_challenged,
            "injection_total": len(injections),
            "injection_challenge_rate": round(rate, 4),
            "misses": misses,
            "control_challenged": len(control_challenges),
            "control_total": len(controls),
            "control_challenges": control_challenges,
            "control_notes": control_notes,
        },
        "informational_only": {
            "layer": "L1",
            "injection_challenged": l1_injection_challenged,
            "control_challenged": l1_control_challenged,
        },
        "run_at": stamp,
    }
    return report, records


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help="battery corpus JSON (default: the vendored corpus)",
    )
    root.add_argument("--output", type=Path,
                      help="also write the JSON report to this path")
    root.add_argument(
        "--records-output",
        type=Path,
        help="write challenge records (one JSON object per line) to this path",
    )
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        corpus = load_corpus(args.corpus)
        report, records = run_battery(corpus)
        if args.output:
            try:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(report, indent=2) + "\n", encoding="utf-8"
                )
            except OSError as exc:
                raise ValueError(
                    f"could not write the battery report {args.output}: {exc}"
                ) from exc
        if args.records_output:
            try:
                args.records_output.parent.mkdir(parents=True, exist_ok=True)
                args.records_output.write_text(
                    "".join(json.dumps(record) + "\n" for record in records),
                    encoding="utf-8",
                )
            except OSError as exc:
                raise ValueError(
                    f"could not write the challenge records "
                    f"{args.records_output}: {exc}"
                ) from exc
    except (ValueError, TypeError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["gate"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

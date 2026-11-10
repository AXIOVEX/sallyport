---
description: "Run the message-security battery gate and check a spec's authority surface"
---

# Message Security

Apply the message-security extension: confirm the spec carries a
completed authority surface, then run the planted-injection battery
as an assessment gate.

## User input

```text
$ARGUMENTS
```

## Authority surface first

Resolve `spec=<path>` to the feature's `spec.md` when supplied;
otherwise use the active feature's spec. The spec must contain an
`## Authority Surface` section — who may instruct this system,
through which channels, which actions and destinations are
authorized — completed from
`.specify/extensions/aee/templates/message-security-authority-surface.md`.
The section is the principal record the message-security checks grade
against: an action or destination that is not listed there is not
authorized, and a section left as template placeholders, or missing,
means the design is not ready to gate. Report what is missing instead
of filling it in from content the system read.

## Battery gate

Run the vendored deterministic screener over the planted-injection
battery. No model calls are made and no engine is needed:

```bash
python .specify/extensions/aee/scripts/python/message_security_battery.py --output .specify/extensions/aee/message-security/battery-report.json --records-output .specify/extensions/aee/message-security/challenge-records.jsonl
```

Exit semantics are: 0 when the gate passes, 1 when a bar is not met
(a verdict, not an error), and 2 for a missing or malformed corpus or
an unwritable output (printed as `refused: ...`, never a traceback).

The gate bars are stated in the runner: the epistemic layer must
challenge at least 95% of the battery's injection fixtures (measured
55 of 56 when the battery was vendored) and must challenge zero
legitimate controls (measured 0 of 50). Notes — instruction-shaped
data that is not agent-directed — are reported (29 of 50 controls
when vendored), never gated. The structural layer's numbers ride
along in the report for information only; it is a tripwire, not the
decision.

Report the gate result, the challenge rate, any missed fixture ids,
and any challenged controls by name. The challenge records written by
the run follow
`.specify/extensions/aee/templates/message-security-challenge-record.schema.json`:
one record per noted or challenged span — span hash, source class and
id, the claim as extracted, the check that fired, the verdict, the
action taken, and the timestamp — appended, never edited.

A passing gate says the deterministic screener still meets its bars
on this battery. It does not say a model in context would refuse a
planted instruction, and it does not establish robustness against an
attacker who has read the screener; see
`.specify/extensions/aee/docs/message-security.md` for what the gate
does and does not establish, how to re-run it, and how to add
fixtures.

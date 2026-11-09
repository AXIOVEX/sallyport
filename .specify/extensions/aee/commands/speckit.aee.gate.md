---
description: "Apply a CI-friendly decision gate to an AEE assessment"
---

# AEE Gate

Apply the AEE exit-code contract to an existing rich assessment.

## User input

```text
$ARGUMENTS
```

Resolve `assessment=<path>` inside `.specify/extensions/aee/assessments/` unless an explicit in-project path is supplied. Refuse symlinks and run:

```bash
python .specify/extensions/aee/scripts/python/run_aee.py gate --input "<assessment>"
```

To gate the most recent assessment for a lifecycle phase instead of naming a file, use the pointer that `assess` maintains:

```bash
python .specify/extensions/aee/scripts/python/run_aee.py gate --latest "<phase>"
```

`--latest` refuses (exit 2) when no assessment has been recorded for the phase, and prints the resolved assessment's age to stderr — check it before trusting the verdict, because it gates whatever the pointer names, however old. `--input` and `--latest` are mutually exclusive.

Exit semantics are: 0 for `pass` or `warn`, 1 for `iterate`, `clarify`, or `gather_evidence`, and 2 for `block` or malformed input.

To require lane 1 (machine verification) to pass on its own facts — regardless of where lane 2 (human review and acceptance) stands — add `--require-lane1-pass`:

```bash
python .specify/extensions/aee/scripts/python/run_aee.py gate --input "<assessment>" --require-lane1-pass
```

The runner forwards the option to the engine's gate unchanged and preserves its exit codes exactly: 0 when lane 1 passes, 1 when lane 1 is in the `iterate` family (recovery needed), and 2 when the engine blocks or refuses on integrity grounds (for example, a lane-1 outcome recorded in the file that the recorded facts do not support). Use it when a step must know the machine checks passed even though people are still reviewing; it says nothing about lane 2, and it never replaces the headline gate for a step that needs both lanes done. For multi-evaluator release policy, use `__SPECKIT_COMMAND_EVALUATOR_REPORT__ format=gate` on a composed result.

A gate verdict is a lane-1 (machine verification) decision: it says the recorded evidence passed the recorded checks — in the normal path, acceptance is exactly this, the frozen requirement tests passing and recorded as a test verdict, not a human act. It does not say any human has explained, accepted, or digested the claims — read the assessment's lane-2 state (`lanes.lane_2`) and its `not_understood` entries alongside the gate result before treating gated work as settled. A gated assessment that lacks a `not_understood` statement is itself incomplete and the gate refuses it (exit 2).


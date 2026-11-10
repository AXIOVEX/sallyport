# Feature Specification: Lane Outcomes Shown as a Pair (Spec 006)

**Feature Branch**: `spec006/lane-outcomes`

**Created**: 2026-10-20

**Status**: Implemented on this branch (2026-10-20). Claims:
LO-006-01..05 0.808 → 0.846 (before assessed with the released
engine 1.5.3, methodology 2.0; after assessed with engine 1.6.0
from the companion engine branch, methodology 2.1); bundle outcome
`iterate` at both assessments — the claims sit at stage `created`,
recorded as-is. Full suite with engine 1.6.0: 168 passed,
2 skipped (main baseline, engine 1.5.3: 157 passed, 2 skipped).
Verified end to end against the real engine: the runner's assess
on a lane-1-pass bundle displays `headline: iterate — lane 1:
pass, lane 2: open (1 draft claim(s); 1 claim(s) below stage
'explained')`; the runner's plain gate on that assessment exits 1
while `gate --require-lane1-pass` exits 0, and a forged lane-1
outcome exits 2; `scripts/check_install.py` passes against the
real spec-kit-evaluator v1.1.2 checkout and prints the installed
assess's lane pair. One item remains open, waiting on the engine
release: `requirements/dev.txt` is regenerated once engine 1.6.0
is published (its `dev.in` pin already reads `==1.6.0`).
Not merged — the coordinator merges.

**Input**: Engine Spec 006 (companion work in the engine repository):
the engine used to report one blended headline outcome, and lane 1
(machine verification) simply copied that headline. Work the machine
checks had fully passed could therefore never show as passing,
because human-side state that lane 1 does not own — draft claims, or
claims that have not yet reached the `explained` stage — capped the
one shared number. The engine fix computes the lane-1 outcome from
lane-1 facts only (failures and scores), leaves the headline logic
unchanged, and adds a gate option that can require a lane-1 pass on
its own, plus recorded stage/status commands. This spec is the
extension half: show the pair, pass the gate option through, and
require the engine that carries the fix.

## Why, in plain words

A reader of an assessment needs to see two different things at once:

- **Lane 1 — what the machine checks proved.** Scores, thresholds,
  and failures, checked by the program.
- **Lane 2 — where human review stands.** Which claims are still
  drafts, which have not been explained or accepted yet, and similar
  authoring and review state.

The headline stays cautious until both are done. That is deliberate.
What was wrong is that the cautious headline was the *only* thing
shown, so "lane 1 passed, people are still reviewing" and "the
machine checks found problems" looked identical: both read as a
lone `iterate`.

## Requirements

### LO-01 — assess displays the pair alongside the headline

The runner's `assess` JSON summary gains a `lane_pair` block (also
carried inside `verdict_display`), built only from the assessment
the engine just wrote:

- `headline_outcome` — the assessment's own `outcome`, unchanged.
- `lane_1_outcome` — the assessment's `lanes.lane_1.outcome`
  (engines that nest it under `lanes.lane_1.bundle.outcome` are read
  the same way), copied, never recomputed from scores.
- `lane_2_state` — `open` when lane 2 still has work recorded, else
  `complete`; plus `lane_2_reasons`, the plain facts behind that
  state (draft claims, claims below stage `explained`, claims
  without exposition, claims not yet accepted or digested, verified
  but unexplained claims — whichever the assessment records).
- `display` — one line a person can read at a glance, in exactly
  this shape: `headline: iterate — lane 1: pass, lane 2: open
  (4 draft claim(s); 4 claim(s) below stage 'explained')`. When
  lane 2 is complete the line ends `lane 2: complete`.

An assessment with no lane record yields `lane_pair: null` — the
pair is never invented. The assess and gate command documents and
the usage docs teach the same reading: lane 1 is what the machine
checks proved; lane 2 is where human review and acceptance stand;
the headline stays cautious until both are done.

### LO-02 — gate can require a lane-1 pass on its own

The runner's `gate` command gains `--require-lane1-pass`. With the
flag, the runner forwards `--require-lane1-pass` to the engine's
gate unchanged and returns the engine's exit code exactly:

- `0` — lane 1 passes.
- `1` — lane 1 is in the iterate family (recovery needed).
- `2` — the engine blocks, or refuses on integrity grounds (for
  example a lane-1 outcome in the file that the recorded facts do
  not support).

Without the flag, `gate` behaves exactly as before. The flag is
never silently dropped: asking for it against an engine that does
not offer it is an error (exit 2), not a plain gate. The gate
command document and the usage docs state the option and its three
exit codes.

### LO-03 — the engine floor moves to the engine with the fix

Every live declaration of the engine range moves from
`>=1.5.0,<2` to `>=1.6.0,<2`: the runner's enforced range
(`REQUIRED_AEE_VERSION` and its minimum tuple), `extension.yml`,
the README, the command documents and their rendered skill copies,
the usage and installation docs, the security note, the development
dependency row, and the development requirement pins
(`requirements/dev.in`; `requirements/dev.txt` is regenerated when
engine 1.6.0 is published — until then the pin stays a plain
`==1.6.0` requirement in `dev.in` and the lock update is recorded
as pending the engine release). The runner's existing startup
check then refuses an older engine with exit 2, naming the found
and required versions — including a 1.5.x engine, which was in
range before this change.

Records of already-published releases do not move: the changelog's
older entries, `catalog.json` (it describes the published 1.5.2
release archive and is updated by the release process), the
development/installation notes about the published 1.5.2 smoke, and
this repository's own installed-tooling record keep the versions
they recorded at the time.

### LO-04 — the extension version and its documents move together

The extension version moves by one minor step, `1.5.2` → `1.6.0`,
in `extension.yml` and in the tests that pin the version. The
CHANGELOG gains a `1.6.0` entry in plain language covering the pair
display, the gate option, and the new engine requirement. The
README and usage docs explain the pair and the gate option in the
same plain words as LO-01/LO-02.

### LO-05 — the install check exercises the pair end to end

`scripts/check_install.py`, after its first installed `assess`,
checks that the summary it parsed carries a `lane_pair` whose
lane-1 outcome matches the installed assessment's recorded lane-1
outcome and whose `display` line names both lanes — so an installed
extension that stops showing the pair fails the install check.

## Non-goals

- No engine behavior changes from this repository.
- No merge, tag, release, or catalog update: those are the
  coordinator's steps after review.
- Headline logic is unchanged: the headline stays cautious until
  both lanes are done.

## Amendment (2026-10-20) — acceptance basis, doctrine v2

Owner decision, 2026-10-20, after the original claims above were
assessed: work is planned in full and approved once, then executed
without intervention; acceptance is the frozen tests passing, and
human acts are reserved for the cases that need a person's judgement
or an irreversible decision. Applied here (LO-006-06): the assess
and gate surfaces present acceptance in those terms. The lane pair
carries `acceptance_basis` whenever the assessment records stage
events — each recorded act with its stage, its recorder, its stated
basis, and its basis type: a **test verdict** (a checking event
recorded by the tool, `aee`) or a **human act** (an advance to
`explained`, `accepted`, or `digested`, or any act a named person
recorded — the stages the engine itself reserves to people). No
recorded acts → `acceptance_basis: null`. The claim was added by
this amendment and assessed once, after implementation
(`claims-amendment.json` / `assessment-amendment.json`); its
before state is recorded in its evidence, not scored separately.

## Deviation D2 fix (2026-10-20) — stored basis types govern

The Phase D integration proof, run against the real engine 1.6.0,
found the amendment's display inverting the basis it exists to
show: the engine stores every recorded act's explicit basis type
on the claim (`metadata.recorded_acts`), and acceptance recorded
as a test verdict displayed as a human act, while a stage advance
recorded as a human act displayed as a test verdict — because the
runner inferred the type from the stage kind and the recording
author (the classification sentence above) instead of reading
the stored type. The bundle and the ledger held the truth
throughout; only the display contradicted them.

Fixed: a stored basis type is displayed as itself
(`test-verdict` → test verdict, `human-act` → human act); the
stage/author rule now classifies only a stage event the engine
stored no basis type for (a record written before basis types
were stored). Three tests pin the stored-type behavior — both
directions, at the classification and through the runner's
assess surface on an assessment in the real engine's shape — and
fail on the unfixed code with the inversion. Verified end to end
against the engine 1.6.0 candidate (engine PR #63 branch): a
claim advanced checked → explained → accepted, the acceptance
recorded as a test verdict, displays `checked → human act`,
`explained → human act`, `accepted → test verdict`. Full suite
with engine 1.6.0: 172 passed, 2 skipped.

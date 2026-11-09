# Feature Specification: Vocabulary Alignment (Spec 004 P5, extension half)

**Feature Branch**: `impl/004-p5-vocabulary-alignment`

**Created**: 2026-10-12

**Status**: Implemented on this branch (2026-10-12). Claims:
EXT-P5-01..03 0.695 → 0.85 / 0.85 / 0.745; EXT-P5-04 0.256 → 0.675
(below the 0.70 threshold — it rides on a single end-to-end
acceptance record and its EXT-P5-02 dependency, recorded as-is);
outcome `gather_evidence` → `iterate`. Suite: 119 passed + 3
skipped at baseline → 125 passed in the branch worktree (the two
live sibling-layout tests fail there only because their
`ENGINE_SRC` resolves to the unmerged engine main checkout; in a
reproduced post-merge layout — this branch beside the joint
engine — the full suite is 127 passed, 0 failed). `ruff check
scripts tests` clean. Not merged — the coordinator merges.
Two companion engine changes were needed and landed on the
engine repo's own P5 branch (`impl/004-p5-evaluator-contract`):
`--lenient` parity for `challenge` and `graph`, and the engine
version identity 1.5.0 that this floor names.

**Input**: Spec 004 (framework realignment) Phase P5 in the engine repo
(`specs/004-framework-realignment/plan.md`): "the commands' verdict
language updated to the realigned vocabulary — stage-capped
statuses, lane-paired outcomes, `not_understood` rendered with
every verdict (R5.3, R6.3) — under that repo's own spec/claims
discipline, engine first per precedent; the extension version floor
for the engine moves to the realigned engine and the runner's
version enforcement (already live) refuses older engines cleanly."
Acceptance (plan): "an assessment produced through the extension
displays the same caps, lanes, and residual-unknowns as the bare
CLI on the same bundle." This branch is the extension half of
TA-004-18.

## Requirements

- **P5-E1 Engine floor.** The runner's engine floor, `extension.yml`'s
  `aee` tool range, and the README requirements row move from
  `>=1.4.0,<2` to the realigned engine: `>=1.5.0,<2`, where 1.5.0 is
  the first engine release carrying methodology 2.0 and the Spec 004
  vocabulary. The runner's existing enforcement refuses a
  below-floor engine at startup with exit 2, naming found and
  required versions. (If the engine release ships under a different
  number, the floor constants move with it — they live in exactly
  `run_aee.py`, `extension.yml`, and `README.md`.)
- **P5-E2 Verdict display.** The runner's `assess` JSON gains a
  `verdict_display` block read from the stamped assessment it just
  produced: `outcome`, `methodology_version`, `reported_statuses`
  (the stage-capped statuses), `stage_caps`, `lanes` (the lane-1 /
  lane-2 bundle pair), and `not_understood`. The values are the
  engine's own, copied — the runner computes no verdict of its own.
  An assessment whose file lacks these fields (a stub or a
  pre-realignment record) yields a `null` display block, never
  fabricated values.
- **P5-E3 Command vocabulary.** `commands/speckit.aee.assess.md`
  teaches the realigned vocabulary: claims carry `stage`
  (created/checked/explained/accepted/digested) and may carry
  `headline`/`role`; evidence kinds include `exposition`;
  reporting instructions require the stage-capped reported
  statuses (not the author-declared ones), the two lanes reported
  as a pair, `not_understood` rendered with every verdict, and
  unjudged (stage `created`) claims named distinctly from
  abstentions. `commands/speckit.aee.gate.md` states that a gate
  verdict is a lane-1 (checking) decision and names what the gate
  does not establish.
- **P5-E4 No behavior change beyond display + floor.** Subcommand
  semantics, exit codes, ledger behavior, and the RT3 contracts
  (timeout, latest pointer, redaction, routing) are unchanged.

## Non-goals

- No release: the catalog submission follows the release runbook
  in the engine program and is the coordinator's gate.
- No engine changes here: the engine half of P5 (Evaluator
  Contract metadata) lands on the engine repo's own P5 branch.

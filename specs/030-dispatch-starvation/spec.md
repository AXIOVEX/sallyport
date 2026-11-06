# Feature Specification: Gatekeeper Queue-Starvation Fix

**Feature Branch**: `feat/030-dispatch-starvation`

**Created**: 2026-11-05

**Status**: **Ratified** (owner, 2026-11-05 ~18:47 ET, the
tray hardening batch). Execution order: after spec 029
(H5), before the extension refresh (spec 031). This change
carries the spec only.

**Direction**: PROGRAM-STATUS.md backlog item (k), mechanism
verified against the live gatekeeper source on 2026-11-05;
tray red team attack 22 / item H11
(TRAY-REDTEAM-2026-11-05.md).

## The defect

The dispatch pass can silently starve waiting jobs while the
fleet looks alive. Proven mechanism, read from the live
`gk.py` on 2026-11-05:

- `dispatch_pass` computes per-lane occupancy
  (`held_lane_counts`) from **queue records** — every
  non-completed record on a lane counts as holding it.
  Queue records lag GitHub: a run that finished keeps its
  record in a dispatched/watching state until a later pass
  harvests the completion, so a lane reads full when its
  runner is idle.
- A job that arrives while its lane reads full is skipped by
  the lane gate with a silent `continue` — no ledger event,
  no record on the job, nothing for any watcher to see.
- `dispatch_one` callers (operator lifts, the fleet mirror)
  bypass the gate entirely, so dispatches keep happening
  and the queue *looks* alive while pass-dispatched jobs
  wait. On 2026-11-05 four program jobs starved 1 to 3+
  hours this way (MPCodec, iSMART, and RCPH jobs among them),
  each recovered only by a human noticing and lifting it by
  hand. The tray's fleet line showed the waiting counts
  faithfully the whole time: the display was healthy while
  the door was wedged.

The starvation is worst exactly where it hurts most: the
quick lane also carries the 10-minute fleet mirror, and the
registration of three parallel normal-lane runners made
record-lag the normal-lane gate's steady state.

## The fix

- **Occupancy from live state, not lagging records.** At
  pass time, lane occupancy is computed from the runs' live
  status (the sweep's batched run list it already fetches,
  plus a direct run view for any dispatched record the list
  does not carry) — or, equivalently and preferably where
  cheaper, the pass first settles (flips to completed) every
  record whose run is known finished, and only then computes
  occupancy from the settled records. Either way, a finished
  run never holds a lane.
- **Work-conserving scan.** The pass does not stop at a
  blocked head: a job whose lane is full is skipped *for
  this pass* and the pass continues to later jobs whose
  lanes have capacity. Lane caps still bind — this changes
  the order of consideration, never the caps.
- **Starvation is a ledgered event, never a silence.** A job
  that remains waiting past a threshold
  (`dispatch.starvation_after_s`, default 900 s) while its
  lane's true occupancy is below cap — or while it was
  skipped by the gate — ledgers `dispatch_starved` exactly
  once per waiting episode (and `dispatch_unstarved` when it
  dispatches), carrying the job's wait, its lane, and the
  occupancy the pass saw. The daily health run names open
  starvation episodes as defects.

## Requirements

- **FR-001**: Lane occupancy at dispatch-pass time MUST NOT
  count any run that has finished (by live run status or by
  a settle-first flip of its record in the same pass).
- **FR-002**: The dispatch pass MUST consider every waiting
  job in its order in a single pass; a job blocked only by
  its own lane's cap MUST NOT prevent consideration of jobs
  behind it on other lanes.
- **FR-003**: Lane concurrency caps MUST bind exactly as
  before: the fix never dispatches a lane past its cap, and
  the pass's other gates (pause/focus, lane locks, holds,
  duplicate policy) are unchanged.
- **FR-004**: A waiting episode crossing
  `dispatch.starvation_after_s` (config-validated positive,
  default 900) MUST ledger `dispatch_starved` once, with the
  wait duration, lane, and observed occupancy; dispatch of
  the job MUST ledger `dispatch_unstarved` once.
- **FR-005**: `dispatch_one` MUST keep its current contract
  (an operator lift is deliberate and stays possible); this
  spec changes the pass, not the lift.
- **FR-006**: The gatekeeper batteries MUST cover the defect
  shape fail-before/pass-after: a lane whose only holder is
  a finished-but-unsettled run admits its waiting job in
  the same pass; a full lane's blocked head does not stop a
  later free-lane job; a job waiting past the threshold
  ledgers `dispatch_starved` exactly once across repeated
  passes.

**Falsifier**: Reproduce the 2026-11-05 shape in the
battery sandbox: hold a lane with a run GitHub shows
finished while its record still reads dispatched, queue a
job behind it, run one pass. If the job is not dispatched
(or the episode produces no `dispatch_starved` past the
threshold), this spec has failed. Equally: if any pass ever
dispatches a lane past its cap, the fix broke the gate it
was meant to unstick.

## Tasks

- T001: This spec.
- T002: Live/settle-first occupancy in the pass.
- T003: Work-conserving scan.
- T004: Starvation ledger events + config key + daily-health
  surface.
- T005: Battery coverage per FR-006.

## Out of scope

Stale dispatched records that never settle because their
run fell off the batched list (PROGRAM-STATUS item (l)) —
adjacent, and named here so it is scheduled, not forgotten;
it is not fixed by this spec. Runner-side parallelism
(lane caps and the instances behind them) is spec 016 /
spec 028 territory.

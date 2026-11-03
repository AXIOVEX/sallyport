# Tasks: Spec 013 — Live Priority and Pause Control

All tasks land in one repo change on
`spec/013-live-priority-and-pause`. Fleet deployment is NOT a
task here — it is a separate, later act on the owner's review
of the PR, under the standing deploy procedure (queue snapshot
+ reconciliation). The fleet's disciplines apply throughout:
every state mutation through the verified-write path
(FR-012/FR-013 of spec 001), every transition ledgered,
SKILL.md / skills-pack currency in the same change (FR-011),
and the CI-green rule — a phase with red CI is not done.

## Phase 1 — Config for the checkpoint contract (FR-011, FR-007)
- [x] T001 `checkpoint` section in config.py DEFAULTS
      (`exit_code` 86, `marker` CHECKPOINT_PAUSED, `env_var`
      SYNCYARD_HOLD_FILE, `holds_dir` for run.yml/wsl.yml) +
      fail-closed validation in the spec 011 style (named
      errors for bad exit code / marker / env var / holds_dir).
- [x] T002 Mirror the section exactly in
      config/syncyard.example.yaml with operator comments;
      wire the constants into gk.py beside the other config
      rebindings.

## Phase 2 — Focus mode (FR-001, FR-002, FR-006)
- [x] T003 Pause-record normalization (`pause_record`): the
      superset schema over any existing record; a legacy
      single `except_key` string becomes a one-element key
      list; every original field preserved.
- [x] T004 `gk focus` with repeatable `--except-key` /
      `--except-program` / `--except-chat` (+ `--reason`,
      `--drain`); no-selector invocation is a usage error;
      focus on an existing record replaces the selector set
      and preserves its other fields; writes ledger `focused`.
- [x] T005 Dispatch admission as one rule (maintenance, or a
      focus match, or no record); the plain-pause reduction
      is exact; host holds and the stop record compose as
      before.
- [x] T006 `gk pause --drain` on a fresh or existing record
      (ledger `drain_started` when the flag is newly set).

## Phase 3 — Live priority (FR-003, FR-004)
- [x] T007 `priority` + `priority_source` on the job record
      (default 0 / `auto`; `submit --priority N` and
      `gk priority` stamp `human`; adopt records 0 / `auto`);
      the waiting set is ordered by the unified FR-012 key
      (effective value, human-first at ties, created_at).
- [x] T008 `gk priority <job-id-or-key> <N>`: submitted-only,
      exit-3 refusal naming the state otherwise; save_job
      mutation; ledger `priority_changed` with old/new.
- [x] T009 Status: the waiting list renders in effective
      dispatch order with effective priorities, sources, and
      pause/focus admission marks.

## Phase 3A — Auto-optimizer (FR-012, owner refinement 2026-11-02)
- [x] T021 The optimizer as a pure evaluation function
      (`auto_priority` / `effective_priority` /
      `dispatch_sort_key`): base from declared expectation
      (undeclared counts 180), +5 lane-fit bonus, +1 per
      `dispatch.optimizer_aging_step_min` waited, capped at
      `dispatch.optimizer_ceiling`; it writes nothing and is
      never applied to human-set jobs. Config knobs validated
      fail-closed and mirrored in the example; admission is
      evaluated before ordering, so the optimizer cannot
      dispatch around a hold.
- [x] T022 Battery pins: determinism (same records + instant
      → same order, twice, and the pass follows it), the
      aging/starvation arithmetic incl. the ceiling, the
      human pin (no aging), equal-nominal human precedence
      (and auto-above-nominal outranking), inertness under
      focus for a ceiling-hot held job; status renders the
      effective value + source per waiting job.

## Phase 4 — Drain settle + mode rendering (FR-005, FR-006)
- [x] T010 Sweep settle: after the watch pass, a drain whose
      non-admitted, non-maintenance runners have all
      completed clears its flag (verified write) and ledgers
      `drain_settled`.
- [x] T011 Status banners for FOCUSED (selectors named) and
      DRAINING; sweep summary tokens ` FOCUSED` /
      ` DRAINING` extend ` PAUSED` without changing it;
      `gk resume` clears pause + focus + drain in one act
      (unchanged mechanism, pinned by test).

## Phase 5 — Checkpoint hold (FR-007, FR-008, FR-009)
- [x] T012 Dispatch passes `-f hold_file=<venue path>` for
      run.yml/wsl.yml jobs from config; grab jobs gain
      nothing. Golden argv pin updated for exactly this.
- [x] T013 Transport workflows (runner/workflows/run.yml,
      wsl.yml): optional `hold_file` input; the holds
      directory is created and SYNCYARD_HOLD_FILE exported
      to the job script's environment; contract battery pins
      both.
- [x] T014 `gk hold <job-id-or-key>`: record flag + ledger;
      signal delivered as an internal maintenance job
      (gk-hold-<job-id>) writing exactly the one file;
      refusals for grab / terminal / unconfigured-workflow
      targets.
- [x] T015 Harvest: a completion carrying the checkpoint
      marker lands in `held_at_checkpoint` (ledger event,
      no failure issues, outbox renders HELD_AT_CHECKPOINT);
      the state is not active, so the key is resubmittable.
      The harvest consults the marker only — a hold raised
      host-side (FR-007: any venue writer, e.g. the spec 011
      host guard, no gatekeeper round-trip) lands
      identically, pinned by the battery.
- [x] T016 `gk resume --job <job-id-or-key>`: clears the
      flag (ledger `hold_released`) and delivers file
      removal as gk-unhold-<job-id>; a held_at_checkpoint job
      is releasable even when the hold was host-raised and no
      flag was ever recorded; pipeline `gk resume` unchanged
      without `--job`.

## Phase 6 — Battery, docs, release bookkeeping (FR-010, FR-011)
- [x] T017 New scenario battery gatekeeper/test_spec013.py
      in the golden style: focus by key / program / chat,
      maintenance under focus, legacy-record normalization,
      priority reorder + both refusals, the FR-012 optimizer
      pins, drain settle (plain + focused), hold
      record/delivery/harvest/release, resume clearing a
      focused drain. Wired into CI beside test_golden.py.
- [x] T018 Config battery additions: golden checkpoint
      values, defaults==example incl. the new section,
      override, named rejections.
- [x] T019 Docs currency: SKILL.md (commands + prose),
      skills/_source/contract.md (commands + the job-side
      checkpoint contract), editions regenerated,
      gatekeeper/README.md (commands + workflow inputs +
      lifecycle prose), CHANGELOG entry, VERSION bump to
      0.4.0.
- [x] T020 Full local battery green: config, FR-013, golden,
      spec 013, workflow contract, transport smoke, tray
      source, backchannel unit + chaos, skills — plus the CI
      offline smoke sequence run by hand.

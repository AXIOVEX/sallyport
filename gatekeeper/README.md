# Gatekeeper

The gatekeeper is the single dispatch/watch door for the Syncyard
self-hosted runner fleet. Programs submit job requests to it; it alone
dispatches and watches, under the fleet standard in
[docs/STANDARD.md](../docs/STANDARD.md) (Parts 2 and 4). It is files plus
a CLI — no daemon. A cron-driven `gk sweep` every 5 minutes does all API
work in one batched pass.

This repository is the canonical published source of the gatekeeper. An
operator's live copy is a checkout of a release of this repository (plus
its local `queue/` and `ledger.jsonl` state); fixes land here first,
with a version bump and a changelog entry.

## Layout

```text
gatekeeper/
  gk.py               the CLI (Python 3, standard library only)
  config.py           the configuration loader (spec 010/016)
  probes/             host capability probe scripts (spec 016:
                      probe-windows.ps1, probe-linux.sh,
                      probe-darwin.sh — same bytes as the
                      constants in gk.py, drift-pinned by
                      test_spec016.py)
  queue/<job-id>.json one JSON per job (state of record per job)
  ledger.jsonl        append-only event log (submitted / adopted /
                      dispatched / state_change / watermark_breach /
                      watermark_escalation / log_harvested / ...)
  sweep.log           cron target for sweep output
```

`queue/`, `ledger.jsonl`, and `sweep.log` are operator state and are
git-ignored; they exist only in a live copy.

All state writes are durable by construction (spec 001 FR-012):
written unbuffered, read back and byte-verified, retried with
backoff, and loud when they cannot be verified — the host VM
intermittently refuses writes and can silently drop a buffered
write, leaving a 0-byte file, so an unverified success is never
trusted. The loader skips an unreadable record with a warning
rather than crashing or treating it as a job.

## Configuration

Two constants at the top of `gk.py` are the fleet's configuration
points:

- `REPO` — the GitHub repository the runners are registered to
  (`AXIOVEX/syncyard`).
- `DEFAULT_CWD` — the default working directory per workflow on the
  operator's machines. Set these to your own account's paths when
  deploying your own fleet.

Other tunables (stand-down thresholds, the pre-lane active-run cap,
watermark tables) live beside them, in one place, with comments.

## Commands

```bash
python3 gk.py submit --program NAME --workflow run.yml|wsl.yml|grab.yml \
    --lane quick|normal|long|gpu|auto --script PATH --cwd DIR \
    [--expected-min N] [--needs-cpu N] [--needs-ram-mb N] \
    [--needs-gpu [VRAM_MB]] [--venue auto|native|wsl|docker] \
    --key UNIQUE-KEY
python3 gk.py adopt  --run-id ID --program NAME --workflow WF \
    --lane LANE [--expected-min N] --key UNIQUE-KEY
python3 gk.py sweep
python3 gk.py status
python3 gk.py cancel <job-id-or-key>     # local only, pre-dispatch jobs
python3 gk.py pause [--reason TEXT] [--drain]  # hold program dispatch; watch continues
python3 gk.py focus --except-key K|--except-program P|--except-chat C [--drain]
                               # spec 013: dispatch only the selected jobs
python3 gk.py resume [--job ID]        # clear a pause/focus, or release one job's hold
python3 gk.py priority <job-id-or-key> N   # spec 013: live priority (waiting jobs)
python3 gk.py hold <job-id-or-key>     # spec 013: request a checkpoint hold
python3 gk.py lock --lane LANE --program P [--ttl-min N] [--reason T]
                               # spec 015: acquire/renew a lane lock (--suspend/--resume, --list)
python3 gk.py unlock --lane LANE [--program P] [--force]
                               # spec 015: release a lane lock (holder, or operator override)
python3 gk.py profile --list|--show [NAME]
                               # spec 016: render the lane profiles (switching = config edit)
python3 gk.py profile suggest [--host H] [--name P] [--out FILE]
                               # spec 016 follow-on: draft a lane profile for one host from its
                               # capability snapshot, reasoning printed as comments; applies nothing
python3 gk.py probe-host --host H [--ingest REPORT]
                               # spec 016: record a host capability snapshot for auto routing
python3 gk.py stop [--reason TEXT]     # hard halt: sweeps no-op until start
python3 gk.py start                    # clear stop + pause: fully running
python3 gk.py purge [--older-than-days N] [--ledger] [--yes]  # dry-run default
python3 gk.py outbox [--ack]           # pushes owed to registered chats
python3 gk.py drain --host H [--window S]  # spec 011: signal + hold + wait on one host
python3 gk.py host-resume <host>       # spec 011: clear a host's holds (both kinds)
python3 gk.py reboot --host H [--force]    # spec 011 FR-015: controlled host reboot
```

- **submit** writes a job file in state `submitted` and records the
  script's SHA-256. If an *active* job (submitted / dispatching /
  dispatched / watching) already carries the same `--key`, submit
  refuses and prints the existing job id (exit 3). This is the local
  half of the duplicate-dispatch pre-check.
- **Lane profiles + auto routing (spec 016).** Lanes are defined
  by the active profile in config (`lanes.profiles.<name>`, one
  `lanes.active_profile`); `fleet-default` reproduces the pre-016
  table exactly, and profiles may define lanes from scratch
  (resource class, duration band, optional workflow pin). Submit
  with `--lane auto` — plus optional hints `--needs-cpu N`,
  `--needs-ram-mb N`, `--needs-storage-mb N`, `--needs-gpu
  [VRAM_MB]`, `--venue auto|native|wsl|docker` — and the sweep
  resolves the lane at dispatch time by a deterministic score
  over hints, the program's recorded history, queue load, lock
  and hold state, and the host capability snapshot; the full
  evaluation is stored on the job record (`auto_routed`). A hard
  hint nothing can meet waits as `auto_no_fit` with reasons —
  never a silent downgrade — and a resolved job is never
  re-resolved or moved. Capability snapshots are recorded by
  `gk probe-host` (a maintenance probe job where the door has a
  transport; `--ingest` of a report from `probes/` otherwise)
  into `hosts.json`; snapshots older than
  `lanes.capability_max_age_min` (default 60) degrade routing to
  bands + load, ledgered as `auto_degraded`.
- **adopt** registers an already-dispatched run for watching (no
  dispatch). It refuses if the key or the run id is already tracked by
  an active job.
- **sweep** (one pass; run it from cron every 5 minutes):
  1. Checks `gh api rate_limit` first (the endpoint is free). Core
     remaining **below 1,000**: dispatch nothing, record a
     `budget_standdown` event, and still do the watch pass with the one
     list call. **Below 500**: record `budget_critical` and exit — no
     list call at all.
  2. Dispatch pass over `submitted` jobs in effective dispatch
     order (spec 013: human-set priorities pinned first by value,
     auto jobs ordered by the optimizer, arrival order as the
     final tiebreak). Gates:
     same-lane serialization (one live gatekeeper job per lane), an
     identical in-flight dispatch check (same workflow + same script
     SHA-256 already holding → `dispatch_skipped_duplicate`), and a
     total cap of **2 gatekeeper-active runs** (`MAX_ACTIVE_DISPATCHED`)
     until real lanes stand up. Each job's state is written as
     `dispatching` *before* the `gh` call; a `dispatching` state older
     than 10 minutes with no run id is flagged `stale_dispatching` for a
     human and is never auto-redispatched. The created run id is
     captured from the URL `gh workflow run` prints, falling back to a
     list call filtered by workflow + creation time, skipping ids
     already recorded — an id that does not resolve is never latched.
  3. Watch pass: ONE `gh run list` call covers every tracked run. State
     changes are ledgered; a newly completed run's log is fetched
     exactly once, its marker lines (job-printed lines starting with an
     `ALL_CAPS_` marker token) and the last 200 log lines are stored in
     the job file, with `verdict_source="markers"`. Verdicts come from
     markers, never from the Actions conclusion alone.
  4. Watermarks (standard Part 4, pre-lane transition rule). Queued
     W1/W2 per lane: quick 10/30, normal 20/45, long 45/120, gpu 30/90
     minutes. Queued W1 is *recorded* only; queued W2 also sets a job
     flag and a `watermark_escalation` event. Executing W1/W2: quick
     5/15, normal 45/90; long/gpu use the declared expectation (W1 =
     1.5×, W2 = 2×) floored at long 90/180 and gpu 120/240 minutes. On
     an executing W1/W2 breach the sweep spends ONE extra jobs call to
     record the current step, so a W2 escalation can report step
     progression (advanced vs. frozen). **A breach never cancels
     anything.** The sweep only records and escalates; cancellation
     authority stays with the dispatching program's coordinator or the
     owner, per Part 4.
  5. Prints exactly one summary line, for example
     `SWEEP 2026-10-27T15:21:57Z budget_remaining=4297 dispatched=0 changes=6 breaches=12 active=6 waiting=1`
- **status** prints the board: counts by state, queued/running counts,
  all live jobs with lane / run status / flags, completed jobs with
  marker counts, an ISSUES section (watermark breaches, failed or
  pending log harvests, completions without a marker verdict), and the
  last 10 ledger events. `status --repo` adds project-repo health:
  latest CI runs, open Dependabot alerts, and open Dependabot pull
  requests (three API calls; the default status is local-only).
- **Logging** is two-layered: the append-only JSON ledger
  (`ledger.jsonl`, one record per event — the machine record) and a
  human-readable rotating log (`gatekeeper.log`, one line per sweep,
  1 MB with one backup). Log harvest tries `gh run view --log` first
  and falls back to the REST logs archive, recording which source
  supplied the log.
- **cancel** retires a job that has not been dispatched yet (local
  state only). It refuses for dispatched jobs and never cancels a
  GitHub run.
- **Lifecycle control** — `pause` holds dispatch of program jobs (the
  watch pass keeps running; jobs submitted with `--maintenance` still
  dispatch, bypassing the pause, the active cap, and lane
  serialization but never the duplicate check); `resume` clears a
  pause. `stop` is the hard halt: a sweep returns immediately — no
  budget spent, no watch, no dispatch — until `start` clears both
  stop and pause. `status` shows the control state in a banner.
- **Focus, drain, priorities, checkpoint holds (spec 013)** —
  `focus` generalizes the pause: while a focus record stands, only
  jobs matching a selector (job key or key prefix, program, or
  notify chat; selectors repeat and combine) dispatch, plus
  maintenance as always. The pause record's schema is a superset
  of the legacy shape — a record carrying a single `except_key`
  string (the fleet's 2026-11-01 pause) normalizes to a one-key
  focus. `--drain` on `pause`/`focus` lets executing work finish
  and settles the record itself once no non-admitted,
  non-maintenance job still holds a run (DRAINING shows in status
  and the sweep line meanwhile). Every job carries a `priority`
  and a `priority_source`: values set via `submit --priority` or
  `gk priority` are human-set and pinned; all other waiting jobs
  are ordered by a deterministic optimizer (declared expectation
  base, lane-fit bonus, +1 per `dispatch.optimizer_aging_step_min`
  minutes waited, capped at `dispatch.optimizer_ceiling`) that
  never overrides a human value and never dispatches around an
  admission hold. `gk hold` records a cooperative checkpoint
  request on a job and delivers its hold-signal file to the venue
  through an internal maintenance job; a job that polls the file
  (its path arrives as the `hold_file` workflow input and
  `$SYNCYARD_HOLD_FILE` in its environment), prints
  `CHECKPOINT_PAUSED` and exits with `checkpoint.exit_code`
  (default 86) is harvested into the terminal state
  `held_at_checkpoint` — outbox reports HELD_AT_CHECKPOINT, never
  a completion — and frees its key for the owning chat's
  continuation submission. `gk resume --job` clears the request
  and withdraws the signal. The signal file may also be
  created directly by a host-local actor at the same per-job
  path (the spec 011 host guard does exactly this when a
  shutdown is imminent — no gatekeeper round-trip), and the
  harvest does not distinguish which side raised it; the
  gatekeeper cannot pause an arbitrary running script
  mid-flight, so the hold is cooperative by design (spec 013
  states the limits in full).
- **Lane priority locks (spec 015)** — `gk lock --lane
  <lane> --program <program>` gives one program exclusive
  dispatch on a lane: while the lock is ACTIVE, only the
  holder's submitted jobs are dispatch-eligible there.
  Non-holder jobs are not refused, cancelled, or reordered —
  they wait (their watermarks escalate), and priority never
  trumps the lock: a non-holder's human priority of 200
  waits while the holder's auto priority 0 dispatches. Lock
  state lives in `LOCKS.json` beside `PAUSED.json`, written
  only through the FR-013 verified-write path; no file means
  no locks and pre-015 behavior exactly. One holder per
  lane: a second acquire is refused (exit 3) naming the
  holder, its state, and its expiry, in both the ACTIVE and
  SUSPENDED states; re-acquire by the holder renews the TTL
  (state preserved). `gk unlock` releases — by the holder,
  or by the operator with `--force` (ledgered
  `lock_released_override`, with an outbox notice to the
  holder's registered chat); a non-holder release without
  `--force` is refused. `gk lock --suspend` / `--resume`
  pause and restore enforcement without surrendering the
  claim. Every lock carries a TTL (config
  `lock.default_ttl_min`, default 240; `lock.max_ttl_min`,
  default 1440; `--ttl-min` outside [1, max] refused);
  expiry is evaluated at dispatch time and behaves exactly
  like a release (`lock_expired` ledgered, holder notified)
  for suspended locks too. Maintenance jobs bypass locks
  exactly as they bypass the pause, and every such dispatch
  under an ACTIVE foreign lock is ledgered
  `lock_bypassed`. Fleet pause/focus never mutate lock
  state, and host drains neither block nor clear locks;
  admission is a conjunction of all gates. `gk status`
  renders a LOCKS section in all modes and marks lock-held
  waiting jobs; `gk lock --list` prints the section
  standalone. Running runs are never touched by any lock
  transition.
  `purge` removes terminal (completed/cancelled) job records, dry-run
  unless `--yes`; `--older-than-days N` restricts it to records
  created more than N days ago, and `--ledger` also archives
  `ledger.jsonl` to a timestamped file and starts a fresh one. Job
  records carry the harvested markers and log tails — they are the
  evidence trail — so purge with an age filter is the norm, not the
  exception.
- **Push notifications** — `submit` and `adopt` accept
  `--notify-chat <chat-id>`. A job carrying one produces pushes —
  started (with its expected length), completed (conclusion plus
  verdict highlights from the harvested markers), W2-escalated —
  which `outbox` prints grouped by chat from the ledger (history
  from before the mechanism existed is never replayed). Delivery
  is the coordinator forwarding each group into the named chat;
  `outbox --ack` then advances the cursor. Programs register once
  and stop polling.

- **Host drain, the guard, guard staleness (spec 011
  Phases 2–3)** — `gk drain --host` is the host-scoped,
  active drain: it signals the host's running
  checkpoint-capable jobs through the same hold machinery,
  sets the host's **drain hold** (a per-host field distinct
  from the FR-006 going-down hold; `gk host-resume` clears
  both), waits out each lane's window (`drain.window_s`,
  default 120, `drain.per_lane_window_s` overrides), driving
  the same `watch_pass` a sweep runs so the harvest is
  identical, and prints CLEAN (exit 0) or INTERRUPTING
  (exit 1) with every job classified. Spec 013's
  `pause --drain` stays what it was — global and passive;
  admission is the conjunction of both layers and neither
  verb clears the other's state. The host guard
  (`runner/guard/`) runs the same drain on the machine when
  a shutdown is imminent and writes the going-down record
  the Phase 1 ingest consumes (the backchannel agent merges
  the guard's files into the heartbeat payload verbatim).
  The guard's beat rides that payload as `guard`; the sweep
  stores it on the host's liveness record and ledgers
  `guard_stale` / `guard_recovered` on transitions
  (`drain.guard_stale_after_s`, default 180), rendered in
  `gk status` — a stopped guard is visible, never silent.

One platform caveat, observed in the field: GitHub's run-level status
for a run can lag or sit at `queued` while the job's log already holds
the complete result. That is why the gatekeeper stores the log and the
markers at completion and treats markers as the verdict source — if the
API state and the log disagree, believe the log.

## Workflow inputs

| Workflow | Inputs | Notes |
|---|---|---|
| `run.yml` | `cmd_b64` (required), `cwd`, `hold_file` | PowerShell, base64 of the script; `hold_file` (spec 013) is the job's checkpoint hold-signal path, exposed as `$env:SYNCYARD_HOLD_FILE` |
| `wsl.yml` | `cmd_b64` (required), `cwd`, `hold_file` | bash in WSL2, base64 of the script; `hold_file` (spec 013) exposed as `$SYNCYARD_HOLD_FILE` |
| `grab.yml` | **`path` only** | Uploads one file from the machine as artifact `grabbed-file`. **No `cmd_b64`, no `cwd`, no script.** |

The grab difference matters: a grab job is submitted with
`--grab-path "<file path on the machine>"` instead of `--script`. File
returns remain a deliberate step: the gatekeeper watches grab runs and
harvests their markers, but does not chain grabs automatically.

## Cron

```cron
*/5 * * * * /usr/bin/env python3 /path/to/gatekeeper/gk.py sweep >> /path/to/gatekeeper/sweep.log 2>&1
```

Every 5 minutes matches the fleet poll floor; the sweep self-locks, so
an overlapping invocation is a no-op.

## The runner watchdog is separate

The desktop-side runner watchdog (`runner/runner-watchdog.ps1`) keeps
runner listeners alive on the machines. It is installed during the
setup session described in [runner/SETUP.md](../runner/SETUP.md) — it
is not installed by anything in this folder.

## What the gatekeeper does not do

- **No webhook push (yet).** Watching is the 5-minute batched sweep. A
  later phase can add push notifications to cut polling further.
- **No cancellation, ever.** Watermark breaches are recorded and
  flagged; the check-before-cancel ladder's human steps and any
  cancellation stay with the dispatching program / the owner.
- **No automatic grab chaining.** File returns are dispatched as their
  own jobs.
- **No probe jobs.** The standard's machine-state probes are a
  coordination decision, not something the sweep fires on its own.

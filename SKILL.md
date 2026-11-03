---
name: syncyard
description: Dispatch and watch jobs on the operator's own machines through the Syncyard gatekeeper (AXIOVEX/syncyard) — self-hosted GitHub Actions runner lanes with a shared API budget. Use whenever work must run on the operator's desktop or laptop: builds, probes, renders, hardware sessions, file returns. Covers submit/adopt/status, lane routing, marker verdicts, API discipline, and watermark escalation etiquette.
---

# Syncyard — agent skill

Syncyard is the one door between agents and the operator's own machines.
Programs do not dispatch GitHub Actions workflows directly, and they never
poll runs on their own. They submit job requests to the **gatekeeper**
(`gatekeeper/gk.py`), which dispatches within the shared API budget,
assigns lanes, watches every run in one batched sweep, and records
verdicts. If you are an agent with work for the operator's machines, work
through the gatekeeper.

Repository: `AXIOVEX/syncyard`. The live gatekeeper copy runs on the
operator's control machine and tracks this repository's releases.

## Keeping this skill current (standing rule)

**This file is updated in the same change as any change to behavior,
commands, lanes, workflows, or operations.** A change that leaves
this skill stale is incomplete. The project is governed by spec-kit
+ AEE: a constitution (`.specify/memory/constitution.md`) and
numbered specs under `specs/`, each requirement a claim with
evidence and a falsification test. Changes cite the spec they
implement; behavior with no spec gets a spec in the same change.

## Commands

```bash
GK="python3 gatekeeper/gk.py"

# Submit a Windows PowerShell job
$GK submit --program <your-program> --workflow run.yml \
    --lane <quick|normal|long|gpu> --script <script.ps1> \
    --cwd '<windows cwd>' [--expected-min N] --key <unique-key>

# Submit a WSL2 bash job
$GK submit --program <your-program> --workflow wsl.yml \
    --lane <lane> --script <script.sh> --cwd '<wsl cwd>' \
    [--expected-min N] --key <unique-key>

# Return one file from the machine as an artifact (no script; path only)
$GK submit --program <your-program> --workflow grab.yml \
    --lane quick --grab-path '<path on the machine>' --key <unique-key>

# Register a run that was dispatched outside the gatekeeper (watch only)
$GK adopt --run-id <id> --program <your-program> --workflow <wf> \
    --lane <lane> --key <unique-key>

# The board: every tracked job, its state, flags, and verdict markers,
# plus queued/running counts and an ISSUES section (see below)
$GK status

# The board + project-repo health: latest CI, open Dependabot alerts
# and PRs (three API calls; the default status above is local-only)
$GK status --repo

# Hold ALL program dispatch (the watch pass keeps running, so verdicts
# and watermarks stay current; jobs already dispatched are untouched —
# the gatekeeper never cancels). Operator maintenance jobs submitted
# with --maintenance still dispatch during a pause.
$GK pause --reason "WSL cleanup + compact"
$GK resume

# Focus (spec 013): pause everything EXCEPT the selected jobs.
# Selectors are repeatable and combinable; a key selector matches
# the key or any key under it as a prefix. While focused, only
# matching jobs (plus maintenance) dispatch. `gk resume` clears
# pause and focus together.
$GK focus --except-program gpu-unlock --reason "only the GPU work runs"
$GK focus --except-key probe- --except-chat <chat-id>

# Drain (spec 013): stop starting new work and let what is
# executing finish; when the last non-admitted job completes the
# pause settles itself (DRAINING shows in status meanwhile).
# Composes with focus: the pipeline drains toward the focus set.
$GK pause --drain --reason "wind down after this batch"
$GK focus --except-program gpu-unlock --drain

# Priorities (spec 013): higher dispatches first within the
# waiting set. Set one at submit, or change it live while the
# job is still waiting (a dispatched or finished job refuses).
$GK submit ... --priority 10 --key <unique-key>
$GK priority <job-id-or-key> 10

# Checkpoint hold (spec 013): ask a job to stop itself at its
# next checkpoint. The gatekeeper writes a hold-signal file on
# the job's venue; a job that polls $SYNCYARD_HOLD_FILE, prints
# CHECKPOINT_PAUSED and exits 86 is harvested as
# held_at_checkpoint — not a failure. Release clears the signal;
# the owning chat resubmits to continue.
$GK hold <job-id-or-key>
$GK resume --job <job-id-or-key>

# Lane priority lock (spec 015): one program holds a lane so
# no other program's jobs can dispatch on it — priority can
# never trump a lock (a non-holder's priority-200 job waits;
# the holder's priority-0 job dispatches). Non-holder jobs are
# not refused or reordered; they wait, and their watermarks
# escalate as usual. Re-running lock as the holder renews the
# TTL. Suspend pauses enforcement but keeps the claim (new
# acquires by others stay refused); resume restores it without
# re-competing. Every lock expires on its TTL (default 240
# min, max 1440) exactly like a release, with a notice to the
# holder's chat. Maintenance jobs bypass locks (each bypass
# is ledgered). Running runs are never touched.
$GK lock --lane gpu --program <your-program> [--ttl-min N] [--reason TEXT]
$GK lock --lane gpu --program <your-program> --suspend
$GK lock --lane gpu --program <your-program> --resume
$GK unlock --lane gpu --program <your-program>
$GK lock --list

# The hard halt: sweeps no-op entirely (no budget spent, no watch)
# until started again. Start clears both a stop and a pause.
$GK stop --reason "fleet maintenance"
$GK start

# Housekeeping: remove terminal (completed/cancelled) job records.
# Dry-run unless --yes; job records carry the harvested evidence, so
# purge with an age filter. --ledger also archives the ledger.
$GK purge --older-than-days 7
$GK purge --older-than-days 7 --yes

# Push notifications: add --notify-chat <chat-id> to any submit or
# adopt and the coordinator is told when the run starts (with its
# expected length), completes (with the marker verdict), or
# escalates — programs register once and never poll. The outbox
# lists the pushes owed, grouped by chat; --ack advances past them
# once they have been delivered.
$GK outbox
$GK outbox --ack

# Host liveness (spec 011): status shows each host UP/DOWN with
# its heartbeat age (the sweep keeps the gatekeeper's own
# last-seen record). When a host reports it is going down, the
# gatekeeper holds dispatch to that host; once the host is back
# and verified, clear the hold (Phase-1 stand-in for the
# recovery handshake):
$GK host-resume <host>

# Controlled reboot (spec 011 FR-015): refuses while jobs are
# active on the host unless --force (the polite path is the
# drain or waiting); on acceptance the going-down record is
# written first, then the host is signaled — via the
# backchannel when its heartbeat is fresh, else via a
# maintenance dispatch on an online runner — with the grace
# delay from config `reboot.grace_s` (default 60 s). Exit 3 =
# refused (active jobs named), 4 = host unreachable (no route
# live; nothing else is written). The host stays held for
# dispatch until it returns and the hold is cleared.
$GK reboot --host <host> [--force] [--delay <seconds>] [--requested-by <chat-id>]

# Host drain (spec 011 FR-007, Phase 2): the polite prelude to
# restarting or shutting down one host. Signals every running
# checkpoint-capable job on the host (spec 013 holds), sets the
# host's drain hold — a hold distinct from the going-down hold;
# `gk host-resume <host>` clears BOTH — then waits out each
# lane's checkpoint window (config `drain.window_s`, default
# 120 s, per-lane overrides in `drain.per_lane_window_s`) and
# prints a per-job verdict. Exit 0 = DRAIN CLEAN (every job
# checkpointed, finished, or ended), 1 = INTERRUPTING (jobs
# named uncheckpointed, with the reason), 2 = unknown host.
# This is NOT spec 013's `pause --drain`: that one is global
# and passive (it waits for the pipeline to empty); this one is
# per-host and active, and neither clears the other's state.
$GK drain --host <host> [--window <seconds>] [--requested-by <chat-id>]
```

- Every job carries a **unique `--key`**. Submitting a key that is already
  active is refused and prints the existing job — that refusal is the
  duplicate-dispatch pre-check working. Never work around it by changing
  the key for the same work; check `gk status` instead.
- `--expected-min` matters for `long` and `gpu` jobs: health watermarks
  are judged against the declared expectation before lane defaults.
- **Priorities auto-optimize by default (spec 013).** A job with no
  explicit priority is ordered by the sweep's optimizer: shorter
  declared expectations first, a small bonus for fitting the lane,
  and steady aging (+1 per 10 minutes waited) so nothing starves —
  an auto job's effective priority caps at 100. An explicit
  `--priority` or `gk priority` value is **human-set and pinned**:
  the optimizer never moves it, it wins any tie against an
  auto-assigned value, and a value above 100 outranks every auto
  job permanently. `gk status` lists the waiting set in effective
  dispatch order with each job's effective priority and source
  (`human`/`auto`), so the optimizer's hand is always visible and
  overridable. Pause, focus, drain, and host holds are admission
  decisions and outrank the optimizer absolutely: a held job never
  dispatches, whatever its effective priority.
- **Checkpoint holds are cooperative (spec 013).** The gatekeeper
  cannot pause an arbitrary running script mid-flight. The signal
  file can be raised by the gatekeeper (`gk hold`), by the job
  itself, or directly by a host-local actor (for example the
  spec 011 host guard when a shutdown is imminent) creating the
  file at the same per-job path — no gatekeeper round-trip — and
  the harvest is identical whichever side raised it. A job that
  wants to be holdable polls the file named by `$SYNCYARD_HOLD_FILE`
  between steps; when the file exists it prints `CHECKPOINT_PAUSED`,
  exits with code 86, and the sweep records `held_at_checkpoint`
  (the run's conclusion is kept verbatim but is not the verdict).
  A signal created after a job's run started reaches that job only
  if its venue directory already exists (the workflows create it
  before the job starts); otherwise the hold takes effect for the
  job's next dispatch. Continuation is a fresh submission by the
  owning chat under a new key, resuming from its own checkpoints.
- **Lane locks are claims, not priorities (spec 015).** A lock is
  per lane and per program, held in `LOCKS.json` beside the pause
  record. While your lock is ACTIVE, only your program's jobs
  dispatch on that lane; while another program's lock is ACTIVE,
  yours wait — submit as usual and let the watermarks speak; do
  not raise priority to fight a lock, it cannot win. Release
  what you take (`gk unlock --lane … --program <you>`), renew by
  re-running `gk lock` before the TTL runs out, and use
  `--suspend` / `--resume` on your own lock when you want the
  lane to flow without surrendering the claim. If someone else
  suspends, resumes, or releases your lock it is always an
  operator override (`--force`): it is ledgered distinctly and
  your registered chat gets an outbox notice — your own actions
  are never self-notified. Pause and focus never change lock
  state: locks pause with the rest of the flow and are exactly
  as you left them when the fleet resumes, and admission is a
  conjunction — a job must pass focus AND (on a locked lane) be
  the holder's. `gk status` shows a LOCKS section in all modes,
  and waiting jobs held by a lock are marked
  `[lock-held by <program>]`.
- `gk cancel <id-or-key>` is local bookkeeping for jobs that were never
  dispatched. It never cancels a GitHub run. There is no command in this
  system that cancels a running job on a timer — see escalation below.
- The status board's **ISSUES** section is the operator's problem
  list: watermark breaches and escalations, log harvests that failed
  or are still pending, a dispatch stuck needing a human, and
  completions with no marker verdict. Read it before concluding the
  fleet is healthy.
- **Logging is two-layered**: `ledger.jsonl` (append-only JSON, one
- Every gatekeeper state write is write-verify-retry (spec 001
  FR-012): verified on read-back or it fails loudly; never trust
  an unverified write, and never treat a 0-byte state file as real. A failed update leaves the last valid
  record standing; only true husks are removed. Record updates are
  atomic swaps (FR-013): a record's file is only ever replaced by
  renaming a fully verified temp file over it — the final path is
  never opened for writing, so a failed save cannot destroy the
  standing record; inside a sweep, a failed per-record save is
  ledgered (`save_failed`) and the pass continues, and a job whose
  pre-dispatch save failed is skipped, never dispatched unrecorded.
  record per event — the machine record) and `gatekeeper.log` (one
  human-readable line per sweep, rotating at 1 MB). At completion the
  sweep harvests a run's log once; if the CLI read fails it falls
  back to the REST logs archive and records which source supplied
  the log.
- A **daily health check** reviews the board, the ledger's errors,
  CI on main, and Dependabot. Green Dependabot patch/minor bumps of
  GitHub Actions are merged by the check; major bumps are reported
  to the operator, never merged.
- **Pause** (`gk pause` / `gk resume`) holds dispatch of program
  jobs while the operator works on the machines themselves. Status
  shows the pause banner with its reason; sweeps print PAUSED.
  Maintenance submissions (`submit --maintenance`) bypass the pause,
  the active cap, and lane serialization — never the duplicate
  check. Use maintenance only for operator work on the fleet itself
  (cleanup, repair, inventory), never for program jobs.
- **Stop and start** (`gk stop` / `gk start`) are the hard lifecycle
  controls: while stopped, a sweep returns immediately — no API
  budget spent, no watch pass, no dispatch — and status shows the
  STOPPED banner. `start` clears both a stop and a pause. Neither
  state ever touches jobs already dispatched.
- **Purge** (`gk purge`) removes terminal (completed/cancelled) job
  records — dry-run unless `--yes` is passed. `--older-than-days N`
  restricts it to records created more than N days ago;
  `--ledger` also archives `ledger.jsonl` and starts a fresh one.
  Records hold harvested markers and log tails, so age-filtered
  purges are the norm.
- **Push notifications** (`--notify-chat` on submit/adopt;
  `gk outbox`) replace polling: a registered job produces a push
  when its run starts (with the job's expected length), completes
  (conclusion plus verdict highlights from the harvested markers),
  or escalates at a W2 watermark. The sweep's outbox prints the
  pushes owed, grouped by chat, from the append-only ledger —
  history from before the mechanism existed is never replayed —
  and the cursor advances (`outbox --ack`) only after the
  coordinator has delivered them into the named chats. Pushes ride
  the existing sweep cadence; there is no server to run.
- **Host liveness and going-down** (spec 011 FR-001/FR-006): the
  sweep reads the backchannel heartbeat once per pass and keeps a
  per-host last-seen record; `gk status` renders each host **UP**
  or **DOWN** (heartbeat older than `liveness.down_after_s`,
  default 900 s) — DOWN means it stopped reporting, nothing more.
  A going-down record from a host ledgers `host_going_down`,
  annotates the affected jobs `interrupted_by: "host-shutdown"`,
  pushes one outbox notice per affected registration, and holds
  dispatch to that host (queued jobs wait; nothing reroutes, and
  the gatekeeper never cancels or re-dispatches). The hold clears
  with `gk host-resume <host>` once the host is back — the Phase-1
  stand-in for the recovery handshake.
- **The host guard and the drain** (spec 011 Phases 2–3,
  Amendment 2): the guard (`runner/guard/`, a windowless sibling
  of the tray with no UI and no exit command) watches the
  running-job registry the transport workflows maintain. While
  checkpoint-capable jobs run it pre-registers the OS shutdown
  block with a count-naming reason; on a shutdown/restart/logoff
  signal it writes the going-down record FIRST (verified write;
  the backchannel agent merges it into the heartbeat payload —
  the fastest channel the Phase 1 ingest reads), raises the
  running jobs' hold files locally (no gatekeeper round-trip),
  vetoes the session end, and drains until the registry empties
  or the checkpoint window expires, then releases the block and
  re-issues the pending action (a restart, unless it was a
  logoff). Delay, never prevent. The guard beats every ~10 s;
  the beat rides the heartbeat as `guard`, and a beat older than
  `drain.guard_stale_after_s` (default 180 s) ledgers
  `guard_stale` and shows in `gk status` — a stopped guard is a
  visible loss of protection, never a silent one. `gk drain
  --host` is the operator-invoked twin of the guard's drain:
  same signals, same harvest, a printed verdict.

## Periodic status-check duty (spec 009 FR-005)

**Every agent that uses Syncyard checks Syncyard status before
submitting desktop work, and at least once per hour during an
active desktop campaign.** Use `gk status` when the gatekeeper is
reachable; otherwise use the failsafe backchannel's read-only
status (`backchannel/bc.py heartbeat` / `bc.py status` — see the
Failsafe Backchannel section below). A stale backchannel
heartbeat (older than 5 minutes), a down tray, or a down runner
is **reported to the Desktop Access chat**
(`01e9a059-0626-4470-b012-2784f420fe5a`) — never worked around,
never routed around silently, and never a reason to improvise an
alternate route to the machines.

## Lane routing

Every dispatch declares a lane. **No lane declared = not dispatched.**

| Lane | Expected duration | Use for |
|---|---|---|
| `quick` | under ~2 min | probes, status checks, small transfers |
| `normal` | ~2–30 min | builds, installs, packaging |
| `long` | over ~30 min, or unknown | batch stages, scoring runs, renders |
| `gpu` | any duration | any job that needs a GPU |

- **GPU overrides duration.** A 30-second GPU probe goes to `gpu`, never
  to `quick`. A long GPU render goes to `gpu`, never to `long`.
- **Unknown duration goes to `long`.**
- A job that overruns its lane's norm twice gets re-laned in its program's
  briefs (quick → normal, normal → long), and the overrun is reported.
- Detached background processes on a machine are not Actions jobs and do
  not take a lane; the status checks *about* them are `quick` jobs.
- Jobs expected to exceed 60 minutes are announced to the coordination
  point before dispatch, with their expected duration.

## Windowless supervision (spec 007)

The runners run under the **Syncyard Tray** supervisor
(`runner/tray/`), never as hand-started console windows:

- The tray is a per-user app with no window of its own. It starts at
  logon (HKCU Run key), shows per-lane state, starts every runner
  hidden, restarts a dead runner up to 3 times per hour and then
  alerts OWNER NEEDED, and hides stray runner console windows. Its
  menu offers Start all and Stop all above the per-lane controls.
  Its icon is the project mark (embedded in the program): clean
  when every runner is up, amber slash when some are down,
  grayscale with a red slash when none are up or one has faulted.
- The tray's own behavior is managed in its Settings dialog
  (spec 014): supervision on/off, the restart cap, the refresh
  cadence, stray-console hiding, autostart (read live from the
  HKCU Run key; written only from the dialog), and per-lane
  supervision toggles — persisted to `tray-settings.json` beside
  the tray exe and applied on save, no reinstall. Per-lane
  Start / Stop / Restart buttons in the dialog act immediately.
- The tray also displays fleet state read-only (spec 014): the
  gatekeeper's mode (spec 013: RUNNING / PAUSED / FOCUSED /
  DRAINING), the waiting queue in dispatch order with each
  job's priority and its human/auto source, and the focus
  selectors — in the menu header, the tooltip, and the
  dialog's fleet section — from a snapshot file at
  `C:\Users\trist\Development\Syncyard\status\fleet-status.json`
  when a publisher writes one. No publisher exists yet
  (spec 014 FR-009), so the section normally reads
  unavailable with its reason; the tray never edits fleet
  state and never invokes the gatekeeper.
- The tray icon also answers "are we connected?" (spec 018):
  a link mark in the icon's corner — solid when connected,
  hollow amber when stale, broken with the mark desaturated
  when disconnected — judged only from signals already on
  the host: the backchannel agent's heartbeat file (default
  `~/syncyard-backchannel/repo/channels/syncyard-dev/heartbeat.json`
  in the owner's WSL distro, read through the `\\wsl.localhost`
  share; the path is a tray setting) and, when a publisher
  exists, the fleet snapshot's stamp. The runner slash is
  unchanged: health and connection are separate dimensions.
  Thresholds are tray settings (stale after 180 s, down
  after 900 s — the down bound mirrors the gatekeeper's
  host liveness limit), edited in the Settings dialog's
  Connection section; every transition is written to
  `tray.log` with the signal age that caused it. The
  indicator is display-only: it never probes the network,
  never writes the signal files, and never gates
  supervision. Until the FR-009 publisher ships, the signal
  set is the backchannel heartbeat only, and the menu and
  dialog say exactly that.
- Runners stay in the owner's interactive session on purpose: jobs
  need his WSL distro, GPU, audio, and bench devices, which a Windows
  service in session 0 cannot reliably reach. Do not register the
  runners as services.
- Runner directories live under the owner's profile at
  `%USERPROFILE%\Development\Syncyard\runners\<lane>` — never in
  the drive root. The tray's runner table points there; if the
  directories move, update the tray and reinstall it.
- Install or reinstall with `runner/tray/install-tray.ps1` in an
  owner session (compiles with the in-box csc; no SDK needed). The
  tray supervisor replaces the `runner-watchdog.ps1` scheduled task —
  do not install that task while the tray is active.
- A job script that starts a program starts it hidden or headless
  and cleans it up when the job ends; a visible window or a lingering
  process after a job is a defect in that script.
- Long-lived agents on Linux or WSL targets run as systemd units.

## Marker verdicts

**A job's verdict comes from the marker lines it prints in its own log —
never from the GitHub Actions success color alone.** Platform run status
has been observed frozen at "queued" long after a job's log held its
complete result; the log is the record.

- Write every job script to print unambiguous marker lines with its
  results, for example `PROBE_DONE gpus=2 free_gb=212` or
  `BUILD_VERDICT: PASS`. Markers are lines that begin with an
  `ALL_CAPS_` token; the gatekeeper harvests exactly those.
- Read verdicts from `gk status` (completed jobs show their marker
  counts) or from the job file the gatekeeper keeps, not from the
  Actions web UI.
- A run that "failed" according to a client printout but whose markers
  say otherwise gets cross-checked against the actual job log before
  anyone believes either.

## API discipline

The GitHub REST quota (5,000 calls/hour) is one account-wide bucket
shared by every program. The gatekeeper exists so the fleet spends it
once, centrally. Your obligations:

- **Never poll a run yourself.** The gatekeeper's sweep (every
  5 minutes) is the only watcher. If you truly must look, one batched
  `gh run list` covering all your runs, no faster than every 3–5
  minutes — never a per-run loop, never `gh run watch` left running
  (its default interval is ~3 seconds).
- **Before any burst of API calls** (more than ~10), run
  `gh api rate_limit` — the endpoint is free. Core remaining **below
  1,000**: stand down to minimum cadence and say so in your report.
  **Below 500**: completion-critical calls only, at most one per
  5 minutes.
- **A 403 rate-limit response is a hard stop** for that scope. Stop, do
  not retry through it, report the rate-limit snapshot and reset time.
  Resume only after the reset *and* a fresh headroom check.
- Dispatch only needed jobs. No speculative duplicates; cancel your own
  superseded queued work through your coordinator.

## Watermarks and escalation etiquette

Each lane has warning watermarks for queue wait and execution time
(the table is in docs/STANDARD.md). The gatekeeper records breaches; it
**never cancels anything**. Your conduct when a breach involves your job:

- **Queued breach = starvation.** Your job is the victim, not the
  problem. Never cancel it. The lane ahead of it is what gets escalated.
- **Executing breach = stall suspect.** Check step progression in the
  record: a job whose step advanced is slow, not stalled — say so and
  stand down to second-warning watch.
- If machine state is needed to tell slow from wedged, that takes a
  quick-lane probe job (process list, GPU state, disk, venue log tail).
  A probe that never returns reclassifies the event as a machine-level
  problem.
- Escalate to the coordination point at the second warning, with the
  evidence: run id, lane, dispatch time, watermark breached, signals
  observed, probe result.
- **Cancellation authority is narrow:** only your own program's
  coordinator, after a recorded check — or the owner. Never another
  program's watcher, never automatically, never for a queued breach.
  A cancelled job is cancelled *with its check record attached*, so it
  can be re-dispatched from a known state.

## Board (hardware) scenario jobs

Jobs that touch physical hardware (development boards on a bench
station) follow an extra discipline on top of everything above:

- They run **one at a time under a claim**: the job registers its claim
  before its first hardware step and releases it with actual times when
  done. A conflicting active claim holds the job; claims are never
  silently stacked.
- The first step is always a **reachability and identity proof** in the
  execution context that can actually reach the hardware (some networks
  are visible only from a specific user environment on the machine, not
  from the runner's default context). Failure aborts before any claimed
  act.
- Safety gates that protect the hardware are **hard steps inside the
  job**: a failed gate aborts before any step that depends on it.
- Physical acts are **exactly-once**: jobs are idempotent or check a
  consumed-marker before repeating an act.
- Steps only a human can do (power cycles, cable moves) are terminal
  states: the job stops and reports "human step required". No retry
  loops against physical hardware.
- The job **harvests before it exits**: all logs and captures are copied
  off the device before the job ends; results left only on the device
  mean the job failed.

## Break-glass

The operator keeps a separate, frozen emergency admin path outside
Syncyard, for the case where the Actions route itself is broken. It
carries **no job traffic, ever** — not for convenience, not for speed,
not because the queue is long. If you believe the emergency path is
genuinely needed (the gatekeeper cannot dispatch and the runners cannot
be reached), stop, say exactly that, and let the owner decide. Do not
improvise alternate routes to the machines.

## Failsafe Backchannel (spec 009)

Syncyard has a failsafe backchannel for the case where Syncyard
itself — the runners, the tray, or gatekeeper dispatch — is down
or blocked. It is deliberately narrow:

- **Channel**: `syncyard-dev` in the **private** repository
  `tbitcs/syncyard-backchannel` —
  `channels/syncyard-dev/cmd.json`, `out.json`, and
  `heartbeat.json`.
- **Owner**: the **Desktop Access chat only**
  (`01e9a059-0626-4470-b012-2784f420fe5a`). It is the sole poster
  to the channel. No other chat or agent may post to it.
- **No program job traffic, ever.** The backchannel carries only
  Syncyard development and recovery work (for example: restart
  the packaged tray in the owner's session, read `tray.log`,
  restart a runner lane, run read-only network diagnostics).
  Programs keep using the gatekeeper in all circumstances.
- **Desktop side**: `backchannel/agent.sh` runs as a WSL systemd
  user service (`syncyard-backchannel.service`) under the owner's
  WSL user, independent of the runners, tray, and gatekeeper. It
  syncs over git (no REST quota), executes only a command whose
  id is new and whose sha256 matches its body's exact bytes
  (verified and run from a file, never a shell variable — so
  multi-line `send --file` bodies execute, and only genuine
  mismatches are refused), writes `out.json`
  matched by id, and heartbeats every poll cycle (~60 s). A
  `channels/syncyard-dev/STOP` file halts execution while the
  heartbeat continues. Its log is
  `~/syncyard-backchannel/agent.log` on the desktop. It never
  writes gatekeeper queue records. Execution is bounded by the
  envelope's `timeout_s` plus a SIGKILL escalation for bodies
  that ignore SIGTERM, and `out.json` records the body's real
  exit status (65 = agent sha refusal, 124/137 = wrapper
  timeout kill).
- **Client**: `backchannel/bc.py heartbeat|status|send|wait`
  (Python stdlib only). `send` packs a command through the GitHub
  contents API — no local git, so it works during VM write waves —
  refuses to overwrite an unacknowledged pending command unless
  `--force`, stamps a unique id + the sha256 of the body, and
  verifies by reading `cmd.json` back.
- **Your part**: read-only. Use `bc.py heartbeat` / `status` for
  the FR-005 status check when the gatekeeper is unreachable,
  and report a stale heartbeat (>5 min) or a down tray/runner to
  the Desktop Access chat. Operator doc: `backchannel/README.md`.

## Security rules that bind agents too

- The runner workflows are **dispatch-only** (`workflow_dispatch`).
  Never propose, write, or merge a workflow that binds self-hosted
  runners to `pull_request`, `pull_request_target`, `issues`, or any
  event an untrusted user can trigger.
- CI runs on GitHub-hosted runners only.
- Never commit a token or credential anywhere in this repository.
  Registration tokens are fetched at setup time and used once.
- Runners register to this repository only, never organization-wide.

## Spec 010 — public release program (specified 2026-10-30)

Owner direction: make Syncyard a general open-source project —
a real configuration system (lane count/names and adopter knobs
as config, spec 010 FR-001/002/007), a tested host matrix
(Windows, WSL2, macOS, Linux, Docker; FR-003/004), the
backchannel kept permanently robust (FR-005), an agent skills
pack with Meta Muse as the primary vehicle (FR-006), tests for
all of it (FR-008), the security posture shipped as a document
(FR-009), and release 0.3.0 when green (FR-010). A GitHub
`gh`-adoption pitch brief is prepared under FR-011 and is
**not sent** without the owner's explicit word. The Phase 1
config cutover LANDED 2026-10-31: the fleet gatekeeper binds
its constants from the effective config (fleet
`syncyard.yaml` beside gk.py, values identical to the
constants it replaced — golden-proven); spec text lives at
`specs/010-public-release/`.

Amendment 1 (2026-10-30): **FR-012** — Syncyard also ships as
a GitHub CLI extension from `AXIOVEX/gh-syncyard` (`gh syncyard
...`); a thin Go dispatcher over the Python gatekeeper,
cross-compiled release binaries via GitHub's official
precompile action. **SHIPPED 2026-10-31: v0.1.0 released
and install-proven** (`gh extension install
AXIOVEX/gh-syncyard`; `gh syncyard status` served the live
fleet gatekeeper). Gatekeeper lookup: $SYNCYARD_GK,
$SYNCYARD_HOME, ~/syncyard, ~/.syncyard, beside the binary,
./gatekeeper. **FR-006 editions are ranked by agent
market share:** Meta Muse primary, then Claude Code, GitHub
Copilot, OpenAI Codex/ChatGPT, Cursor, Gemini/Antigravity,
generic + CLI-only. Phase 1 slice 1 has landed additively:
`config/syncyard.example.yaml` + `gatekeeper/config.py`
(stdlib loader, validation, golden-tested against the live
constants by `gatekeeper/test_config.py`); `gk.py` does not
read config yet.

Phase 4 (FR-006) has landed: the adopter-facing skills pack
lives under `skills/` — eight editions (Meta Muse primary,
then Claude Code, GitHub Copilot, OpenAI Codex/ChatGPT,
Cursor, Google Gemini/Antigravity, generic-agent, CLI-only)
generated from the single shared source
`skills/_source/contract.md` by `skills/build_skills.py`, with
the T016 currency battery (`skills/test_skills.py`) in CI.
This fleet SKILL.md remains the operator's own copy; adopter
editions derive from the shared contract, and behavior changes
update both in the same change.

## Spec 011 — shutdown protection and recovery (specified 2026-10-31, draft)

Owner direction: when a host machine shuts down, Syncyard
must handle outstanding work — delay the shutdown while a
bounded drain checkpoints active jobs and supervised work,
record a going-down record with the affected run ids, and on
the host's return recover runners without an interactive
logon, relaunch supervised campaigns from their checkpoints,
reconcile every interrupted run against GitHub's truth, and
issue a recovery report that states any loss plainly. The
protection level is **delay + drain + checkpoint + notify —
never prevent**: nothing in the spec vetoes a forced
shutdown, a power loss, or an insisting administrator, and
its text says so in those words. The spec also generalizes
the proven scheduled-task anchor (supervisor + ensure +
heartbeat) into a first-class supervised-work vehicle, and
names its tension with spec 007 FR-003 (runners are not
Windows services) as an owner-gated evaluation, not a
presumption. Spec text lives at
`specs/011-shutdown-protection-recovery/`; Phase 1 (host
liveness + going-down ingest, T001–T003) shipped 2026-10-31.
**Amendment 1 (2026-10-31)** records the owner's
boot-autonomy ruling: Syncyard starts automatically on boot
with no interactive logon (the FR-009 gate, answered by
directive; autologon excluded), service-mode runners running
as the owner account are the selected shape with per-lane
`boot-runnable` / `logon-bound` labels, the backchannel
agent joins the boot set, and new **FR-015** adds `gk
reboot` — a controlled, drain-aware host reboot that writes
the going-down record before it signals anything.
Implementation follows in Phase 5 as amended. **T021
shipped (2026-11-01)**: `gk reboot` is implemented in the
gatekeeper (refusal / backchannel / maintenance / unreachable
pinned by golden scenario 7); the live fire drill on the
production host is T022, still open. **Amendment 2
(2026-11-02)** is the owner's speed directive ("block it,
pause all jobs, wait for checkpoints — as fast as we can"):
**Phases 2–3 built repo-side** — `gk drain --host` (T004–
T005: host-scoped, active, per-job verdicts; distinct from
spec 013's global passive `pause --drain`, and from the
FR-006 hold) and the host guard + going-down writer
(T006–T007: `runner/guard/`, the agent merge, guard-beat
staleness). T008 (the production deploy) and T005's owner
oracle pass remain field steps; nothing in Phases 2–3 is
fleet-deployed by this change.

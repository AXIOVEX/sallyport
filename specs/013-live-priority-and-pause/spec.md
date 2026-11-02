# Feature Specification: Live Priority and Pause Control

**Feature Branch**: `spec/013-live-priority-and-pause`

**Created**: 2026-11-02

**Status**: **Implemented in this change** (gatekeeper + config +
transport workflows + batteries). Fleet deployment is a separate,
later act under the standing deploy procedure and the owner's
review of this PR — nothing in this change touches the running
fleet, its pause record, or its queue.

**Direction (owner, 2026-11-02 ~02:08 ET)**: "add direct
capability to syncyard to change priorities of tasks while the
pipeline is running, and pause at any tasks checkpoint and
allow smooth transitions — in particular add a way to basically
say (pause all but this <task/side chat/etc>)".

**The limit, stated plainly up front**: the gatekeeper controls
*dispatch* — what starts, in what order, and what is admitted
while a hold stands. It cannot reach into an arbitrary running
script and freeze it: a running GitHub Actions job is another
process on another machine, and the gatekeeper has never had a
cancel or suspend verb (Part 4: watchers do not cancel runs).
So this spec's "pause at a checkpoint" is a *cooperative
contract*: the gatekeeper writes a hold-signal file on the
venue, a job that polls for it stops at its own next checkpoint
and says so with a marker, and the gatekeeper records that as a
distinct, non-failure state and holds the pipeline around it.
A job that never polls the file is never paused by it — it runs
to its natural end while the pipeline-level controls (focus,
drain, priority) shape everything around it. Every requirement
below is written inside that envelope.

## The gap

Today's controls are one bit wide:

- **Pause is all-or-nothing.** `PAUSED.json` either exists
  (every program job holds; maintenance flows) or it does not.
  The fleet's real use is narrower: the 2026-11-01 fleet pause
  carried a single named exception (one GPU-unlock job) while
  21 jobs waited, and the exception had to ride the
  `--maintenance` flag — an operator-work bypass — because the
  pause record's own `except_key` field is informational only:
  nothing in the dispatch pass reads it. "Pause all but this"
  is the operation the fleet actually performs, and it is not
  a command.
- **Dispatch order is arrival order.** Within a lane, submitted
  jobs dispatch strictly by `created_at`. When the pipeline is
  running and one queued job becomes urgent — a probe the
  owner is waiting on, a fix ahead of a batch — there is no
  way to move it ahead except cancelling and resubmitting
  (losing its place in the record) or waiting out the queue.
  And left entirely alone the queue is dumb in the other
  direction too: a two-minute probe submitted behind a
  six-hour render waits six hours, and a job nobody remembers
  can sit behind fresher work forever. The queue should
  optimize itself by default — shortest and oldest first —
  under the operator's explicit decisions, never over them
  (FR-012).
- **Stopping is abrupt at the pipeline level too.** A plain
  pause freezes admission instantly; there is no "let what is
  running finish, start nothing new, then settle" transition,
  so a planned stop either waits by hand or strands the
  operator's attention on the board until the last run lands.
- **A long job has no sanctioned way to stop early and be
  believed.** Jobs are checkpointed and resume-safe by fleet
  discipline, but a job that stops at its own checkpoint today
  exits as a failure-coloured run, and the gatekeeper records
  a plain completion. There is no signal path from the
  operator ("hold this one") to the running job, and no state
  that says "held at checkpoint, on purpose, resumable".

## User scenarios

1. **Pause all but one program.** A hardware session becomes
   the fleet's top priority. The operator focuses the pipeline
   on that program's chat: every other program's queued jobs
   hold, the focused program's jobs keep flowing, maintenance
   still flows, and status says FOCUSED with the selectors
   named.
2. **Reprioritize mid-flight.** A queued inventory job is
   suddenly the blocker for three other chats. The operator
   raises its priority from the CLI; the next sweep dispatches
   it ahead of older, lower-priority work in its lane. A
   priority change aimed at a job that already dispatched is
   refused, by name, because ordering cannot apply retroactively.
3. **Smooth stop for the night.** The operator drains: running
   jobs finish, nothing new starts, and the sweep itself
   settles the record to PAUSED when the last non-admitted run
   completes — no babysitting the board.
4. **Hold one long job at its checkpoint.** A multi-hour
   render is halfway through when priorities change. The
   operator requests a hold; at its next checkpoint the job
   sees the signal file, prints `CHECKPOINT_PAUSED`, and exits
   with the dedicated code. The gatekeeper records
   `held_at_checkpoint` — not a failure — and the owning chat
   later releases the hold and resubmits to continue from the
   job's own checkpoint state.

## Requirements

Each requirement carries a behavior statement, an acceptance
mode (**test** = an automated battery decides; **oracle** =
the owner or operator judges from presented evidence;
**gate** = a named human decision must precede the act), and
a *this does not establish* line bounding what passing it
proves.

### Part A — Focus mode ("pause all but X")

- **FR-001 — Focus is a first-class command over a superset
  pause schema.**
  *Behavior*: `gk focus` accepts repeatable selectors —
  `--except-key <key-or-prefix>`, `--except-program <program>`,
  `--except-chat <notify-chat-id>` — and writes the pause
  record as a superset schema: the legacy fields (`ts`,
  `reason`, and any operator fields already present) plus
  `mode`, `except_keys`, `except_programs`, `except_chats`
  lists, through the same verified-write path as every other
  gatekeeper state write (spec 001 FR-012/FR-013). A key
  selector matches a job whose key equals it or begins with
  it (prefix), so a chat can focus a whole job family.
  Reading a record in the current fleet shape — a single
  `except_key` string, no `mode`, operator fields such as
  `paused_by` / `amendment` — normalizes it to focused mode
  with a one-element key list, preserving every field.
  `gk focus` with no selector is a usage error (exit 2):
  holding everything is `gk pause`.
  *Acceptance*: test — the spec 013 battery pins the written
  schema, the normalization of a fleet-shaped fixture record
  (every original field preserved, `except_key` honored as a
  key selector), and the no-selector refusal.
  *This does not establish* that an excepted job *should*
  flow — the selectors are the operator's judgment; the
  mechanism only makes the record's stated exception
  effective.

- **FR-002 — Admission under pause and focus is one rule.**
  *Behavior*: while a pause record stands, the dispatch pass
  admits a submitted job exactly when it is a maintenance job
  (as today), or it is the gatekeeper's own `gk-reboot-`
  signal job under a host hold (spec 011 FR-015, unchanged),
  or it matches at least one focus selector (key / program /
  chat). With no selectors recorded — a plain pause — the
  rule reduces to today's behavior exactly. A stop record
  still halts everything non-maintenance in the pass (and
  sweeps still no-op while stopped). Host dispatch holds
  (FR-006 of spec 011) compose unchanged: focus never admits
  a job onto a held host.
  *Acceptance*: test — the battery pins admission by each
  selector kind, non-admission of non-matching jobs, the
  maintenance exemption under focus, and (via the unchanged
  golden battery) identical decisions when no pause record
  exists.
  *This does not establish* fairness between admitted
  programs — focus is deliberately absolute: non-matching
  work waits, however long the focus stands.

### Part B — Live priority

- **FR-003 — Priority is a job-record field with a source.**
  *Behavior*: every job record carries `priority` (an integer,
  default 0) and `priority_source` — `human` when the value
  was set explicitly (`gk submit --priority N` or
  `gk priority`, FR-004), `auto` otherwise. Records written
  before this change carry neither field and read as
  priority 0 / source `auto`. The waiting set is considered
  in the effective dispatch order FR-012 defines — human
  values are pinned, auto values are assigned by the
  optimizer — and the existing gates (lane serialization,
  the active cap, duplicate refusal) apply in that order.
  `gk status` lists the waiting jobs in effective dispatch
  order, each with its effective priority and its source, and
  marks each held or admitted under the current pause/focus
  record.
  *Acceptance*: test — the battery pins that a later-created,
  higher-priority human job is decided ahead of an earlier,
  lower-priority one in the same lane, that equal human
  priorities keep arrival order, and that the golden
  battery's pinned decisions (no human priorities anywhere)
  are unchanged.
  *This does not establish* any bound on starvation by
  itself: a steady stream of higher-priority human
  submissions can hold a low-priority job indefinitely.
  The bound that exists for auto-ordered work is FR-012's;
  human-vs-human ordering is the operator's lever, not a
  scheduler fairness policy.

- **FR-004 — Priority changes are live, bounded, and
  ledgered.**
  *Behavior*: `gk priority <job-id-or-key> <N>` changes the
  priority of a job in state `submitted` only, and stamps
  `priority_source: human` with it — a job the operator has
  touched is pinned from that moment (FR-012), whatever its
  source was before. For a job in any other state —
  `dispatching`, `dispatched`, `watching`,
  `held_at_checkpoint`, `completed`, `cancelled` — the
  command refuses (exit 3) naming the job and its state,
  because ordering cannot apply to work already dispatched
  or done. The mutation goes through `save_job` (the FR-013
  verified write); a failed save leaves the old priority
  standing and exits non-zero. Every change ledgers
  `priority_changed` with the old and new values.
  *Acceptance*: test — the battery pins a successful change
  (record + ledger), the refusal for a dispatched job, and
  the refusal for a completed job.
  *This does not establish* that the reprioritized job
  dispatches on the very next sweep — lane serialization,
  the active cap, budget stand-down, and pause/focus
  admission still gate it, in that order.

- **FR-012 — Auto-optimized priorities, under human
  precedence.** *(Added by the owner's refinement,
  2026-11-02; numbered FR-012 to keep the earlier FR numbers
  stable, and placed here because it completes Part B.)*
  *Behavior*: the gatekeeper auto-optimizes the waiting set
  by default. For every waiting job whose priority is not
  human-set, the sweep's dispatch evaluation — and `gk
  status`, rendering the same function — assigns an
  **effective priority** by a deterministic integer policy
  over the job's own record:
  *base* from its declared expectation (≤2 min: 50; ≤10: 40;
  ≤30: 30; ≤120: 20; longer, or undeclared — which counts as
  180: 10), plus a *lane-fit* bonus (+5 when a declared
  expectation fits within its lane's queued-W2 watermark),
  plus *aging* (+1 per full `dispatch.optimizer_aging_step_min`
  minutes waited since `created_at`, default 10), the total
  capped at `dispatch.optimizer_ceiling` (default 100). The
  evaluation is a pure function of the job records and the
  evaluation instant: it writes nothing and ledgers nothing,
  so no optimizer state exists to go stale, and a human
  decision takes effect at the very next evaluation.
  The unified dispatch order over the whole waiting set is:
  **effective priority descending** (a human job's pinned
  value; an auto job's assigned value), then **source —
  human before auto**, then `created_at`. Human precedence
  is therefore exact and bounded: the optimizer never
  changes a human value; at equal nominal value the human
  job always goes first; and a human value above the
  optimizer ceiling outranks every auto job permanently,
  because no auto value can reach it. The starvation bound
  for auto work: no fresh auto job enters above 55 (base 50
  + fit 5), and every waiting auto job gains +1 per aging
  step, so any auto job strictly outranks all newly
  submitted auto jobs after at most (55 − its base + 1) ×
  step minutes of waiting (≤ 460 minutes at the defaults),
  and reaches the ceiling — where arrival order decides —
  after at most (ceiling − base) × step minutes (≤ 900 at
  the defaults). Admission is evaluated before ordering:
  under pause/focus/drain (FR-002, FR-005) a held job is
  never ordered into a dispatch, whatever its effective
  priority — human control commands outrank the optimizer
  absolutely.
  *Acceptance*: test — the battery pins determinism (the
  same fixture records at the same evaluation instant yield
  the same order, twice), the aging bound (an old base-10
  auto job outranks a fresh base-55 auto job once its aging
  passes it), the human pin (a human job's ordering value
  never moves with age, and `auto` evaluation skips it), the
  equal-nominal precedence (human 60 dispatches before an
  auto 60; an auto 61 dispatches before a human 60 — the
  precedence is a tie rule, not a class rule), and optimizer
  inertness under focus for a held auto job.
  *This does not establish* that the optimizer's policy is
  optimal — only that it is deterministic, bounded, and
  subordinate to human decisions. Whether shortest-first
  with aging is the *right* policy for a given fleet is the
  operator's judgment; the constants are config, and the
  owner's explicit value always wins over the policy's
  opinion.

### Part C — Smooth transitions

- **FR-005 — Drain: executing work finishes, then the pause
  settles itself.**
  *Behavior*: `gk pause --drain` (and `gk focus --drain`)
  records `drain: true` on the pause record. While draining,
  admission follows FR-002 unchanged — the pipeline drains
  *toward* the admitted set: non-admitted jobs stop starting;
  admitted and maintenance jobs may still start, so a drain
  can target "everything except the focused program winds
  down". On each sweep, after the watch pass, if no
  non-admitted, non-maintenance job still holds a run, the
  sweep clears the drain flag in the record (verified write)
  and ledgers `drain_settled` with the settled mode. Status
  and the sweep summary show DRAINING while the flag stands.
  `gk pause` without `--drain` keeps today's immediate
  semantics exactly.
  *Acceptance*: test — the battery pins that a drain with an
  executing non-admitted job does not settle, that it
  settles on the first sweep after that job completes, and
  that a focus drain does not settle while a non-admitted
  job executes but is not blocked by an admitted one.
  *This does not establish* that draining is fast or that an
  executing job checkpoints — drain waits for natural
  completion; a job that never ends never settles the drain
  (its watermarks still escalate as today).

- **FR-006 — Mode is always visible; resume clears
  everything.**
  *Behavior*: `gk status` prints the pipeline mode — RUNNING
  (no banner), PAUSED (the existing banner line, unchanged),
  FOCUSED (a banner naming the selectors), plus a DRAINING
  line while a drain stands. The sweep summary's mode token
  extends the existing ` PAUSED` token to ` FOCUSED` /
  ` PAUSED DRAINING` / ` FOCUSED DRAINING` without changing
  the plain-pause token. `gk resume` removes the pause
  record entirely — pause, focus, and drain clear in one
  act, as resume clears a pause today.
  *Acceptance*: test — the battery pins the FOCUSED and
  DRAINING status lines and that resume removes the record
  for a focused, draining pause.
  *This does not establish* that a reader of the sweep line
  parses the new tokens — consumers that match ` PAUSED`
  literally see focused sweeps as unpaused, which is why the
  coordination procedure reads mode from `gk status`, not
  from the sweep token. (Deploy note, not a code claim.)

### Part D — Checkpoint hold (cooperative, job level)

- **FR-007 — The hold-signal contract.**
  *Behavior*: at dispatch, a script job (`run.yml` /
  `wsl.yml`) is passed a `hold_file` workflow input naming a
  per-job venue path from config (`checkpoint.holds_dir`:
  `C:\Users\trist\Development\Syncyard\holds\<job-id>.hold`
  on the Windows side, `/home/tpierson/.syncyard/holds/
  <job-id>.hold` in WSL). The transport workflows create the
  holds directory and export the path to the job's script as
  the environment variable named by `checkpoint.env_var`
  (default `SYNCYARD_HOLD_FILE`). The convention a job
  programs against: *if a file appears at that path, stop at
  the next checkpoint, print the marker of FR-008, and exit
  with `checkpoint.exit_code` (default 86).* The path is
  unique per job id, so a signal can never be stale from
  another job. The contract is the file, not the writer:
  the signal may be raised by any actor able to write the
  venue path — the gatekeeper's delivery job (FR-008), the
  job itself, or a **host-local actor with no gatekeeper
  round-trip** (the spec 011 host guard is the named
  consumer: when a shutdown is imminent it creates the
  running jobs' signal files directly at these same paths
  and the jobs checkpoint out under it). Correspondingly,
  the FR-008 harvest never asks which side raised the hold:
  the marker in the job's log is the whole condition.
  Removal is `gk resume --job` (FR-009) or the raising
  host actor deleting the file it created.
  *Acceptance*: test — the dispatch battery pins the
  `hold_file` input in the dispatch argv for script jobs and
  its absence for grab jobs; the workflow contract battery
  pins the input declaration and env export in both
  transport workflows.
  *This does not establish* that any existing job honors the
  contract — honoring it is each job author's code, adopted
  job by job. Jobs that never poll the file are unaffected
  by it in either direction.

- **FR-008 — A checkpoint stop is a distinct state, not a
  failure.**
  *Behavior*: `gk hold <job-id-or-key>` records
  `hold_requested` on the job (verified write, ledgered
  `hold_requested`) and delivers the signal file to the
  venue as an internal maintenance job (`gk-hold-<job-id>`,
  same workflow as the target, quick lane) whose only act
  is creating that one file. Grab jobs (no script
  environment), terminal jobs, and workflows with no
  configured holds directory are refused (exit 3) with the
  reason. A completed run whose harvested markers contain
  the checkpoint marker (`checkpoint.marker`, default
  `CHECKPOINT_PAUSED`) is recorded in state
  `held_at_checkpoint` with a `held_at_checkpoint` ledger
  event — never as a failed completion: it raises no
  failure issue lines, the outbox renders it
  HELD_AT_CHECKPOINT, and the run's GitHub conclusion is
  recorded verbatim alongside (it will typically read
  `failure`, because the dedicated exit code is non-zero;
  the state, not the conclusion, carries the meaning).
  `held_at_checkpoint` is terminal for the record and is
  *not* an active state, so the owning chat may immediately
  resubmit the same key to continue the work.
  *Acceptance*: test — the battery pins the hold command's
  record + signal job + script, the grab refusal, the
  harvest of a marker-carrying completion into
  `held_at_checkpoint`, a marker-less completion landing
  in `completed` as the control, and the same harvest for a
  marker-carrying completion whose record carries no
  `hold_requested` — the host-raised case (FR-007), which
  must land identically.
  *This does not establish* that the job actually stopped
  at a checkpoint rather than merely printing the marker —
  the gatekeeper believes markers, as it does for every
  verdict (the fleet rule: verdicts come from markers,
  never from the run's color). A job that prints the marker
  without checkpointing is lying to its own record; no
  mechanism here can detect that.

- **FR-009 — Releasing a hold is one command, and
  continuation is a new submission.**
  *Behavior*: `gk resume --job <job-id-or-key>` clears
  `hold_requested` (verified write, ledgered
  `hold_released`) and delivers removal of the venue signal
  file as an internal maintenance job (`gk-unhold-<job-id>`).
  A `held_at_checkpoint` job is releasable whether or not a
  gatekeeper request was ever recorded — the withdrawal is
  queued the same way when the hold was raised host-side
  (FR-007) and the flag clear is simply a no-op.
  On a still-running job this disarms the hold — its next
  poll finds no file and it runs on. On a
  `held_at_checkpoint` record the state stays
  `held_at_checkpoint` (it is the truthful history of that
  run); releasing the hold is what permits the owning chat's
  continuation submission to start clean, under the same key
  (FR-008) or a new one.
  *Acceptance*: test — the battery pins the flag clearing,
  the ledger event, and the removal job for both a running
  target and a held-at-checkpoint target.
  *This does not establish* a resume of the stopped run
  itself: the exited run is never restarted by the
  gatekeeper. Continuation is a new dispatch whose script
  resumes from the job's own checkpoint state — sufficiency
  of that state is the job's contract, as in spec 011 FR-008.

### Part E — Invariants and config

- **FR-010 — Nothing else moves.**
  *Behavior*: the duplicate script-hash and duplicate
  active-key refusals, lane serialization, the active cap,
  watermark recording, the dispatch input size guard, host
  holds, and the budget discipline are unchanged; every new
  mutation (focus/pause writes, priority changes, hold flags)
  goes through the verified-write discipline and ledgers an
  event; the golden battery's pinned pre-spec-013 decisions
  stand except where this spec deliberately changes them
  (the dispatch argv gains exactly one input, FR-007).
  *Acceptance*: test — the full local battery (config,
  FR-013, golden, workflow contract, transport smoke, tray
  source, backchannel unit + chaos, skills) is green with
  only the FR-007 argv pin updated.
  *This does not establish* fleet behavior — the battery is
  fixture-level; the live proof is the deploy procedure's
  reconciliation on the owner's word, as with every prior
  gatekeeper change.

- **FR-011 — Config and documentation currency.**
  *Behavior*: the checkpoint contract's tunables live in a
  new config section, `checkpoint` (`exit_code`, `marker`,
  `env_var`, `holds_dir`), validated fail-closed like the
  spec 011 sections (non-positive or out-of-range exit code,
  a marker that is not a valid marker token, an empty env
  var name, or a non-mapping holds_dir each fail at load
  with a named error), and mirrored exactly in
  `config/syncyard.example.yaml`. SKILL.md, the skills-pack
  source (and its generated editions), the gatekeeper
  README, and the CHANGELOG describe the new commands in
  the same change, citing this spec, and VERSION bumps.
  *Acceptance*: test — the config battery pins the golden
  checkpoint values, the defaults==example surface including
  the new section, overrides, and the named rejections; the
  skills battery pins edition currency at the new VERSION.
  *This does not establish* that an operator's existing
  `syncyard.yaml` needs the section — absent keys fall back
  to the defaults, as with every config section.

## Non-requirements (explicit)

- **No preemption.** Priority never interrupts, suspends, or
  cancels a running job. A reprioritized queue still waits
  for lanes to free by natural completion.
- **No mid-flight freeze of uncooperative jobs.** The
  checkpoint hold is cooperative by construction (see the
  limit statement). Nothing here kills a run, a process, or
  a venue session.
- **No per-job pause of queued work.** A queued job the
  operator wants held is served by focus (exclude it), by
  priority (demote it), or by `gk cancel` (retire it) —
  there is deliberately no fourth state for "submitted but
  held" in this change.
- **No change to the fleet by this change.** Deployment of
  the new gatekeeper to the fleet tree, and any migration of
  the live PAUSED.json, happen later, separately, on the
  owner's review — the live record is read-compatible from
  the first new sweep by FR-001's normalization, and no
  conversion step is needed or performed here.

## Acceptance shape

The spec lands behind the scenario battery: fixtures pin
focus admission by key, program, and chat; the legacy-record
normalization; priority ordering and its refusals; drain
settle timing in both plain and focused modes; the hold
signal's record, delivery job, harvest state, and release;
and resume clearing a focused drain. The pre-existing
batteries stay green with exactly one deliberate pin change
(FR-007's dispatch argv). Per the fleet rule, CI green is
the finish line for the repo change; the fleet deploy is a
separate gated act (FR-010's note).

# Feature Specification: Shutdown Protection and Recovery

**Feature Branch**: `spec/011-shutdown-protection-recovery`

**Created**: 2026-10-31

**Status**: **Specified — draft for owner review. No implementation
in this change.** Phase 1 (T001–T003, gatekeeper side) has since
been implemented and merged (2026-10-31). **Amendment 1
(2026-10-31) resolves the FR-009 owner gate by owner directive
and adds FR-015, controlled host reboot — see the Amendment 1
section below.**
This spec gives Syncyard a shutdown story it does not have
today: when a host machine is shut down or restarted, active
work is drained and checkpointed instead of silently killed,
the shutdown itself is delayed while that drain happens, and
when the host comes back the fleet recovers on its own —
runners, WSL agents, supervised work, and a written account of
what was interrupted, what resumed, and what was lost.

**Direction (owner, 2026-10-31 ~14:51 ET)**: "Update Syncyard
to have some level of protection so if a machine is shut down,
it handles outstanding things for clean shutdown, stop
immediate shutdown if someone triggers shutdown. And whatever
law we can do to make sure we have a robust recovery path when
things go wrong."

**The protection level, stated plainly up front**: Syncyard
can *delay + drain + checkpoint + notify*. It cannot
*prevent*. No design in this spec stops a forced shutdown
(`shutdown /f`), a power loss, a held power button, or an
administrator who insists. Every requirement below is written
inside that envelope, and any UI, log line, or document
produced under this spec must say so in the same words. A
guard that claims more than this is a defect under this spec.

## Amendment 1 — Owner ruling: boot autonomy and controlled reboot (2026-10-31, ~23:57 ET)

**Direction (owner, 2026-10-31 ~23:57 ET)**: "Add capability
for Syncyard to reboot the desktop and make sure Syncyard
starts automatically on boot and does not require logging
into user account."

This amendment is a ruling, not a proposal. It lands the same
night the failure it answers was observed in production: at
2026-11-01T02:29Z TRISTEN-DESKTOP's runners and backchannel
agent went silent together and stayed silent — the fleet's
most fragile boot leg (The gap, first bullet) failing in the
field, with jobs queued behind a host only a person at the
keyboard could revive.

- **The FR-009 owner gate (T012) is satisfied by this
  directive.** The chosen outcome is boot-start with **no
  interactive logon**. Shape (b), autologon + tray, is
  excluded by the ruling's own words: it logs into the user
  account (automatically) and leaves an unlocked console —
  the tradeoff FR-009 said the owner must accept explicitly;
  the owner has instead required the property (b) cannot
  give.
- **Implementation shape, selected under the ruling**:
  service-mode runners (FR-009 shape (a)) **running as the
  owner account** — Windows services with automatic start
  whose service logon is the owner's own account, so each
  runner process holds the owner's profile, WSL distro
  registration, and device posture without any interactive
  session existing. FR-009's honesty mechanism is unchanged
  and binding: a lane whose job classes need the interactive
  session (audio endpoints, bench devices) is labeled
  `logon-bound` in config and renders DOWN pre-logon, never
  online-but-wrong. The one-lane proof (T011, as amended)
  precedes fleet conversion; if that proof shows the owner's
  WSL distro unreachable from the service session, the
  fallback shape is (c), a boot-scheduled launcher in the
  owner's name, under the same labels. Adopting service-mode
  amends spec 007 FR-003; that amendment rides with the T013
  implementation change, as T012 requires.
- **The backchannel agent joins the boot set.** The agent is
  a systemd user service; the conversion enables linger for
  its user and pulls the FR-010 step (1) distro-start ensure
  forward, so the heartbeat resumes after a boot with no
  logon.
- **The tray stops being load-bearing.** Its logon autostart
  is retired in the same conversion, so a session-0 service
  and a session-1 tray starter never run the same runner
  twice. Nothing else about the tray changes in this
  amendment.
- **FR-015 (Part D, below) is added**: a controlled,
  drain-aware host reboot verb for the gatekeeper.

## Amendment 2 — Owner speed directive: the drain and the guard (2026-11-02, ~02:29 ET)

The owner, ordering this build (verbatim intent): "make sure
when we are running, we block boot and when we see a
reboot/shutdown eminent, we pause all jobs and wait for
checkpoints - be as fast as we can." This amendment states
the Phase 2 (drain) and Phase 3 (guard + going-down writer)
requirements at the speed the directive demands. The
protection level is unchanged: **delay + drain + checkpoint
+ notify, never prevent.**

**The two drains, reconciled (spec 013 vs this spec).** Spec
013's `gk pause --drain` (and `gk focus --drain`) is
**pipeline-scoped and passive**: it signals nothing, holds
nothing per host, and merely lets the sweep notice that the
globally executing set has emptied before it stamps the
pause record settled — its subject is the pipeline. This
spec's `gk drain --host` (FR-007) is **host-scoped and
active**: it sets the host's drain hold (below), raises
checkpoint holds on the host's running jobs through spec
013's FR-008 signal path, waits for each job's own deadline,
and returns a per-job verdict — its subject is one machine
about to go away. A job dispatches only when **both** layers
admit it: the global admission (pause / focus / optimizer)
and the per-host holds (the FR-006 going-down hold and this
drain hold) are a conjunction, and no layer's verb clears
another layer's state — `gk resume` clears the global record
only; `gk host-resume <host>` clears that host's holds (both
kinds, one act). The drain hold is a distinct field on the
host's liveness record, not a reuse of the FR-006 hold: a
going-down ingest must never be able to clear an operator's
drain, nor an operator's drain masquerade as a shutdown.

- **FR-007-A (`gk drain --host`, the operator drain).**
  Behavior: signal every running checkpoint-capable job on
  the host first, then set the host's drain hold, then wait
  per lane (the host's checkpoint window, FR-005) and print
  a per-job verdict — CHECKPOINTED, FINISHED, ENDED, or
  UNCHECKPOINTED with a named reason — exiting 0 exactly
  when no job is left uncheckpointed. Queued jobs on the
  host are never signaled; they wait under the drain hold.
  The verdict covers gatekeeper jobs only: no supervised
  work is registered until the FR-008 vehicle exists
  (Phase 4), and the verdict text says so. Acceptance mode:
  test (`gatekeeper/test_spec011.py`, scenarios 1–10) +
  human-as-oracle (T005's live pass, still owed). *This
  does not establish* that any individual job honors its
  hold — the harvest is the contract's marker, and a job
  that never prints it is named UNCHECKPOINTED, not assumed
  checkpointed.
- **FR-002-A (guard duty (a) — the block, immediately).**
  The guard MUST register the OS shutdown block with a
  reason string naming the running-job count as soon as it
  observes running work (pre-registered in steady state, so
  no registration race exists inside the shutdown's own
  countdown), and MUST veto a session-end attempt while a
  drain is incomplete. Acceptance mode: test (source
  battery, `runner/guard/test_guard_source.py`) + oracle at
  T006. *This does not establish* the block against a forced
  shutdown (`/f`, a critical session end): Windows law
  (the design envelope) says those cannot be reliably
  blocked, and the guard's critical path records and signals
  without claiming a veto.
- **FR-008-A (guard duty (b) — local hold raising).** On a
  session-end signal the guard MUST raise the hold file of
  every running checkpoint-capable job it can see in the
  running-job registry, at the exact spec 013 paths carried
  by each registry entry, by local file creation only — no
  gatekeeper round-trip, no network call of any kind exists
  in the component. The registry is maintained by the
  transport workflows themselves (an entry written before
  the job runs, removed when it ends, aged out by its
  `started` stamp when orphaned): the guard covers exactly
  the checkpoint-capable set, the same set `gk drain`
  signals. Acceptance mode: test (source battery + workflow
  contract battery) + oracle at T006. *This does not
  establish* coverage of jobs whose registration failed
  (registration is best-effort by design — its failure
  degrades coverage, never the job) or of job classes with
  no hold venue (grab, docker, POSIX lanes), which the
  guard cannot see and does not claim to.
- **FR-003-A (guard duty (c) — the writer, fastest
  channel).** The guard MUST write the going-down record —
  the Phase 1 shape exactly, dedupe keyed by (host, ts) —
  BEFORE vetoing, through a verified write (temp file,
  read-back compare, rename), to the well-known host file
  the backchannel agent merges into the heartbeat payload.
  **Channel, stated and justified**: the heartbeat payload
  is the only input the shipped Phase 1 ingest reads, and
  the sweep already spends exactly one read of it per pass;
  the merge therefore adds no new API class, no new
  heartbeat format, and no ingest change — the fastest
  channel the existing contract supports. Host-local
  latency is one file write (sub-second); end-to-end
  ingest latency is bounded by the agent cycle (~60 s) plus
  the sweep cadence, and that bound is part of this
  requirement, not a footnote. `uncheckpointed` at write
  time is the at-risk set; the record is never rewritten —
  final checkpoint accounting is the gatekeeper's marker
  harvest, and a same-(host, ts) rewrite would be deduped
  away by design. Acceptance mode: test (the writer-contract
  scenarios ingest the guard's exact record shape in
  `gatekeeper/test_spec011.py`, including exactly-once) +
  oracle at T006. *This does not establish* the supervised
  half of the record: the guard writes `supervised: []`
  because no FR-008 registrations exist on the host yet —
  Phase 4 populates it, and the empty list is the honest
  current value.
- **FR-005-A (guard duty (d) — bounded release + re-issue).**
  The guard MUST release the block when the registry empties
  (every running job checkpointed or ended) or when the
  configured checkpoint window expires — the guard's own
  `window_s`, default 120 s, set independently of the
  gatekeeper's `drain.window_s` because the guard must act
  with the gatekeeper unreachable — MUST write a drain
  outcome record either way, and MUST then re-issue the
  pending action: a logoff via ExitWindowsEx, anything else
  as a restart. **Restart is a named design decision for
  the owner's review**: WM_QUERYENDSESSION cannot
  distinguish the vetoed action (restart vs poweroff); a
  restart returns the host to service under Part C's
  boot story, while a poweroff leaves the fleet dark until
  someone is at the machine. The guard yields exactly once
  to its own re-issued action so it cannot veto-loop.
  Acceptance mode: test (source battery settle paths) +
  oracle at T006. *This does not establish* behavior under
  a second, genuinely new shutdown arriving inside the
  10-minute yield — that attempt is allowed by design
  (it is indistinguishable from the re-issued one), and the
  limitation is stated here rather than discovered later.
- **Config (`drain` section).** `drain.window_s` (default
  120), `drain.per_lane_window_s` (per-lane overrides of
  the FR-005 per-lane deadline), `drain.guard_stale_after_s`
  (default 180): fail-closed validated, mirrored exactly in
  `config/syncyard.example.yaml`. The guard-staleness rule
  implements T007's gatekeeper half: the guard's beat rides
  the heartbeat as `guard`, staleness is judged against the
  carrying heartbeat's own timestamp (both host-clock), a
  transition ledgers `guard_stale` / `guard_recovered`
  exactly once, and `gk status` shows the beat and its
  staleness — a stopped guard is a visible host-health
  entry, never silent.

## The gap

Syncyard's components each survive some failures and none of
them survive a host shutdown as a system:

- **Boot recovery depends on a human logging on.** Runners are
  started by the Syncyard Tray at logon (HKCU Run, spec 007);
  they are not Windows services. Field proof, 2026-10-31:
  after a reboot all four runners showed OFFLINE until the
  tray started in the owner's interactive session — while the
  WSL backchannel agent (a systemd user service) came back on
  its own as soon as its distro started. The fleet's most
  fragile boot leg is the one that needs a person.
- **A host that vanishes mid-run leaves runs in limbo.** The
  gatekeeper writes job state with verified writes before
  dispatch and reconciles via a watch pass each sweep, but a
  run whose host disappears hangs in GitHub `queued` /
  `in_progress` with no host-side explanation. The gatekeeper
  holds no host liveness record of its own; the backchannel
  heartbeat exists but the sweep does not read it.
- **There is no drain signal.** Nothing tells a host "work is
  about to stop — checkpoint now," and nothing tells the
  gatekeeper "this host is going down with these runs active."
  The 2026-10-31 fleet stop was handled by hand: the owner
  ordered a stop, the gatekeeper was paused, and one running
  job was waited out before the restart. That worked because a
  human ran the protocol. This spec makes the protocol a
  mechanism.
- **Long work already knows how to survive — in one program's
  private pattern.** Detached processes launched from inside a
  job die at job teardown on the fleet host (proven). The
  answer in production is a scheduled-task *anchor*: a
  supervisor process plus a 15-minute ensure task plus a
  heartbeat file, under which checkpointed campaigns relaunch
  and skip completed units (the 2026-10-31 restart interrupted
  two such campaigns mid-run; both resumed from their
  checkpoints). That pattern is currently one program's
  hand-built rig. It should be a Syncyard vehicle any program
  can use.
- **Loss, when it happens, is reconstructed from memory.**
  There is no standing recovery report. What a shutdown cost
  is whatever the operators can piece together afterward.

## The design envelope (Windows law, named honestly)

The host-side mechanisms this spec may use, and their hard
limits. These are the "law" the owner asked for — what the
platform actually permits:

- A session application can call
  `ShutdownBlockReasonCreate` to register a human-readable
  reason and can veto a **non-forced** shutdown or logoff by
  handling `WM_QUERYENDSESSION`. The veto holds only while
  the application keeps responding and only against
  cooperative shutdowns.
- A service can request pre-shutdown notification
  (`SERVICE_ACCEPT_PRESHUTDOWN`) and an extended wait hint,
  buying an ordered, longer drain window than a session app
  gets.
- A shutdown already in progress **with a timeout** can be
  aborted with `shutdown /a` by an administrator.
- **None of this stops**: `shutdown /f` (forced), an immediate
  `shutdown /t 0` issued by an administrator who proceeds
  anyway, power loss, a kernel crash, or a held power button.
  WSL2 distros are terminated by host shutdown and by
  `wsl --shutdown` regardless of what user services inside
  them want; a distro's systemd user services return only
  when the distro starts again.
- Consequence for the design: the guard buys a bounded drain
  window and spends it on checkpoints and records — written
  *before* the drain, not after — so that even when the
  shutdown cannot be delayed at all, the recovery path (Part
  C) still has everything it needs. The going-down record is
  written at the *start* of a drain, never at the end.

## User scenarios

1. **Accidental shutdown with jobs running.** The owner (or a
   family member, or Windows Update) triggers a restart while
   two gatekeeper jobs and one supervised campaign are active.
   The shutdown is held with a visible reason naming the
   active work; the jobs get their checkpoint window; when
   the drain completes — or the window expires — the shutdown
   proceeds, and on boot everything recovers and reports.
2. **Deliberate owner restart.** The owner wants to restart
   now. One command (or one tray action) asks Syncyard for a
   drain: the gatekeeper pauses dispatch to that host, active
   jobs checkpoint, and the owner is told "clean to restart"
   or "these runs will be interrupted and will resume / will
   be lost," in plain terms, before he acts.
3. **Power loss / forced shutdown.** Nothing drains. On boot,
   recovery runs anyway: the going-down record may be absent,
   and the recovery report says so and reconciles from the
   gatekeeper's own records and GitHub's truth instead.
4. **Host never comes back (hardware death).** The gatekeeper
   declares the host down from its liveness record, reconciles
   its active runs against GitHub, and closes them out with
   the loss stated — no run waits forever for a dead host.

## Requirements

Each requirement carries a behavior statement, an acceptance
mode (**test** = an automated battery decides; **oracle** =
the owner or operator judges from presented evidence;
**gate** = a named human decision must precede the act), and
a *this does not establish* line bounding what passing it
proves.

### Part A — Shutdown guard (host side)

- **FR-001 — Host liveness is a gatekeeper record.**
  *Behavior*: every host emits a liveness heartbeat the
  gatekeeper itself reads on its sweep cadence (the
  backchannel heartbeat may be the transport, but the
  gatekeeper keeps its own last-seen timestamp per host and
  `gk status` shows host age next to runner state). A host
  older than a configurable threshold is shown DOWN, not
  merely stale.
  *Acceptance*: test — a fixture host heartbeat aging past
  the threshold flips the status rendering to DOWN in the
  status battery; the sweep's host read is pinned by the
  golden battery so it costs no extra API class beyond what
  the sweep already spends.
  *This does not establish* that a DOWN host has shut down
  cleanly — only that it stopped reporting; the cause is
  Part C's reconciliation to determine.

- **FR-002 — The guard registers a shutdown block while work
  is active.**
  *Behavior*: a host-side guard component (running in the
  owner's session alongside the tray, and/or as the tray
  itself grown this duty) registers a shutdown block reason
  via `ShutdownBlockReasonCreate` whenever the host carries
  active gatekeeper jobs or running supervised work, and
  releases it the moment the host is idle. The registered
  reason names the counts ("Syncyard: 2 jobs running, 1
  supervised campaign — checkpointing before shutdown").
  A non-forced shutdown or logoff arriving while blocked is
  vetoed via `WM_QUERYENDSESSION` for the drain window
  (FR-005), then allowed.
  *Acceptance*: oracle — on a test host with a fixture job
  active, a non-forced restart is observed held with the
  reason string visible in the Windows shutdown UI, and is
  observed proceeding after the drain window; with no active
  work, a restart is observed unheld.
  *This does not establish* any hold against a forced
  shutdown, `shutdown /a`-class administrative action, or
  power loss — see the design envelope; the spec's claim is
  delay, never prevention.

- **FR-003 — The going-down record is written first.**
  *Behavior*: the instant the guard detects an impending
  shutdown or logoff — before any drain step — it writes a
  going-down record with verified-write discipline (spec
  001's FR-012/FR-013 pattern: temp file, read-back verify,
  atomic rename): host, timestamp, trigger (shutdown / logoff /
  update / unknown), the active gatekeeper run ids, and the
  supervised-work ids with their checkpoint pointers. The
  record is written host-side *and* pushed to the gatekeeper
  through the backchannel, so a copy survives even if the
  host's disk state is the thing being lost.
  *Acceptance*: test — a simulated shutdown notification on
  the test host produces a going-down record whose run-id
  list equals the fixture's active set, on both sides,
  before the fixture's drain step is allowed to run.
  *This does not establish* that the listed runs checkpointed
  — only that they were active and are now accounted for by
  name in Part C.

- **FR-004 — Shutdown events notify immediately.**
  *Behavior*: the going-down record's arrival at the
  gatekeeper produces a push through the existing outbox
  mechanism to the owning chat and to every notify target
  registered on an affected run: "host going down, N runs
  interrupted, drain in progress / drain skipped (forced)."
  Notification rides the backchannel and the sweep's normal
  cadence; it never depends on the host staying up.
  *Acceptance*: test — a fixture going-down record produces
  exactly one outbox group per affected notify registration
  and one to the owning chat, with no duplicates on re-sweep.
  *This does not establish* delivery to a human's eyes — the
  outbox's existing delivered-on-ack semantics remain the
  delivery record.

### Part B — Clean drain protocol

- **FR-005 — A bounded checkpoint window, then release.**
  *Behavior*: on a drain signal (from the guard, or from the
  deliberate-drain command of FR-007), each active job and
  each supervised campaign gets a bounded window —
  configurable per lane, default 120 seconds — to reach a
  checkpoint and record its resume state. When every active
  unit has checkpointed, or the window expires, the guard
  releases the block and the shutdown proceeds. Units that
  did not checkpoint are named in the going-down record as
  uncheckpointed.
  *Acceptance*: test — fixture units that checkpoint inside
  the window are recorded checkpointed; a fixture unit that
  ignores the signal is recorded uncheckpointed at window
  expiry and the simulated shutdown is observed released,
  never held past the bound.
  *This does not establish* that a checkpoint is *sufficient*
  to resume — sufficiency is the job's own contract (FR-008)
  and is proven per program at its adoption, not by the drain.

- **FR-006 — The gatekeeper records host-going-down against
  every affected run.**
  *Behavior*: on receiving a going-down record, the
  gatekeeper ledgers a `host_going_down` event carrying the
  host, timestamp, and trigger, and annotates each affected
  job record with `interrupted_by: host-shutdown` and the
  going-down timestamp. Dispatch to that host stops
  immediately (the host is DOWN per FR-001 until its
  recovery handshake, FR-010); queued jobs for the host wait
  rather than rerouting — no silent venue fallback (spec 010
  FR-004 is unchanged).
  *Acceptance*: test — the golden battery drives a fixture
  going-down record through the sweep and pins the ledger
  line and the per-record annotations byte-for-byte.
  *This does not establish* the runs' final outcomes — a run
  annotated interrupted may still complete in GitHub while
  the host dies; final truth is adopted in FR-011, never
  assumed here.

- **FR-007 — A deliberate drain is one command.**
  *Behavior*: `gk drain --host <host>` (and the matching
  tray action) runs the owner-restart protocol of scenario
  2: pause dispatch to the host, signal active units to
  checkpoint, wait out the window, then print the verdict in
  plain language — CLEAN (nothing active, or everything
  checkpointed) or INTERRUPTING (naming what will be
  interrupted and whether each unit resumes or is lost).
  The command never shuts anything down itself; it tells the
  owner when *he* can.
  *Acceptance*: oracle — the owner runs a deliberate drain on
  the production host during a quiet window and during a
  busy window; the printed verdict matches the gatekeeper's
  own records in both cases.
  *This does not establish* that a CLEAN verdict makes the
  *restart* safe for non-Syncyard work on the host — the
  verdict covers Syncyard's jobs and supervised work only.

- **FR-008 — Supervised work is a first-class Syncyard
  vehicle (the anchor, generalized).**
  *Behavior*: Syncyard ships a supervised-work vehicle
  generalizing the proven anchor pattern: a supervisor
  process per campaign, a periodic ensure mechanism that
  relaunches a dead supervisor, and a heartbeat file the
  gatekeeper and the guard both read. A campaign is
  registered with the gatekeeper (program, key, checkpoint
  contract: what a completed unit looks like and how the
  campaign skips it on relaunch). Job-launched detached
  processes are explicitly *not* a vehicle — they die at
  job teardown on the fleet host (proven) — and the docs say
  so where an adopter would otherwise reach for them.
  Campaigns are checkpoint-and-resume by contract: a
  campaign that cannot skip completed units cannot register
  as supervised work.
  *Acceptance*: test + gate — the battery kills a fixture
  campaign's worker mid-unit on a test host and observes the
  relaunch complete only the remaining units; registering a
  real program's campaign additionally requires that
  program's coordinator to state its checkpoint contract in
  the registration (gate).
  *This does not establish* exactly-once semantics inside a
  unit — a unit interrupted mid-write is that program's
  idempotency problem, and the registration contract must
  say how the campaign makes a repeated unit safe.

### Part C — Recovery path

- **FR-009 — Runners return without an interactive logon.**
  *Behavior*: on host boot, every runner lane returns to
  online without the owner logging on interactively. The
  spec requires the implementation phase to evaluate, on a
  test host, three shapes and pick by evidence:
  (a) **service-mode runners** — full boot independence, but
  session 0 cannot reliably reach the owner's WSL distro,
  GPU, audio, or bench devices (the reason spec 007 FR-003
  forbids this conversion today; adopting it *amends
  spec 007* and narrows which job classes those lanes may
  carry);
  (b) **autologon + tray** — the tray starts the runners in a
  real owner session at boot; honest tradeoff: the console
  session is unlocked (or auto-unlocks) at boot, a physical-
  access and credential-posture cost the owner must accept
  explicitly;
  (c) **boot-scheduled launcher** — a startup task in the
  owner's name starts the tray's runner-starter at boot
  without an interactive logon, recovering the runners whose
  jobs need no interactive-session devices, and the tray
  re-adopts them at logon for the rest.
  Whichever shape is chosen, lanes whose jobs need the
  interactive session are labeled as such in config, and a
  lane that cannot honestly run is shown DOWN — never shown
  online-but-wrong.
  *Acceptance*: gate + oracle — the choice among (a)/(b)/(c)
  is the owner's, made from the test-host evidence (gate);
  after the choice, a reboot with no logon is observed to
  return every honestly-runnable lane to online (oracle).
  *This does not establish* that every job class works
  pre-logon — device-bound classes (GPU, audio, bench) are
  expected to remain logon-bound under (b) and (c), and the
  config labels, not hope, carry that fact.

- **FR-010 — Boot recovery is a sequenced handshake, not a
  hope.**
  *Behavior*: on host return, in order: (1) the WSL ensure
  mechanism (a Windows-side scheduled task, the proven
  anchor-ensure shape) starts the distro and verifies the
  backchannel agent answers with a fresh self-test;
  (2) runners return per FR-009; (3) supervised campaigns
  relaunch from their checkpoints per FR-008; (4) the host
  sends a recovery handshake to the gatekeeper carrying the
  going-down record it matches (or `none` — the forced /
  power-loss case) and its post-boot self-report. The
  gatekeeper keeps the host DOWN for dispatch until the
  handshake lands.
  *Acceptance*: test — on a test host, a reboot runs the
  sequence with each step's evidence in the host log, and a
  handshake withheld (step disabled) leaves the host DOWN
  at the gatekeeper despite the runners being up.
  *This does not establish* that recovered components are
  *healthy* — the handshake reports presence and self-test
  results; job-level health is proven by the next jobs'
  markers, as always.

- **FR-011 — The gatekeeper reconciles every interrupted run
  against GitHub's truth.**
  *Behavior*: when a host's recovery handshake lands (or the
  host is declared dead under FR-001/scenario 4), the sweep
  reconciles each run that was active at the going-down
  record — or, with no record, each run the gatekeeper
  believes was active on that host — by adopting GitHub's
  terminal state exactly once: completed / failed /
  cancelled / never-started. A never-started or
  host-killed run is closed in the gatekeeper with
  `interrupted_by: host-shutdown` and is *not* silently
  re-dispatched; re-dispatch is the owning program's call,
  made from the recovery report (FR-012). Marker verdicts
  are harvested for any run whose log exists, per the
  standing rule that the log, not the run color, is the
  record.
  *Acceptance*: test — fixture runs in each of the four
  GitHub states reconcile to exactly those states in the
  golden battery, with the interrupted annotation on the
  host-killed ones and no re-dispatch side effects.
  *This does not establish* that a "completed" GitHub state
  means the job's *work* completed — the marker verdict
  remains the verdict; a completed run with no markers is
  an ISSUES entry, as today.

- **FR-012 — Every shutdown produces a recovery report, and
  loss is stated plainly.**
  *Behavior*: after reconciliation, the gatekeeper emits a
  recovery report to the owning chat covering: the shutdown
  (time, trigger, drained or forced), what was interrupted
  (each run and campaign by name), what resumed (campaigns
  relaunched, units skipped as already-complete), what was
  lost (runs closed without a verdict, units a campaign
  must redo, anything uncheckpointed), and the host's
  current state. Loss lines are never smoothed: a thing that
  was lost is listed as lost, with its name. A shutdown with
  zero loss says "nothing was lost" as a positive statement
  backed by the reconciliation, not as a default.
  *Acceptance*: oracle — the owner reads the report from a
  real (deliberate) restart and confirms each line against
  the gatekeeper's records; the forced-shutdown drill's
  report names its uncheckpointed fixture unit as lost.
  *This does not establish* that the *next* shutdown will
  lose the same or less — the report is an account of one
  event, not a guarantee about the mechanism.

- **FR-013 — The whole path is drilled, not just built.**
  *Behavior*: the release battery includes three drills on a
  test host: (1) cooperative shutdown with active fixture
  work (drain + checkpoint + recover); (2) forced shutdown
  mid-run (no drain; recover from records, report the loss);
  (3) host-never-returns (reconcile and close out from the
  gatekeeper side alone). Drills run in CI-adjacent form
  where the platform allows and as a scripted manual drill
  where it does not; a release with a red drill does not
  ship, per spec 010 FR-008's standing rule.
  *Acceptance*: test — the three drill scripts exist, are
  wired into the release checklist, and their last-run
  results are recorded in the tasks file before the version
  that ships this spec is tagged.
  *This does not establish* production-host behavior — drills
  run on a test host; the first production shutdown under
  this spec is itself evidence, and its recovery report
  (FR-012) is the review artifact for it.

- **FR-014 — Configuration, docs, and skill currency.**
  *Behavior*: the drain window, liveness threshold, and
  per-lane session requirements are config (spec 010 FR-001
  system), documented in the example config with defaults
  and failure modes. `SECURITY.md` gains a shutdown-and-
  recovery section stating the protection level in this
  spec's own words (delay + drain + checkpoint + notify,
  never prevent) and the FR-009 boot shape's security
  tradeoff as chosen. SKILL.md and the skills pack gain the
  drain command, the supervised-work vehicle, and the
  host-DOWN states in the same change as the behavior (the
  standing currency rule).
  *Acceptance*: test — the config validation battery covers
  the new keys (inverted window/threshold pairs fail
  closed); the skills currency battery (spec 010 T016)
  covers the new contract text.
  *This does not establish* adopter comprehension — docs are
  evidence of intent; the drills (FR-013) are the evidence
  of behavior.

### Part D — Controlled reboot (Amendment 1)

- **FR-015 — The gatekeeper can reboot a host, on request,
  under the drain discipline.**
  *Behavior*: `gk reboot --host <host>` requests a controlled
  reboot of a fleet host. The gatekeeper first accounts for
  the host's active jobs: with any active and `--force`
  absent, it refuses and names them — the polite path is the
  drain (FR-007) or waiting. On acceptance it writes the
  going-down record first (FR-003, trigger `remote-reboot`),
  ledgers a `host_reboot_requested` event naming the
  requesting chat, notifies per FR-004, and only then signals
  the host. Signal routes, in order: (1) the backchannel,
  when its heartbeat is fresh — the agent issues the host
  OS's reboot with a grace delay (default 60 s) so in-flight
  uploads and log writes finish; (2) a maintenance dispatch
  on any online runner of the host, issuing the same delayed
  reboot; (3) when neither route is live, the command reports
  the host unreachable and does nothing — an unrebootable
  host is a stated result, never a silent success. After
  signaling, the host is held for dispatch (FR-006) until the
  FR-010 handshake or `gk host-resume` clears it, and its
  return is expected to satisfy FR-009 as amended: boot-
  runnable lanes and the backchannel heartbeat come back
  with no logon.
  *Acceptance*: test + oracle — the fixture batteries pin the
  refusal with active jobs, the record-before-signal order,
  and the route selection; one live fire on the production
  host, counted as a drill under FR-013, observes the reboot
  and the no-logon return.
  *This does not establish* reachability of a powered-off,
  hung, or network-isolated host. The verb reports that
  honestly instead of acting, and the FR-009 boot autonomy is
  what makes a host that *does* come back come back useful.

## Non-goals

- **No prevention.** Nothing here vetoes a forced shutdown,
  survives a power loss on the host, or outranks an
  administrator. See the design envelope.
- **No silent re-dispatch.** Interrupted runs are closed and
  reported; whether a program's work re-runs is that
  program's decision (FR-011).
- **No new trigger surfaces.** The dispatch-only invariant
  (spec 010 FR-009) is untouched: the guard, the drain, and
  the recovery handshake add no way to start a runner job
  except through the gatekeeper.
- **No cross-host migration.** Work interrupted on a dead
  host is not failed over to another host; Syncyard remains
  one owner's machines, addressed by name (spec 010
  non-goal, unchanged).
- **No change to the backchannel's exclusivity.** The
  going-down and handshake traffic rides the backchannel as
  Syncyard recovery traffic — its chartered purpose (spec
  009) — and carries no program job content beyond run ids
  and checkpoint pointers already in gatekeeper records.

## Conflicts with existing specs, named at draft time

- **Spec 007 FR-003 ("Runners are not converted to Windows
  services") vs this spec's FR-009(a).** Direct tension.
  FR-009 does not presume (a); it requires the evaluation
  and the owner's gate, and adopting (a) amends spec 007
  FR-003 and SKILL.md's Windowless Supervision section in
  the same change. Shapes (b) and (c) keep spec 007 intact.
- **Spec 007 tray scope.** The tray is a session supervisor
  for runners; FR-002 grows a guard duty (shutdown block +
  drain trigger) into the same session component or a
  sibling. The tray's "exiting it never kills a runner" rule
  is unaffected, but the tray spec's task list gains the
  guard's lifecycle (the guard must be *harder* to exit than
  the tray, or the block is voluntary in the bad sense).
- **Spec 009 backchannel charter.** FR-003/FR-004/FR-010 ride
  the backchannel with gatekeeper-originated recovery data.
  This is inside the charter ("Syncyard development and
  recovery only") but extends the agent's message set; the
  implementation phase updates spec 009's runbook, and the
  heartbeat format change rides spec 010 FR-005's self-test
  field rather than inventing a parallel one.
- **Spec 001 gatekeeper semantics.** FR-006/FR-011 add a
  ledger event type and a terminal annotation; the "the
  gatekeeper never cancels" rule is preserved (reconciliation
  adopts GitHub's state; it cancels nothing), and the
  exactly-once dispatch rule is preserved (no re-dispatch).

## Success criteria

- A cooperative restart during active fixture work loses
  nothing: every run and campaign either completes, or
  checkpoints and resumes, and the recovery report says so
  line by line.
- A forced restart loses only what was uncheckpointed at the
  moment of the kill, and the recovery report names exactly
  that — nothing more claimed, nothing less admitted.
- After any reboot, the fleet returns to dispatch-ready with
  no interactive logon for every lane config labels
  boot-runnable, and the gatekeeper's host state matches
  reality at every step in between.
- The owner can run `gk drain` before a deliberate restart
  and act on its one-word verdict without reading a log.

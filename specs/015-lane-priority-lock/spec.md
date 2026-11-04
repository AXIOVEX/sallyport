# Feature Specification: Lane Priority Lock

**Feature Branch**: `spec/015-lane-priority-lock`

**Created**: 2026-11-02

**Status**: **Implemented in this change** (gatekeeper + config +
batteries + docs). Fleet deployment is a separate, later act
under the standing deploy procedure and the owner's review of
this PR — nothing in this change touches the running fleet, its
pause record, its lock state (it has none), or its queue. This
branch stacks on `spec/013-live-priority-and-pause` (PR #41):
merge #41 first, then this branch retargets to main. Spec 011
phases 2–3 (PR #42) are an independent sibling stack on the
same base; neither stack depends on the other.

**Direction (owner, 2026-11-02 ~09:47 ET)**: "we need some
sort of priority lock for agents so that when an agent has a
priority, it can lock a lane so other jobs cannot trump its
jobs. Make sure we add this and make sure it can be gracefully
released and paused as well — pause with the rest of the flow.
Don't forget spec-kit + AEE and test cases for all of this."

**The limit, stated plainly up front**: like every gatekeeper
control, a lane lock shapes *dispatch* — what starts, on which
lane, in what order. It never touches a running run: the
gatekeeper has no cancel verb (Part 4: watchers do not cancel
runs), and a lock acquired while another program's job is
already running on the lane leaves that run entirely alone.
The lock governs the *next* dispatch decisions on the lane,
from the moment it lands. Every requirement below is written
inside that envelope.

## The gap

Spec 013 gave the fleet live priorities, but priority is a
*relative* claim, and relative claims can always be trumped:

- **A human priority is only a number.** A program in the
  middle of a coordinated sequence of jobs on one lane — a
  hardware session, a multi-stage build whose stages must not
  interleave with strangers — can set each job's priority
  high, and another program can still set a higher one, or
  the optimizer can age an old job past it. There is no way
  to say "this lane is *mine* until I am done": the strongest
  statement the system understands is "prefer me", and
  preference is exactly what fails when two programs both
  believe they are the priority.
- **Focus is the wrong shape for it.** Focus (spec 013
  FR-001) is an operator act over the whole pipeline: pause
  everything except X. A lane lock is narrower and belongs
  to the program itself: one lane, one holder, acquired and
  released by the work that needs it, composing with —
  never replacing — the operator's pause and focus controls.
- **Nothing degrades gracefully today because nothing
  exists.** A lock that can only be removed by its holder
  would wedge a lane forever the first time an agent crashed
  mid-campaign. The lock therefore needs the full lifecycle
  in its first version: a bounded lifetime (TTL), a graceful
  release (including an operator override, ledgered as
  such), and a suspend that pauses enforcement without
  surrendering the claim — "pause with the rest of the flow".

## User scenarios

1. **A program holds its lane through a sequence.** The
   GPU-unlock program is mid-campaign on the `gpu` lane. It
   locks the lane; its stages dispatch in their own order as
   each lands, and no other program's job — however high its
   priority — can interleave. When the campaign ends, it
   unlocks; the waiting work flows on the next sweep.
2. **A forgotten lock cannot squat a lane.** An agent locks
   `long`, then its session ends without unlocking. The
   lock's TTL expires; the sweep treats the expiry exactly
   like a release, ledgers it, and notifies the holder's
   chat. The lane flows again with no operator intervention.
3. **The operator needs the lane back now.** A lock is
   blocking urgent operator work. The operator releases it
   with an override; the release is ledgered distinctly
   (`lock_released_override`) and the holder's chat gets an
   outbox notice that its lock was released by someone else.
4. **A lock pauses with the rest of the flow.** The fleet
   pauses for the night; the lock stands untouched and is
   exactly as it was when the fleet resumes. Separately, the
   holder itself suspends its lock during a quiet stretch —
   other programs' jobs flow — then resumes it without
   re-competing for the lane, because the claim was never
   surrendered.

## Requirements

Each requirement carries a behavior statement, an acceptance
mode (**test** = an automated battery decides; **oracle** =
the owner or operator judges from presented evidence;
**gate** = a named human decision must precede the act), and
a *this does not establish* line bounding what passing it
proves.

### Part A — Acquire and admission

- **FR-001 — One holder per lane, in a dedicated verified
  state file.**
  *Behavior*: `gk lock --lane <lane> --program <program>
  [--ttl-min N] [--reason TEXT]` acquires the lane's lock
  for a program (the agent). Lock state persists in
  `LOCKS.json` beside `PAUSED.json` — a mapping of lane to
  `{program, state, acquired_at, expires_at, ttl_min,
  reason, notify_chat}` — written ONLY through the FR-013
  verified-write path (`_write_text_file`: verified temp
  file, atomic rename, read-back), exactly like the pause
  and hosts records. A second acquire on a lane already
  locked — in either state, ACTIVE or SUSPENDED — by a
  different program is REFUSED (exit 3) naming the current
  holder, its state, and its expiry. Re-acquire by the same
  holder is a renewal: the TTL is extended from now (a given
  `--ttl-min` replaces the stored one; otherwise the stored
  TTL is reused), the lock's state is preserved (a suspended
  lock stays suspended — renewal never silently
  re-activates), the reason is replaced only when a new one
  is given, and the renewal is ledgered (`lock_renewed`).
  A fresh acquire is ledgered `lock_acquired`. The stored
  `notify_chat` is the holder's registered chat, resolved at
  acquire/renew time from the most recent job of that
  program carrying one, and is what FR-003/FR-007 notices
  address.
  *Acceptance*: test — the spec 015 battery pins the written
  record, the second-holder refusal naming holder, state,
  and expiry, and the same-holder renewal (TTL extended,
  state preserved, ledgered).
  *This does not establish* that the holder *should* hold
  the lane — acquiring is first-come; the mechanism records
  and enforces the claim, it does not judge it. Contention
  policy between programs stays a coordination matter, with
  the operator override (FR-003) as the standing remedy.

- **FR-002 — The lock is a lane-scoped admission filter,
  joined to spec 013's single admission evaluation; priority
  never trumps it.**
  *Behavior*: the dispatch pass evaluates the lock as one
  more admission gate inside the existing evaluation, after
  the pause/focus/stop gate and the host-hold gate: while a
  lane is ACTIVE-locked by program P, a submitted job on
  that lane is dispatch-eligible only if it is P's job (or
  maintenance, FR-008). A non-holder job is NOT refused,
  cancelled, or reordered in its record — it simply does not
  dispatch this pass; it stays `submitted`, its watermarks
  keep escalating (that escalation is the visibility
  mechanism), and it dispatches on the first pass after the
  lock ceases to be ACTIVE. Priority is evaluated exactly as
  before and *inside* this filter: a non-holder job with a
  human-set priority of 200 still waits, and a holder job
  with auto priority 0 still dispatches. The holder's own
  jobs gain nothing else: they still obey the budget guard,
  the duplicate refusal, the active cap, lane serialization,
  host holds, and the focus conjunction (FR-004).
  *Acceptance*: test — the battery pins holder-only dispatch
  on an ACTIVE-locked lane, the priority-200 non-holder
  waiting while a priority-0 holder dispatches, and a
  non-holder dispatching on the pass after release (FR-003).
  *This does not establish* any fairness bound for the
  waiting jobs — a lock held to its TTL bounds their wait by
  the TTL (FR-007), but within a lock's life the holder's
  exclusivity is deliberately absolute, as focus is under
  spec 013 FR-002.

### Part B — Graceful release

- **FR-003 — Release is one command, effective at the sweep
  boundary, with a distinctly ledgered operator override.**
  *Behavior*: `gk unlock --lane <lane> [--program
  <program>] [--force]` removes the lane's lock through the
  verified-write path. The actor discipline is uniform
  across lock verbs: `--program` names the actor. Release
  by the holder (`--program` equals the holder) ledgers
  `lock_released` with the actor. Release by anyone else
  requires `--force` (the operator override, the same
  precedent as `gk reboot --force`): it ledgers
  `lock_released_override` with holder and actor, and
  appends an outbox notice to the holder's registered
  notify chat when one is known (FR-009). Release by a
  non-holder without `--force` is REFUSED (exit 3) naming
  the holder, its state, and its expiry. Unlocking a lane
  with no lock in effect is a no-op that prints so and
  exits 0 (the `gk host-resume` precedent). Release takes
  effect at the next dispatch decision — the sweep boundary
  — and never touches a running run.
  *Acceptance*: test — the battery pins holder release
  (record gone, `lock_released` ledgered, a waiting
  non-holder dispatching on the following pass and not
  before), the non-holder refusal, and the override release
  (distinct event, notice addressed to the holder's chat).
  *This does not establish* that the release is *timely*
  relative to a sweep already in flight — a pass that
  evaluated admission before the release landed decides by
  the state it read, as every gatekeeper control does.

### Part C — Pause interplay ("pause with the rest of the flow")

- **FR-004 — Global pause and focus never mutate lock state;
  focus and lock compose by conjunction.**
  *Behavior*: `gk pause`, `gk focus`, `gk resume`, and
  `gk start` neither create, alter, nor clear any lock;
  `LOCKS.json` is not read or written by those commands'
  state transitions. While the fleet is paused or stopped,
  no dispatch happens anyway; when it resumes, every lock
  is exactly as it was. Admission composes by conjunction:
  a job dispatches only if it passes the pause/focus gate
  (spec 013 FR-002) AND, on an ACTIVE-locked lane, is the
  holder's (FR-002) — focus admitting a program does not
  admit it onto another program's locked lane, and holding
  a lock does not admit a job the focus excludes.
  *Acceptance*: test — the battery pins lock state
  byte-identical across a pause/resume cycle with dispatch
  held throughout, and the conjunction in both directions
  (focus-admitted non-holder still waits on the locked
  lane; lock holder not admitted by the focus still waits).
  *This does not establish* anything about the TTL clock
  during a long fleet pause: locks keep expiring on
  wall-clock time (FR-007) even while the fleet is paused —
  expiry during a pause is harmless, since nothing
  dispatches either way, and a lock the holder still needs
  after a long pause is renewed on resume like any other.

- **FR-005 — A lock can suspend and resume its own
  enforcement without surrendering the claim.**
  *Behavior*: `gk lock --lane <lane> --suspend` moves an
  ACTIVE lock to SUSPENDED: the lane flows as if unlocked
  (any program's jobs are dispatch-eligible), but the claim
  is remembered — a SUSPENDED lock still blocks NEW
  acquires by other programs (FR-001's refusal applies in
  both states). `gk lock --lane <lane> --resume` moves
  SUSPENDED back to ACTIVE without re-competing: no other
  program could have taken the lane meanwhile, so resuming
  never races. Both transitions go through the
  verified-write path and are ledgered (`lock_suspended`,
  `lock_resumed`) with the actor. The actor discipline of
  FR-003 applies: the holder acts freely; anyone else needs
  `--force`, ledgered with `override: true` and noticed to
  the holder's chat (FR-009) — suspending or resuming
  *someone else's* lock is never silent. Suspend/resume on
  a lane with no lock, and suspending an already-suspended
  (or resuming an already-active) lock, are no-ops that
  print so and exit 0.
  *Acceptance*: test — the battery pins suspend → the lane
  flows + new acquires still refused; resume → enforcement
  restored; the non-holder refusal without `--force`.
  *This does not establish* a queue of would-be acquirers —
  a refused acquirer is told who holds the lane and must
  retry; the lock has no waitlist, by design (the job-level
  waiting of FR-002 is the queue).

- **FR-006 — Host drain and host holds are unaffected, in
  both directions.**
  *Behavior*: a host drain or going-down hold (spec 011
  FR-006/FR-007 vocabulary: the going-down ingest, the
  per-host dispatch hold, `gk host-resume`, and spec 013
  FR-005's pipeline drain settle) neither reads, blocks on,
  nor clears lock state, and a lock neither blocks nor is
  affected by any of them. The two compose only where they
  already meet — inside the dispatch evaluation, as
  independent conjunction terms (FR-002, FR-004): a job on
  a held host waits whether or not it holds the lane's
  lock, and a host-resume restores exactly the pre-drain
  lock picture. No drain path writes `LOCKS.json`; no lock
  path writes `hosts.json` or `PAUSED.json`.
  *Acceptance*: test — the battery pins a going-down ingest
  plus `gk host-resume` (and a pipeline drain settle)
  leaving `LOCKS.json` byte-identical, with the lock still
  enforced afterwards.
  *This does not establish* behavior of spec 011 phases 2–3
  (`gk drain --host`, the host guard — PR #42, a sibling
  stack) against locks: that code is not on this base. The
  conjunction stated here is the contract it lands against;
  its own battery owns proving its side when the stacks
  merge.

### Part D — Expiry

- **FR-007 — Every lock expires; expiry is a release by
  another name.**
  *Behavior*: every lock carries a TTL. The bounds are
  config (FR-011): `lock.default_ttl_min` (default 240)
  when `--ttl-min` is not given, and `lock.max_ttl_min`
  (default 1440); a `--ttl-min` outside [1, max] is REFUSED
  (exit 3) naming the bounds. Expiry is evaluated at
  dispatch time — at the head of the dispatch pass, and on
  entry to the lock/unlock commands — never by a separate
  timer. An expired lock is removed through the verified
  write and behaves EXACTLY like a release: the lane flows
  from that pass on, `lock_expired` is ledgered (lane,
  holder, the state it expired from, expiry), and an outbox
  notice goes to the holder's registered chat when known
  (FR-009). SUSPENDED locks expire on the same clock: a
  forgotten suspended claim must not squat a lane forever —
  suspension pauses enforcement, not mortality. If the
  expiry write itself cannot be verified, the expiry does
  not take effect this pass (the lock stands, fail-closed)
  and the failure is logged — an expiry is a state change
  and obeys the same write discipline as every other.
  *Acceptance*: test — the battery pins expiry at the
  dispatch boundary (a non-holder waiting before the expiry
  instant dispatching on the first pass after it), the
  ledger event and holder notice, a suspended lock expiring
  on the same clock, and the TTL bounds refusals.
  *This does not establish* a bound tighter than the sweep
  cadence — an expired lock is noticed at the next dispatch
  evaluation, not at the expiry instant, as with every
  sweep-evaluated state in this system.

### Part E — Maintenance exemption (RECOMMENDATION)

- **FR-008 — Maintenance jobs bypass lane locks, and every
  bypass is ledgered. (RECOMMENDATION — flagged in the PR
  for owner ratification.)**
  *Behavior*: a maintenance job (`submit --maintenance` —
  the operator's own infrastructure class, including the
  gatekeeper's internal `gk-reboot-` and `gk-hold-`/
  `gk-unhold-` signal jobs) is dispatch-eligible on an
  ACTIVE-locked lane exactly as it is under a pause: the
  lock filter does not apply to it. Every maintenance
  dispatch that lands while its lane is ACTIVE-locked by a
  different program is ledgered `lock_bypassed` with the
  job id, key, lane, and holder. Rationale: the operator
  must never be locked out of his own door by an agent's
  lock — maintenance is how the operator repairs, reboots,
  and signals the fleet, and a lock that could block a
  `gk hold` signal or a reboot route would invert the
  authority the lock exists inside. *Considered and
  rejected*: locks block maintenance too. Rejected because
  it makes an agent's convenience claim senior to the
  operator's recovery path; the bypass is narrow (one job
  class), visible (every instance ledgered), and the
  holder's own remedy — release, or the operator's
  override — is unaffected.
  *Acceptance*: test — the battery pins a maintenance job
  dispatching on an ACTIVE-locked lane held by another
  program, and the `lock_bypassed` ledger line naming the
  job key and holder.
  *This does not establish* that the ratification has
  happened — this FR encodes the recommendation; the PR
  flags it for the owner's explicit ratification, and if
  the owner rules the other way the change is one gate in
  the dispatch pass plus this FR's rewrite.

### Part F — Visibility

- **FR-009 — Lock state is visible on the board, in the
  outbox, and standalone.**
  *Behavior*: `gk status` renders a LOCKS section in all
  modes (running, paused, focused, draining, stopped): one
  line per lock — lane, holder program, state (ACTIVE /
  SUSPENDED), `acquired_at`, `expires_at`, reason — or
  `none` when no lock stands. The waiting list in status
  additionally marks a waiting non-holder job on an
  ACTIVE-locked lane `[lock-held by <program>]`, beside the
  existing pause/focus marks. `gk lock --list` prints the
  same section standalone. Lock events append outbox
  NOTIFY groups addressed to the holder's registered chat
  (the lock record's stored `notify_chat`, falling back to
  the holder's jobs) on exactly these: the lock **expired**
  (FR-007), the lock was **override-released** (FR-003),
  and the lock was **suspended or resumed by someone else**
  (FR-005 override). A program's own actions on its own
  lock are never self-notified: same-holder events carry no
  notice address, and override events carry the holder's —
  so the notice always lands on the party that did *not*
  act.
  *Acceptance*: test — the battery pins the status LOCKS
  section, the waiting-list lock mark, `gk lock --list`,
  and the outbox group for an override release addressed to
  the holder's chat (with a same-holder release producing
  no group).
  *This does not establish* delivery — the outbox is the
  owed-pushes list, as always; the coordinator delivers and
  acks, unchanged.

### Part G — Invariants and config

- **FR-010 — Nothing else moves; absence of the file is the
  old world.**
  *Behavior*: locks never cancel, preempt, suspend, or
  reorder a running run, and never reorder the waiting set
  — the filter removes eligibility, it does not touch order
  (spec 013 FR-012's sort is evaluated exactly as before).
  Every lock mutation is an FR-013 verified write: a failed
  write leaves the prior `LOCKS.json` byte-identical and
  the command exits non-zero with nothing ledgered as done.
  Lock state survives a gatekeeper restart by construction
  (it is a file, read fresh by every command and pass; there
  is no in-memory lock state to lose). Legacy state — no
  `LOCKS.json` at all — means no locks: every pre-015
  battery decision stands unchanged, and the file is
  created only by the first lock mutation.
  *Acceptance*: test — the spec 015 battery pins the
  write-failure case (prior state byte-identical, non-zero
  exit) and the persistence round-trip across a simulated
  restart (a fresh module load over the same state dir sees
  and enforces the lock); the full pre-existing battery set
  (config, FR-013, golden, spec 013, workflow contract,
  transport smoke, tray source, backchannel, chaos, skills,
  smoke) stays green unmodified except the config battery's
  FR-011 additions.
  *This does not establish* fleet behavior — the battery is
  fixture-level; the live proof is the deploy procedure's
  reconciliation on the owner's word, as with every prior
  gatekeeper change.

- **FR-011 — Config and documentation currency.**
  *Behavior*: the lock tunables live in a new config
  section, `lock` (`default_ttl_min` 240, `max_ttl_min`
  1440 — RECOMMENDATION values, flagged in the PR for owner
  ratification), validated fail-closed in the spec 011/013
  style (non-integer, boolean, non-positive, or
  `default > max` each fail at load with a named error), and
  mirrored exactly in `config/syncyard.example.yaml`.
  SKILL.md, the skills-pack source (and its generated
  editions), the gatekeeper README, and the CHANGELOG
  describe the lock verbs in the same change, citing this
  spec. VERSION is NOT bumped by this change: spec 013 owns
  0.4.0 on this stack, and this branch merges behind it.
  *Acceptance*: test — the config battery pins the golden
  lock values, the defaults==example surface including the
  new section, a valid override, and the named rejections;
  the skills battery pins edition currency.
  *This does not establish* that an operator's existing
  `syncyard.yaml` needs the section — absent keys fall back
  to the defaults, as with every config section.

## Non-requirements (explicit)

- **No preemption, ever.** A lock does not interrupt,
  suspend, checkpoint, or cancel a running job — including
  a job that was already running on the lane when the lock
  was acquired. Priority's non-requirement in spec 013 is
  inherited unchanged.
- **No lock stealing.** There is no "break the lock" verb
  for programs. The only ways a lock ends are the holder's
  release, the operator's ledgered override, and expiry.
- **No waitlist or lock queue.** A refused acquirer retries
  or coordinates; jobs (not locks) are the queued thing.
- **No cross-lane or multi-lane atomic locks.** One command
  locks one lane; a program needing two lanes locks them in
  two commands and owns the ordering risk, as it does with
  submissions.
- **No change to the fleet by this change.** Deployment of
  the new gatekeeper to the fleet tree happens later,
  separately, on the owner's review — a fleet tree with no
  `LOCKS.json` behaves identically under the new code until
  the first lock is taken (FR-010).

## Acceptance shape

The spec lands behind the scenario battery: fixtures pin
acquire + holder-only dispatch; the second-holder refusal;
priority never trumping (200 waits, 0 dispatches); release
at the sweep boundary; running runs untouched; expiry as
release (ledger + notice) for ACTIVE and SUSPENDED locks;
suspend/resume semantics; pause/focus conjunction and
non-mutation; the maintenance bypass and its ledger line;
drain leaving lock state byte-identical; persistence across
a simulated restart; legacy no-file behavior; TTL bounds;
and the FR-013 write-failure invariant. The pre-existing
batteries stay green with no pin changes except the config
battery's deliberate FR-011 additions. Per the fleet rule,
CI green is the finish line for the repo change; the fleet
deploy is a separate gated act (FR-010's note).

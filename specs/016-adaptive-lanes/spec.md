# Feature Specification: Adaptive Lanes — Profiles, Custom
Lanes, and Resource-Aware Auto Routing

**Feature Branch**: `spec/016-adaptive-lanes`

**Created**: 2026-11-02

**Status**: **Implemented in this change** (gatekeeper + config +
probe parsers + batteries + docs). Fleet deployment is a
separate, later act under the standing deploy procedure and
the owner's review of this PR — nothing in this change touches
the running fleet, its config file, its pause or lock records,
or its queue. This work was built stacked on
`spec/015-lane-priority-lock` (PR #43); #43 and spec 011
phases 2–3 (PR #42) both merged into
`spec/013-live-priority-and-pause` (PR #41) the same day, and
this branch was rebased onto that branch's tip — the spec
011 drain validation now checks its per-lane windows against
the active profile's lanes, the one place the two features
meet in config. This branch's PR targets the 013 branch:
merge #41 first, then this branch retargets to main.

**Direction (owner, 2026-11-02 ~09:58 ET)**: "we want lane
customization, it would be good to have auto as well and this
system should use CPU, GPU, RAM, storage, and other things to
estimate the best balance with several profiles and allowing
completely custom. should also consider Docker and WSL
(remember that this is cross platform so on macOS and Linux,
it will be different so make sure all paths work)."

**The limit, stated plainly up front**: like every gatekeeper
control, routing shapes *dispatch* — what starts, on which
lane. An auto-routed job is resolved to a concrete lane by the
sweep, on the record, before any dispatch decision is taken
for it; from that moment it is an ordinary job on that lane,
under every existing gate (pause/focus, host holds, lane
locks, lane serialization, the active cap, the budget). Auto
routing never moves a job that has dispatched, never
re-resolves a job that has a lane, and never kills, cancels,
or migrates a job that outgrows its estimate — estimates are
advisory, forever (FR-007). The scoring is a deterministic
pure function of recorded inputs, so any routing decision can
be re-derived and audited after the fact from the job record
alone. Every requirement below is written inside that
envelope.

## The gap

Spec 002 fixed the fleet at four lanes — quick, normal, long,
gpu — and spec 010 made their count and names configuration,
but the shape of the lane system has not moved since:

- **The lane set is one fixed menu.** An adopter with a small
  single host, or a GPU-heavy box, edits the same four-lane
  table every other fleet uses. There is no way to name a
  *posture* — "this machine is a light single host", "this one
  is a GPU farm" — and switch between postures as one act, and
  no first-class place for the per-lane facts routing would
  need (what kind of resource the lane is, what duration band
  it exists for).
- **Every submitter must name a lane.** The submit contract
  (spec 002 FR-002: no lane declared = not dispatched) puts
  the placement decision on every calling program, forever,
  with only duration folklore to guide it ("GPU overrides
  duration; unknown goes to long"). The gatekeeper — the one
  component that can see queue depth, executing load, lock
  state, host state, and the recorded history of what a
  program's jobs actually took — has no say in placement.
- **The gatekeeper knows nothing about the machines.** It
  routes to a lane label and trusts the venue. CPU cores, RAM,
  storage headroom, what GPUs exist and how much memory they
  carry, whether Docker or WSL is actually available on a
  host: none of it is recorded anywhere the dispatcher can
  read, so no placement decision can take any of it into
  account — and a job that needs more RAM than a host has is
  discovered only when it dies there.
- **What history exists is unused for placement.** Every
  completed job's record carries how long it actually ran and
  on which lane. A program whose "quick" jobs have taken two
  hours the last ten times is routed by its own say-so today;
  the actuals sit in the queue records, unread by any routing
  decision.

## User scenarios

1. **A small host runs a light posture.** An adopter's laptop
   loads the `single-host-light` profile: two lanes, sized for
   one small machine. Later the same config file switches
   `active_profile` to a fully custom profile the adopter
   defined — a lane for their build box with its own
   watermarks, resource class, and duration band — and the
   next sweep dispatches against exactly that lane set.
2. **A program submits without placing.** A chat submits a
   render with `--lane auto --needs-gpu 8192`. The sweep
   scores the candidate lanes from the job's hints, the
   program's recorded medians, current queue depth and load,
   and the host's probed capability (two GPUs, the larger with
   16 GB), resolves the job to the gpu lane, and writes the
   whole input snapshot onto the record: `auto_routed`,
   auditable forever. The job then dispatches under every
   normal gate.
3. **A hint that cannot be met waits, honestly.** A job asks
   `--needs-gpu` on a profile with no GPU lane. It is never
   silently downgraded onto a CPU lane to fail at runtime: it
   waits, its record says `auto_no_fit` with the unmet hint,
   and status shows why. When the profile gains a GPU lane
   (or the submitter fixes the hint), it routes.
4. **A lock is not a routing puzzle to solve around.** While
   the gpu lane is ACTIVE-locked by another program (spec
   015), an auto job from a third program never resolves onto
   it — the locked lane is simply not a candidate for
   non-holders. The holder's own auto jobs may resolve there.
   Focus, host holds, and the profile pin bind exactly as
   they bind explicit submissions.
5. **A stale probe degrades, visibly.** The desktop's
   capability snapshot is three hours old. Auto routing falls
   back to duration-band routing — bands, load, and history,
   with every capability-derived check neutral — and the
   degradation is ledgered on the job's routing event, not
   silently absorbed. A fresh `gk probe-host` restores full
   scoring.

## Requirements

Each requirement carries a behavior statement, an acceptance
mode (**test** = an automated battery decides; **oracle** =
the owner or operator judges from presented evidence;
**gate** = a named human decision must precede the act), and
a *this does not establish* line bounding what passing it
proves.

### Part A — Lane profiles and custom lanes

- **FR-001 — Lanes live in named profiles; exactly one is
  active.**
  *Behavior*: the config's `lanes` section becomes a profile
  table: `lanes.active_profile` (a profile name),
  `lanes.auto_default` (FR-004; default false), `lanes.
  capability_max_age_min` (FR-005; default 60), and `lanes.
  profiles.<name>` — each profile a lane table in the
  pre-016 per-lane shape (host, runner_label, concurrency,
  queued/executing watermarks) plus the FR-002 fields. The
  loader validates the whole table, then *resolves* the
  active profile's enabled lanes into the flat lane mapping
  every existing consumer reads (`cfg["lanes"]`), and
  exposes the full table, the active name, and the two
  routing knobs alongside it (`cfg["lane_profiles"]`,
  `cfg["active_lane_profile"]`, `cfg["lanes_auto_default"]`,
  `cfg["lanes_capability_max_age_min"]`). Resolution is
  therefore the only place profiles exist: dispatch,
  watermarks, locks, status, and routing consume resolved
  lanes exactly as they consumed the flat table. Three
  profiles ship built in (**RECOMMENDATION**, flagged in the
  PR for owner ratification): `fleet-default` — today's four
  lanes with today's numbers, so the default fleet's resolved
  lane table and watermarks are identical to the pre-016
  constants; `single-host-light` — two lanes (quick, long)
  for small machines; `gpu-farm` — a GPU-weighted set (quick
  for control jobs, gpu with concurrency 2, gpu-long for
  renders). `gk profile --list` renders every profile with
  its lane count and the active marker; `gk profile --show
  [name]` renders one profile's lanes (name, host, resource
  class, concurrency, duration band, enabled, queued
  watermarks, workflow pin), defaulting to the active
  profile. Switching the active
  profile is an operator *config* act, not a runtime verb:
  editing `lanes.active_profile` and letting the next process
  load it. There is deliberately no `gk profile --set`:
  profile membership changes which lanes exist, and a lane
  set must never change underneath a process that has
  already resolved it — the change takes effect at the next
  sweep's dispatch decisions (a fresh process), and running
  runs are untouched, as with every config change.
  *Acceptance*: test — the config battery pins the resolved
  fleet-default table against the pre-016 golden constants;
  the spec 016 battery pins `--list`/`--show` rendering and
  that a profile switch (a second load with a different
  active profile) resolves the other table.
  *This does not establish* that the built-in profile set is
  the right set — the names and numbers are the flagged
  recommendation; the mechanism (named, validated, resolvable
  profiles) is what the battery proves.

- **FR-002 — A profile may define lanes from scratch, and
  validation is fail-closed.**
  *Behavior*: a lane in any profile is a mapping with the
  pre-016 fields plus optional `resource_class` (`cpu` |
  `gpu` | `any`, default `any`), `duration_band` (`[lo, hi]`
  in minutes, either end nullable for an open band, default
  open), `enabled` (boolean, default true), and `workflow`
  (a declared workflow name pinning the lane to that one
  workflow, default unpinned). Validation rejects, each with
  a named `ConfigError` at load: a profile or lane name
  outside `^[a-z][a-z0-9-]*$`; a duplicate lane name within a
  profile (the subset parser already refuses duplicate keys
  in any map); a lane naming an unknown host or (via the pin)
  an undeclared workflow; a non-integer or non-positive
  concurrency; an unknown resource class; a malformed band
  (not a pair, a negative end, lo > hi); a non-boolean
  `enabled`; an `active_profile` naming no profile; a
  profile with zero enabled lanes; a `lanes` section
  carrying any key outside the four named ones — which is
  also how a pre-016 flat `lanes:` table fails, with an
  error that names the profile shape as the migration.
  Disabled lanes are excluded from resolution entirely:
  they do not dispatch, cannot be submitted to, and render
  as disabled under `gk profile --show`. A lane workflow pin
  joins routing: `route_check` returns no route for a job
  whose workflow differs from its lane's pin, so a pinned
  lane refuses other workflows exactly as an unserving host
  does (spec 010 FR-004: never silently rerouted). Every
  profile in the table is validated, active or not — a
  typo'd inactive profile is a landmine at switch time, so
  it fails at load.
  *Acceptance*: test — the spec 016 battery pins each named
  rejection, a from-scratch custom profile (new lane name,
  new host) loading and dispatching a job end-to-end against
  fake gh, a workflow pin refusing the other workflows, and
  a disabled lane absent from the resolved table.
  *This does not establish* that a custom lane's *runner*
  exists: registration and identity proof of a new lane's
  runner remain spec 002's stand-up acts on the machine.
  Config can name a lane no runner serves; jobs then wait
  behind a label nothing polls, exactly as a misspelled lane
  label behaves today — visible in status, not hidden.

### Part B — Auto routing

- **FR-003 — Jobs declare resource hints at submit.**
  *Behavior*: `gk submit` gains `--needs-cpu N`,
  `--needs-ram-mb N`, `--needs-storage-mb N` (non-negative
  integers; a negative value is a usage refusal, exit 2) and
  `--needs-gpu [VRAM_MB]` (the flag alone declares a GPU
  need with the size open; a value declares a minimum VRAM),
  plus `--venue <auto|native|wsl|docker>` (default auto).
  Hints and venue are recorded on the job (`needs`,
  `venue`) for any submission, explicit lane or auto: on an
  explicitly laned job they are informational only — auto
  never overrides an explicit lane (FR-004) — and on an auto
  job they are scoring inputs (FR-006). A submission's
  record is otherwise unchanged; pre-016 submissions carry
  no `needs` and read as no hints.
  *Acceptance*: test — the battery pins the recorded hint
  shapes (including flag-alone `--needs-gpu`), and the
  negative-value refusal.
  *This does not establish* that a hint is *true* — hints
  are the submitter's declaration, like `expected_min`
  today. A job that declares no GPU need and then reaches
  for one is misdeclared, and no mechanism here detects
  that; the venue's runtime is where it fails, as today.

- **FR-004 — `--lane auto` opts in; the default does not
  move.**
  *Behavior*: `gk submit --lane auto` submits a job with no
  lane (`lane: null`, `lane_request: "auto"`), resolved by
  the sweep under FR-006. `--lane` may be omitted only when
  config `lanes.auto_default` is true, in which case the
  omission means auto; with it false (the default —
  **RECOMMENDATION**, flagged in the PR), an omitted lane is
  the usage error it has always effectively been, so every
  existing submitter sees zero change unless it opts in.
  Resolution happens once, at the head of the first dispatch
  pass that sees the job submitted, and the resolved lane is
  written onto the record through the verified-write path —
  from then on the job is indistinguishable from an explicit
  submission to that lane. An auto job the pass cannot place
  stays lane-less and is skipped by the dispatch loop
  (never dispatched lane-less, never crashed on); it is
  re-evaluated every pass, so a lock release, a profile
  change, or a fresh capability snapshot can place it later
  with no resubmission.
  *Acceptance*: test — the battery pins an auto submit's
  record shape, resolution landing on the record at the
  pass, an omitted lane refused under `auto_default: false`
  and accepted as auto under `true` (a fixture config), and
  an unplaceable auto job surviving a pass untouched and
  un-dispatched.
  *This does not establish* that resolution is *prompt*
  beyond the sweep cadence — auto inherits the dispatch
  cadence, as every control in this system does.

- **FR-005 — Hosts carry a normalized capability snapshot,
  probed per platform, honest when unknown.**
  *Behavior*: a host's capability snapshot is a record in
  the gatekeeper's host-state file (`hosts.json`, beside the
  liveness record, under the host's `capability` key):
  `{platform, hostname, cpu_cores, cpu_model, cpu_load_1m,
  ram_total_mb, ram_free_mb, storage_free_gb, gpus:
  [{name, vram_mb, class}], venues, probed_at}` — where
  `class` is `discrete` | `unified` | `none`, `venues` lists
  the execution venues the probe actually found (`native`,
  `wsl`, `docker`), and every measured field may be null:
  unknown is recorded as unknown, never as zero. Snapshots
  are produced by parsing probe output. The probe scripts
  ship in `gatekeeper/probes/` (one per platform family) and
  print a sectioned report (`SYNCYARD_PROBE 1` … sections …
  `PROBE_END`); the parsers — one per platform — turn a
  report into the normalized record. Windows probes via CIM
  (`Win32_Processor` / `Win32_OperatingSystem` /
  `Win32_LogicalDisk`) plus `nvidia-smi` where present;
  Linux via `/proc/cpuinfo`, `/proc/meminfo`, `/proc/loadavg`,
  `df`, plus `nvidia-smi` where present; macOS via `sysctl`,
  `vm_stat`, `df`, and `system_profiler` — with **no wsl
  venue, ever** (the venue list is what the probe found, and
  WSL does not exist there), and an Apple-silicon GPU
  recorded as class `unified` whose `vram_mb` is the unified
  memory pool (the machine's RAM total) and flagged
  `vram_shared` — never presented as a discrete budget; an
  Intel Mac's discrete GPU parses its `VRAM (Total)` as
  discrete. Two recording routes, one parser: `gk probe-host
  --host <host>` dispatches the probe as a maintenance job
  through the door itself where this door has a transport
  for the host (a Windows host via `run.yml`, a WSL2 host
  via its `via` host's `wsl.yml` — the completed probe job's
  harvested log is parsed and stored by the watch pass), and
  `gk probe-host --host <host> --ingest <file>` records a
  report produced by running the shipped script on the host
  by any other means — the route for macOS and Linux hosts,
  for which this door has no transport workflow. A host type
  with no transport is refused by the dispatch route with
  the ingest route named (exit 3); an unparseable report is
  refused and nothing is stored. Storage writes go through
  the same host-state file discipline as liveness.
  Freshness: a snapshot older than `lanes.
  capability_max_age_min` (default 60 — **RECOMMENDATION**)
  is *stale*; FR-006 defines what stale costs.
  *Acceptance*: test — the battery proves all three parsers
  against recorded-style fixtures (Windows CIM + nvidia-smi
  text; Linux /proc + `df` + nvidia-smi; Darwin sysctl +
  `system_profiler` including the unified-GPU classification
  and the absent wsl venue), the ingest route storing a
  snapshot, the probe-job route's completion ingest, the
  no-transport refusal naming the ingest route, and an
  unparseable report storing nothing. Live probing of a
  real Mac is a **human-as-oracle** follow-up, named here
  and in the PR; it is not claimed by this change.
  *This does not establish* that a snapshot is *current*
  between probes — capability is a sampled record with a
  timestamp and a staleness rule, not telemetry. A machine
  that gains a GPU after its probe is a machine whose record
  is wrong until the next probe; `probed_at` is always on
  the record so the age of the claim is never hidden.

- **FR-006 — Auto scoring: eligible lanes only, deterministic,
  fully recorded.**
  *Behavior*: for an auto job, the sweep builds the candidate
  set from the active profile's resolved lanes and applies
  hard eligibility first. A lane is *ineligible* when: its
  host does not route the job's workflow (including a lane
  workflow pin); its host is under a going-down dispatch
  hold; it is ACTIVE-locked by another program and the job
  is not maintenance (spec 015 FR-002's rule, applied at
  routing time so auto can never resolve onto a lane the
  dispatch pass would then hold — and the dispatch pass
  re-checks the lock anyway, so a lock taken between
  resolution and dispatch still binds); a hard hint cannot
  be met — `--needs-gpu` requires a `gpu`-class lane, or an
  `any`-class lane on a host whose fresh snapshot lists a
  GPU (a config class assertion is declarative truth; an
  `any` lane borrows the probe's word), and a VRAM minimum
  requires a listed GPU at least that large; `--needs-ram-mb`
  / `--needs-cpu` / `--needs-storage-mb` require the fresh
  snapshot's totals to reach them (RAM and storage compare
  against totals/free as recorded: RAM against
  `ram_total_mb` — a job needing more than the machine has
  can never run there — storage against `storage_free_gb`);
  or the venue preference cannot be served — `docker`
  requires an image on the job (the existing `--image`
  support; an auto job has no lane host to default an image
  from, so the image must be the submitter's) and, on a
  fresh snapshot, `docker` among the host's venues; `wsl`
  requires the lane's host to serve `wsl.yml` or be a WSL2
  host, and a fresh Windows snapshot not listing `wsl`
  vetoes it; `native` excludes WSL2 and Docker-type hosts.
  Capability checks use a snapshot only when it is fresh
  (FR-005); a missing or stale snapshot makes every
  capability-derived check *neutral* (unknown never blocks,
  never counts as zero) and marks the evaluation
  **degraded** — routing then rests on duration bands, load,
  locks, holds, and history alone, which is today's routing
  information plus the band table. Each *eligible* lane is
  scored by a deterministic integer function of recorded
  inputs: a base of 100, minus load (15 per executing job on
  the lane, 5 per waiting one), plus/minus duration-band fit
  of the job's expected duration (its declared
  `expected_min`, else its program's history median, else no
  band term) against the lane's band (+20 inside, −40 below
  the band's floor, −10 above its ceiling), plus history
  affinity (the program's dominant historical resource class
  matching the lane's, +10; the lane the program last
  completed on, +5), plus a venue-preference match (+10),
  plus RAM headroom when a fresh snapshot shows free RAM at
  twice the declared need (+5). The winner is the highest
  score; ties break to the lexicographically first lane
  name. The full evaluation — hints, venue, history
  (sample count, median, dominant class), per-lane
  eligibility with the blocking reason or the score, the
  capability facts used per host (with `probed_at` and the
  stale flag), the degradation flag, the chosen lane and its
  score — is written onto the job record (`auto_route`) and
  ledgered (`auto_routed`; plus `auto_degraded` when
  degraded). When *no* lane is eligible, the job waits: the
  evaluation is recorded (`auto_wait`, with the per-lane
  reasons), `auto_no_fit` is ledgered with the unmet-reason
  set — once per distinct reason set, not once per sweep —
  and the job is never silently downgraded onto a lane that
  failed a hard check. History is computed from the queue
  records themselves: the program's ten most recent completed
  jobs, their run durations (first-seen-in-progress to
  completion, falling back to dispatch-to-completion), the
  median, and the dominant resource class of the lanes they
  ran on.
  *Acceptance*: test — the battery pins determinism (the
  same records evaluated twice yield the same lane and the
  same snapshot), a GPU hint landing on the gpu lane, a
  VRAM hint no listed GPU meets producing `auto_no_fit`,
  history influence (a program whose actuals are long routes
  its undeclared job to the long lane over quick), lock
  respect in both directions, host-hold exclusion, venue
  eligibility (docker without an image waits; wsl on a
  macOS-only profile has no candidate), staleness
  degradation (a stale snapshot's RAM total no longer
  blocks, and the event carries the degradation), and the
  `auto_routed` snapshot's completeness.
  *This does not establish* that the scoring weights are
  *right* — they are a stated, deterministic policy whose
  every decision is recorded with its inputs, so a bad
  weight shows up in the record as a bad decision with its
  reasons attached. Tuning the weights is future config
  work; what this change guarantees is that routing is
  eligible-only, explainable, and subordinate (FR-004,
  FR-007).

- **FR-007 — Auto is subordinate, and estimates are advisory
  forever.**
  *Behavior*: auto never overrides: an explicitly named lane
  (resolution simply never runs for such jobs); any human
  decision (a human priority, a focus selector, a lock, a
  hold — resolution composes with all of them as one more
  eligibility term, and the dispatch pass's own gates apply
  unchanged after resolution); the profile pin (candidates
  come only from the active profile on the fleet's own
  configured hosts — auto never invents a lane, a profile,
  or a host); and the budget guard (the sweep's stand-down
  still suppresses the whole dispatch pass, resolution
  included, exactly as it suppresses dispatch). And once a
  job is running, its routing is history: an auto-routed job
  that overruns its band or its estimate is never killed,
  cancelled, migrated, or re-laned mid-run — the existing
  watermarks make the overrun visible, and the job's actuals
  join its program's history for the next estimate (FR-006).
  *Acceptance*: test — the battery pins an explicitly laned
  job's record passing through a dispatch pass byte-identical
  in its routing fields, and a resolved auto job never being
  re-resolved by a later pass even when the landscape
  changes under it.
  *This does not establish* fairness between programs under
  auto: two programs' auto jobs contend under the same
  priority discipline as explicit ones (spec 013 FR-012),
  and a program that wants placement certainty has the
  explicit lane and the lane lock, unchanged.

### Part C — Visibility and config

- **FR-008 — Status shows the profile, the capabilities, and
  every auto decision's why.**
  *Behavior*: `gk status` prints the active profile (name,
  resolved lanes, whether auto is opt-in or default) beside
  the mode banners; a CAPABILITY section rendering each
  host's snapshot (platform, CPU cores, RAM total, GPU
  count, venues, `probed_at`, fresh/STALE) or `unknown (no
  probe recorded)`; and in the waiting list, an unresolved
  auto job renders lane `auto` with `[auto: no fit yet —
  <reasons>]` from its last evaluation, while a resolved
  auto job carries `[auto→<lane> score=<n>]` (plus
  `degraded` when its evaluation was) from its recorded
  snapshot. The main job table renders a lane-less job's
  lane as `auto`. `gk profile --list` / `--show` are
  FR-001's surface.
  *Acceptance*: test — the battery pins the status lines for
  the profile, a fresh and an unknown capability, and both
  waiting-list marks.
  *This does not establish* that status is a routing oracle
  — it renders recorded snapshots and decisions; the next
  pass may decide differently when the landscape changed.

- **FR-009 — Config and documentation currency.**
  *Behavior*: the routing knobs live in the `lanes` section
  (FR-001) validated fail-closed and mirrored exactly in
  `config/syncyard.example.yaml`, whose adopter examples are
  updated to the profile shape (a pre-016 flat example would
  teach a shape the loader now refuses). SKILL.md, the
  skills-pack source (and its generated editions), the
  gatekeeper README, and the CHANGELOG describe profiles,
  custom lanes, the hint flags, `gk profile`, `gk
  probe-host`, and auto routing in the same change, citing
  this spec. VERSION is NOT bumped by this change: spec 013
  owns 0.4.0 on this stack, and this branch merges behind
  it. CI runs the new battery with the rest.
  *Acceptance*: test — the config battery pins the golden
  profile values, the defaults==example surface including
  the profile table, valid overrides in the new shape, and
  the named rejections; the skills battery pins edition
  currency.
  *This does not establish* that an operator's existing
  `syncyard.yaml` survives unchanged — one in the pre-016
  flat `lanes:` shape now fails closed at load with the
  migration named (FR-002). Converting the fleet's file is
  part of the separate, gated deploy, and until then the
  fleet keeps running the gatekeeper it has.

## Non-requirements (explicit)

- **No runtime profile mutation.** There is no `gk profile
  --set`, no lane add/remove verb, no hot reload. Profiles
  are config; config changes land at process boundaries, as
  every config change in this system does.
- **No preemption, migration, or re-laning of running work.**
  Inherited from specs 013/015 unchanged: routing is a
  dispatch-time act; a running run is never touched, and a
  dispatched job's lane never changes.
- **No learned or probabilistic routing.** The scorer is a
  deterministic integer function of recorded inputs. History
  enters as recorded medians and class counts — there is no
  model, no randomness, and no hidden state; identical
  records at an identical landscape give the identical lane.
- **No cross-host or cross-profile routing.** Candidates are
  the active profile's lanes on the configured hosts. Auto
  is not a failover mechanism and never reroutes around a
  down host (spec 010 FR-004's rule, unchanged) — a held
  host's lanes are ineligible, and the job waits.
- **Venue scope, stated honestly.** In this spec a venue
  preference is an *eligibility* condition plus a scoring
  bonus (FR-006) — auto does not rank venues beyond that,
  does not choose between two equally-eligible venues on
  cost or speed evidence (none is recorded), and does not
  translate a job between venues: a `docker` job still runs
  through the existing image transport on its lane's host, a
  `wsl` job through `wsl.yml`. This does not establish
  venue *optimization* — only venue-aware eligibility,
  recorded like every other term.
- **No live-Mac claim.** The Darwin parser is proven against
  recorded-style fixtures; probing a real Mac end-to-end is
  the named oracle follow-up (FR-005), not a claim of this
  change.
- **No change to the fleet by this change.** Deployment of
  the new gatekeeper (and conversion of any fleet
  `syncyard.yaml` to the profile shape) happens later,
  separately, on the owner's review.

## Acceptance shape

The spec lands behind its scenario battery: fixtures pin
profile validation (each named rejection) and fleet-default
equivalence against the pre-016 constants; a from-scratch
custom profile dispatching end-to-end; hint recording; auto
scoring determinism; hint satisfaction and `auto_no_fit`;
history-median influence; lock respect; focus conjunction
preserved across resolution; capability staleness
degradation and unknown-field neutrality; all three platform
parsers against fixtures; venue eligibility; explicit-beats-
auto; and the `auto_routed` ledger's completeness. The
pre-existing batteries stay green with exactly the deliberate
config-battery fixture updates this spec's schema change
requires (the section-4 merge fixture and the lanes-shaped
rejection fixtures move to the profile shape; the golden
lane checks read the *resolved* table, which is the
equivalence FR-001 promises). Per the fleet rule, CI green
is the finish line for the repo change; the fleet deploy is
a separate gated act (FR-009's note).

# Feature Specification: Tray Liveness Heartbeat (hardening H1)

**Feature Branch**: `feat/025-tray-liveness-heartbeat`

**Created**: 2026-11-05

**Status**: **Ratified** (owner, 2026-11-05 ~18:47 ET: "I ratify
all specs and approve all decisions based on your
recommendations"). Execution order for the hardening batch:
H1 first. This change carries the spec and the implementation.

**Direction**: the tray hardening batch from the 2026-11-05
tray red team (TRAY-REDTEAM-2026-11-05.md, item H1; attacks 1,
14, 21; inventory section 1.10).

## The gap

The tray emits no heartbeat. Its only liveness records are a
local log line and its process, so on both fleet machines "the
tray is gone" is discoverable only by a human looking at the
taskbar. The field has proven the shape twice: the 2026-11-04
morning disappearance of the desktop fleet tray (an open defect
the owner found, not any monitor) and the owner's 2026-11-05
report, "what happened to the tray? It disappeared." The
backchannel heartbeat attests the agent on the same machine,
never the tray, and the fleet-status snapshot carries no
per-host tray state, so no off-box watcher can distinguish
"the host is down" from "the host is fine and its tray is
dead" — the second state being the one that silently ends
runner supervision on that machine.

## The design

Three parts, all riding transport the fleet already has:

- **The tray beats.** The tray writes a small heartbeat file,
  `tray-heartbeat.json`, beside its log (the `%LOCALAPPDATA%\SyncyardTray`
  directory), about every 30 s and once at startup. The record
  carries: `host` (the gatekeeper config host name, from the
  `heartbeatHost` settings key, default `desktop` — the host
  guard's convention, runner/guard/SyncyardGuard.cs), `build`,
  `pid`, `session`, `started_at`, `ts`, `tick`, and `lanes`
  (one entry per supervised lane: `lane`, `running`,
  `faulted`). The write is atomic: a temp file beside the
  target, then replace — the pattern the backchannel agent
  already uses for its own heartbeat writes.
- **The agent carries it.** The backchannel agent merges the
  tray's file into its heartbeat payload under a `tray` key,
  exactly the way it already merges the host guard's records
  (agent.sh `write_heartbeat`): verbatim, non-object or
  unreadable files skipped, a bad tray file never fails the
  heartbeat. The source path is `SYNCBC_TRAY_FILE`, defaulting
  to the desktop's `%LOCALAPPDATA%` layout under `/mnt/c`; a
  second machine overrides it by environment, the same
  per-machine discipline as `SYNCBC_GUARD_FILE`. No new
  transport, no new network surface.
- **The gatekeeper judges.** The sweep's liveness pass stores
  the beat on the host's record and judges it the way it
  already judges the guard's beat (spec 011 T007): relative to
  the heartbeat carrying it, because both timestamps come from
  the host's own clock. A tray beat older than
  `liveness.tray_stale_after_s` (default 300 s) relative to its
  carrier means the agent is alive and the tray is dead. The
  transition ledgers exactly once (`tray_stale` /
  `tray_recovered`), and `gk status` shows the beat on the
  host line, with a `TRAY STALE (agent alive, tray not
  reporting)` marker while stale. A host whose agent is also
  dead reads DOWN by the existing liveness rule, not tray
  stale: this judge fires only on the live-host/dead-tray
  shape.
- **A bounded restart exists on both machines.** The tray
  package gains `runner/tray/tray-watchdog.ps1`: a one-shot
  script a 1-minute scheduled task runs (the desktop's loose
  vbs watchdog's shape, now versioned in the repo and
  identical on both machines). Each run: if the tray exe is
  missing, it writes an owner-needed marker and does nothing
  else (a quarantined or half-swapped exe is an alarm, not a
  relaunch loop); if a stopped-by-user marker stands (the
  tray's Exit path writes `tray-stopped-by-user.json` beside
  its log, and any tray start clears it), it stands down; if
  the tray process is alive, it stands down; otherwise it
  relaunches the tray, recording the relaunch in
  `tray-watchdog-state.json` beside the log and capping
  relaunches at 5 per hour — past the cap it writes the
  owner-needed marker instead of relaunching, so a crash loop
  becomes an alarm. Spec 029 (H5) packages the install of
  this watchdog; this spec defines and ships the mechanism.

## Requirements

- **FR-001**: The tray MUST write `tray-heartbeat.json` beside
  its log at startup and on a ~30 s wall-clock cadence while
  it runs, atomically (temp file + replace), with the fields
  named in the design. A failed write MUST NOT kill or stall
  the tray; the failure is logged on its first occurrence
  until a write succeeds again.
- **FR-002**: The beat MUST carry the tray's real per-lane
  state at write time (a lane at its restart cap reads
  `faulted: true`), its build stamp, its pid and session, and
  the gatekeeper host name from settings (`heartbeatHost`,
  default `desktop`; the settings writer MUST emit exactly
  the keys the reader consumes, battery-proven as for every
  settings key).
- **FR-003**: The backchannel agent MUST merge a present,
  non-empty tray object into its heartbeat payload under the
  key `tray`, from `SYNCBC_TRAY_FILE` (or its default), and
  MUST skip a missing, unreadable, or non-object file without
  failing the heartbeat — the guard merge's contract, verbatim.
- **FR-004**: The gatekeeper MUST store each tray beat on its
  host's liveness record and MUST judge staleness relative to
  the carrying heartbeat's `ts` against
  `liveness.tray_stale_after_s` (config-validated positive
  number, default 300). Transitions MUST ledger exactly once
  (`tray_stale`, `tray_recovered`).
- **FR-005**: `gk status` MUST render the tray beat on the
  host line (beat time and build) and MUST mark a stale beat
  distinctly as the agent-alive/tray-dead state. A host with
  no tray beat recorded renders no tray claim at all — the
  honest unknown, never a guessed state.
- **FR-006**: The packaged watchdog (`runner/tray/tray-watchdog.ps1`)
  MUST: relaunch an absent tray whose exe exists; refuse to
  relaunch past 5 relaunches in any rolling hour, writing an
  owner-needed marker instead; write the owner-needed marker
  and never attempt a launch when the exe is missing; honor
  a standing stopped-by-user marker; and record every
  decision (relaunch, stand-down reason, cap, missing exe)
  in its log beside the tray log. The tray's Exit path MUST
  write the stopped-by-user marker and any tray start MUST
  clear it.
- **FR-007**: The test batteries MUST fail before and pass
  after: the tray source battery pins the heartbeat writer
  (fields, atomic write, cadence, settings parity, the Exit
  marker), the agent source battery pins the merge, and a
  gatekeeper scenario battery proves the judge (fresh beat
  stored and not stale; a beat lagging its carrier past the
  bound is stale, ledgered once across repeat sweeps, and
  shown in the status lines; a fresh beat recovers, ledgered
  once).

**Falsifier**: Run the venue tray, kill it with the agent
still beating, and watch one sweep: if `gk status` does not
show the host's tray as stale (or the ledger carries no
`tray_stale`) while the agent heartbeat advances, this spec
has failed. Equally: if a dead agent (host DOWN) ever renders
as tray stale, or a tray beat from before this spec's field
deploy renders as anything but absent, the judgement is
wrong and the spec has failed.

## Tasks

- T001: This spec.
- T002: Tray heartbeat writer + `heartbeatHost` setting +
  Exit stopped-by-user marker (tray build 0.4.4.0).
- T003: Agent merge under `tray` (SYNCBC_TRAY_FILE).
- T004: Gatekeeper judge: config key, hosts record, sweep
  ingest, status line.
- T005: Packaged watchdog script + scheduled-task template.
- T006: Batteries: tray source, agent source, gatekeeper
  scenario (test_spec025.py) + CI wiring.
- T007 (field, Desktop Access chat, not this change): deploy
  tray 0.4.4.0 and the watchdog to both machines, set
  `heartbeatHost` and `SYNCBC_TRAY_FILE` per machine, and
  prove the judge end to end from the live records.

## Out of scope

The watchdog's installer integration and the retirement of
the desktop's loose vbs (spec 029), the tray version the
beat carries being *judged* for drift (spec 027 — this spec
only carries the build), and any change to the agent's
transport or the gatekeeper's heartbeat read path.

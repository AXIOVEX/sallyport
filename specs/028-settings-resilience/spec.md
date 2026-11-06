# Feature Specification: Settings Resilience, Per-Machine Lane Sets, and Multi-Instance Runner Supervision (hardening H4)

**Feature Branch**: `feat/028-settings-resilience`

**Created**: 2026-11-05

**Status**: **Ratified** (owner, 2026-11-05 ~18:47 ET). The
batch scope as ratified names this spec as carrying two
related fixes alongside settings resilience: the per-machine
lane-set fix and multi-instance runner supervision
(owner decision 2026-11-05 ~12:40 ET, reaffirmed ~14:31 ET:
"Keep it bundled with the hardening specs"; per-machine lane
rule: the GPU lane applies only to machines with GPUs).
Execution order: after spec 027 (H3). This change carries
the spec only.

**Direction**: the tray hardening batch from the 2026-11-05
tray red team (TRAY-REDTEAM-2026-11-05.md, items H4 and H14;
attacks 5, 6, 7, 20), plus the owner's per-machine lane-set
decision and the parallel-lanes field state of 2026-11-05
(desktop normal lane running three runner instances against
one tray-built lane row).

## The gaps

1. **Settings are one torn write away from another machine's
   defaults.** `TraySettings.Save` is a single
   `File.WriteAllText` on the live path — no temp file, no
   backup. An interrupted save (kill, power, full disk)
   corrupts the file, and the loader's unreadable-file path
   returns compiled `Defaults()` wholesale. Those defaults
   are desktop-shaped (runner root, fleet path, and the
   desktop channel's heartbeat path under the desktop's
   profile), so on the laptop a corrupt settings file makes
   the tray present a live icon while supervising nothing
   and judging its connection from the wrong machine's
   signals. Deleting the file has the same shape: tuned
   values (the desktop's `fleetStaleSeconds` 900) silently
   revert to compiled ones (300), shorter than the mirror
   cadence, so a healthy fleet flags STALE half of every
   cycle.
2. **The lane set is compiled in.** The tray builds its
   runner table from four hardcoded lane names
   (long/quick/normal/gpu) off the runner root. The laptop
   has no GPU and its lanes are long and quick, yet its tray
   draws lane-normal and lane-gpu rows for lanes that do not
   exist there — the owner was told to ignore two menu rows
   on that machine. Lane existence is a property of the
   machine, not of the build.
3. **One lane, one instance.** The gatekeeper now runs the
   desktop normal lane at concurrency 3 with three registered
   runner instances (normal, normal2, normal3). The tray
   models exactly one runner directory per lane, so the two
   extra instances are outside its supervision: nothing
   restarts them durably, and their liveness rests on a
   manual start through the backchannel. Supervision must
   cover every instance of a lane the machine carries.

## The design

- **Atomic save with last-good fallback.** Save writes a
  temp file beside the settings file and replaces, keeping
  the previous good file as `tray-settings.json.bak`. Load
  tries the live file, then the `.bak`, then defaults — and
  the note it logs names which source won, so a fallback is
  a recorded event, not a silent one.
- **Per-machine default profiles.** The defaults fallback is
  no longer only compiled constants: the installer writes a
  per-machine profile (`tray-profile.json` beside the exe:
  runner root, fleet path, heartbeat channel path,
  `heartbeatHost`, stale bounds, and the machine's lane set)
  and Load's last resort is the machine's profile, not
  another machine's shape. Compiled defaults remain only as
  the no-profile-anywhere floor.
- **A tray supervising nothing says so.** If the runner root
  does not exist, or no enabled lane directory exists under
  it, the menu header and the tray heartbeat (spec 025) carry
  "supervising nothing: runner root missing" instead of a
  normal presentation.
- **The lane set comes from the machine.** The runner table
  is built from the profile's lane set (falling back to the
  directories actually present under the runner root), so a
  machine's tray draws exactly its own lanes — the laptop
  shows long and quick, the desktop shows its four. The
  `laneEnabled_*` settings keys key off that set.
- **Multi-instance supervision.** A lane's entry in the
  profile may name several instance directories (the
  desktop's normal lane: `normal`, `normal2`, `normal3`).
  The tray supervises each instance under its lane: state,
  restart cap accounting (per instance, disk-persisted per
  gap 3 below), and the lane row aggregates its instances.
  The heartbeat reports per-instance state under its lane.
- **Restart state survives the tray.** Runner restart
  history and Faulted state persist to disk (the
  runner-watchdog.ps1 `restarts.json` pattern the tray
  regressed to in-memory state): a tray restart no longer
  zeroes a crash-looping lane's cap (red team H14).

## Requirements

- **FR-001**: Settings save MUST be atomic (temp + replace)
  and MUST keep the last good file as `.bak`; load MUST fall
  back live file → `.bak` → machine profile → compiled
  defaults, logging which source won whenever it is not the
  live file.
- **FR-002**: The tray MUST read its per-machine default
  profile from data installed beside the exe (never compiled
  constants naming another machine's paths); the profile
  carries at least runner root, fleet path, heartbeat path,
  `heartbeatHost`, stale bounds, and the lane set.
- **FR-003**: When the runner root is missing or no enabled
  lane directory exists under it, the tray MUST present
  "supervising nothing" in its menu header and in its
  heartbeat, and MUST NOT present a normal all-up state.
- **FR-004**: The tray's runner table MUST be built from the
  machine's lane set; a lane with no directory on the machine
  MUST NOT appear in the menu, the status window, or the
  heartbeat.
- **FR-005**: A lane with multiple instance directories MUST
  be supervised per instance (liveness, restart, cap), with
  the lane's surfaces aggregating its instances; the
  heartbeat MUST report each instance's state.
- **FR-006**: Restart history and Faulted state MUST persist
  across tray restarts (disk-backed), so the restart cap is
  a property of the lane instance, not of the tray process.
- **FR-007**: Listener identity MUST be the exact runner
  directory (the listener exe's directory equals the lane
  instance dir), never a path-prefix match, in both the tray
  and `runner/runner-watchdog.ps1` (red team H14).

**Falsifier**: In the venue: corrupt the settings file
mid-save (kill during write) and restart the tray on a
laptop-shaped profile — if it loads anything but the last
good settings or the laptop profile, or presents a normal
icon while its runner root is absent, this spec has failed.
Equally: stop one of three instances of a multi-instance
lane — if the tray neither restarts it nor reports it in
the heartbeat, the spec has failed.

## Tasks

- T001: This spec.
- T002: Atomic save + `.bak` + load fallback chain.
- T003: Machine profile (schema, installer write, load).
- T004: Supervising-nothing surface (menu + heartbeat).
- T005: Lane set from profile/directories.
- T006: Multi-instance supervision + persisted restart state.
- T007: Exact-directory listener identity (tray + watchdog).
- T008: Batteries: tray source pins for each behavior,
  settings round-trip parity kept.

## Out of scope

The tray heartbeat itself (spec 025), the watchdog package
(spec 029), and gatekeeper-side lane configuration (spec
016 owns adaptive lanes; this spec is the tray's view of
the machine it runs on).

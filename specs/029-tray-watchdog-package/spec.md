# Feature Specification: One Packaged Tray Watchdog (hardening H5)

**Feature Branch**: `feat/029-tray-watchdog-package`

**Created**: 2026-11-05

**Status**: **Ratified** (owner, 2026-11-05 ~18:47 ET, the
tray hardening batch). Execution order: after spec 028
(H4). This change carries the spec only. The watchdog
mechanism itself (script, decision rules, caps, markers) is
defined and shipped by spec 025 (H1) FR-006; this spec makes
it an installed, versioned part of every machine's tray
install.

**Direction**: the tray hardening batch from the 2026-11-05
tray red team (TRAY-REDTEAM-2026-11-05.md, item H5; attacks
1, 10, 14, 15).

## The gap

Today the desktop's tray watchdog is a loose `tray-watchdog.vbs`
that exists only on that machine: not in this repo, not
versioned, no test battery, and its relaunch proof is a local
log. The laptop has no watchdog on record at all — its tray's
fatal-crash gaps ran 39 and 59 minutes and ended only when an
install job or a human started a tray. Autostart state is
similarly unaudited: three mechanisms coexist in the field
(the NSIS Run value, a laptop logon task, the desktop
watchdog task) and none checks the others, and the desktop
watchdog cannot tell an owner's Exit from a death because
the tray wrote no stopped-by-user marker (spec 023 FR-007
gap; spec 025 now has the tray write and clear that marker).

## The design

- The tray installer (the NSIS package and `install-tray.ps1`,
  one behavior) installs the packaged watchdog from spec 025:
  `runner/tray/tray-watchdog.ps1` plus its scheduled-task
  definition (1-minute repetition, the installing user's
  session, windowless), rendered with the machine's real
  paths from its profile (spec 028). Installing the tray and
  installing its watchdog are one act.
- The installer verifies the result from records: the task
  exists and is enabled, the script on disk hashes to the
  packaged source, and the first watchdog log line appears.
- The same install retires the desktop's loose vbs watchdog
  (task removed, files left in place as history, the
  retirement recorded) so exactly one watchdog generation
  exists per machine.
- Autostart audit: the watchdog's hourly pass (its state
  file's cadence, not a second task) checks the machine's
  armed autostart (the Run value the tray's own discipline
  owns) and the task's own enabled state, logging drift
  instead of silently repairing state the owner may have
  set deliberately — drift is a log + heartbeat-visible
  owner-needed marker, not a silent rewrite.
- The watchdog's log rotates (size cap with one backup, the
  gatekeeper.log pattern) — red team H10's rule applied to
  the watchdog's own log here; the tray's and agent's log
  rotation stay their own follow-ups.

## Requirements

- **FR-001**: The tray installer MUST install the packaged
  watchdog (script + scheduled task) on every machine it
  installs the tray on, with the task enabled, running the
  packaged script from the install directory, on a 1-minute
  repetition, windowless.
- **FR-002**: The installer MUST prove the install from
  records (task present and enabled, script sha256 equal to
  the packaged source, a first watchdog log line) and MUST
  report any leg it could not prove as not installed, never
  as installed.
- **FR-003**: Installing on the desktop MUST retire the
  loose pre-repo watchdog task in the same install, and the
  retirement MUST be recorded in the install log.
- **FR-004**: The watchdog MUST audit the machine's armed
  autostart and its own task state on an hourly cadence and
  MUST surface drift as an owner-needed marker (visible via
  the tray heartbeat's directory and the watchdog log), and
  MUST NOT silently change autostart state.
- **FR-005**: The watchdog log MUST rotate at a size cap
  with one backup.
- **FR-006**: The install battery (source pins plus the
  installer's own checks) MUST cover: task definition
  renders with no unsubstituted token, script hash pin,
  and the retirement step's presence. The decision rules
  themselves are spec 025's battery, unchanged.

**Falsifier**: Install the tray fresh on a venue machine:
if the tray can be killed and stay dead past ~60 s with no
watchdog log line explaining why, this spec has failed.
Equally: if the desktop still carries two watchdog
generations after the install, the retirement leg failed.

## Tasks

- T001: This spec.
- T002: Installer integration (NSIS + install-tray.ps1).
- T003: Task template rendering from the machine profile.
- T004: Loose-vbs retirement on the desktop pattern.
- T005: Autostart audit + log rotation in the watchdog.
- T006: Install battery coverage.

## Out of scope

The watchdog's decision rules and caps (spec 025), tray and
agent log rotation (red team H10 follow-up), and any change
to the Run-value discipline itself (spec 014 FR-005 stands).

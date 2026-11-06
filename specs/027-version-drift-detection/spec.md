# Feature Specification: Version Drift Detection (hardening H3)

**Feature Branch**: `feat/027-version-drift-detection`

**Created**: 2026-11-05

**Status**: **Ratified** (owner, 2026-11-05 ~18:47 ET, the
tray hardening batch). Execution order: after spec 026 (H2).
This change carries the spec only.

**Direction**: the tray hardening batch from the 2026-11-05
tray red team (TRAY-REDTEAM-2026-11-05.md, item H3; attacks 2,
14, 30).

## The gap

Nothing notices when a fleet machine runs an old tray. The
laptop sat two builds behind (0.4.0.0 running against a
published 0.4.1.0, with 0.4.2.0 already merged) with a broken
update click path, so it could neither catch up nor be
caught. Separately, the tray carries multiple version truths
that can drift apart silently: the `.nsi` VERSION define
(which becomes the installer's DisplayVersion), the assembly
attributes, and the `BuildId` string the logs and update
comparisons use (the source comment at the stamp admits a
drift can mislabel).

## The design

- **Drift judgement.** The tray heartbeat (spec 025) already
  carries the running build per host. The gatekeeper compares
  each host's reported build against the current tray release
  (the latest published tray release's version, read from the
  release manifest the updater itself consumes, cached on the
  sweep's existing cadence — no new poll loop) and records a
  per-host drift state on the host record: `current`,
  `behind` (with the running and current versions), or
  `unknown` (no beat, or the release read failed — an unread
  manifest is never read as "everyone is current").
  Transitions ledger once (`tray_drift`, `tray_current`).
  `gk status` shows the drift on the host line, beside the
  tray beat.
- **One version truth at the source.** The `.nsi` VERSION
  define, the assembly version attributes, and the `BuildId`
  string are pinned equal by the tray source battery on every
  build (the battery extracts all three and asserts equality),
  so the stamps the drift judge compares can only move
  together.

## Requirements

- **FR-001**: The gatekeeper MUST record, per host with a
  tray beat, whether the reported build equals the current
  published tray release version, and MUST ledger transitions
  exactly once (`tray_drift` carrying both versions,
  `tray_current`).
- **FR-002**: A failed or absent release-manifest read MUST
  leave the drift state `unknown`, never `current`; the
  status line MUST render `unknown` as unknown.
- **FR-003**: `gk status` MUST show each host's tray build
  and its drift state on the host line.
- **FR-004**: The tray source battery MUST assert the `.nsi`
  VERSION define, the assembly `AssemblyVersion` /
  `AssemblyFileVersion`, and the `BuildId` string are all
  equal, and MUST fail when any one moves alone.
- **FR-005**: The manifest read MUST ride an existing sweep
  cadence and cache; it MUST NOT add a per-status or
  per-minute poll (fleet GitHub discipline: the release read
  is not a contents-API heartbeat read and is budgeted
  accordingly).

**Falsifier**: Point a host's tray beat at an old build (or
run an old tray in the venue): if `gk status` does not show
that host behind within one sweep of the beat arriving, this
spec has failed. Equally: break the manifest read and show
any host as `current` — that is the failure this spec exists
to prevent.

## Tasks

- T001: This spec.
- T002: Gatekeeper drift judge + host record + status line.
- T003: Source-battery version-truth pin.
- T004: Scenario battery coverage (behind, current, unknown).

## Out of scope

Forcing or performing the update (install stays the owner's
click, spec 021), the gh extension's own version drift, and
publisher/agent version drift.

# Feature Specification: CI Compiles the Tray (hardening H2, remainder)

**Feature Branch**: `feat/026-tray-ci-build`

**Created**: 2026-11-05

**Status**: **Ratified** (owner, 2026-11-05 ~18:47 ET, the
tray hardening batch). Execution order: after spec 025 (H1).
This change carries the spec only; implementation is its own
follow-on change.

**Direction**: the tray hardening batch from the 2026-11-05
tray red team (TRAY-REDTEAM-2026-11-05.md, item H2 and
BACKCHANNEL-ROBUSTNESS-GAPS-2026-11-04.md section 4, item 3).

## Scope note

H2 as originally framed had three parts: field-deploy tray
0.4.2.0 through the broken updater, prove the genuine
self-update once from 0.4.2.0, and add CI compilation of the
tray. The first two are **done** (field record 2026-11-05:
0.4.2.0 installed and verified on both machines, and the
laptop tray log carries the genuine self-update proof, an
update check reading "up to date" against the published
manifest from the tray's own code path). What remains — and
all this spec covers — is the CI build.

## The gap

CI never compiles the tray. `runner/tray/test_tray_source.py`
states it in its own header: the battery is source level
because CI has no .NET Framework compiler. The 2026-11-04
menu crash class (an ObjectDisposedException rooted in menu
lifetime) is exactly the defect family a compiler plus a
smoke run catches and a source battery can only pattern-pin:
the 0.4.3.0 build needed three desktop build attempts, two
of them compile failures (CS2005, CS1061) found by the field
build venue, not by any check that runs before a human does.

## The design

- A CI job on a Windows GitHub-hosted runner (the CI
  workflow's standing rule stands: GitHub-hosted only, never
  the self-hosted fleet) compiles `runner/tray/SyncyardTray.cs`
  with the in-box csc route the field builds use (the compile
  line in the source header), producing the exe as a job
  artifact.
- The job asserts the built exe's identity stamps (product,
  company, file version) equal the source's assembly
  attributes and the release version being built — the H3
  discipline (spec 027) applied at build time.
- A smoke step launches the built exe in the hosted session
  with a scratch settings file and a stub runner root, waits
  for its first heartbeat file (spec 025) and its log's start
  lines, and stops it. A build that compiles but cannot start
  fails the job.
- The compile runs on every PR that touches `runner/tray/`,
  and on the release path. The source battery keeps running
  as-is; this job adds the compiler and the run, it does not
  replace source-level pins.

## Requirements

- **FR-001**: CI MUST compile the tray from source on a
  GitHub-hosted Windows runner for every pull request
  touching `runner/tray/`, and MUST fail the check when csc
  fails. No step may run on a self-hosted runner.
- **FR-002**: The job MUST verify the built exe's identity
  stamps against the source's declared identity (assembly
  version, file version, product, company) and fail on any
  mismatch — a build that compiles to the wrong identity is
  a failed build.
- **FR-003**: The job MUST smoke-run the built exe with
  scratch state (its own settings, log dir, and stub runner
  root — never any fleet path), MUST observe its startup
  from its own records (log start lines and a first
  heartbeat file per spec 025), and MUST fail if neither
  appears within a bounded wait.
- **FR-004**: The existing tray source battery MUST keep
  passing unchanged in behavior; this spec adds coverage, it
  does not retire source pins.

**Falsifier**: Introduce a deliberate compile error (and,
separately, a build whose identity stamps disagree with the
source) on a branch: if either PR shows a green tray check,
this spec has failed.

## Tasks

- T001: This spec.
- T002: CI job (compile + identity assertion + artifact).
- T003: Smoke run step against scratch state.
- T004: Falsifier runs on throwaway branches, recorded.

## Out of scope

Release publishing from CI (releases stay the owner's pull
model), code signing (spec red team H15, owner purchase),
and any change to the tray source itself.

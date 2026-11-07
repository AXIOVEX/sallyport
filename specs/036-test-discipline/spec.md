# Feature Specification: Test Discipline and Traceability (the binding gate)

**Feature Branch**: `feat/036-test-discipline`
**Created**: 2026-11-07
**Status**: **RATIFIED 2026-11-07 as a block with specs 034-039 (owner decision, Desktop Access chat). Implementation may proceed under the program sequence.**
**Direction**: Owner test-quality order 2026-11-06 11:40 ET: tests and coverage are not good enough; every defect class that reached the field is a missing requirement plus a missing test; full spec-kit + AEE traceability. Measured gap at order time: **zero** existing specs carry a test-case section; the CI battery is ~20 source-pinning scripts with no field-behavior simulation and no traceability enforcement.
**Companions**: All of specs 034, 035, 037, 038, 039 are authored under this discipline: every FR carries at least one named test case and one acceptance mode in the spec text.

## The rule set

### 1. The traceability matrix

A matrix is **generated** from the spec files (never hand-maintained): every FR in every ratified spec maps to at least one named test case and exactly one primary acceptance mode — `test`, `human-as-oracle`, or `gate`. CI fails if: an FR has no test, a named test does not exist in the suite, a test names no FR, or an acceptance mode is missing. "100% coverage" in this program means exactly this matrix is green plus the per-crate line-coverage bars in section 4 — it never means a rounded-up claim.

### 2. Field-simulation suites

Per surface, the suite simulates field behavior, not just source shape:

- **Tray rendering fixtures**: captured window/icon fixtures checked visually (icon presence, tab titles, CRLF rendering, scroll behavior) at desktop and small widths, per spec 035.
- **Runner-log noise fixtures**: real runner chatter (including the BrokerServer cancellation dump) must never surface as an agent error.
- **Ghost-record fixtures**: queue/GitHub state pairs reproducing the ghost geometry (finished run, record still dispatched) for the dispatch census and reconciliation (spec 038).
- **Wave fixtures**: save paths exercised under simulated write denial (EPERM on write-family calls) for every state writer.
- **Fault-injection fixtures**: the daemon killed at every dispatch-protocol step (red-team C2 / ERR-01).
- **Mixed-version fixtures**: component pairs at train N / N-1 per the compatibility matrix (red-team M6 / UPD-08).

### 3. Defect-to-test rule

No field defect closes without a failing-before test added in the same change, **named after the defect**. A test that never failed on the defective code does not satisfy the rule. Defect-named tests already required by this package include: `single-path-publisher-defect-2026-11-06`, `stranded-lock-defect-2026-11-06`, `stray-frun-marker-lines-2026-11-06`, `laptop-two-tray-fight-2026-11-06`, `non-elevated-raw-write-2026-11-06`, `elevated-raw-device-open-2026-11-06`.

### 4. Coverage bars

Rust core line coverage is measured per crate and reported per release. Bars (initial, ratchetable only upward by owner ruling): gatekeeper daemon crate >= 90%; CLI crate >= 85%; tray/app crate >= 80%; backchannel agent crate >= 85%; shared contract crate >= 95%. A release below any bar fails the gate regardless of test pass state. Coverage is reported as measured, never rounded up.

### 5. Release gate

No release (any component, any train) ships unless: the traceability matrix is green, all field-simulation suites pass, coverage bars are met, the fault-injection suite passes, and the release's compatibility matrix (UPD-08) is published with it. The gate is mechanical (CI), not a human checklist.

### 6. Owner-manual recovery drill (red-team M8)

Per release train, one owner-manual recovery drill per critical component: the owner, a written runbook, no agent involvement. The runbook itself is a named program deliverable with an owner and a review cadence (missing program item 8). A train whose drill has not been run and recorded does not ship.

### 7. Scenario: unattended degradation (red-team M8)

A scenario test proves the fleet degrades safely under 24 hours of zero agent attention, including a VM replacement and a write wave: jobs pause, nothing corrupts, state is recoverable.

### 8. Restore verification (missing program item 1)

Backups are believed only when restored. A scheduled restore-verification requirement with a named test: restic snapshots restore to a scratch target on a stated cadence and the restored tree verifies against its manifest.

## Requirements

| FR | Requirement | Named test case(s) | Acceptance mode |
|---|---|---|---|
| FR-036-01 | The traceability matrix MUST be generated from the spec files in CI; an FR with no named test, a named test missing from the suite, a test naming no FR, or a missing acceptance mode MUST fail CI. | T036-01 matrix-generation-completeness; T036-02 matrix-fails-on-orphan-fr | gate |
| FR-036-02 | Every FR in specs 034-039 MUST carry at least one named test case and one acceptance mode (test / human-as-oracle / gate) in the spec text itself. | T036-03 package-fr-table-audit | gate |
| FR-036-03 | Field-simulation suites MUST exist per surface: tray rendering fixtures, runner-log noise fixtures, ghost-record fixtures, wave fixtures, fault-injection fixtures, mixed-version fixtures. | T036-04 field-simulation-suites-present-and-run | gate |
| FR-036-04 | No field defect MUST close without a failing-before test named after the defect in the same change. | T036-05 defect-to-test-audit | gate |
| FR-036-05 | Rust core line coverage MUST be measured per crate against the bars in section 4 and reported as measured; below-bar MUST fail the release gate. | T036-06 coverage-bars-enforced | gate |
| FR-036-06 | No release MUST ship unless matrix green + simulation suites pass + coverage bars met + fault-injection suite passes + compatibility matrix published. | T036-07 release-gate-composition | gate |
| FR-036-07 | The fault-injection suite MUST kill the daemon at every dispatch-protocol step and prove zero double dispatches and zero lost dispatches (ERR-01). | T036-08 fault-injection-dispatch-protocol | test |
| FR-036-08 | Per release train, one owner-manual recovery drill per critical component MUST be run and recorded (owner + runbook, no agent) before the train ships (red-team M8). | T036-09 owner-recovery-drill-record | human-as-oracle |
| FR-036-09 | A scenario test MUST prove safe degradation under 24 h of zero agent attention including a VM replacement and a write wave (red-team M8). | T036-10 unattended-24h-degradation | test |
| FR-036-10 | Backup restore verification MUST run on a stated cadence: a restic snapshot restores to scratch and verifies against its manifest (missing item 1). | T036-11 restore-verification-drill | test |
| FR-036-11 | A macOS compile-only CI target MUST build every Rust crate from the first release, so portability debt is measured continuously (red-team L1; full host is spec 039). | T036-12 macos-compile-only-ci | gate |
| FR-036-12 | An automated token-scope audit MUST run in the release suite proving per-function credentials are least-privilege (red-team C5). | T036-13 token-scope-audit | test |
| FR-036-13 | History compaction MUST preserve a verifiable hash chain over the pre-compaction ledger; chain verification MUST be a release test; compaction MUST be forbidden during a detected write wave (red-team L3). | T036-14 compaction-hash-chain-verified; T036-15 compaction-blocked-in-wave | test |
| FR-036-14 | The gh extension's command surface MUST be generated from the CLI's own schema; a CI drift test MUST fail when they disagree (red-team L2). | T036-16 extension-schema-drift | gate |
| FR-036-15 | Runner-log noise fixtures MUST prove normal GitHub runner chatter (BrokerServer cancellation dump class) never surfaces as an agent error. | T036-17 runner-noise-not-an-error | test |
| FR-036-16 | Wave fixtures MUST exercise every state writer's save path under simulated write denial; no writer may die on an uncaught write error (ERR-08 test half). | T036-18 wave-fixture-all-writers | test |

## Falsifiers

- A release ships with any FR in any ratified spec lacking a passing named test: this spec has failed, and the release is recalled as unverified.
- A field defect is closed with no defect-named failing-before test in its change: this spec has failed.
- A coverage figure is reported rounded up, or a matrix is hand-edited to green: this spec has failed.

## Tasks

- T001: This spec.
- T002: Matrix generator + CI wiring (after ratification).
- T003: Field-simulation suites per surface.
- T004: Coverage measurement per crate.
- T005: Owner recovery runbook (deliverable, missing item 8) + first drill scheduling.

## Out of scope

Test content for individual features (each spec's FR table owns its tests). Changing the bars without an owner ruling.

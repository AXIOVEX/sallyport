# Feature Specification: Tray Presentation (Successor App)

**Feature Branch**: `feat/035-tray-presentation`
**Created**: 2026-11-07
**Status**: **RATIFIED 2026-11-07 as a block with specs 034-039 (owner decision, Desktop Access chat). Implementation may proceed under the program sequence.**
**Direction**: The Rust successor app replaces the C# tray. This spec folds the presentation requirements of specs 012 (presentation integrity), 018 (connection indicator), 023 (tray convention), and 025 (liveness heartbeat) into the successor, restated for a cross-platform (Windows / Linux / macOS menu-bar) app.
**Companions**: Spec 034 (progress values the tray displays), spec 037 (job control surface), spec 038 (identity, update, staleness machinery underneath), spec 039 (macOS menu-bar form).

## The gap

The current tray's presentation rules live in four specs written against the old build, and the field record shows why each exists: invented statuses forwarded as results (012), a host silently off the fleet for ~1h45m with no indicator (018), a tooltip cut off mid-word in the owner's screenshot (023), and a tray that vanished with no off-box witness (025). The laptop additionally runs a build-folder tray copy fighting the canonical install (field defect, 2026-11-06), so presentation truth also depends on *which binary is running* being visible and singular.

## The design

The successor app is one binary per platform with one canonical install path per host. Every displayed value is observed from its source record on the app's own refresh tick; nothing is rendered from memory of what the app last set. Identity is displayed, not liveness: every surface carries the running build's train number and build stamp (spec 038, UPD-01), and a stamp that disagrees with the train is a defect state, not a footnote.

Presentation integrity (from spec 012): the app never originates a status value. Job states, verdicts, counts, and timestamps are rendered verbatim from gatekeeper records; interim deliveries are shown only as "still running" with elapsed time. The deterministic evidence block (`gk report` form) is the only forwardable status text, and the app links to it rather than paraphrasing it.

Connection (from spec 018): three states — CONNECTED / STALE / DISCONNECTED — computed from host-local signals only (backchannel heartbeat age primary; fleet snapshot timestamp secondary). The app makes no network probe of its own and never becomes a second watcher. Thresholds default to 180 s / 900 s and are settings.

Convention (from spec 023): one-line tooltip under a 63-character budget, parts dropped whole, never truncated; shared vocabulary (up/down, linked / link stale / unlinked, "N waiting"); menu skeleton in the shared order (status header, Open where a surface exists, per-item controls, Settings, Check for updates, Exit last); Start/Stop verbs, Enable/Disable reserved; greyed means unavailable-now with the reason visible; Exit is last, always enabled, logged, labeled with its effect, and final via a stopped-by-user marker the watchdog honors.

Liveness (from spec 025): the app writes `tray-heartbeat.json` at startup and ~every 30 s (atomic temp + replace) carrying host, build, pid, session, started_at, ts, tick, and per-lane state; the backchannel agent merges it; the gatekeeper judges staleness relative to the carrying heartbeat (default 300 s) and ledgers `tray_stale` / `tray_recovered` exactly once per transition.

Settings live inside the one app as a separate surface (owner ruling Q3/Q7, spec 038 FR-038-38): a distinct settings view, never a second binary or a second app.

Staleness is a first-class rendered state (red-team M7): any value whose source record exceeds its stated freshness bound renders as stale with its age, never as last-known truth. The reporting pipeline's health (queue depth, dedupe rate, cap state; red-team H5 / REP-03) is visible in the app so a report flood is a displayed condition.

## Requirements

| FR | Requirement | Named test case(s) | Acceptance mode |
|---|---|---|---|
| FR-035-01 | Every status surface MUST show the running build's train number and build stamp; a stamp/train disagreement MUST render as a defect state (UPD-01 display half). | T035-01 identity-stamp-on-all-surfaces; T035-02 stamp-mismatch-is-defect | test |
| FR-035-02 | The app MUST render job state, verdicts, counts, and timestamps verbatim from gatekeeper records and MUST NOT originate, paraphrase, or complete any status value (spec 012 FR-001/FR-003 fold-in). | T035-03 no-originated-status-values | test |
| FR-035-03 | Interim/in-flight work MUST render only as "still running" plus elapsed time and the latest spec 034 progress snapshot with its age. | T035-04 interim-renders-running-only | test |
| FR-035-04 | Connection MUST be computed from host-local signals only (heartbeat age primary, snapshot timestamp secondary) into CONNECTED / STALE / DISCONNECTED with defaults 180 s / 900 s; no signal ever seen MUST render DISCONNECTED/unknown, never connected (spec 018 fold-in). | T035-05 connection-states-from-fixtures; T035-06 fresh-install-never-claims-connected | test |
| FR-035-05 | The icon MUST carry runner health and connection as independent marks; a connection-only change MUST re-render; rendering is on-change only, never per tick (spec 018 FR-002 fold-in). | T035-07 icon-marks-independent | test |
| FR-035-06 | The menu and tooltip MUST state connection in words naming the signal rested on, and MUST state "signal: backchannel heartbeat only" when no snapshot exists (spec 018 FR-003/FR-004 fold-in). | T035-08 connection-wording-and-honesty | test |
| FR-035-07 | Every connection transition MUST be logged with timestamp and causing signal age; steady state MUST log nothing (spec 018 FR-006 fold-in). | T035-09 connection-transition-log | test |
| FR-035-08 | The connection indicator MUST be display-only: it MUST NOT start, stop, or restart anything and MUST NOT write the signal files (spec 018 FR-007 fold-in). | T035-10 connection-path-writes-nothing | test |
| FR-035-09 | The tooltip MUST be one composed line under 63 characters, parts dropped whole in fixed priority order, never truncated mid-word (spec 023 FR-001 fold-in). | T035-11 tooltip-budget-composition | test |
| FR-035-10 | Status vocabulary MUST be exactly the shared set: up/down, linked / link stale / unlinked, "N waiting" (spec 023 FR-002 fold-in). | T035-12 vocabulary-pinned | test |
| FR-035-11 | The menu MUST follow the shared skeleton order with Exit last and always enabled (spec 023 FR-003/FR-007 fold-in). | T035-13 menu-order-pinned | test |
| FR-035-12 | Action verbs MUST be Start/Stop (Restart beside them where genuine); Enable/Disable MUST be reserved for supervisor-permission flips and never used as Start/Stop synonyms (spec 023 FR-004 fold-in). | T035-14 verb-set-pinned | test |
| FR-035-13 | A greyed item MUST mean unavailable-now, with the reason visible in the label or header; absent features MUST be omitted, never greyed; status lines MUST NOT render as disabled action items (spec 023 FR-005 fold-in). | T035-15 greyed-means-unavailable | test |
| FR-035-14 | Every displayed state MUST be derived from direct observation on the app's own tick; icon, tooltip, and menu MUST move together from the same observation (spec 023 FR-006 fold-in). | T035-16 observed-status-moves-together | test |
| FR-035-15 | Exit MUST write a stopped-by-user marker and a log line before ending, MUST state its effect on supervised things in its label, and the watchdog MUST honor the marker so a user exit stays ended (spec 023 FR-007/FR-008 fold-in). | T035-17 exit-finality-and-marker; T035-18 exit-stays-ended-10min | test + human-as-oracle |
| FR-035-16 | The app MUST write tray-heartbeat.json at startup and ~every 30 s, atomically, with host, build, pid, session, started_at, ts, tick, and per-lane running/faulted state; a failed write MUST NOT stall the app (spec 025 FR-001/FR-002 fold-in). | T035-19 heartbeat-writer-contract | test |
| FR-035-17 | Exactly one app instance per host from the one canonical install path MUST be enforced (singleton by full install path); a second copy (e.g. a build-folder copy) MUST refuse to run and MUST say so in its log (defect: laptop-two-tray-fight-2026-11-06). | T035-20 singleton-by-install-path | test |
| FR-035-18 | Any displayed value whose source record is older than its stated freshness bound MUST render as stale with its age, including capability and fleet values (red-team M7 display half). | T035-21 stale-values-render-stale | test |
| FR-035-19 | Reporting-pipeline health (queue depth, dedupe rate, daily-cap state) MUST be visible in the app (red-team H5 / REP-03 display half). | T035-22 reporting-health-visible | test |
| FR-035-20 | Settings MUST be a separate surface inside the one app; there MUST be no second settings binary or external settings file editor shipped (owner ruling; spec 038 FR-038-38). | T035-23 settings-single-app-surface | test |
| FR-035-21 | No app surface MAY open a visible console window on any platform, ever (fleet hard constraint). | T035-24 no-visible-console-anywhere | test + gate |

## Falsifiers

- The owner opens the app on a host whose tray process was killed an hour earlier and any surface implies the tray was alive in between: this spec has failed.
- A tooltip over 63 characters, or cut mid-word, on any platform build: this spec has failed.
- Two app instances from different paths run simultaneously on one host: this spec has failed.

## Tasks

- T001: This spec.
- T002: Successor app presentation layer per FR table (after ratification).
- T003: Fixture battery: tray rendering fixtures per spec 036 (icon presence, tab titles, CRLF rendering, scroll behavior).
- T004: Field verification on tower-offsite (Linux/COSMIC) and desktop/laptop (Windows) per spec 038 host matrix.

## Out of scope

Job control actions themselves (spec 037). Update mechanics (spec 038; this spec only displays their state). macOS-specific form (spec 039, which inherits this spec's rules).

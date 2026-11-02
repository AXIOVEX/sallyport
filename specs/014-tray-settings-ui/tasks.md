# Tasks — 014 Tray Settings UI and Dynamic Fleet Display

All tasks land in one repo change on
`spec/014-tray-settings-ui`. Deployment to the fleet desktop
is NOT a task here — it is a separate, later act on the
owner's review of the PR, and likewise the fleet-snapshot
publisher (spec FR-009) is a gated follow-up, not a task.
The PR #39 disciplines bind throughout: the icon renders
only on a runner-state change (clone + `DestroyIcon`,
bitmap disposed, replaced menu disposed), ticks run under
catch-and-log, and no new allocation churn may ride the
5 s tick.

## Phase 1 — Settings persistence (FR-001, FR-002)

- [x] T001 `TraySettings` in `runner/tray/SyncyardTray.cs`:
      flat-JSON `tray-settings.json` beside the exe;
      documented defaults; load-at-startup with clamp +
      log-on-fallback; hand-rolled writer/reader in the
      one source file (no new compile reference).
- [x] T002 Wire the settings through the supervision pass:
      timer interval from `tickSeconds` at construction
      and again in the apply path; restart cap, master
      supervision toggle, and per-lane toggles consulted
      in `StartMissing`; stray-console auto-hide gated by
      its setting.

## Phase 2 — The settings dialog (FR-003 – FR-006)

- [x] T003 `SettingsForm`: single-instance, opened only
      from the tray menu's "Settings..." item; plain
      WinForms in the tray's idiom; no icon/bitmap
      resources of its own.
- [x] T004 Tray-behavior section: supervision on/off,
      restart cap (1–20), refresh cadence (2–60 s),
      stray-console hiding on/off — all persisted via
      T001 and applied on save (FR-002), with the save
      logged.
- [x] T005 Autostart: checkbox initialized from the live
      HKCU Run value at dialog open; Run-key write/remove
      only inside the dialog's save path; the tray never
      re-registers itself at startup.
- [x] T006 Per-lane rows: live state label, "supervised"
      checkbox (a saved setting), and Start / Stop
      (confirmed) / Restart buttons acting immediately
      through the tray's existing acts and log lines;
      copy in the dialog states which controls are
      settings and which are immediate acts.

## Phase 3 — Fleet display (FR-007, FR-008)

- [x] T007 `FleetStatus` reader: at most one read per
      tick of the snapshot at `fleetStatusPath`;
      tolerant hand-rolled parse of the spec 013 status
      shape (`generated_at`, `mode`, `draining`, `reason`,
      `focus` selectors, `waiting` in dispatch order with
      `priority` / `priority_source` / `held`); unknown
      mode strings carried verbatim.
- [x] T008 Unavailability states with named reasons:
      missing / unparseable / stale (with the snapshot's
      age always shown); the tray's only file writes
      remain its settings file and its log.
- [x] T009 Surfaces: tray menu header lines (mode line +
      next job), tooltip carrying mode + waiting count
      (length-capped), and the dialog's read-only fleet
      section (mode, selectors, reason, queue list) with
      a note that fleet settings are managed from the
      `gk` commands.
- [ ] T010 Publisher follow-up (FR-009): a gatekeeper-side
      or backchannel-side writer landing the snapshot at
      the contract path. NOT in this change — owner's
      gate on if/when, and through which transport.

## Phase 4 — Dynamism discipline (FR-010, FR-011)

- [x] T011 Dialog refresh on the tray tick by re-setting
      text on existing controls; the queue list rebuilds
      only when the snapshot's `generated_at` changes;
      fleet data never reaches `SetIcon`; no `Console.`
      use and exactly one `Form`-derived class in the
      source.

## Phase 5 — Battery and currency (FR-012)

- [x] T012 Extend `runner/tray/test_tray_source.py`:
      settings writer/reader key parity (the persistence
      round-trip proof at source level), defaults pinned,
      single settings `WriteAllText` call site, dialog
      single-instance guard, Run-key confined to the
      apply path, supervision consultations, fleet parser
      key set + unavailability reasons, stamp-gated queue
      rebuild, allocation counts unchanged outside the
      icon path, no-`Console.` pin. Full local CI battery
      run and reported.
- [x] T013 SKILL.md windowless-supervision section +
      CHANGELOG `[Unreleased]` entry citing spec 014;
      VERSION deliberately untouched (spec 013 owns the
      next bump — see spec FR-012).

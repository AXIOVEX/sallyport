# Plan: Tray Connection Indicator (spec 018)

**Surface**: `runner/tray/SyncyardTray.cs` only, plus its
source battery (`runner/tray/test_tray_source.py`), the
settings plumbing spec 014 built, and docs. No gatekeeper,
runner-workflow, or backchannel code changes.

## Approach

1. **Signal reader**: a small host-local reader that stats
   the backchannel heartbeat file (path from tray settings,
   defaulting to the agent's host-local heartbeat location)
   and, when present, the spec 014 fleet-status snapshot
   (path already known to the Fleet section), returning the
   newest (timestamp, signal-name) pair or none. Read-only;
   tolerant of a file vanishing mid-read (retry once, then
   treat as missing for this tick).
2. **State function**: pure (age, thresholds) -> state, so
   the battery can drive it without the UI. Thresholds come
   from `tray-settings.json` (spec 014 loader) with the
   FR-001 defaults when absent.
3. **Rendering**: `SetIcon` gains the connection dimension —
   the existing runner dot is drawn as today; the link mark
   is drawn from the connection state; the whole icon still
   renders only when either displayed state changes.
   Menu/tooltip text per FR-003; Fleet-section detail per
   FR-004.
4. **Logging**: transitions appended to the tray log in the
   FR-006 format from the tick loop (compare-and-log).
5. **Settings**: two validated fields in the dialog's
   connection group, save-path only (FR-005), mirroring the
   spec 014 field discipline.
6. **Battery**: extend `test_tray_source.py` with the FR
   cases (state bounds, missing file, wording, render-on-
   change, no-write pinning, settings round-trip/refusal,
   transition log format).

## Sequencing

Spec first (this change). Implementation as one follow-up
change on this branch once the spec's flagged decisions are
ratified or their defaults accepted. Desktop install is a
separate field step after the tray install hold (Bitdefender
exclusions + the r9 settings-tray install) clears — the
indicator build installs together with or after spec 014's
tray, never before it.

## Risks

- **GDI discipline**: any new per-tick rendering repeats
  the PR #39 leak class; the render-on-change pin in the
  battery is the guard.
- **Signal path variance**: the heartbeat file's host-local
  location must be confirmed against the deployed agent at
  implementation time (fixture-first until then).

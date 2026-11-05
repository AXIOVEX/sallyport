# Feature Specification: Tray Self-Update

**Feature Branch**: `feat/tray-self-update`

**Created**: 2026-11-04

**Status**: **Implemented** in this change (tray source, source
battery, and this spec land together). Deployment follows the
note at the end.

**Direction (owner, 2026-11-04)**: "add syncyard tray updates
and ability to update to latest, then deploy it ASAP. We have
two machines needing it now."

## The gap

Every tray improvement so far (specs 014, 018, the PR #56 menu
fix, the PR #58 exit forensics) reached the desktop through a
manual install job: build, pin the hashes, dispatch, verify.
That path is careful but slow, and the tray itself carried no
build stamp a support conversation could cite: the menu and
the log said nothing about which build was running, and the
tray had no update path of its own at all.

The tray now carries its build stamp and can pull the latest
published build itself, on the owner's explicit menu click,
with the download verified before anything is swapped.

## The manifest contract

The update source is a file asset attached to the project's
GitHub releases, fetched through the stable latest-release
download URL:

`https://github.com/AXIOVEX/syncyard/releases/latest/download/tray-update.json`

Fields: `version` (string, the published build's version),
`sha256` (lowercase hex SHA-256 of the published exe), `url`
(direct download URL for that exe asset), `notes` (optional,
free text, shown in the tray log when an update is found).
Publishing a tray update means attaching `SyncyardTray.exe`
and an updated `tray-update.json` to a release; nothing else
in the release process changes.

## Requirements

### FR-001 — Build stamp

The tray assembly version is 0.4.0.0 (AssemblyVersion and
AssemblyFileVersion; the NSIS product and file version
stamps match). A `BuildId` constant carries the same value
in source. The stamp shows as a disabled header item at the
top of the tray menu ("Syncyard Tray v0.4.0.0") and is
written to tray.log at startup beside the existing startup
lines ("tray build 0.4.0.0").

### FR-002 — Update check

The menu carries one update item, "Check for updates" until
a check finds something newer. Clicking it downloads the
manifest on a background thread (15 s request timeout) and
compares the manifest version against the running assembly
version with a System.Version compare. The check never
blocks or crashes the UI: every failure is caught and
written to tray.log, and the item reverts to "Check for
updates". No update found: the item reads "Up to date" until
the next check. Update found: the manifest is remembered
and the item reads "Update to v<version>". Checks happen
only on the owner's click; the tray never polls, and the
spec 018 connection indicator stays signal-file only.

### FR-003 — Verified download

Clicking "Update to v<version>" downloads the exe from the
manifest's `url` to `SyncyardTray.exe.new` beside the
installed exe, then computes its SHA-256 and compares it
against the manifest's `sha256`. On a mismatch the download
is deleted, "update aborted: hash mismatch" is logged, and
nothing proceeds. Only a verified download arms the swap.

### FR-004 — Helper-mode swap

On a verified download the tray logs "tray exiting for
update", starts the same exe detached with the argument
`--apply-update "<path to the .new file>"`, and exits. The
helper branch runs in Main before the singleton mutex and
before any UI exists: it takes no mutex, creates no icon,
window, or prompt, and logs every step to the same tray.log.
It waits until no other process running the same exe path
remains (500 ms polls, up to about 60 s; if one remains it
aborts with nothing changed), renames the installed exe to
`SyncyardTray.exe.bak` (deleting any older .bak first),
renames the .new file into the exe's place, starts the new
exe with no arguments, and exits with code 0.

### FR-005 — Failure and rollback

Any failure before the swap (network, manifest shape, hash)
leaves the installed build untouched and running. A failure
during the swap attempts rollback: if the exe is missing
and the .bak exists, the .bak is renamed back. Whatever exe
is in place at the end is the one the helper relaunches, so
a failed swap still leaves a runnable, running tray.

### FR-006 — Existing behavior preserved

Supervision, the fleet panel, the connection indicator, the
settings dialog, the PR #56 menu-open rebuild guard, and the
PR #58 exit-forensics hooks are unchanged. The update items
are built inside the same menu construction as every other
item, so they inherit the menu-open guard.

### FR-007 — Per-machine runner root

The tray supervises the lanes of the machine it runs on, so
the runner root is a setting (`runnerRoot` in
tray-settings.json), not a compiled-in path. Its default is
the original desktop layout
(`C:\Users\trist\Development\Syncyard\runners`), so the
desktop's behavior is unchanged with no settings edit. A
machine whose lanes live elsewhere (the laptop:
`D:\Development\Syncyard\runners`) sets runnerRoot in its
settings file, and the lane directories are built from it.
Settings load before the runner table is built. A lane that
is disabled or whose directory is absent is neither started
nor faulted, exactly as before.

## Falsifier

A tampered or stale manifest must never replace the exe: a
manifest naming an older or equal version is reported as up
to date, and a download whose bytes differ from the
manifest's hash is deleted with nothing swapped, which the
source battery pins by construction order (the hash is
computed and compared before the swap is armed). A failed
swap must leave a runnable tray: the .bak rollback above,
also pinned. If either property is ever observed broken in
the field, this spec's design is wrong, not just its code.

## Deployment note

Self-update only works from an update-capable build onward.
The first 0.4.0.0 build reaches each machine by the existing
hash-pinned install-job path; from that build on, published
updates are one menu click. The .bak the helper keeps is
the rollback artifact for the most recent swap and is
replaced on the next one.

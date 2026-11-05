# Feature Specification: Fleet Tray Convention

**Feature Branch**: `spec/023-tray-convention`

**Created**: 2026-11-04

**Status**: **Specified** (this change carries the spec and the
conformance checklist only). No tray code changes in this
change; the gaps the checklist names are findings for each
tray's own follow-on work, in its own repo, on its owner's
schedule.

**Direction (owner, 2026-11-04 ~17:55 ET, with a screenshot
of a cut-off Syncyard tooltip reading "Syncyard: 4/4 runners
up - Connection: disconnected (no heartbe…")**: "fix this:
cut off text and reduce the needed text" and "make sure that
all our tray applications for all muse projects are similar
with the flow and how things enable/disable, status, and
exit."

## The gap

The fleet now runs three project trays, built independently:

- **Syncyard tray** — `runner/tray/SyncyardTray.cs` in this
  repo (C#, .NET Framework, build 0.4.1.0). Supervises the
  runner lanes of its machine; tooltip composition and the
  menu header were fixed most recently by PR #67, exit
  forensics by PR #58, self-update by spec 021.
- **EdgeLab tray** — `tray.py` + `supervisor.py` in
  `tbitcs/edgelab-tray` (Python, pystray; local clone
  `~/workspace/edgelab-tray`). Supervises the services in
  its `services.json` registry (today: the EdgeLab app);
  owned by a Task Scheduler task named `edgelab-tray`.
- **RealLoom tray** — `packaging/tray/localgen_tray.py` in
  the RealLoom tree (`~/workspace/opengen-local`, deployed
  under `C:\Users\trist\Development\RealLoom`) (Python,
  pystray, tray v2 / spec 012 in that project). The user's
  hands on the RealLoom stack; the stack itself is
  supervised by the RealLoom-Watchdog task.

Each tray answers the same four questions (what is up, how
are we connected, what can I start or stop, how do I leave)
in different words, different menu orders, different
enable/disable behavior, and — sharpest — different exit
semantics: one tray's exit leaves its services running and
says so, one stops its services and does not say so, one
leaves its stack running and logs nothing, and one tray is
relaunched by its watchdog within about a minute of a user
exit because no stopped-by-user marker exists for the
watchdog to honor. The owner's screenshot is the tooltip
form of the same defect: text composed without a budget
reads as cut off and sloppy. A user who learns one tray
should already know the others.

This spec fixes the shared surface. What a tray supervises,
what it is named, and what its icon looks like stay
per-project (FR-009).

## Requirements

### FR-001 — Tooltip: one short line, composed, never chopped

The tray tooltip is ONE line composed of short parts in a
fixed priority order (the tray's own name and up/down count
first, then the connection word where the tray has a
connection dimension, then a waiting count, then a stale
flag). The composition carries a hard budget of **63
characters** (the Windows NotifyIcon limit under .NET
Framework: the setter throws above 63, and 63 is the budget
every fleet tray composes to regardless of its toolkit, so
the trays read alike). A part that would cross the budget
is **dropped whole**; text is never truncated, substringed,
or ellipsized mid-word, and the composed line is never
sliced to a length after composition. Detail belongs in the
menu header (FR-003), not the tooltip.

*Acceptance (test)*: the tray's test battery drives the
composition at its longest realistic state (all parts
present, two-digit counts) and pins that the result is
≤ 63 characters, ends on a whole part, and contains no
truncation artifact. *This does not establish* that the
shell displays all 63 characters on every Windows build —
it establishes that the tray never hands the shell a line
that has to be cut.

### FR-002 — Status vocabulary: the same words for the same states

All trays use the same words for the same states:

- Supervised things are **up** or **down** (a count reads
  "N/M runners up", "N/M services up"). Transitional or
  held states a supervisor genuinely has (starting,
  parked) may be added per tray, but up/down are never
  renamed (never "ready/off", "live/dead", "active" for
  the same fact).
- Connection, where a tray has that dimension, is ONE word
  (or one fixed two-word form) from exactly this set:
  **linked** / **link stale** / **unlinked**. The tooltip
  uses these forms and no others. A menu header line may
  carry the longer detail (which signal, its age) after
  the same word.
- A queue the fleet is waiting on reads "**N waiting**".

*Acceptance (test)*: battery cases pin the tooltip and
header strings for each state the tray can be in. *This
does not establish* that the states are correct — FR-006
covers how a state is earned; this requirement fixes only
the words it is reported in.

### FR-003 — Menu flow: one order

The tray menu, top to bottom:

1. **Status header**: at most 3 short lines, disabled
   (greyed) as text, never as fake controls: the build
   line, the supervised-set summary, the connection line
   (where that dimension exists).
2. **Open** — where an app surface exists (a URL or window
   the tray can show). A tray supervising pure machinery
   with no surface omits it rather than faking one.
3. **Per-service / per-runner controls** — each supervised
   thing appears with its current state in its label (or
   its submenu's label), carrying the Start/Stop actions
   of FR-004. Project extras that act on the supervised
   things (console visibility, log views) group here or in
   the item's own submenu.
4. **Settings**.
5. **Check for updates** — where an update channel exists;
   a tray whose channel is staged releases instead offers
   the staged update here, greyed with the reason in its
   label when none is staged.
6. **Exit** — the LAST item, always present, always
   enabled (FR-007).

*Acceptance (test)*: the battery pins the item sequence
and labels of the built menu. *Human-as-oracle*: the owner
opens two fleet trays side by side and finds Exit, the
per-service controls, and Settings in the same positions
in both. *This does not establish* identical menus — extra
project items are allowed by FR-009; it establishes that
the shared skeleton is in the shared order.

### FR-004 — Verbs: Start/Stop for supervised things

One verb pair fleet-wide for acting on a supervised
process or service: **Start** / **Stop**, with **Restart**
offered beside them where a combined act is genuinely
useful. **Enable** / **Disable** are reserved for flipping
whether a supervisor may run something (a lane's enabled
setting, a watchdog task) — an act that does not itself
start or stop a process — and are never used as synonyms
for Start/Stop in a menu. Every Start/Stop pair the
underlying supervisor exposes is reachable from the menu:
a service that can be started in code can be started from
its menu item, named Start, not via a Restart that happens
to start it.

*Acceptance (test)*: the battery enumerates the menu's
action items and pins the verb set; a supervisor API with
a start operation and no matching menu Start fails it.
*This does not establish* that the actions work — each
tray's own tests cover that; it establishes that the owner
never has to guess which word starts a stopped thing.

### FR-005 — Disabled means unavailable now, with the reason visible

A greyed (disabled) menu item means exactly one thing:
**this action is not available in the current state** —
already running, already stopped, a check already in
flight, supervised elsewhere. It never means the feature
is absent from this build (an absent feature is omitted),
and a status line is never rendered as a disabled action
item. Where the reason is not self-evident from the
state in the same label, the label or the header says it
("Update (none staged)"). State changes reflect in the
menu **within one poll cycle** of the tray's own refresh
(a menu already open may rebuild on close, per the
standing menu-open guard, but a reopened menu is never
stale). Start all / Stop all aggregates follow the same
rule (greyed when nothing can start / nothing can stop).

*Acceptance (test)*: battery cases drive each supervised
thing through up and down and pin which action items are
enabled in each state, including the aggregates. *This
does not establish* freshness of the underlying state —
that is FR-006; it establishes that greying is a state
report, not decoration.

### FR-006 — Status is observed, never assumed

Every state a tray displays is derived from the thing it
supervises by direct observation on the tray's own poll
cycle: the supervised process's presence, a health answer
from the served surface (its build stamp where the surface
carries one), the connection signal file's age. No tray
displays a state it merely set earlier and never re-read,
and the icon and the text are refreshed from the same
observation (an icon left on a previous health level while
the text moves on is a defect under this requirement).

*Acceptance (test)*: fixture cases kill/raise the observed
signal between polls and pin that icon, tooltip, and menu
all move together on the next cycle. *This does not
establish* that the observation is the fleet's truth — the
gatekeeper's view remains that; it establishes that the
tray's picture is earned from its own host, freshly.

### FR-007 — Exit: last, always enabled, stays ended, logged, and says its effect

The exit item is named **Exit** (a project suffix is
allowed: "Exit tray"), is the last menu item, is always
present, and is never greyed. Choosing it:

- ends the tray process, and the tray **stays ended** until
  the user or a logon starts it again. Any watchdog,
  supervisor, or scheduled task that would relaunch the
  tray must stand down on a user exit: the exit path
  writes a stopped-by-user marker (or the equivalent
  record in the supervisor's own state) before ending,
  the relauncher checks it before any relaunch, and the
  marker is cleared only by an explicit later start of
  the tray (logon start included, per the tray's own
  documented rule). A crash or external kill is not a
  user exit: the marker is absent, and relaunch after a
  crash remains the watchdog's job;
- writes a log line naming the exit as the user's act
  before the process ends (every exit path that can log,
  logs; a path that cannot log is covered by the watchdog
  or heartbeat record from the other side);
- states its effect on the supervised things in the item
  label or the header, because the fleet's trays differ
  here by design today (Syncyard's runners keep running;
  EdgeLab's supervised services are stopped; RealLoom's
  stack keeps running under its watchdog) and the owner
  must never have to remember which: the label says it,
  e.g. "Exit tray (runners keep running)".

*Acceptance (test)*: the battery pins the exit item's
position and enabled state, that the exit path writes the
marker and the log line before ending, and that the
relauncher's check honors the marker. *Human-as-oracle*:
the owner exits a tray and it is still exited ten minutes
later with its watchdog's cycle having run. *This does
not establish* protection against an external kill, which
by definition cannot write a marker; it establishes that
a deliberate exit is final until the user reverses it.

### FR-008 — The stack-stop pattern is the same marker discipline

FR-007's marker discipline is not tray-exit-only. Where a
tray (or its user) stops a supervised stack that a
watchdog would otherwise revive, the stop sets the same
kind of stopped-by-user record and stands the watchdog
down, and the matching Start clears it and re-enables the
watchdog. The fleet's reference implementation today is
the RealLoom tray's `stack_stop()` (writes
`output/stack-stopped.flag`, disables the RealLoom-Watchdog
task; `stack_start()` removes the flag and re-enables it).
A stop that the watchdog silently undoes within its cycle
is a defect under this requirement, in any tray.

*Acceptance (test)*: per tray, a fixture stop followed by
a watchdog cycle leaves the stack down and the marker
present; a Start leaves both reversed. *This does not
establish* anything about non-tray stops (a service
stopped in a service manager is the watchdog's own
policy); it covers stops issued through the tray surface.

### FR-009 — What is per-project, what the convention fixes

Per-project, never fixed by this convention: the tray's
name and icon art; the list of supervised services/runners
and their names; the supervised things' own extra actions
(console visibility, per-service Open URLs, log locations);
thresholds and poll intervals; whether a connection
dimension exists at all; whether supervised things keep
running or stop at tray exit (FR-007 requires the label to
say which, not which it is); the update channel's shape
(manifest check vs staged release).

Fixed by this convention, not per-project: the tooltip
budget and composition rule (FR-001); the status
vocabulary (FR-002); the menu skeleton and order (FR-003);
the Start/Stop verb rule and Enable/Disable reservation
(FR-004); the meaning of a greyed item (FR-005); observed
status (FR-006); exit position, finality, marker, logging,
and effect labeling (FR-007, FR-008).

*Acceptance*: the conformance checklist below is the
worked form of this split; a future tray is reviewed
against the checklist, and any row it cannot point to in
its source is a gap by construction.

## Conformance checklist (2026-11-04, from source)

Read from: Syncyard `runner/tray/SyncyardTray.cs` (build
0.4.1.0, this repo at `93a0f1f`); EdgeLab `tray.py` +
`supervisor.py` (`tbitcs/edgelab-tray` at `f50ac67`);
RealLoom `packaging/tray/localgen_tray.py` (tray v2, in
`~/workspace/opengen-local`). C = conforms, G = gap.

### Syncyard tray

| Req | Verdict | Evidence / gap |
| --- | --- | --- |
| FR-001 | C | `TooltipText()` composes parts under the 63 budget, drops whole parts, no substring (PR #67). |
| FR-002 | C | Tooltip uses linked / link stale / unlinked and "N waiting"; header carries the spec 018 detail form after the same state. |
| FR-003 | C (minor note) | Header is exactly 3 lines (build, fleet, connection); Settings, then Open tray log, then Check for updates, then Exit last. Note: no Open item (no app surface — allowed), and the log item sits between Settings and the update item rather than with the runner group. |
| FR-004 | C | Start all runners / Stop all runners; per-lane "Start hidden" / "Stop runner". |
| FR-005 | G | Aggregates grey correctly (Start all iff running < count; Stop all iff running > 0; update item disabled while a check runs), but the per-lane submenu's Start hidden / Stop runner set no `Enabled` in `RebuildMenu()`: Start is offered for a running runner and Stop for a stopped one. |
| FR-006 | C | Runner state from the runner processes, connection from heartbeat age, fleet from the snapshot file; icon and text set from the same rebuild. |
| FR-007 | G | Exit is last, always enabled, logs "tray exited by owner; runners left as-is", and its label states the effect. BUT the exit path writes no stopped-by-user marker, and the fleet tray watchdog (`SyncyardTrayWatchdog` task, `tray-watchdog.ps1`, fleet-side, installed 2026-11-04) relaunches the tray whenever it is absent while the user session runs: a user Exit is undone within about a minute. |
| FR-008 | n/a | The tray has no watchdog-revived stack stop of its own; its runner stops are direct process stops. |

### EdgeLab tray

| Req | Verdict | Evidence / gap |
| --- | --- | --- |
| FR-001 | G | `Supervisor.tooltip()` joins "Name: state" parts and hard-chops the result: `text[:127]` (`supervisor.py`). A mid-word cut is possible, there is no part-dropping, and the budget is 127, not 63. |
| FR-002 | G (partial) | Service states are up / down (conforming) plus unknown / starting / parked, which are extra words for transitional and crash-held states; no connection word (this tray has no connection dimension — that absence is allowed, the extra state words are the gap). |
| FR-003 | G | No status header block (status rides in each per-service item label instead); Open is per-service inside the submenu, not a top-level step; no Settings item; no Check for updates item; Quit is last. Items present: per-service submenus, Open log, Reload config, Quit. |
| FR-004 | G | The supervise-mode submenu offers Restart and Stop only. `Supervisor.start_service()` exists (`supervisor.py`) but `build_menu()` never exposes it: a manually stopped service can only be brought back through Restart, which misnames a start from rest. |
| FR-005 | G | Restart / Stop are never greyed, whatever the service state. A service with no actions renders its status as a disabled top-level item (`enabled=False`), which reads as an unavailable control rather than a status line. |
| FR-006 | C | States come from the spawned process plus the service's health probe (expected status and substring at its own URL); aggregate icon is worst-state-wins; menu, icon, and tooltip rebuild on a signature change in the 1 s supervise loop. |
| FR-007 | G (partial) | Quit is last and always enabled, logs "quit requested; stopping supervised services" before ending, and nothing relaunches the tray (its Task Scheduler task is a logon start, not a watchdog), so it stays ended — the finality half conforms. The gaps: the item is named "Quit", not Exit; and the label does not state its effect, which is the opposite of the Syncyard tray's (Quit here also stops the supervised services, per `quit_tray()` → `supervisor.shutdown()`). |
| FR-008 | C | `stop_service()` sets `manual_stopped`, and the supervisor does not auto-restart a manually stopped service (it restarts only its own crashes, inside the restart budget) — the marker discipline in supervisor-local form. |

### RealLoom tray

| Req | Verdict | Evidence / gap |
| --- | --- | --- |
| FR-001 | G | The tooltip is the full `status_text()` sentence assigned to `icon.title` every poll, with no length budget, composition rule, or truncation handling; the down-state sentence alone ("RealLoom: UI is DOWN (watchdog should revive it)") plus a build stamp in the up form can cross 63 characters. |
| FR-002 | G | Status words are project sentences: "UI is DOWN (watchdog should revive it)", "video engine ready / off", "second engine up / off", "STOPPED by you". Down appears, but ready/off are renames of up/down for the engine facts. No connection dimension (allowed); the vocabulary is the gap. |
| FR-003 | C (minor notes) | `build_menu()`: one disabled status line, Open RealLoom, Start stack, Stop stack, Restart stack, Check logs, Update, Quit tray last. Notes: no Settings item; the exit item is named "Quit tray"; the update slot carries the staged-release form the convention allows. |
| FR-004 | C | Start stack / Stop stack / Restart stack, one pair, no Enable/Disable synonyms in the menu. (The watchdog task enable/disable those verbs name happens inside `stack_stop()` / `stack_start()`, not as menu items — consistent with the reservation.) |
| FR-005 | G (partial) | The Update item is the model case: greyed when no release is staged, with the reason in its label ("Update (none staged)"). But Start stack / Stop stack / Restart stack are never greyed: Start is offered while the stack runs and Stop while it is stopped. |
| FR-006 | C | Health is the served `/config` build stamp plus ComfyUI `/system_stats`, probed every 30 s, menu and tooltip rebuilt each cycle. Recorded asymmetry (2026-11-04): the icon image swaps only when the health LEVEL transitions while text refreshes every poll — a stale-icon risk this requirement names; the row is marked conforming on the observation itself, with the asymmetry carried as the known defect against the "move together" clause. |
| FR-007 | G (partial) | "Quit tray" is last, always enabled, and nothing relaunches the tray itself (the watchdog supervises the stack, not the tray), so it stays ended. Gaps: Quit writes no log line (`log()` covers stack actions and probe failures, not the tray's own exit) and no marker exists; the label does not state that the stack keeps running after the tray leaves. |
| FR-008 | C | The reference implementation: `stack_stop()` writes `output/stack-stopped.flag` and disables the RealLoom-Watchdog task before ending the stack tasks; `stack_start()` removes the flag and re-enables the watchdog. |

## Falsifier

This convention is wrong, not just unmet, if a tray can be
built to every requirement above and the owner still cannot
predict, standing at any fleet tray: where Exit is, what a
greyed item means, which word starts a stopped thing, or
whether an exit will still be in effect ten minutes later.
It is likewise wrong if two trays both pass the checklist
and still report the same observed state in different
words. Any such observation reopens this spec, not just
the tray it was observed on.

## Out of scope

- Fixing any gap named above. Each fix lands in its own
  tray's repo under its own spec or change (the Syncyard
  tray's rows under this repo's tray specs; the EdgeLab and
  RealLoom rows in theirs).
- A shared tray library or a single fleet tray binary.
  The convention is a surface contract, not a codebase.
- The Syncyard tray watchdog's other duties (crash
  relaunch, kill-test evidence) — FR-007 adds exactly one
  duty to it: honor the marker.

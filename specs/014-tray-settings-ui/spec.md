# Feature Specification: Tray Settings UI and Dynamic Fleet Display

**Feature Branch**: `spec/014-tray-settings-ui`

**Created**: 2026-11-02

**Status**: **Implemented in this change** (tray source + source
battery + docs). Nothing here installs or deploys: the desktop
still runs the packaged build until the owner installs a build
carrying this change, by the usual route. The sibling spec 013
change is in flight on its own branch; this spec is written
against spec 013's published status shape and degrades
gracefully where the two have not yet met (FR-006).

**Direction (owner, 2026-11-02)**: "add settings inside the
tray which allow changing settings from an easy UI, and fully
managing them. and update tray to be more dynamic with the
changes and keep it always relevant with the shape of the rest
of the tool."

## The gap

The tray supervises the runners (spec 007) but every knob it
has is a compile-time constant or not a knob at all:

- The restart cap (3/hour) and the 5 s tick are constants in
  the source. Changing either means editing, recompiling, and
  reinstalling the tray.
- Supervision itself has no off switch short of exiting the
  tray (which also removes the status surface), and no
  per-lane way to say "leave this lane alone" — a lane being
  serviced by hand still gets auto-restarted underneath the
  operator.
- Autostart is owned by whichever installer ran last; there
  is no in-app way to see whether the tray will start at
  logon, or to change that.
- The tray shows only runner-local state. The fleet side of
  the tool — the gatekeeper's mode (spec 013: RUNNING /
  PAUSED / FOCUSED / DRAINING), the waiting queue in
  effective dispatch order, priorities and who set them —
  has no surface on the desktop at all, so the tray falls
  out of relevance next to the rest of the tool: the owner
  watches a runner light while the pipeline's actual state
  lives in a CLI on another machine.

## The boundary, stated plainly

The tray runs on the desktop; the gatekeeper and its
`syncyard.yaml` (spec 010) live on the operator VM. The tray
therefore manages **only what it owns on the desktop** —
its own behavior and the runner processes it supervises —
and **displays** fleet state read-only from a published
snapshot when one exists (FR-007). It never edits fleet
config, never writes fleet state, and never invokes the
gatekeeper. Where no snapshot exists, the fleet section
says so, with the reason, instead of inventing a transport
or showing stale guesses as fact (FR-007, FR-008).

## User scenarios

1. **Tune supervision from the tray.** The owner opens
   Settings from the tray menu, raises the restart cap,
   slows the refresh cadence, saves — the new values are in
   force immediately, persist in `tray-settings.json` beside
   the tray exe, and survive a tray restart. No reinstall.
2. **Service one lane by hand.** The owner unticks a lane's
   "supervised" box and saves: the tray stops auto-starting
   and auto-restarting that lane (its state still shows),
   while the dialog's Start/Stop/Restart buttons — like the
   menu's — keep working as explicit acts.
3. **Glanceable fleet state.** With a fleet snapshot being
   published, the tray tooltip and menu header read
   "PAUSED · 21 waiting" and the Settings dialog's fleet
   section shows the mode, the focus selectors, and the
   waiting queue in dispatch order with each job's priority
   and whether a human or the auto-optimizer set it. With
   no publisher, the same places say the feed is
   unavailable and why.
4. **Autostart under owner control.** The Settings dialog
   shows whether the tray starts at logon (read live from
   the HKCU Run key, never assumed), and saving with the
   box changed adds or removes that Run value.

## Requirements

Each requirement carries a behavior statement, an acceptance
mode (**test** = an automated battery decides; **oracle** =
the owner or operator judges from presented evidence;
**gate** = a named human decision must precede the act), and
a *this does not establish* line bounding what passing it
proves.

### Part A — Settings persistence

- **FR-001 — A tray-owned settings file, loaded at startup,
  defaulted safely.**
  *Behavior*: the tray persists its settings as flat JSON in
  `tray-settings.json` beside the tray executable
  (`AppDomain.CurrentDomain.BaseDirectory`). The file is
  written and read by one class (`TraySettings`) whose
  writer emits exactly the keys its reader consumes (the
  battery proves the parity). A missing file, a missing
  key, or an unparseable file yields documented defaults
  (supervision on, restart cap 3, tick 5 s, stray-console
  hiding on, all lanes supervised, fleet staleness bound
  300 s) plus a line in `tray.log` — settings can never
  prevent the tray from starting. Values are clamped to
  their documented ranges on load and on save. The format
  is hand-rolled JSON in the tray's single source file,
  matching the repo's JSON state files (the gatekeeper's
  queue records and pause record) — deliberately no new
  compile reference, so the in-box csc route
  (`/target:winexe`, WinForms + Drawing only) compiles the
  tray exactly as before.
  *Acceptance*: test — the tray source battery pins the
  writer/reader key parity, the presence of every default,
  and the single `File.WriteAllText` call site inside
  `TraySettings.Save`.
  *This does not establish* that a hand-edited settings
  file with exotic content parses the way its author
  hoped — the reader is tolerant by design (unknown keys
  ignored, bad values fall back to defaults), and the
  dialog is the supported editing surface.

- **FR-002 — Settings take effect without reinstalling.**
  *Behavior*: saving in the dialog applies the new values
  to the running tray in the same act: the supervision tick
  interval is re-assigned from the saved cadence, the
  restart cap and supervision/lane toggles gate the very
  next supervision pass, and the save is recorded in
  `tray.log` with the values that changed hands. A tray
  restart re-reads the same file (FR-001), so the running
  state and the persisted state cannot drift apart by more
  than one explicit save.
  *Acceptance*: test — the battery pins that the timer
  interval is assigned from settings at construction and
  again in the apply path, and that the supervision pass
  consults the settings for the cap, the master toggle,
  and the per-lane toggle.
  *This does not establish* immediacy finer than one tick:
  a cadence change is itself applied at save, but state
  *display* still refreshes on the (possibly new) cadence.

### Part B — The settings dialog

- **FR-003 — One dialog, user-summoned, the tray's only
  window.**
  *Behavior*: the tray context menu gains a "Settings..."
  item that opens a single-instance settings dialog:
  opening it again while it is up activates the existing
  dialog instead of stacking a second. The dialog is the
  only window the tray ever shows, and only on this
  explicit menu act — the tray itself stays windowless on
  the supervision path (spec 007 FR-001/FR-002, whose
  "no window of its own" is hereby read as *no window
  except a settings surface the owner opens*, an
  interpretation this spec records for the owner's
  ratification). The dialog is created on open and
  disposed on close; it allocates no icon or bitmap
  resources at all.
  *Acceptance*: test — the battery pins the dialog class,
  the single-instance guard, and that the tray source
  still contains exactly one `GetHicon` call site and no
  new `Bitmap`/`Graphics.FromImage` allocations outside
  the existing icon path; oracle — the owner judges the
  dialog's layout and wording on the desktop.
  *This does not establish* that the dialog is beautiful —
  it is a plain WinForms surface in the tool's existing
  idiom; the oracle acceptance is where look and feel is
  judged.

- **FR-004 — Tray behavior settings.**
  *Behavior*: the dialog's tray section manages:
  supervision on/off (the master switch for auto-start and
  auto-restart; the status surface and all manual controls
  keep working while it is off), the restart cap
  (restarts per runner per hour, 1–20), the refresh
  cadence (2–60 s), and stray-console hiding on/off (the
  automatic leg; the menu's manual "Hide runner consoles
  now" remains an explicit act regardless). Every change
  is a settings-file value (Part A) applied on save
  (FR-002) and logged.
  *Acceptance*: test — the battery pins the four settings
  in the persisted key set and the supervision-pass
  consultations of FR-002.
  *This does not establish* a supervision guarantee:
  supervision off is a real off — a runner that dies while
  supervision is off stays down until a manual start, and
  the icon state says so, as it does today.

- **FR-005 — Autostart, told truthfully and changed only
  from the dialog.**
  *Behavior*: the dialog's autostart checkbox is
  initialized from the live HKCU Run value
  (`...\CurrentVersion\Run\SyncyardTray`) at dialog open,
  so what it shows is the machine's actual state, whether
  an installer, the spec 011 conversion, or a previous
  dialog session set it. Saving with the box changed adds
  the value (pointing at the currently running exe) or
  removes it. Nothing else in the tray writes the Run key:
  in particular the tray never re-registers itself at
  startup, so an external decision to retire autostart
  (the spec 011 T013 conversion does exactly that) is
  never silently undone by the tray merely running.
  *Acceptance*: test — the battery pins the Run-key read
  and write inside the settings apply path and their
  absence from the startup path.
  *This does not establish* that autostart survives the
  spec 011 service conversion — that conversion retires
  the tray's runner-starting role on its own terms; this
  checkbox manages the world as it stands until then.

- **FR-006 — Per-lane controls in the dialog.**
  *Behavior*: for each lane the dialog shows its live
  state (running / stopped / faulted) and offers:
  a "supervised" checkbox (a settings value — while
  unticked, the supervision pass skips the lane entirely:
  no auto-start, no auto-restart; the lane's state still
  displays everywhere it displayed before), and Start,
  Stop, and Restart buttons that act immediately, like
  the menu's controls, with Stop asking for confirmation
  first (a stopped listener kills its running job — the
  menu's existing discipline), and every action logged to
  `tray.log` in the existing idiom. Restart is Stop +
  fault-clear + Start as one act. Lane enablement and
  lane actions are different kinds of thing and the
  dialog says so: checkboxes are saved settings, buttons
  are immediate acts that Cancel does not undo.
  *Acceptance*: test — the battery pins the per-lane
  enable in the persisted keys, the supervision pass
  skipping disabled lanes, and a Restart path composed
  of the existing stop/start acts.
  *This does not establish* that a manually started,
  unsupervised lane is supervised — it is not; it runs
  until it exits or is stopped, and only its state is
  shown. That asymmetry is the point of the toggle.

### Part C — Fleet display (read-only)

- **FR-007 — The fleet section renders spec 013's
  published status shape.**
  *Behavior*: when a fleet snapshot is available, the
  tray renders: the pipeline mode — RUNNING, PAUSED,
  FOCUSED (with its selectors: keys, programs, chats),
  plus DRAINING whenever a drain stands (spec 013
  FR-005/FR-006) — the recorded reason, the count of
  waiting jobs, and the waiting queue in effective
  dispatch order (priority, then arrival — spec 013
  FR-003), each row showing key, program, lane, priority,
  the priority's source when the snapshot carries one
  (`priority_source`: `human` or `auto` — a human
  decision outranks the auto-optimizer's, and the row
  says which it is looking at), and whether the job is
  held or admitted under the current pause/focus. A mode
  string the tray does not recognize is shown **verbatim**
  rather than dropped or guessed at, so a gatekeeper
  newer than the tray degrades to readable, not blank.
  The same mode + waiting count + next job surface in the
  tray menu's header lines and in the tooltip (truncated
  to the notification area's safe length).
  *Acceptance*: test — the battery pins the mode tokens
  and the queue fields in the parser, and the verbatim
  fallback (the parsed mode is displayed as read, never
  whitelisted); oracle — the owner compares the dialog's
  fleet section against `gk status` output on the
  operator side.
  *This does not establish* that the snapshot is the
  gatekeeper's live state — the tray displays a
  *published snapshot* with its age; FR-008 bounds how
  old a snapshot may be and still be presented as
  current, and beyond that bound it is labeled stale.

- **FR-008 — The feed contract, and honest unavailability.**
  *Behavior*: the tray reads an optional JSON snapshot
  from the path named by the `fleetStatusPath` setting
  (default `C:\Users\trist\Development\Syncyard\status\
  fleet-status.json`) at most once per tick. The
  snapshot's shape mirrors spec 013's status: top-level
  `generated_at` (ISO 8601), `mode`, `draining`, `reason`,
  `focus` ({`keys`, `programs`, `chats`}), and `waiting`,
  an array in effective dispatch order of {`key`,
  `program`, `lane`, `priority`, `priority_source`,
  `held`}. Absent file, unparseable content, or a
  snapshot older than the `fleetStaleSeconds` setting
  yields the fleet section in an unavailable (or
  stale-with-age) state **naming the reason** — never a
  fabricated or silently frozen fleet picture. The tray
  writes nothing to the fleet path: its only file writes
  are its settings file and its log.
  *Acceptance*: test — the battery pins the snapshot key
  set in the parser, the three unavailability reasons,
  and that `File.WriteAllText` occurs exactly once in the
  tray source (the settings save).
  *This does not establish* freshness of the desktop's
  clock against the publisher's — `generated_at` is
  compared against the desktop clock, and a skewed
  desktop clock skews the staleness call; the age is
  always displayed, so a wrong call is visible as one.

- **FR-009 — Publishing the snapshot is a named follow-up,
  not part of this change.**
  *Behavior*: this change builds the reader and the
  contract (FR-008), not the publisher. Until a publisher
  exists — a gatekeeper-side or backchannel-side writer
  that lands the snapshot at the contract path on a
  cadence — the fleet section's unavailable state is the
  correct and expected display on the fleet desktop, and
  the desktop-local settings (Parts A and B) work fully
  regardless. Recording the publisher as a follow-up is
  deliberate: the transport between the operator VM and
  the desktop is a fleet-architecture decision (the
  backchannel is the obvious candidate), not a tray
  decision to smuggle in.
  *Acceptance*: gate — the owner decides if and when the
  publisher is built, and through which transport; this
  spec is complete without it.
  *This does not establish* any commitment to build the
  publisher — it establishes the contract a publisher
  must satisfy to light the section up.

### Part D — Dynamism on the PR #39 discipline

- **FR-010 — State changes show on the existing tick, with
  no new rendering churn.**
  *Behavior*: the menu header, the tooltip, and (while it
  is open) the settings dialog's dynamic sections refresh
  on the tray's existing supervision tick — the dialog by
  re-setting text on its existing controls and rebuilding
  its queue list **only when the snapshot's `generated_at`
  changes**, never by recreating the dialog or its
  controls per tick. Fleet data never triggers an icon
  render: the icon still renders only when the
  runner-derived icon state changes, through the existing
  cached `SetIcon` path (PR #39: clone + `DestroyIcon`,
  bitmap disposed, replaced menu disposed). Settings
  changes made in the dialog are visible in the menu and
  tooltip on the same tick that applies them.
  *Acceptance*: test — the battery pins the stamp-gated
  queue rebuild, the absence of any `GetHicon` /
  `Bitmap` / `Graphics.FromImage` outside the existing
  icon path, and that `SetIcon` is still invoked only
  with the runner-state value.
  *This does not establish* a bound on the dialog's own
  ordinary WinForms repainting — controls repaint as
  Windows repaints them; the discipline pinned here is
  about *allocations the tray controls* (icons, bitmaps,
  menus, controls), which is where the field crash lived.

- **FR-011 — Windowless discipline is unchanged.**
  *Behavior*: the tray stays a GUI-subsystem
  (`/target:winexe`) application; no code path in the
  tray writes to or allocates a console (the source
  contains no `Console.` use, pinned by the battery);
  the settings dialog of FR-003 is the single,
  user-summoned exception to the tray having no window,
  and nothing in the supervision path can open it.
  *Acceptance*: test — the battery pins the absence of
  `Console.` and that exactly one class in the source
  derives from `Form`.
  *This does not establish* anything about job payloads'
  windows — that stays spec 007 FR-004's discipline,
  unchanged.

### Part E — Currency

- **FR-012 — Documentation currency, minimal-overlap.**
  *Behavior*: SKILL.md's windowless-supervision section
  and the CHANGELOG's `[Unreleased]` section describe the
  settings surface and the fleet display in the same
  change, citing this spec. VERSION is deliberately
  **not** bumped here: the sibling spec 013 change owns
  the next version bump together with its skills-edition
  regeneration (the skills battery pins editions to
  VERSION), and two changes bumping the same line
  guarantees a needless conflict; whichever change lands
  second carries the tray's entry under its version.
  *Acceptance*: test — the CI changelog/VERSION check
  stays green exactly as it stands (VERSION 0.3.0 has its
  entry), and the skills battery is untouched and green.
  *This does not establish* the release the tray change
  eventually ships in — that is decided when the two
  in-flight specs land.

## Non-requirements (explicit)

- **No fleet configuration from the tray.** No control in
  the tray edits `syncyard.yaml`, the pause/focus record,
  priorities, or any gatekeeper state. Fleet operation
  stays with the `gk` commands on the operator side
  (spec 013); the tray's fleet section is a window, not a
  console.
- **No new transport.** The tray does not poll the
  gatekeeper, the GitHub API, or the backchannel. Its
  only fleet input is the snapshot file of FR-008, read
  from the local disk.
- **No runner-directory management.** Where a runner
  lives stays an install-time fact (spec 007 T010); the
  settings surface manages supervision and behavior, not
  paths.
- **No compile-route change.** The tray remains one C#
  source file compiled by the in-box csc with the
  existing two references; the NSIS package and
  `install-tray.ps1` are untouched.
- **No deployment.** Installing a build carrying this
  change on the fleet desktop is a separate act under
  the standing procedure, after the sibling spec 013
  change and this one have both landed.

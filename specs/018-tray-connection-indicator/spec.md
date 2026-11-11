# Feature Specification: Tray Connection Indicator

**Feature Branch**: `spec/018-tray-connection-indicator`

**Created**: 2026-11-02

**Status**: **Specified** (this change carries the spec only so
far). Implementation follows under this spec; nothing here
touches the running fleet, the desktop, or the installed tray.
The branch is based on `main` (the spec 014 tray, PR #40) and
is independent of the spec 013/015/016/017 stacks.

**Direction (owner, 2026-11-02 ~11:28 ET)**: "add TODO to add
connection indicator on the Syncyard tray. you can work on
that here just not on the remote until syncyard is back after
the reboot."

## The gap

The tray's icon today reports **runner health only** (spec
007 FR-007: state 0 all runners up, 1 degraded, 2 faulted).
It says nothing about **connection**: whether this host is
currently in contact with the fleet — the backchannel agent
syncing, heartbeats flowing, the fleet's picture of this host
fresh. The two failures look identical from the owner's
chair: runners can be perfectly healthy on a host that has
silently fallen off the fleet (the 2026-10-31 night outage
was found by silence, not by any indicator), and runners can
be down on a host whose connection is fine. During that
outage the machine sat at a logon screen for ~1h45m with
every Syncyard surface dark and nothing anywhere said so at
a glance.

The owner should be able to look at the tray and answer one
question instantly: **are we connected?**

## What "connected" means here

Connection is judged **only from signals already present on
the host** — the tray gains no new network authority, makes
no calls of its own, and never becomes a second watcher
(the fleet has exactly one watcher: the gatekeeper sweep).

- **Primary signal**: the backchannel agent's heartbeat
  file on this host (the agent writes its heartbeat into the
  host-local backchannel state on its ~60 s cycle; the file's
  freshness is the host's own proof it is syncing with the
  fleet's backchannel repository).
- **Secondary signal** (when present): the fleet-status
  snapshot file spec 014's Fleet section reads (default
  `C:\Users\trist\Development\Syncyard\status\fleet-status.json`,
  written by the FR-009 publisher when that ships). When the
  snapshot exists, its embedded timestamp also feeds the
  judgement; until the publisher ships, the primary signal
  alone carries it, and the indicator must say so honestly
  (see FR-004).

## Requirements

### FR-001 — A connection state, computed on the tray's tick

The tray computes one of three connection states on each
existing refresh tick, from the age of the newest available
signal:

- **CONNECTED** — newest signal age ≤ `connection.stale_s`
  (default 600 s).
- **STALE** — age ≤ `connection.down_s` (default 900 s,
  mirroring the gatekeeper's host liveness limit).
- **DISCONNECTED** — age beyond `connection.down_s`, **or
  no signal file has ever been seen** (a fresh install must
  not claim a connection it has never observed).

*Acceptance (test)*: the source battery drives the state
function against fixture signal files at ages straddling
both bounds and against a missing file; each case yields
the named state. *This does not establish* that the fleet
can reach the host — it establishes that the host's own
fleet signals are fresh, which is the direction the owner
reads from the tray.

### FR-002 — The icon carries the connection state

The tray icon renders the connection state **without
conflating it with runner health**: the existing runner
dot (color by runner state) stays; connection is drawn as
the icon's **link mark** — a small link glyph in the icon's
corner, solid when CONNECTED, hollow/amber when STALE, and
replaced by a broken-link mark (with the icon desaturated)
when DISCONNECTED. Rendering stays render-on-change only
(the spec 014 / PR #39 discipline: the icon is re-rendered
when a state it displays changes, never per tick).

*Acceptance (test)*: the source battery pins that the icon
render path consumes both states independently, that a
connection-only change re-renders, and that the per-tick
path renders nothing when neither state changed.
*Human-as-oracle*: at install verification, the owner sees
the three marks cycled (via a fixture signal file) and
confirms each is legible at tray size.

### FR-003 — The menu and tooltip say it in words

The tray menu gains a **Connection** line (and the tooltip
carries the same text) of the form:

    Connection: connected (heartbeat 42 s ago)
    Connection: stale (heartbeat 6 min ago)
    Connection: disconnected (no heartbeat for 22 min)
    Connection: unknown (no signal seen yet)

The age is recomputed on the tick that opens/renders the
menu; the text names the signal it rests on (`heartbeat`,
or `fleet snapshot` when that is the newest signal).

*Acceptance (test)*: battery cases pin the four wordings
against fixture ages and the missing-file case, including
the signal name switching when the snapshot is newest.

### FR-004 — Honesty about the signal set

When only the primary signal exists (today's fleet: the
FR-009 snapshot publisher is unbuilt), the settings
dialog's Fleet section and the menu line's detail view
state `signal: backchannel heartbeat only`. The indicator
never implies a fleet-side confirmation it does not have.

*Acceptance (test)*: with no snapshot file present, the
rendered detail names the single signal; with one present
and newest, it names both.

### FR-005 — Thresholds are tray settings

`connection.stale_s` and `connection.down_s` live in
`tray-settings.json` beside the other spec 014 settings,
edited through the existing settings dialog (validated:
positive integers, stale < down; invalid values refused at
save with the field named, prior values kept). Defaults
reproduce FR-001's numbers exactly.

*Acceptance (test)*: round-trip and refusal cases in the
source battery, mirroring the spec 014 settings cases.

### FR-006 — Connection transitions are logged

Every connection state transition is written to the tray
log with its timestamp and the signal age that caused it
(`connection CONNECTED -> STALE (heartbeat age 204 s)`).
No transition, no line — the log must be able to answer
"when did we lose the fleet?" after the fact (the question
the 2026-10-31 outage posed with no local answer).

*Acceptance (test)*: a fixture sequence of ages produces
exactly the transition lines, in order, and steady-state
ticks produce none.

### FR-007 — No behavior change to supervision

The indicator is **display only**. It never starts, stops,
or restarts a runner, never writes the signal files, and
never gates any existing tray action on the connection
state. A disconnected host's tray keeps supervising its
runners exactly as today.

*Acceptance (test)*: the battery pins that the connection
code path performs no process or signal-file writes (the
source carries no such call in the connection section).

### FR-008 — Documentation in the same change

`SKILL.md`, the skills pack, the tray's README surface, and
`CHANGELOG.md` describe the indicator, its three states,
and its signal honesty rule in this change (the standing
currency rule). VERSION is not bumped by this spec (the
spec 013 stack owns 0.4.0; a tray-only release decision is
the owner's).

## Decisions flagged for the owner (recommendations marked)

1. **(RECOMMENDATION) Icon form**: link-mark overlay +
   desaturation when disconnected (keeps runner dot
   semantics untouched). Alternative considered: a second
   dot — rejected, two dots invite misreading which is
   which at 16 px.
2. **(RECOMMENDATION) Defaults 600 s / 900 s** for
   stale/down, the down bound mirroring the gatekeeper's
   own host liveness limit so tray and gatekeeper agree on
   when a host is "down".
3. **(RECOMMENDATION) No tray-side network probe**, ever —
   the indicator reads host-local signals only; a tray that
   pings becomes a second watcher and a second source of
   contradictory truth.

## Out of scope

- The FR-009 fleet-status snapshot **publisher** (spec 014
  follow-up, owner-gated separately). This spec consumes
  the snapshot when it exists; it does not build it.
- Any fleet-side or gatekeeper change: the gatekeeper's
  liveness view is unchanged and remains the fleet's truth.
- Desktop installation of the built tray (field step,
  owner-scheduled — and, per the owner's 2026-11-02
  instruction, no remote work at all until Syncyard is back
  after the Session 2 reboot).

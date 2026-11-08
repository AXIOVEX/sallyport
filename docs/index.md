# Syncyard

**Synchronized, gated dispatch to your own machines: self-hosted
CPU/GPU runner lanes plus an API-budget gatekeeper for agent
workflows.**

A marshalling yard sorts work onto parallel tracks so everything
moves in order instead of colliding. Syncyard does that for a
fleet of AI agents and automation programs running real work on
the owner's own machines — renders, builds, bench runs, GPU jobs
that have to happen on the hardware the owner actually has.

The obvious bridge to that hardware is self-hosted GitHub Actions
runners, and it fails two ways at once. The **budget**: several
programs sharing one GitHub account share one 5,000-call hourly
REST quota, and a morning of every program dispatching and polling
its own jobs can exhaust it for everyone. The **queue**: a
self-hosted runner runs one job at a time, so a two-minute probe
can wait an hour behind a long render. And underneath both, a
hazard: a self-hosted runner reachable by the wrong trigger is
remote code execution on your own computer.

Syncyard's answer is ordering and a gate, not more machines:

- A **gatekeeper** is the only door. Programs submit job requests
  to it; it checks API headroom before dispatching, refuses
  duplicates, assigns lanes, and watches every run in one batched
  sweep.
- **Lanes** split the runners by job shape, so quick checks never
  stand behind long renders.
- **Marker verdicts**: a job's result is read from the marker
  lines it prints in its own log, never from the Actions success
  color alone.
- A written **fleet standard** covers poll discipline, lane
  routing, and job health checks.

## Two install routes

Syncyard is one engine with two ways in:

- **Standalone** — `pipx install syncyard` (or `pip install
  syncyard`): the gatekeeper itself as a Python package, driven
  as the `syncyard` command. See [Installation](installation.md).
- **GitHub CLI extension** — `gh extension install
  AXIOVEX/gh-syncyard`: adds `gh syncyard …` and routes every
  verb to the engine that serves it. See [Extension](extension.md).

Both drive the identical code and the identical verb surface.
Which you pick depends on whether `gh` is already your front
door.

## The Rust core

The engine is being rebuilt in Rust under specs 034-039:
a RAM-authoritative daemon, a CLI at verb parity, a streaming
progress protocol, a backchannel agent, and a successor tray
app with signed job control. The Python gatekeeper stays the
production engine until the spec 038 one-move cutover.
[Rust core](rust-core.md) describes what exists today,
including — plainly — what does not yet, and
[Acceptance gates](acceptance.md) defines the mechanical gate
the new stack must pass before it carries the fleet.

## Where to next

- [Installation](installation.md) — both routes, plus the
  checkout route the founding fleet runs.
- [Quickstart](quickstart.md) — register a runner, submit a
  first job, read its verdict.
- [Concepts](concepts.md) — lanes, the gate, marker verdicts,
  pause and stop, and what is still only specified.
- [Configuration](configuration.md) — every knob in
  `syncyard.yaml`, annotated.
- [Extension](extension.md) — the gh-syncyard front door and
  its per-engine routing.
- [Rust core](rust-core.md) — the daemon, CLI, agent, and
  tray of the successor stack, and their honest status.
- [Architecture](architecture.md) — the one-move cutover,
  venue-bound lanes, the Docker posture, and reconciliation.
- [Verb reference](verbs.md) — every verb on every surface,
  pinned to the engines' own schema output.
- [Progress protocol](progress.md) — spec 034's live progress
  lines, and why they are never verdicts.
- [Job control](control.md) — spec 037's signed command files
  and the repair kill switch.
- [Operations](operations.md) — pause discipline, the sweep,
  the outbox, the mirror, and self-healing.
- [Acceptance gates](acceptance.md) — spec 036's mechanical
  definition of "production stable," and where it stands.
- [Skills pack](skills.md) — the operating contract for AI
  agents, in eight editions.

## What Syncyard deliberately does not do

- **It does not prevent a shutdown.** Its shutdown story is
  *delay + drain + checkpoint + notify, never prevent*: it can
  hold a polite shutdown long enough for work to checkpoint —
  it cannot stop a forced shutdown, a power loss, or a held
  power button, and does not pretend otherwise.
- **It does not fail over between hosts.** A job addressed to a
  lane runs on that lane's host or is refused — never silently
  rerouted to a different machine.
- **It is not a general CI system.** Syncyard exists to run an
  owner's own work on an owner's own machines, under one
  person's rules: no multi-machine pooling, no runner
  autoscaling, no multi-tenancy.

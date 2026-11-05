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
  AXIOVEX/gh-syncyard`: a thin dispatcher that adds `gh syncyard …`
  and forwards to an installed gatekeeper, unchanged.

Both drive the identical code and the identical verb surface.
Which you pick depends on whether `gh` is already your front
door.

## Where to next

- [Installation](installation.md) — both routes, plus the
  checkout route the founding fleet runs.
- [Quickstart](quickstart.md) — register a runner, submit a
  first job, read its verdict.
- [Concepts](concepts.md) — lanes, the gate, marker verdicts,
  pause and stop, and what is still only specified.
- [Configuration](configuration.md) — every knob in
  `syncyard.yaml`, annotated.
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

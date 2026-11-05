# Feature Specification: gh Extension Refresh (STUB)

**Feature Branch**: `feat/031-gh-extension-refresh`

**Created**: 2026-11-05

**Status**: **Ratified as a stub** (owner, 2026-11-05 ~18:47
ET, the tray hardening batch; owner standing rule
2026-11-05 13:37 ET: every Syncyard change is backported
into the gh extension as the work lands, and the extension
stays current with the engine at all times). Execution
order: last in the batch. This stub records the workstream;
the full spec is written when the batch's earlier specs have
landed, because the refresh must describe the engine as it
then stands.

## The gap, briefly

AXIOVEX/gh-syncyard is at v0.1.0 (released 2026-10-31), a
thin Go dispatcher forwarding any gatekeeper verb to gk.py.
It has already drifted from the engine: its help text omits
verbs the engine has since gained (drain, lane locks,
priority, probe-host, snapshot, hold/withdraw, spec 016
routing), and its install-path lookup does not know this
fleet's paths. Each spec in this batch widens that drift
unless the refresh closes it.

## What the full spec must cover (recorded so it is not lost)

- Help and verb coverage at parity with the engine as of the
  last batch spec landed, proven by a parity check (the
  engine's verb list against the extension's), not by eye.
- Install-path lookup covering this fleet's gatekeeper
  locations, with an honest failure when none is found.
- Version bump and release through the extension's own
  release path; per the owner's sequence decision
  (2026-11-05 13:40 ET), only when the extension is solid
  and released does it become the front door used here, and
  only after that does a thin Muse connector wrapping
  `gh syncyard` (never gk.py directly) get specced. The
  connector is deferred, not cancelled, and is out of scope
  here.

## Tasks

- T001: This stub.
- T002 (later): the full spec, written against the landed
  engine.

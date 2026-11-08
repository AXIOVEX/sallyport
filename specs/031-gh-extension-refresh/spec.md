# Feature Specification: gh Extension Refresh

**Feature Branch**: `feat/031-gh-extension-refresh`

**Created**: 2026-11-05

**Status**: **Implemented and released 2026-11-08** as
gh-syncyard 0.4.0 (AXIOVEX/gh-syncyard, commit fd0abce,
release v0.4.0 with all 12 platform assets; CI + release
workflows green). Written as a stub 2026-11-05 (owner,
2026-11-05 ~18:47 ET, the tray hardening batch; owner standing
rule 2026-11-05 13:37 ET: every Syncyard change is backported
into the gh extension as the work lands, and the extension
stays current with the engine at all times). Executed last in
the batch, as P5 of the Rust implementation program, against
the engine as it landed in P1-P4. This full spec (T002)
describes that landed state.

## The gap (as recorded by the stub)

AXIOVEX/gh-syncyard was at v0.1.0 (released 2026-10-31), a
thin Go dispatcher forwarding any gatekeeper verb to gk.py.
Its help text omitted verbs the engine had since gained
(drain, lane locks, priority, probe-host, snapshot,
hold/withdraw, spec 016 routing), and its install-path lookup
did not know this fleet's paths. Because it forwarded blind,
the drift was silent.

## The refresh as landed

### FR-031-01 — Parity, proven by check, not by eye
The extension's verb surface is classified per verb by engine
(python / rust-cli / rust-socket / extension-native) and the
classification is pinned by drift tests to machine sources:
- `testdata/gk_verbs.txt` — the verb list extracted
  mechanically from gk.py's argparse setup; the extension's
  Python verb table must equal it exactly.
- `testdata/cli_schema.json` — the output of the built
  syncyard-cli's own `schema` verb (the generation source for
  the shim, spec 038 section 9); every schema verb must be
  accounted for, and every schema `not_yet_verbs` entry must
  appear in the extension's gap table with the schema's own
  note, and be served by the Python engine.
Any future drift fails the test suite until the extension is
refreshed. Unknown verbs are refused (exit 2, parity surface
printed) instead of forwarded blind.

### FR-031-02 — Python engine routing
All 22 gk.py verbs forward verbatim (stdin/stdout/stderr and
exit code unchanged, the 0.1.0 pattern): submit, status,
snapshot, sweep, outbox, cancel, pause, focus, resume, hold,
priority, lock, unlock, start, stop, drain, host-resume,
reboot, adopt, purge, profile, probe-host. Hold/withdraw is
`hold` plus `resume --job` (spec 013 FR-009); there is no
separate withdraw verb in either engine and none was invented.

### FR-031-03 — Spec 016 routing surface
`submit`'s auto-routing arguments (`--lane auto`,
`--needs-cpu`, `--needs-ram-mb`, `--needs-storage-mb`,
`--needs-gpu`, `--venue`, `--image`, `--mounts`) forward
untouched; help and README name them, and a test pins the
verbatim forwarding of a full routing invocation. `profile`
(incl. `profile suggest`) and `probe-host` are named verbs.

### FR-031-04 — Rust engine routing
Verbs that exist only in the Rust engine delegate to
syncyard-cli, always with an explicit `--home` (FR-038-11:
the CLI has no compiled-in state-home default): progress
(spec 034), kill-switch (spec 037 FIX-08, the CLI face of the
daemon's kill_switch verb), ledger, dispatch,
migrate-history, flush. Verbs both engines implement route to
the Python gatekeeper until the spec 038 one-move cutover;
the routing table records which those are.

### FR-031-05 — Spec 037 control surface
The daemon's control surface is exposed over its own
newline-JSON Unix socket (`<state home>/gk.sock`), the same
channel the tray's GatekeeperChannel uses:
- `control-outcomes` (daemon `control_outcomes`) — the
  published outcomes an app may treat as confirmation.
- `control-command FILE` (daemon `control_command`) —
  delivers one signed tray command file; verification and
  execution are daemon-side; the extension only transports.
- `kill_switch` read/set is reached through the CLI's
  `kill-switch` verb (FR-031-04).
The daemon-internal `agent_poll` / `agent_completion` /
`ping` socket verbs are deliberately NOT exposed: they are
the syncyard-agent venue channel and a liveness probe, not
operator verbs. That exclusion is data in the extension
(parity output), not silence.

### FR-031-06 — Install-path lookup (fleet)
Gatekeeper resolution order: `$SYNCYARD_GK`,
`$SYNCYARD_HOME/gatekeeper/gk.py`, `~/syncyard`,
`~/.syncyard`, `~/workspace/desktop-access-migration/
gatekeeper/gk.py` (this fleet's VM gatekeeper),
`/var/lib/syncyard/gatekeeper/gk.py` (mini-server system
install), beside the binary / one level up, `./gatekeeper`.
Failure is honest: setup help naming every location tried.
syncyard-cli resolution: `$SYNCYARD_CLI`,
`rust/target/{release,debug}` under the checkout, beside the
binary, PATH. Rust state home: `$SYNCYARD_STATE_HOME`, else
the resolved gatekeeper's directory. `doctor` reports every
resolution (and the daemon socket's presence) and exits
non-zero only when the Python engine cannot be found.

### FR-031-07 — Native surface
`version`, `help`, `doctor`, plus two new native verbs:
`parity` (the routing table; `--json` for machine form) and
`schema` (the parity surface as JSON — the extension's
counterpart of the Rust CLI's schema verb).

### FR-031-08 — Version and release
One version train (spec 038 UPD-01): the extension carries
the engine's VERSION, released as **v0.4.0** (0.2.x/0.3.x were
never released; the jump is recorded in the extension's
CHANGELOG). Release through the extension's own path:
tag + GitHub release, assets built by the repo's
gh-extension-precompile workflow (12 platform binaries,
matching v0.1.0's set); a new CI workflow runs vet + tests +
windows/darwin builds. Per the owner's sequence decision
(2026-11-05 13:40 ET), the extension now becomes the front
door used by the Desktop Access chat; the thin Muse
connector wrapping `gh syncyard` (never gk.py directly) is
specced after, and remains out of scope here.

## Named gaps carried forward
- The Rust CLI's nine `not_yet_verbs` (sweep, priority, lock,
  unlock, focus, drain, probe-host, adopt, purge) are served
  by the Python gatekeeper; Rust-side they land with the
  GitHub transport phase. Routing flips at the cutover.
- `control-command` delivers to a daemon on the same host.
  The backchannel leg carrying signed command files from a
  tray host to the gatekeeper host is field wiring (spec 037
  gap carried from P4 of the Rust program).
- The daemon socket channel is unix-only in this build (the
  tray carries the same limitation); on Windows the control
  surface goes through the tray app.

## Tasks
- T001: This stub (2026-11-05). Done.
- T002: The full spec, written against the landed engine —
  this document (2026-11-08, with the P5 implementation).

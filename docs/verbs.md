# Verb reference

Syncyard has three verb surfaces, and this page is the whole
of all three. The tables are pinned to machine sources by
drift tests: the Python list is extracted from `gk.py` itself,
and the Rust list is captured from the CLI's own `schema`
verb. If a verb is not in these tables, it does not exist; the
gh-syncyard extension refuses unknown verbs (exit 2, with the
parity surface printed) instead of forwarding them blind.

All surfaces are at version **0.4.0**, contract version **1**.

## Python gatekeeper verbs (gk.py) — 22

The production engine today. Every verb is forwarded verbatim,
with exit-code propagation, by the gh-syncyard extension.

| Verb | What it does |
|---|---|
| `submit` | Submit a job (script, lane or `--lane auto` routing, expected duration, notify chat). Spec 016 routing flags forward untouched: `--needs-cpu`, `--needs-ram-mb`, `--needs-storage-mb`, `--needs-gpu`, `--venue`, `--image`, `--mounts`. |
| `status` | The board: budget, pause state, active and waiting jobs. |
| `snapshot` | The machine-readable fleet snapshot (the fleet-status publisher's source). |
| `sweep` | One batched watch pass over all runs: harvest, watermarks, dispatch. |
| `outbox` | Push notifications owed to program chats; `--ack` after delivery. |
| `cancel` | Cancel a job (GitHub-side cancel where a run exists, ledgered). |
| `pause` | Write PAUSED.json: program dispatch holds, maintenance still flows. |
| `focus` | Dispatch focus on one program or job class. |
| `resume` | Release a pause, or release a per-job hold (`resume --job`). |
| `hold` | Hold one waiting job. Hold/withdraw is `hold` + `resume --job` (spec 013 FR-009); there is no separate withdraw verb in either engine. |
| `priority` | Reprioritize a waiting job / operator priority dispatch surface. |
| `lock` / `unlock` | Lane locks: take a lane out of, or return it to, the dispatch scan. |
| `start` / `stop` | Start or stop the dispatch engine (stop is the hard halt). |
| `drain` | Let active jobs finish; dispatch nothing new. |
| `host-resume` | Resume a host after a reboot-class interruption. |
| `reboot` | Ledgered host reboot act. |
| `adopt` | Adopt an externally-created run into a queue record. |
| `purge` | Destructive record cleanup (operator act, named scope). |
| `profile` | Show or set a host capability profile (spec 016). |
| `probe-host` | Run the capability probe against a host (spec 016). |

## Rust CLI verbs (syncyard-cli schema) — 18

Advertised by `syncyard-cli schema`. Until the spec 038
one-move cutover, shared verbs (submit, status, snapshot,
outbox, pause, stop, start, hold, resume, cancel) are served
by the Python engine; the extension's routing table records
that explicitly per verb.

| Verb | Served by | Notes |
|---|---|---|
| `submit` | Python until cutover | Record shape round-trips gk.py JSON. |
| `status` | Python until cutover | Merges the live spec 034 progress view per job. |
| `progress` | Rust CLI | One job's live progress view; honestly degrades to "no progress reported" in file-fallback mode (progress is daemon RAM). |
| `snapshot` | Python until cutover | The daemon snapshot carries the per-job summary array the tray renders from. |
| `outbox` | Python until cutover | |
| `pause` | Python until cutover | |
| `stop` | Python until cutover | |
| `start` | Python until cutover | |
| `hold` | Python until cutover | |
| `kill-switch` | Rust CLI | The CLI face of the daemon `kill_switch` verb (FIX-08): read, or set/clear as a ledgered operator act. See [Job control](control.md). |
| `resume` | Python until cutover | |
| `cancel` | Python until cutover | The daemon path shares one cancel implementation with tray control commands (FR-037-09). |
| `ledger` | Rust CLI | Read the ledger through the daemon or the file fallback. |
| `dispatch` | Rust CLI | Daemon-side; refused by name in file-fallback mode (transport is daemon-side). |
| `migrate-history` | Rust CLI | Import a Python gatekeeper directory with SHA-256 manifest + hash-chained ledger. |
| `flush` | Rust CLI | Force the daemon's write-behind flush. |
| `version` | Rust CLI | Workspace version (0.4.0). |
| `schema` | Rust CLI | This surface as JSON: verbs, `not_yet_verbs` with reasons, contract version. The generation source for the extension's surface. |

## Not yet in the Rust CLI — 9, all served by Python

These are named in the schema output itself, with reasons.
The gap is Rust-side only and closes with the GitHub transport
phase; none of them is missing from the product.

| Verb | Schema note |
|---|---|
| `sweep` | Harvest/reconcile loop lands with the GitHub transport. |
| `priority` | Lane priority surface, later phase. |
| `lock` | Lane locks, later phase. |
| `unlock` | Lane locks, later phase. |
| `focus` | Dispatch focus, later phase. |
| `drain` | Host drain, later phase. |
| `probe-host` | Capability probe, later phase. |
| `adopt` | Run adoption, later phase. |
| `purge` | Destructive; requires its own operator design. |

## Daemon socket verbs — 6

Newline-JSON over `<state home>/gk.sock`, the same channel
shape the tray uses.

| Verb | Exposed through the extension? |
|---|---|
| `control_command` | Yes, as `control-command`: transports one signed command file; verification and execution stay daemon-side. |
| `control_outcomes` | Yes, as `control-outcomes`: the published outcomes the tray confirms against. |
| `kill_switch` | Yes, via the Rust CLI's `kill-switch` verb. |
| `agent_poll` | No, by design: it is the agent venue channel. |
| `agent_completion` | No, by design: it is the agent venue channel. |
| `ping` | No, by design: it is a probe. |

## Extension-native verbs (gh-syncyard) — 5

| Verb | What it does |
|---|---|
| `version` | Extension version (0.4.0, the engine's train). |
| `help` | The full parity surface, gaps named. |
| `doctor` | Resolves both engines, the state home, and socket presence; prints setup help naming the fleet install locations when a gatekeeper is not found. |
| `parity` | The routing table (or `--json`): every verb, its engine, its state. |
| `schema` | The parity surface as JSON. |

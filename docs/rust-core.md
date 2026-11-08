# The Rust core

Syncyard's engine is being rebuilt in Rust under specs 034-039
(ratified as a block, 2026-11-07). The Python gatekeeper remains
the production engine until the spec 038 one-move cutover; the
Rust core is built, tested, and traced against the ratified
specs in the open, and this page describes exactly what exists
today, including what does not.

One version, one train (spec 038 UPD-01): every Rust crate
carries the workspace version, read from the repo `VERSION`
file — currently **0.4.0**, the same number the Python package
and the gh-syncyard extension carry.

## The crates

The workspace lives at `rust/` in the repository:

| Crate | Role | Coverage bar (spec 036) |
|---|---|---|
| `syncyard-contract` | The shared wire types: job and queue records that round-trip the Python gatekeeper's JSON byte-for-byte in shape, the spec 034 progress protocol types, and the spec 037 signed command types (Ed25519). | 95% |
| `syncyard-daemon` | The state engine: RAM-authoritative records, single-writer flock, fencing epoch on the off-VM record, two-phase ledgered dispatch, boot and ghost reconciliation, progress ingestion, control-command execution. Serves a Unix socket at `<state home>/gk.sock`. | 90% |
| `syncyard-cli` | The operator CLI: a thin socket client when the daemon is up, with a direct-file fallback that takes the same flock. Its `schema` verb is the machine-readable source of the verb surface (see [Verb reference](verbs.md)). | 85% |
| `syncyard-agent` | The backchannel agent: polls for work, executes payloads, streams progress, reports completion with the payload's own exit code. Singleton by install path; windowless by construction. | 85% |
| `syncyard-tray` | The successor tray app (specs 035 + 037): presentation core and job-control client. See the honest-status note below. | 80% |

A dev tool, `tools/syncyard-tracegen`, generates the spec 036
traceability matrix from the spec files (never hand-maintained)
and fails on any gap class. It is not shipped and carries no
coverage bar.

## Building

Requires a current stable Rust toolchain (the fleet builds
with cargo/rustc 1.99.1):

```bash
cd rust
cargo build --workspace
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --check
```

State is never compiled in: the daemon and CLI both require an
explicit state home (`--home DIR` or `$SYNCYARD_HOME`), per
FR-038-11. Test state belongs in scratch directories, never in
a live gatekeeper's home.

## Daemon

```bash
syncyard-daemon serve --home DIR [--offvm DIR]
```

Boot order is lock, load, fencing epoch, reconciliation,
announce, serve. Records are RAM-authoritative; a write-behind
flush lands them every 60 seconds and on transitions, with
read-back verification. Dispatch is two-phase and ledgered:
intent, off-VM witness confirmed by read-back, idempotent
trigger, capture, commit. Boot reconciliation lets GitHub win
on run state and never overwrites a payload verdict.

**Honest gap:** no live GitHub transport is wired yet. The
dispatch and run-state surfaces sit behind traits with
in-memory fakes for tests, and the daemon **refuses to serve
without `--allow-no-transport`** so it can never be mistaken
for production. The transport lands with the cutover wiring.

## CLI

The CLI covers the core verb surface today: submit, status,
progress, snapshot, outbox, pause, stop, start, hold, resume,
cancel, ledger, dispatch, migrate-history, flush, kill-switch,
version, schema. Nine verbs the Python gatekeeper serves are
named in the schema as `not_yet_verbs` and are served by
Python until the transport phase; they are listed, with their
reasons, in the [Verb reference](verbs.md). Nothing is
silently absent: `syncyard-cli schema` prints the whole
surface, implemented and not-yet alike.

`migrate-history` imports a Python gatekeeper directory
(read-only) into the daemon's history store with a per-record
SHA-256 manifest and a hash-chained ledger, and verifies
byte-exact on export.

## Agent

The agent's channel is a trait: poll, progress, completion. It
pre-creates the per-job progress-file directory, launches the
payload with `SYNCYARD_PROGRESS_FILE` set (see
[Progress protocol](progress.md)), tails the file forwarding
complete lines only, and at exit reports the payload's own
exit code — a payload that never started, or whose exit status
is unreadable, reports an error, never a manufactured success.
On Windows, payloads spawn through a plan that always carries
`CREATE_NO_WINDOW`, stdio goes to per-job log files, and the
agent binary itself is built for the Windows subsystem: no
console window, ever, by construction.

Status: proven end-to-end in-process against the real daemon
over a real socket. It has not yet run on a live host; first
live exercise is a field-phase act.

## Tray (successor app)

The presentation core is platform-independent and headlessly
proven: identity with train + build stamp on every surface,
connection derived from host-local signals only (backchannel
heartbeat primary, fleet snapshot secondary), fleet line with
staleness, job rows rendered verbatim from the daemon
snapshot, and the spec 037 control client (per-host signing
keys, signed command files, confirmation read only from the
gatekeeper's published outcomes — see [Job control](control.md)).

The singleton is keyed on the **canonical install path**
(FR-035-17): a second launch of the same install is refused
and the refusal is logged. That is the fix for the founding
fleet's two-tray defect, where two installs of the old tray
ran side by side.

**Honest gap:** there is no live GUI shell yet — no OS tray
icon, native menu, or event loop on any platform. The binary
performs one headless diagnostic pass and exits. Field
verification on real Windows, Linux, and macOS hosts is an
acceptance-phase act (see [Acceptance gates](acceptance.md)).

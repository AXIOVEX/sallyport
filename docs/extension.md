# The gh-syncyard extension

`gh syncyard …` is the fleet's front door: a GitHub CLI
extension (separate repository, `AXIOVEX/gh-syncyard`) that
routes every verb to the engine that serves it. It carries
the engine's version — **0.4.0** — on the one version train.

```bash
gh extension install AXIOVEX/gh-syncyard
gh syncyard version        # gh-syncyard 0.4.0
gh syncyard doctor         # resolves engines, state home, socket
gh syncyard status         # the live board
```

Upgrade in place with `gh extension install --force
AXIOVEX/gh-syncyard`.

## Routing, per verb

The 0.1.0 extension forwarded everything blind to the Python
gatekeeper, and its help listed only the verb set of its day;
drift passed silently. The refreshed extension routes **by
engine**, from tables pinned to machine sources by drift
tests (the Python list extracted from `gk.py`, the Rust list
captured from the CLI's `schema` verb):

- **Python gatekeeper — 22 verbs** forwarded verbatim with
  exit-code propagation: the production surface (see the
  [Verb reference](verbs.md)).
- **Rust CLI — 6 verbs** delegated with an explicit `--home`:
  `progress`, `kill-switch`, `ledger`, `dispatch`,
  `migrate-history`, `flush`.
- **Daemon socket — 2 verbs** speaking newline-JSON to
  `<state home>/gk.sock`: `control-outcomes`,
  `control-command`.
- **Extension-native — 5 verbs**: `version`, `help`, `doctor`,
  `parity`, `schema`.

Verbs both engines implement route to Python until the
spec 038 one-move cutover; the routing table records that
explicitly instead of hiding it. Unknown verbs are refused
(exit 2, parity surface printed).

```bash
gh syncyard parity         # every verb, its engine, its state
gh syncyard parity --json  # the same, machine-readable
gh syncyard schema         # the CLI's advertised surface
```

Spec 016 routing is first-class: `submit --lane auto` with
`--needs-cpu`, `--needs-ram-mb`, `--needs-storage-mb`,
`--needs-gpu`, `--venue`, `--image`, and `--mounts` forwards
untouched, and `profile` / `probe-host` are named verbs.

## Finding the engines

`doctor` resolves, in order: `$SYNCYARD_GK` / `$SYNCYARD_HOME`,
`~/syncyard`, `~/.syncyard`, beside the extension binary,
`./gatekeeper`, and the founding fleet's known install paths
(the control VM's gatekeeper checkout, and the always-on
server's `/var/lib/syncyard/gatekeeper`). Failure prints setup
help naming those locations instead of a bare not-found.

The Rust CLI is found via `$SYNCYARD_CLI`, the checkout's
`rust/target/{release,debug}`, beside the extension binary,
or PATH. The state home is `$SYNCYARD_STATE_HOME`, else the
gatekeeper's directory.

One known gap, named in the extension's own help: in a bare
shell on the founding fleet's control VM, `doctor` reports
syncyard-cli NOT FOUND until `$SYNCYARD_CLI` is set, because
the only build there is a debug binary inside the program's
clone, not a lookup root for the live checkout. A
release-profile install location lands with the one-train
release.

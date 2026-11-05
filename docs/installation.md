# Installation

There are three ways to put the Syncyard gatekeeper on a
machine. All three run the identical code — the package and the
checkout are byte-for-byte the same files (spec 017 FR-003) —
so the choice is about how the machine is managed, not about
behavior.

## Route 1: standalone package (pip / pipx)

Requires Python 3.9 or newer (proven on 3.12). The package has
**zero runtime dependencies** — the gatekeeper is Python
standard library only.

```bash
# Isolated (recommended): the command lands on your PATH,
# the environment is the app's own
pipx install syncyard

# Or into any virtual environment
pip install syncyard
```

Then drive the gatekeeper as `syncyard`:

```bash
syncyard status
syncyard submit --program myprogram --workflow run.yml \
    --lane quick --script ./probe.ps1 --key probe-1
```

The full verb surface — `submit`, `adopt`, `status`, `cancel`,
`sweep`, `pause`, `resume`, `start`, `stop`, `purge`, `outbox`,
`host-resume`, `reboot` — is the gatekeeper's own; the console
command is a direct entry point to it, not a wrapper (spec 017
FR-002). The failsafe backchannel client ships in the package
as a module:

```bash
python3 -m syncyard.backchannel.bc status
```

**Where state lives.** The gatekeeper keeps its state (job
records, the append-only ledger, pause/stop files, its log) in
files beside its own code. In an install, that is the package
directory inside the environment — for `pipx`, the app venv —
which is writable, self-contained, and removed with the app.
An installed gatekeeper is therefore its *own* gatekeeper with
its own board; it does not share state with any checkout on
the same machine.

**Pointing it at a fleet config.** Configuration resolves in
this order: the `SYNCYARD_CONFIG` environment variable, then
`SYNCARD_CONFIG`, then a `syncyard.yaml` beside the code, then
built-in defaults. To run an installed gatekeeper against an
existing fleet configuration:

```bash
SYNCYARD_CONFIG=/path/to/syncyard.yaml syncyard status
```

See [Configuration](configuration.md) for the file itself.

## Route 2: GitHub CLI extension

```bash
gh extension install AXIOVEX/gh-syncyard
gh syncyard status
```

The extension (separate repository, `AXIOVEX/gh-syncyard`) is
a thin dispatcher: it locates a gatekeeper on disk — via
`$SYNCYARD_GK`, `$SYNCYARD_HOME`, `~/syncyard`, `~/.syncyard`,
beside the extension binary, or `./gatekeeper` — and forwards
arguments, output, and exit codes unchanged. It pairs naturally
with either of the other two routes as the gatekeeper source.

## Route 3: from a checkout

The founding fleet runs the gatekeeper straight from a clone,
by path:

```bash
git clone https://github.com/AXIOVEX/syncyard
python3 syncyard/gatekeeper/gk.py status
```

State then lives in the checkout's `gatekeeper/` directory and
the config is the `syncyard.yaml` beside `gk.py`. This is the
route to take when the gatekeeper's tree is itself managed as
part of the fleet (pinned to a release, reviewed as code).

## After installing

The gatekeeper is only the door; the work runs on **runners**
you register on machines you own. The [Quickstart](quickstart.md)
walks the first one end to end, and the repository's
`runner/SETUP.md` carries the full per-host runbook (Windows,
WSL2, Linux, macOS, Docker venues).

One security note before any of that: runners are reachable
**only** via `workflow_dispatch` from the gatekeeper. Read the
project's `SECURITY.md` before you deploy anything — the
dispatch workflows live in a **private** door repository, and
that privacy is part of the design, not an accident of the
founding fleet.

# Operations

Running a Syncyard fleet day to day: the pause discipline,
the watch loop, the notification outbox, and the self-healing
surfaces. Everything here is an operator act with a record;
nothing on this page happens silently.

## Pause, stop, start

- **`pause`** writes the pause record (PAUSED.json). Program
  dispatch holds; maintenance jobs (the fleet-status mirror)
  still dispatch, and the sweep still watches. The pause stays
  in force until the owner lifts it — there is no timeout.
- **`stop`** is the hard halt of the dispatch engine; **`start`**
  resumes it. A stop is stronger than a pause and is treated
  as an incident-class act.
- **Per-job holds** (`hold`, released by `resume --job`) hold
  one waiting job without touching the rest of the board. The
  ghost-reconciliation flow uses holds as its hold-first
  precondition (see [Architecture](architecture.md)).
- **Drain** lets active jobs finish while dispatching nothing
  new — the polite way to empty a host before maintenance.

Operator lifts bypass the pause deliberately, one job at a
time, and each one ledgers an `operator_priority_dispatch`
event naming the gates it bypassed. A lift is a decision, and
the ledger keeps it.

## The sweep

One watcher watches everything. The sweep is a single batched
pass that harvests finished runs (markers and verdicts from
the runs' own logs), advances watermarks, reconciles records,
and dispatches what the lanes allow. Poll discipline is a
fleet rule, not a preference: 3-to-5-minute minimum intervals,
one watcher per run, and no per-program polling — the account
shares one 5,000-call hourly GitHub REST bucket, and the sweep
is how that budget survives.

Budget guardrails are mechanical: below 1,000 calls remaining
the sweep stands down to minimum cadence; an HTTP 403 is a
hard stop for that scope, reported, never retried around.

Verdicts come from the harvested record — the payload's own
markers and exit code — never from the wrapper's completion
state (spec 020). A job whose payload verdict is missing is
UNVERIFIED, and is reported as such.

## The outbox

Completions owed to program chats collect in the **outbox**,
grouped by destination chat. The delivery discipline is
strict:

1. Read the live outbox.
2. Deliver every group, from its queue record.
3. Only then ack — an ack covers only groups already delivered
   in the same pass — and verify the outbox reads empty.

Acks before delivery lose notifications; deliveries repeated
from stale snapshots double-report. The live outbox read
governs, always.

## The fleet-status mirror

The desktop tray reads its Fleet line from a **local** file.
A publisher on the control VM writes a fresh fleet snapshot
every 2 minutes to a private backchannel repository, and a
mirror cycle every 10 minutes carries that snapshot through
the gatekeeper to both tray destinations on the desktop (the
Syncyard status directory and the tray's own app-data copy),
writing nothing else. Each landing is verified from the queue
record: both `MIRROR_WROTE` markers with the dispatched hash,
then `MIRROR_DONE`.

## Self-healing publisher

The publisher is a systemd timer on a VM that gets replaced
without warning, and replacements silently remove its units —
the fleet's most repeated staleness incident. The mirror
detects it: if the published snapshot is older than 6 minutes,
the mirror runs the repair itself — recreate the snapshot
drop-in, run the packaged installer, reload, restart, verify
the timer active and a fresh publish — at most one repair per
cycle, ledgered in the mirror log as `SELFHEAL_OK` or
`SELFHEAL_FAIL`. A stale tray Fleet reading is treated as an
incident, diagnosed and fixed, never waited out.

The repair kill switch (FIX-08) halts all of this self-healing
without halting job execution — see [Job control](control.md).

## Ghost reconciliation

Ghosts (runs terminal on GitHub, records still reading
dispatched) are reconciled from GitHub's terminal state, each
completion ledgered as `operator_record_repair`, and always
**hold-first**: the pause or per-job holds are written before
any ghost completes, so freed lanes cannot dispatch work
nobody decided to run. See [Architecture](architecture.md).

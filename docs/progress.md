# Progress protocol (spec 034)

Long jobs used to be opaque until their terminal record
landed. Spec 034 gives every payload a trivial way to report
live progress, and every surface (CLI, tray, watcher) the same
live view of it — without ever letting a progress line
impersonate a verdict.

## The progress file

Every job is launched with the environment variable
`SYNCYARD_PROGRESS_FILE` set to a per-job path, and the runner
creates the parent directory before the payload starts
(FR-034-01). The file is line-oriented, append-only, UTF-8.
The runner never invents lines: a job that writes nothing
produces no progress, and "no progress reported" is a
displayable state, never an error by itself.

A payload opts in by appending lines. In any language:

```bash
echo "STARTED" >> "$SYNCYARD_PROGRESS_FILE"
echo "PROGRESS 3/10 compiling modules" >> "$SYNCYARD_PROGRESS_FILE"
echo "MARKER checkpoint reached" >> "$SYNCYARD_PROGRESS_FILE"
echo "HEARTBEAT" >> "$SYNCYARD_PROGRESS_FILE"
```

## Line grammar

The grammar is exactly five line shapes (FR-034-02):

| Line | Meaning |
|---|---|
| `STARTED` | The payload has begun executing (distinct from wrapper start). |
| `MARKER <text>` | A free-text milestone, verbatim from the payload. |
| `HEARTBEAT` | The payload is alive at write time. |
| `PROGRESS <k>/<n> <label>` | k of n units complete, 0 <= k <= n, n >= 1. |
| `COMPLETED <verdict-word>` | The payload's own final line. Advisory only — see below. |

Consumers ignore unknown lines, and count malformed `PROGRESS`
lines without ever failing the job over them.

## Transport and ingestion

The agent tails the progress file on the forward interval
(production default 60 s), forwarding complete lines only — a
half-written line waits for its newline. The daemon ingests
batches onto the job's RAM record; progress never touches the
durable record and never ledgers.

Ingestion is strict about attribution, in this order:

1. **Attribution.** A batch must name the record's run (or bind
   the run it names) and the source identity established for
   that run. Anything else is quarantined and counted, never
   folded.
2. **Cadence.** At most one accepted refresh per job per
   cadence interval (default 60 s). Throttled batches are
   acknowledged as throttled so the sender retains its lines
   and re-sends them with the next batch.

A failed feed marks only that job "progress unknown". The
job's verdict path is untouched (FR-034-07). After a daemon
restart, a running job reads "no progress reported" until its
next batch lands — a known, spec-conformant degradation of at
most one cadence interval.

## Display states

Every surface renders the same view, from the same snapshot
value (FR-034-08, T034-14):

- *no progress reported* — nothing has arrived.
- *progress unknown* — the feed failed; the job itself is
  unaffected.
- *in progress* — with age, elapsed time, fraction, and
  regression and quarantine counts.
- *progress stale* — no accepted batch for 180 s.
- *payload reports completed, verdict pending harvest* — a
  `COMPLETED` line arrived; see the next section.

`PROGRESS k/n` is monotonic as displayed: a later snapshot
with a smaller k is shown **with a regression note**, never
silently clamped (FR-034-06).

## Progress is never a verdict

No progress line, including `COMPLETED`, is a verdict, and no
surface may render, forward, or ledger one as a pass or fail
(FR-034-03). The verdict is the payload's own exit code in the
terminal record, and nothing else (spec 020; the fleet rule
that wrapper success is not job success). The spec's own
falsifier states it plainly: a job whose payload writes
`COMPLETED` and then exits non-zero, displayed anywhere as
passed, means this spec has failed.

The agent's completion report makes the rule code: exit 0 is
success, any other exit is failure, and a payload that never
started (or whose exit status is unreadable) is an error —
never a manufactured success.

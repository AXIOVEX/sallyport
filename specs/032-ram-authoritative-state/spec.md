# Feature Specification: RAM-Authoritative Gatekeeper State (wave-proof writes)

**Feature Branch**: `feat/032-ram-state`
**Created**: 2026-11-06
**Status**: **Ratified** (owner, 2026-11-06 ~10:00 ET,
"yes do all this to your recommendation"). Execution order:
after spec 030 (dispatch starvation), before spec 031
(gh extension refresh). Directed by the owner 2026-11-06
~09:56 ET: "find a better way to handle gatekeeper
writes that does not rely on file access... do in RAM natively and
cache on filesystem periodically when no write denial is in effect."
**Companions**: the publisher already runs this pattern in one
direction (state in memory, published to the GitHub Contents API);
the wave sampler proves sockets stay up during waves; the safe-write
package (~/workspace/system/safe-write/) is the existing
verified-write layer this spec extends rather than replaces.

## The defect

Every gatekeeper state change is a filesystem write, and the wave
denies exactly those. Measured costs, 2026-11-06 alone:

- `dispatch_one` refuses to dispatch what it cannot record first
  (spec 001 FR-013), so a wave freezes ALL dispatching, including
  operator lifts (Arcturus stack2 sat 30 min; the lift returned
  False at the pre-dispatch save).
- Submits die record-less after 100+ second timeouts (tray probe,
  two mirror cycles), so the submitter cannot tell whether the job
  exists.
- Sweep harvests succeed (the GitHub log archive read works) but
  their saves fail, so verdicts sit unrecorded in queue records
  showing stale state (itemG-patch2: Actions completed, queue
  record still "dispatched", markers 0).
- The outbox ack fails on its cursor write, so delivered groups
  re-list and must be deduped by hand.
- `save_job` burns up to 8 write-verify attempts with exponential
  backoff per record per pass, so every sweep cycle during a wave
  is slow as well as ineffective.

The verified-write defense itself is correct and stays: nothing was
corrupted in any wave, because every failed write was refused. The
defect is the ARCHITECTURE: the filesystem is the only store, so a
filesystem denial is a total outage.

## Measured shape of the problem (2026-11-06)

- Queue: 1,701 records, 42 MB total, but only 23 live
  (non-terminal) records totalling 23 KB. The bulk is history:
  log_tail and markers embedded in completed records.
- Ledger: 2.7 MB append-only JSONL.
- Wave signature: write-family syscalls fail EPERM to file-backed
  fds; reads, creates, opens succeed; open(O_TRUNC) destroys content
  at open. Sockets are unaffected (the wave sampler serves over a
  Unix socket through waves; agent tool channels work through
  waves).

## The design

### 1. Authority moves by record class

- **Live job records** (submitted, dispatched, running): RAM is
  authoritative. A long-lived gatekeeper daemon holds them in
  memory. The filesystem copy becomes a write-behind cache.
- **Ledger**: RAM-buffered append stream, flushed as one verified
  batch. During a wave the buffer holds; nothing is dropped.
- **History** (completed records): stays file-backed and read-only
  in steady state. Bulk moves to a compacted archive file; live
  lookups read the archive, never 1,700 individual files.
- **Outbox cursor**: a RAM counter in the daemon; its disk copy is
  a cache. A stale cursor after a crash re-lists groups, which the
  existing dedupe discipline already handles safely.

### 2. The daemon (gk serve)

- One long-lived process per VM boot, started by the same recovery
  path that reinstalls the memory watchdog after a replacement
  (and added to that checklist; the wave sampler joins it too).
- Serves a Unix domain socket at
  ~/workspace/desktop-access-migration/gatekeeper/gk.sock.
  Sockets pass during waves (proven by the sampler), so every CLI
  verb (submit, dispatch, status, outbox, snapshot) works at full
  speed mid-wave against RAM state.
- CLI verbs become thin socket clients when the daemon answers,
  and fall back to today's direct file mode when it does not
  (daemon down must never be worse than today).
- The daemon is the ONLY writer of queue state while it runs;
  direct file mode takes an exclusive lock the daemon also holds,
  so the two writers can never interleave.

### 3. Write-behind cache with periodic verify

- The daemon attempts a verified flush of dirty state every cycle
  (60 s) and on every state transition, using the existing
  safe-write machinery unchanged.
- A failed flush is a NON-EVENT: state stays dirty in RAM, the
  flush retries next cycle, and the flush health is itself a
  snapshot field (state_dirty_records, last_flush_ok_at) so the
  tray and the sweeps can see it.
- When a wave clears, the first successful flush lands the whole
  backlog at once: every record the wave held back appears on disk
  exactly as if it had been written live.

### 4. The GitHub leg leaves the filesystem entirely

- The one write with an outside witness, the pre-dispatch record,
  stops depending on local disk: before dispatching, the daemon
  writes the job record to a state branch in the door repo via the
  Contents API (the publisher's proven path), and dispatches only
  after that write is confirmed. A wave cannot block an HTTPS call.
- Consequence: dispatching works mid-wave, which is the single
  highest-value change in this spec. The local disk record becomes
  cache for a record that already exists off-VM.
- Submissions likewise acknowledge from RAM plus the off-VM record,
  so a submitter always learns whether its job exists.

### 5. Crash and replacement honesty

- The VM is replaced roughly every 2 hours, and the daemon dies
  with it. Unflushed RAM state at that moment is LOST, and this
  spec does not pretend otherwise. The bound: any job that was
  DISPATCHED has its record off-VM (section 4) before the dispatch,
  so a replacement loses at most (a) submitted-but-never-dispatched
  jobs, which their submitters re-submit on a missing-record check,
  and (b) unflushed ledger lines and harvest results, which the
  next sweep re-derives from GitHub, the standing re-derivation the
  sweep already performs.
- On boot, the daemon loads disk state plus the off-VM records,
  reconciles against GitHub run states (the sweep's existing
  logic), and serves.

### 6. What does NOT change

- The verified-write defense, everywhere it applies today. RAM
  authority ADDS a store; it never weakens the rule that a disk
  write is only believed after read-back verification.
- Payload verdict discipline, the ledger's event vocabulary, the
  queue record schema (additive fields only), the sweep's
  re-derivation behavior, and the CLI surface.
- The tray and publisher read paths: they read the published
  snapshot (Contents API) and mirrored files, both of which the
  daemon keeps feeding.

## Falsifiers

- Kill the daemon mid-wave with dirty state, restart it, and the
  reconciled state MUST equal the GitHub-derived state for every
  dispatched job; any dispatched job missing its off-VM record
  fails the spec.
- During a wave, a submit plus dispatch through the socket MUST
  complete in seconds (today: 100+ s timeout, no record); a
  completion time over 30 s mid-wave fails the spec.
- A flush after a wave MUST land byte-identical records to what
  the same transitions would have written live (hash comparison
  against a no-wave control run).
- With the daemon stopped, every CLI verb MUST behave exactly as
  today (fallback parity battery).

## Sequencing

This spec joins the ratified hardening queue. Recommended slot:
after spec 030 (dispatch starvation), before spec 031 (extension
refresh), because the extension should wrap the socket client, not
the file mode. Specs 028 and 029 are tray-side and unaffected.
Interim relief already in force until this ships: operator lifts
retry in the first clear window, harvests are read from the GitHub
log archive directly when record saves fail, and mirror submits
retry next cycle by design.

## Explicitly out of scope

- Any change to the wave itself (a platform defect, filed per
  instance under the standing order).
- Moving history into a database; the compacted archive stays
  plain files, readable by every existing tool.
- Multi-VM gatekeeper state; the gatekeeper lives on this VM by
  design, with GitHub as the off-VM witness.

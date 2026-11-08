# Diagnostics: privacy & retention

What the fleet's diagnostics carry, what they must never
carry, and how long they live (spec 038 section 10, SEC-02).

## What diagnostics carry

- Job ids, run ids, state transitions, lane and venue names.
- Payload exit codes and verdict sources.
- Counts: lines forwarded, batches throttled or quarantined,
  retries, ages.
- Content identity: SHA-256 hashes of records and artifacts.
- Failure classes from the mechanical severity map, never
  free-text guesses.

## What diagnostics must never carry

- **Credential material.** Any pipeline that can emit a report
  scans its input first; credential-shaped content refuses
  the report, naming the shape and line only.
- **Payload output beyond the bounded tail.** Job log tails
  are capped; the full output lives in the run's own record
  on the host that ran it, not in fleet-wide diagnostics.
- **Personal data from payloads.** Payloads process the
  owner's work; diagnostics describe the machinery, not the
  work.
- **Job data in published status.** The published snapshot is
  a fixed field allowlist (ids, states, counts, run linkage).
  Script paths, working directories, markers, log lines and
  declared needs never appear in it.

## Retention

- **Ledger:** append-only for the life of the deployment;
  it is the audit trail, compacted only into the verified
  history archive at migration.
- **Queue records:** terminal records are kept with the
  ledger; the history archive carries per-record SHA-256.
- **Reports digests:** daily digests are kept 90 days;
  individual reports are reproducible from the ledger, so
  the digest is a convenience, not the record.
- **Diagnostics bundles:** assembled on demand, kept 30
  days, and scanned before they leave the host that made
  them.

## The test

If a diagnostic string could embarrass the fleet when read
aloud in public — a secret, a payload's private output, a
person's data — it does not belong in the pipeline. The
scanner enforces the mechanical half; this page is the
other half.

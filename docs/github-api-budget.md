# GitHub API budget

The GitHub REST quota is one account-wide bucket (5,000
calls per hour) shared by every program and chat in the
fleet (spec 038 section 13). On 2026-10-27 the combined
polling of many legs exhausted it and stopped a harvest
mid-flight. These rules are the fix, and they bind the Rust
core exactly as they bound the Python gatekeeper.

## Rules

1. **Check headroom before bursts.** `gh api rate_limit` is
   free. If core remaining is under 1,000, stand down to
   minimum cadence and say so in the report.
2. **Poll at 3–5 minute intervals, minimum.** One watcher
   per run. No tight poll loops, and never leave
   `gh run watch` running at its default ~3 s interval
   unattended.
3. **Batch reads.** One status call per interval per run;
   prefer list endpoints over per-item calls.
4. **Dispatch only needed jobs.** The Actions runners are
   serial and shared; no speculative duplicates.
5. **A 403 is a hard stop** for that scope in that task.
   The fix is cadence, not retries. Report the stop, name
   the account, and describe the consequence the evidence
   supports (a temporary restriction is not a suspension).
6. **A billing-blocked zero-step run is NO VERDICT.** It is
   never reported as green; local checks substitute and the
   merge record says so.

## How the Rust core complies

- The daemon's dispatcher is idempotent by key: a retry
  after a crash re-derives the same key instead of creating
  a second run.
- Harvest reads the runs list once per sweep cycle, not per
  job.
- The extension (`gh syncyard`) performs no background
  polling at all; it answers the verb it was given.

# Cutover plan

How the fleet moves from the Python gatekeeper to the Rust
core (spec 038 section 2), and what happens if it goes
wrong.

## The shape

Cutover is a rehearsed, reversible move of ONE control
plane, in this order:

1. **Freeze.** Submissions pause (the PAUSED record), the
   sweep drains or holds everything in flight, and the
   queue is photographed: record count, ledger tail hash,
   per-state counts.
2. **Rehearse on the copy.** A byte-copy of the live
   gatekeeper directory is migrated with `migrate-history`,
   verified against its SHA-256 manifest, exported, and
   compared byte-exact. Any mismatch stops the cutover
   (ERR-06); there is no "close enough".
3. **Migrate for real.** The same machinery runs against
   the live directory (read-only) into the Rust state home.
4. **Shadow.** The daemon loads the migrated state and
   serves reads (status, snapshot, ledger) while the Python
   gatekeeper still owns dispatch. Divergences are ledgered
   and resolved in GitHub's favor on run state, always.
5. **Primary.** Dispatch flips to the daemon (the fencing
   epoch moves; the Python writer can no longer take the
   state lock). The extension's six engine verbs now answer
   from the Rust core.
6. **Watch.** The first campaign under the Rust primary is
   the iSMART migration, run clean under close monitoring
   before general traffic resumes.

## The fallback: the state-freeze window

If anything in steps 3–5 misbehaves, the fallback is the
**state-freeze window**: dispatch holds, the queue stays
frozen at the photographed state, and the Python gatekeeper
resumes from the same directory it never stopped owning.
The window lasts as long as diagnosis takes — hours are
acceptable; a corrupt queue is not. Nothing in the Rust
home is written back into the Python directory during a
fallback: the migration is one-way, and re-migration after
a fix starts from a fresh photograph.

## The end state

However the cutover goes, it ends with a single control plane
whose home is the Rust daemon's state directory on the
gatekeeper host (mini-server, `/var/lib/syncyard`), with the
off-VM witness store as its recovery anchor and the
CLI/extension as its only front doors. There is never a
steady state with two writers; shadow mode is a phase with
an end date, not an architecture.

## Named gates

- The live byte-copy rehearsal (step 2 against the real
  directory) is a cutover-day act, run by the operator with
  this plan open.
- The pause lift that ends the freeze is the owner's call.
- Production release of the Rust train waits for the
  acceptance gate, not for this plan's completion.

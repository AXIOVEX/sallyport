# Python decommissioning plan

The Rust core replaces the Python gatekeeper; it does not
strangle it (spec 038 section 13). This plan names what
retires, in what order, and what must be true first.

## End state

One control plane: the Rust daemon owns queue state, the
Rust CLI and the `gh syncyard` extension are the front
doors, and the Python gatekeeper is removed from every
host. The door repository (workflows) stays — it is GitHub
Actions YAML, not Python machinery.

## Preconditions (all must hold)

1. The acceptance gate is green: traceability matrix green
   (or carrying only its recorded field-gated list with
   field proofs scheduled), coverage bars met per crate.
2. The cutover rehearsal has run against a byte-copy of the
   live gatekeeper directory with a byte-exact round trip.
3. The daemon has run the fleet in shadow, then as primary,
   through one full iSMART campaign under watch.
4. Backups verified: a restore drill from the fleet archive
   passes against the Rust state home.

## Retirement order

1. **Sweep and snapshot crons** move to the daemon's verbs
   (`snapshot` first, then the sweep's dispatch leg).
2. **The mirror** reads from the daemon only; the Python
   fleet_status publisher is retired after one clean week.
3. **`gk.py` stops accepting submissions**; the CLI's socket
   path is the only submit route. The file fallback remains
   available read-only for one release train.
4. **Host services** (Task Scheduler / systemd units) are
   repointed at the Rust binaries; the Python units are
   disabled, not deleted, for one train.
5. **Removal:** after one full train with no fallback use,
   the Python gatekeeper directory is archived into the
   history store (with its manifest) and deleted from live
   hosts.

## What never retires

- The ledger. It migrates; it is never rewritten.
- The door workflows (`run.yml`, `wsl.yml`, `grab.yml`) —
  versioned and owned per the door-repo record.
- The fleet's naming and the queue JSON contract: the Rust
  contract crate round-trips the Python shapes, so history
  stays readable forever.

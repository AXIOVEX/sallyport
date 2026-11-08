# Architecture

This page is the readable form of spec 038 (Rust core
architecture), with its inherited companions: spec 032
(RAM-authoritative state), spec 030 (dispatch occupancy),
spec 033 (Docker-native execution), and spec 036 (the binding
test gate, covered in [Acceptance gates](acceptance.md)).

## One-move cutover

The Python gatekeeper is replaced in **one move**. There is no
parallel soak and no gradual flag-flip. The word "rollback" is
removed from the cutover plan unless a rehearsed reverse
migration exists: before cutover day, a rehearsal on a
byte-copy of live state must prove the full round trip (Python
state to Rust state and back) with zero record loss and zero
double dispatch, and the drill re-runs on every release train.
If the round trip cannot be proven, the plan instead carries a
state-freeze window in which in-flight jobs complete before
the switch, named as such. An unrehearsed "keep the old binary
on hand" is a hypothesis, not a capability, and is never
described as rollback.

## RAM-authoritative daemon

Live records live in the daemon's memory; disk is a
write-behind replica (60-second flush and on transitions,
every write verified by read-back). One writer only: a flock
shared by the daemon and the CLI's direct-file fallback, with
dead-owner takeover — a lock is taken over only when its owner
pid is provably dead, never while it lives.

Ordering never trusts wall clock. Records carry a monotonic
sequence, and the off-VM record is fenced by a monotonic epoch
guarded by compare-and-swap on its content hash: a fenced-out
former writer can no longer publish state.

Dispatch is a two-phase protocol: intent ledgered, an off-VM
witness written and confirmed by read-back, an idempotent
trigger (the key is a hash of job, sequence, script hash, and
epoch), then capture and commit. A crash at any step recovers
to exactly one outcome — adopt the dispatch by its key, or
return the job to submitted. Zero double, zero lost
(ERR-01).

## Lanes bind to venues

A lane binds to a **venue**, not a runner label:

- `native` — the host OS itself; hardware jobs live here
  permanently.
- `docker` — the container venue (below).
- `windows-vm` — one Windows guest per host (below).

A host offers the venues its capability probe proves.
Occupancy is computed settle-first (a finished run never holds
a lane), the dispatch scan is work-conserving (a blocked head
job never stops later jobs), caps bind exactly, and starvation
past `dispatch.starvation_after_s` (default 900) is ledgered
once per episode as `dispatch_starved` / `dispatch_unstarved`.

Declared job needs are enforced as hard limits (cgroup or the
venue equivalent): an under-declared job kills itself, not its
host or its co-tenants. Measured-versus-declared use is
recorded per program, and repeat under-declaration widens that
program's enforced envelopes automatically.

## Docker posture (spec 033)

- Four shared images only — `linux-dev`, `data-py`, `zephyr`,
  `node-web` — pinned by digest, publishing multi-arch
  manifests (x86_64 and aarch64 ship with every release).
- Warm containers per (host, image), with persistent named
  volumes.
- Containers run non-root with a read-only rootfs and all
  capabilities dropped. Secrets are injected per job via tmpfs
  and removed at job end. The Docker socket is **never**
  mounted into a job container.
- Egress is deny-by-default with per-image allowlists.
- Job start asserts a clean environment, including secret
  absence everywhere a prior job could have written: a planted
  leftover secret makes the next job refuse to start.
- No project job may require WSL2; WSL exceptions are named,
  owned, and carry exit conditions.

## Windows VM venue

One Windows guest per physical host under KVM/libvirt on a
Linux-native host, with ACPI MSDM/SLIC and SMBIOS passthrough
so the host's own OEM key activates the guest on that machine.
Guests never move hosts. An activation proof runs before the
guest's first job, and activation is thereafter a continuously
probed capability on the guest's heartbeat: deactivation takes
the venue offline with a named constraint. Machine type and
SMBIOS presentation are pinned in version control.

## Ghost-record reconciliation

A record whose run is terminal on GitHub but whose queue
record still reads dispatched is a **ghost**. Reconciliation
is a built-in daemon function, not a hand edit: census from
GitHub-side evidence, complete each ghost truthfully from its
run's log and conclusion, ledger an `operator_record_repair`
per record.

It is **hold-first**: before any ghost completes, a pause
record or per-job holds cover every waiting non-maintenance
job — completing ghosts frees lanes, and freed lanes must
never dispatch held work with no human decision. Holds are
released only by a named operator act, per job or per program.

## Recovery rules (ERR summary)

- ERR-01: fault injection at every dispatch step; zero double,
  zero lost.
- ERR-02: VM loss loses no dispatched job, verdict, or
  acknowledged submit; unrecorded acknowledged submits are
  reported by name at reconciliation.
- ERR-03: host power loss leaves jobs in named recoverable
  states, reconciled from GitHub evidence before new lane work.
- ERR-04: every publish destination is verified by read-back;
  success only if all verify.
- ERR-05: dead-owner lock takeover only (above).
- ERR-06: lossless migration round trip with per-record
  manifest; any failure blocks cutover.
- ERR-07: GitHub wins reconciliation disagreements; payload
  verdicts are never overwritten.
- ERR-08: a detected write wave switches writers to fail-fast
  with a named ledger constraint; RAM state is never lost and
  the backlog lands on the first healthy flush.
- ERR-09: every component self-starts from its own state after
  any restart and announces recovered state and gaps by name.

## Control plane and budget

The control plane has exactly one home at any time; its
end-state is the always-on server (mini-server in the founding
fleet), with the fencing story above. Two control-plane homes
is never a steady state.

The GitHub REST quota is one account-wide bucket (5,000 calls
per hour) shared by every program. The system's budget is a
requirement, not a habit: per-component shares, 3-to-5-minute
minimum polling, one watcher per run, and stand-down behavior
on HTTP 403.

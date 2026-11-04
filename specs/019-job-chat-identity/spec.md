# Feature Specification: Job–Chat Identity and Routing Integrity

**Feature Branch**: `spec/019-job-chat-identity`

**Created**: 2026-11-04

**Status**: **Specified** (this change carries the spec only so
far). Implementation follows under this spec; nothing here
touches the running fleet until the fleet-adoption task (T008),
which rides the next scheduled fleet deploy.

**Direction (owner, 2026-11-04 ~09:08 ET)**: "make sure above
all else, jobs are tracked so that they never end up in the
wrong chat. we must make sure this identity is robust and
unfoilable and if any discrepancies occur, a red team attack
is forced."

## The gap

A gatekeeper job's routing identity today is **submitter-asserted
free text, twice over**: the `--program` string and the
`--notify-chat` id are whatever the submitting command line
said. Nothing validates either against a canonical source,
nothing binds them to each other, and the sweep's outbox
routes completions by the queue record's stored chat id alone.
A typo, a stale id copied from another program's job, or a
corrupted record routes a job's results to the wrong chat
**silently** — the system has no representation of "wrong"
to catch.

This is not hypothetical. The 2026-11-04 census of all 1,030+
queue records (taken the morning this spec was ordered) found:

- `syncyard` jobs registered to the RealLoom chat (x2), against
  38 to the Desktop Access chat — one of them would have
  delivered Syncyard's own results into another program's chat.
- A `desktop-access` job registered to the Glossa chat (x1).
- `gpu-unlock` jobs registered to the main chat (x8) against
  140 to the program's own chat.
- `cleanup-coord` split across two chats (x2 / x1), a program
  with no single owner in the record.
- One queue record whose program field is a corrupted string
  matching no program at all.
- Whole programs with no registration at all (arcturus family,
  51 jobs), where routing exists only as operator habit.

The fabricated-delivery findings of 2026-11-04 sharpen the
point: a notification's *content* is already treated as a
claim and verified against the queue record before it is
forwarded. Its *destination* deserves the same discipline.
Today the destination is the one part of a completion that is
never checked against an independent source.

## The identity model

Three records, one truth, agreement required:

1. **The registry** (gatekeeper config, versioned with the
   code) maps each program to exactly one owning chat. It is
   the single routing truth. Changing it is a reviewed config
   change, never a submit-time side effect.
2. **The ledger identity event**: at submit, the gatekeeper
   appends an event carrying the job's key, program, the
   registry-derived chat, and the script's SHA-256. The ledger
   is append-only; the event cannot be rewritten afterwards.
3. **The queue record** stores the same identity at submit.
   At completion, the outbox derives the destination from the
   registry and requires **three-way agreement** — registry,
   ledger event, queue record — before any notification for
   the job exists.

A job's identity is therefore not a field someone typed; it
is a fact three records must agree on, anchored in a reviewed
registry. Disagreement is not an error to paper over: it is
the signal the owner ordered a red team for.

## Requirements

- **FR-001 — Registry.** Gatekeeper config gains a routing
  section mapping program → chat id. Loading a config whose
  routing section names a program twice, or a chat id that is
  not a plausible chat id, fails closed.
  *Falsifier: a config with a duplicated program loads and runs.*
- **FR-002 — Submit derives routing.** `gk submit` takes the
  program; the notify chat is derived from the registry. An
  explicit `--notify-chat` that disagrees with the registry is
  refused (exit 2, named error). An unregistered program is
  refused the same way — registration is a deliberate act
  (FR-007), not something a submit line invents.
  *Falsifier: a submission naming a non-registry chat, or an
  unregistered program, is accepted.*
- **FR-003 — Ledger identity event.** Every accepted submit
  appends a ledger event carrying key, program, derived chat,
  and script SHA-256, written before dispatch is possible.
  *Falsifier: a dispatched job with no identity event, or an
  identity event whose chat differs from what the outbox used.*
- **FR-004 — Three-way check at notification.** The sweep's
  outbox emits a job's notification only when registry,
  ledger identity event, and queue record agree on
  (program, chat). The check runs per job, per sweep, from
  the records — not from cached state.
  *Falsifier: a notification emitted where any pair disagrees.*
- **FR-005 — Discrepancy quarantine.** Any disagreement —
  including an unknown or corrupted program string — quarantines
  that job's notification: it is never sent and never rerouted
  by inference. The sweep writes a `routing_discrepancy` ledger
  event carrying all three values as found, and surfaces the
  quarantine as material in its summary. The job's completion
  record itself is untouched; only routing is held.
  *Falsifier: a quarantined job's notification reaches any chat,
  or a discrepancy passes with no ledger event.*
- **FR-006 — Forced red team.** A quarantined notification
  stays quarantined until a red team pass on the junction
  (registry history, the identity event, the record's
  provenance) is recorded as a ledger event: `routing_released`
  naming the correct chat **from evidence**, or `routing_voided`
  closing the notification as undeliverable. No other path
  releases a quarantine; in particular, editing the queue
  record to match is itself a discrepancy class (the ledger
  event already pinned the original).
  *Falsifier: a quarantine released by record edit, operator
  flag, or passage of time.*
- **FR-007 — Registration is deliberate.** Adding or changing
  a program → chat mapping happens only as a config change in
  the repo (reviewed, attributed, reversible). The gatekeeper
  never learns a mapping from a submission.
  *Falsifier: a mapping that exists at runtime but not in the
  reviewed config.*
- **FR-008 — Migration.** The registry is seeded from the
  2026-11-04 census's dominant mappings (26 programs; the seed
  table ships with the implementation PR and is reviewable
  line by line). The historical anomalies in "The gap" are
  documented in the seed PR, not silently normalized.
  Programs with no registration (arcturus family, pwless-proof,
  syncyard-tray, smoke-test) receive **no routing**: their
  completions report only in the operator chat, exactly as
  today. `cleanup-coord` is retired as a program name; its
  work registers under each owning program instead.
  *Falsifier: a seeded mapping that contradicts the census's
  dominant registration without a named reason in the PR.*
- **FR-009 — Fleet adoption.** The live gatekeeper adopts the
  registry at the next fleet deploy. Until then the operator
  applies FR-004/FR-005 procedurally: every forward is checked
  against a census-derived registry file kept with the fleet
  gatekeeper, and a mismatch is held and reported, never
  forwarded. (In force from 2026-11-04, the day of the census.)
  *Falsifier: a fleet forward made against a mismatching
  registry entry during the interim.*

## Non-goals

- No change to dispatch, lanes, harvesting, or verdicts: this
  spec governs **where results go**, never what they say.
- No cryptographic signing of chat ids. The observed failure
  classes are drift, error, corruption, and fabricated
  presentation; the registry + ledger + record three-way check
  catches all four. Signing is named as a possible later
  hardening and is not required to close the observed class.
- No retroactive judgement of historical jobs: the census
  anomalies are evidence for the design, not incidents to
  prosecute. Jobs already completed stay completed.

## Acceptance

A `test_spec019` battery (wired into CI beside the other spec
batteries) proves, from the records: submit refusal for a
non-registry chat and for an unregistered program; the identity
event's presence and contents; a clean three-way agreement
emitting exactly today's outbox shape; each discrepancy class
(registry/record, ledger/record, unknown program, corrupted
program string) quarantining with a ledger event and no
notification; release only via a recorded red-team event.
`test_config` mirrors the new config section exactly, per the
standing golden-fixture rule.

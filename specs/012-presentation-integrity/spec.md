# Feature Specification: Presentation Integrity

**Feature Branch**: `spec/012-presentation-integrity`

**Created**: 2026-11-01

**Status**: **Specified — draft for owner review. No implementation
in this change.**

**Direction (owner, 2026-11-01 ~06:47 ET)**: asked what can fix
the presentation layer that keeps inventing gatekeeper statuses,
the owner ordered both halves of the answer: "draft the Syncyard
spec and file the platform report." The platform report covers
the layer Syncyard does not own (interim background-result
delivery in the agent runtime). This spec covers everything
Syncyard *does* own: how statuses are rendered, forwarded,
validated, and reconciled on this side of that layer.

**The limit, stated plainly up front**: this spec cannot make
an upstream summarizer truthful. It makes Syncyard's own
surfaces incapable of *originating* a status value, makes any
invented value *fail a mechanical check* before it can be
forwarded as a Syncyard result, and makes the stored record
*provable* rather than merely consistent. Any document produced
under this spec must keep that scope: prevention inside
Syncyard's channel, detection at its boundary, evidence outside
it.

## The evidence this spec answers

Between 2026-10-29 and 2026-11-01 the fleet recorded 14
presented-status fabrication sightings across four programs
(Spec-Kit, Glossa, Chronoc, and this project's own sweep
pipeline). The adjudicated findings, each verified first-hand
against stored records and, for run identifiers, against the
GitHub Actions API:

1. **The gatekeeper's stored records — `ledger.jsonl` and
   `queue/*.json` — contained none of the invented values in
   any checked incident.** Every invented run identifier that
   was preserved (7 of them) returns 404 from the GitHub
   Actions API in the door repo; genuine runs checked as
   controls return 200. Evidence of record:
   `github-id-verification-2026-11-01.txt` in the fleet
   gatekeeper folder. The founding incident (2026-10-29)
   preserved no verbatim presented output, so its invented
   identifiers can never be checked retroactively; that gap
   is permanent and is recorded here so it is never papered
   over.
2. **The corruption locus is interim presentation.** Where
   raw evidence was preserved (Chronoc, 2026-11-01), the
   fabrications appeared in interim "background result"
   deliveries of still-running processes; the same processes'
   terminal outputs matched the ledger in every case checked.
3. **The deterministic surfaces cannot support a prose
   verdict.** `gk sweep` prints one counts line; `gk outbox`
   prints fixed-format groups; `gk status` contains no
   verdict text at all. Verdict text exists in exactly one
   local place — the queue records (`markers`, `log_tail`).
   Three model-in-the-loop transformations sit between a job
   completing and a program chat reading about it: the sweep
   worker's report, the coordinator's forwards, and each
   program chat's own watches.
4. **The inventions are synthesizable from context.** Job
   scripts contain their own verdict lines as literal text,
   so an interim summary can render the *expected* output.
   In 4 incidents the invented content later became
   substantially true — same verdicts, different run
   identifiers (pre-echoes).
5. **Invention is not limited to identifiers.** One sighting
   attached an invented completion and marker count (11) to a
   *real* run that later completed with 351 markers.
   Identifier checking alone is therefore insufficient;
   verdict content must be bound to records too.
6. **The records have their own honest weaknesses**, which
   this spec also addresses: they can *lag* the truth
   (harvest-known/save-pending during write-denial waves),
   they are not exactly-once (duplicate completion events
   exist), they carry no hash chain or external anchor, and
   run-id recovery (`capture_run_id`) is a timing correlation
   that could in principle attach the wrong *real* run to a
   job — a record-level falsehood mechanism that internal
   consistency checks cannot detect. No incident is attributed
   to it; it is hardened here because it exists.

## Requirements

- **FR-001 — Deterministic status rendering.** A new
  `gk report <job-id|key>` command renders a fixed-format
  evidence block from the queue record and ledger lines only:
  job id, key, program, state, run id, conclusion, and the
  record's verdict lines quoted verbatim, plus the SHA-256 of
  the queue record file. This block is the only sanctioned
  text for forwarding a job's status; coordinators forward it
  verbatim rather than paraphrasing it.
- **FR-002 — Outbound validation.** A `gk verify-text`
  command takes a draft message and a set of cited job
  records and fails (exit non-zero, listing each offender) if
  the draft contains any run identifier, verdict line, count,
  or timestamp that does not appear in the cited records.
  Intended to gate coordinator forwards and program-chat
  status posts alike.
- **FR-003 — Terminal-only admissibility (doctrine).**
  SKILL.md, the skills pack, and the gatekeeper README state
  as doctrine: interim deliveries of in-flight work are
  non-evidence; a status is admissible only from a queue
  record carrying a `log_harvested` ledger event, read from
  the stored record. Interim material may be reported only as
  "still running" with elapsed time.
- **FR-004 — Sweep reconciliation against GitHub.** Each
  sweep's existing batched run-list pass also diffs GitHub
  runs against ledger and queue and ledgers an
  `integrity_alert` event on: a local run id with no GitHub
  object (subject to an in-flight grace window), a GitHub run
  with no local mapping, a completed record without
  `log_harvested`, duplicate completion events for one job,
  or queue/ledger state disagreement beyond a bounded lag.
  No new pollers; the check rides the calls the sweep already
  makes, inside the fleet API discipline.
- **FR-005 — GitHub triangulation for presented
  completions.** The reconciliation in FR-004 is the standing
  form of the retroactive check performed by hand on
  2026-11-01: a completion whose run does not resolve in the
  door repo's Actions API is never presentable as a Syncyard
  result, and the disagreement itself becomes an incident
  record.
- **FR-006 — Hash-chained, externally anchored ledger.**
  Each new ledger event embeds the previous event's hash;
  queue saves record the ledger head they were written
  against; the sweep periodically anchors (head hash + line
  count) to an external append-only surface (a commit in this
  repository). Pre-chain history is not rewritten; the chain
  starts at a recorded genesis event naming the pre-chain
  head hash and line count.
- **FR-007 — Run-id correlation hardening.** `capture_run_id`
  may attach a recovered run id only when workflow, creation
  window, and (where the API exposes them) dispatch inputs
  all agree; on any ambiguity it records `run_id = null` plus
  an `integrity_alert` ledger event instead of guessing.
- **FR-008 — Incident evidence bundle.** A fabrication
  incident is recorded as a four-artifact bundle with hashes:
  the presented bytes, the process's terminal output, a
  synchronous ledger slice + queue-record read from the
  incident moment, and GitHub API reads for the claimed and
  true run ids. `gk incident` scaffolds the bundle so the
  next sighting is preserved to this standard by default —
  the 2026-10-29 gap (no verbatim output preserved) must not
  recur.
- **FR-009 — Skill and documentation currency.** SKILL.md,
  the skills pack editions, and the CHANGELOG are updated in
  the same changes that land FR-001 through FR-008, citing
  this spec, per the standing currency rule.

## Non-requirements (explicit)

- This spec does **not** attempt to fix, patch, or route
  around the agent runtime's interim delivery layer; that is
  the platform report's subject, filed separately with the
  owner's approval on 2026-11-01.
- No new watcher processes, pollers, or polling cadence
  changes. Fleet GitHub API discipline (one watcher, 3–5
  minute minimum intervals, hard stop on 403) is preserved.
- No weakening of the fail-closed persistence rules
  (FR-012/FR-013 of spec 001): a component that cannot
  persist must not dispatch, and reconciliation must treat
  record lag as a known state, never silently as error or as
  truth.

## Acceptance shape

Each phase lands behind the golden battery: fixtures include
a fabricated draft that `verify-text` must reject, a real-run/
invented-completion pair that reconciliation must flag, an
ambiguous run-id correlation that must record null, and a
hash-chain verification over a fixture ledger. A phase is
done when its battery is green in CI and the fleet deploy
follows the standing snapshot + reconciliation procedure —
the CI-green rule applies: red at day's end means not done.

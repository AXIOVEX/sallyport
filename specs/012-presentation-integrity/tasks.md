# Tasks: Spec 012 — Presentation Integrity

Sequenced cheapest-and-strongest first: the doctrine and the
deterministic renderer remove most of the risk before any
record-format change is contemplated. The fleet's existing
disciplines apply throughout: pause-first for changes to live
components, queue snapshot + reconciliation around every
fleet deploy (spec 009 FR-006), SKILL.md / skills-pack
updates in the same change as any behavior (FR-009), and the
CI-green rule — a phase with red CI is not done.

## Phase 1 — Doctrine and the evidence block (FR-003, FR-001)
- [ ] T001 Doctrine text: SKILL.md + skills pack + gatekeeper
      README state terminal-only admissibility and the
      forward-the-block rule (FR-003), citing this spec.
- [ ] T002 `gk report <job-id|key>`: fixed-format evidence
      block rendered from queue record + ledger lines only,
      verdict lines verbatim, queue-record SHA-256 included.
      Golden battery pins the block byte-for-byte on fixtures,
      including a record whose verdict lives only in log_tail
      (hyphenated key) and a bare-value verdict line.

## Phase 2 — Outbound validation (FR-002)
- [ ] T003 `gk verify-text`: extract run identifiers, verdict
      lines, counts, and timestamps from a draft; fail listing
      every value absent from the cited records. Battery:
      a fabricated draft (invented run id) rejected; a
      real-run/invented-completion draft rejected; a verbatim
      `gk report` block accepted.
- [ ] T004 Coordinator procedure update: forwards in the
      Desktop Access pipeline run T003's check before send;
      procedure written into the gatekeeper README.

## Phase 3 — Reconciliation and triangulation (FR-004, FR-005)
- [ ] T005 Sweep reconciliation pass riding the existing
      batched run list: the five alert classes of FR-004,
      ledgered as `integrity_alert` with the disagreeing
      values. Battery pins alert/no-alert on fixtures,
      including the bounded-lag allowance so write-wave lag
      does not cry wolf.
- [ ] T006 Completion triangulation: a presented completion
      whose run does not resolve in the door repo is blocked
      at report time (FR-001 block refuses to render it as
      completed) and alerted per T005.

## Phase 4 — Record provability (FR-006, FR-007)
- [ ] T007 Ledger hash chain: genesis event naming the
      pre-chain head; each new event embeds the previous
      hash; queue saves record their ledger head. Migration
      is append-only; pre-chain history untouched.
- [ ] T008 External anchor: sweep commits (head hash + line
      count) to this repository on a configurable cadence;
      anchor failures queue and report, never block dispatch.
- [ ] T009 `capture_run_id` hardening (FR-007): workflow +
      window + inputs must all agree; ambiguity records
      `run_id = null` + `integrity_alert`. Battery pins the
      ambiguous-burst case.

## Phase 5 — Incident bundle and close-out (FR-008, FR-009)
- [ ] T010 `gk incident` scaffolding: the four-artifact
      bundle of FR-008 with hashes, filed under the
      gatekeeper's incident folder in one command.
- [ ] T011 Retroactive bundle for the 2026-11-01 sightings
      from the preserved evidence (Chronoc incident file +
      `github-id-verification-2026-11-01.txt`), marked as
      retroactive where an artifact (presented bytes) was
      preserved by another program rather than captured live.
- [ ] T012 Docs/skill currency pass + CHANGELOG; spec marked
      implemented; fleet deploy via snapshot + reconciliation,
      with the deploy record naming the pre/post gatekeeper
      hashes as in prior deploys.

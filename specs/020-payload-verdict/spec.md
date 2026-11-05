# Feature Specification: Payload Verdict as a First-Class Record

**Feature Branch**: `spec/020-payload-verdict`

**Created**: 2026-11-04

**Status**: **Specified** (this change carries the spec only so
far). Implementation follows under this spec; fleet adoption
rides the next scheduled fleet deploy (T007), with the interim
operator rule (FR-008) in force from today.

**Direction (owner, 2026-11-04, issued in the RCPH chat)**:
a gatekeeper job completing with GitHub conclusion "success"
while its payload failed is "unacceptable and must be fixed in
places for all projects here in muse where this could impact."

## The gap

A gatekeeper job has two different outcomes that the record
today fuses into one:

- the **transport conclusion**: whether the Actions run and
  the wrapper completed (what `run_conclusion` records);
- the **payload verdict**: whether the work the job carried
  actually succeeded (the build compiled, the test passed, the
  probe measured).

Field case, 2026-11-04: job `spec053-f0-build-2026-11-04`
(run 41850176626) completed with `run_conclusion=success`
while its build payload exited **127** (command not found;
the build never ran). The payload's own verdict existed only
in a venue-side file (`BUILD_EXIT_CODE` beside the build log).
The queue record had **no field for it**. Any consumer reading
state and conclusion alone, which is what status surfaces and
completion forwards lead with, records failed work as done.
The operator's forward in that instance separated the two
facts by hand; the schema must not depend on the operator
remembering to.

**Wrapper success is not job success.** The fleet-wide
behavioral rule of the same name was added to AGENTS.md on
2026-11-04; this spec is its code form.

## Definitions

- **Payload exit code**: the exit code of the job's payload
  (the script's own work), as distinct from the wrapper's.
- **Payload verdict**: derived at harvest, one of PASS, FAIL,
  or UNVERIFIED, from the payload exit code and any
  submit-time contract (FR-004). UNVERIFIED is a verdict, not
  an omission: it means the record cannot establish that the
  payload succeeded.

## Requirements

- **FR-001 — Wrapper contract.** The door workflows
  (`run.yml`, `wsl.yml`, `grab.yml`) run the payload, capture
  its exit code, and emit the reserved terminal marker
  `GK_PAYLOAD_EXIT <code>` as the final line of the job log.
  The wrapper's own exit stays a pure transport signal (the
  wrapper exits non-zero only when the transport itself
  failed), so the Actions conclusion remains readable as
  transport truth and nothing else.
  *Falsifier: a payload exiting 127 produces a log whose final
  marker is absent, or a wrapper that reports the payload's
  failure as its own.*
- **FR-002 — First-class fields.** At harvest, the gatekeeper
  parses the terminal marker into the queue record as
  `payload_exit_code` (integer, or null when absent) and
  derives `payload_verdict` (PASS / FAIL / UNVERIFIED) onto
  the same record. No per-job convention is involved: a job
  cannot opt out of carrying the fields.
  *Falsifier: a completed post-deploy job whose record lacks
  either field.*
- **FR-003 — Presentation leads with the payload.** `gk
  status` and the sweep's outbox completion lines render the
  payload verdict with the completion: a non-zero exit renders
  FAILED with its code; an absent marker renders UNVERIFIED;
  neither is ever presented as a bare success. The transport
  conclusion remains available, labelled as transport.
  *Falsifier: any status or forward surface where a FAIL or
  UNVERIFIED payload reads as plain success.*
- **FR-004 — Submit-time contract.** `gk submit` accepts
  `--expect-exit N` (default 0) and `--expect-marker REGEX`
  (optional, matched against the harvested log). The payload
  verdict is PASS only when the exit code matches the
  expectation and, when declared, the marker is found. An
  unmet contract is FAIL, with the reason recorded on the
  queue record.
  *Falsifier: a job whose declared contract is unmet and whose
  verdict reads PASS.*
- **FR-005 — Grab jobs.** For `grab.yml`, the payload is the
  grab script: its exit code feeds the same fields, and the
  declared-output expectation (the artifact) stays as today.
  Same presentation rules.
  *Falsifier: a grab whose script failed and whose record
  reads PASS.*
- **FR-006 — History reads honestly.** Records written before
  this spec carry no payload fields; every surface renders
  them UNVERIFIED on the payload axis, never retroactively
  PASS.
  *Falsifier: a pre-spec record presented as payload-PASS.*
- **FR-007 — Acceptance.** A `test_spec020` battery proves,
  against fixture logs and the harvest path: the deliberate
  exit-127 payload surfaces as FAILED in the queue record and
  in the outbox line while its Actions conclusion is success
  (the owner's acceptance case); exit 0 with no contract reads
  PASS; absent marker reads UNVERIFIED; contract cases
  (exit mismatch, missing marker) read FAIL with reasons;
  pre-spec records read UNVERIFIED.
  *Falsifier: any acceptance case failing in CI.*
- **FR-008 — Interim rule (in force 2026-11-04).** Until the
  fleet deploy carries this build, the operator's completion
  forwards lead with the payload verdict read from the record's
  own markers, and label the job UNVERIFIED on the payload axis
  when the record carries none. This is procedure over the old
  schema, stated here so the gap is owned, not implicit.
  *Falsifier: a fleet forward in the interim that leads with
  the transport conclusion alone.*

## Non-goals

- No change to dispatch, lanes, or verdict content: this spec
  governs how a job's outcome is **recorded and presented**,
  never what the payload does.
- The wrapper does not start failing Actions runs for payload
  failures. Red runs stay meaningful as transport failures;
  the payload verdict gets its own field and its own headline
  instead of borrowing the transport's.
- No retroactive repair of historical records (FR-006 covers
  their honest presentation).

## Acceptance

The owner's case, verbatim as the bar: a deliberate exit-127
payload must surface as FAILED in both the queue record and
the completion forward while its Actions run concludes
success. The battery (FR-007) encodes it; CI green or the
work is not done.

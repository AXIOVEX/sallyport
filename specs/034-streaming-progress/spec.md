# Feature Specification: Streaming Progress (Rust Core)

**Feature Branch**: `feat/034-streaming-progress`
**Created**: 2026-11-07
**Status**: **RATIFIED 2026-11-07 as a block with specs 034-039 (owner decision, Desktop Access chat). Implementation may proceed under the program sequence.**
**Direction**: Latency program 2026-11-06 (clean population n=469, median submit-to-verdict 421s, p90 5,691s; 51% of jobs waited >10 min for dispatch). Operators and program chats cannot tell a slow job from a wedged job without progress evidence. This spec defines the progress contract for the Rust core.
**Companions**: Spec 020 (payload verdict), spec 030 (dispatch starvation), spec 032 (RAM-authoritative state, inherited by spec 038), spec 036 (test discipline — this spec is authored under it).

## The gap

Today a job is silent between dispatch and harvest. The sweep learns the verdict only at completion; interim background deliveries are non-evidence under the fabricated-delivery protocol, yet they are the only interim signal that exists. The result: starved, wedged, and healthy long jobs are indistinguishable for hours, and progress claims get invented by presentation layers to fill the silence (spec 012, findings 4-5).

## The design

### 1. The progress file

A payload MAY write progress to the path named by the environment variable `SYNCYARD_PROGRESS_FILE`, set by the runner step for every job. The file is line-oriented, append-only, UTF-8. The runner never invents lines; a job that writes nothing produces no progress, and "no progress" is a displayable state, never an error by itself.

### 2. Line grammar

Each line is one of:

- `STARTED` — the payload has begun executing (distinct from wrapper start).
- `MARKER <text>` — a free-text milestone, verbatim from the payload.
- `HEARTBEAT` — the payload is alive at write time.
- `PROGRESS <k>/<n> <label>` — k of n units complete, 0 <= k <= n, n >= 1.
- `COMPLETED <verdict-word>` — the payload's own final line; advisory only (see FR-034-03).

Unknown lines are preserved in the harvest and ignored by consumers. Malformed `PROGRESS` lines are ignored and counted.

### 3. Transport

The backchannel agent (or the runner's log harvest, where no agent exists) copies the progress file's tail to the gatekeeper at most every ~60 s per running job. The gatekeeper stores the latest progress snapshot on the job's RAM record (spec 038) and exposes it through `gk status`, the daemon socket, and the MCP surface. Transport failure degrades to "progress unknown", never to a fabricated value.

### 4. Markers are never verdicts

Progress lines, including `COMPLETED`, are **not** verdicts. The verdict remains the payload exit code plus the harvested terminal record (spec 020; fleet rule: wrapper success is not job success). No surface may render a progress line as a pass/fail. A `COMPLETED` line with no terminal record is displayed as "payload reports completed, verdict pending harvest".

### 5. Log-harvest fidelity

Harvested marker sets are attributed per run. Lines originating from another run's log, another job's record, or checkout noise (observed 2026-11-06/07: stray `HEAD is now at cf35db2 staged: stage frun driver + current-job (frun)` lines inside mirror records) MUST NOT be attributed to a payload. Attribution is by run id plus log-source identity, verified at harvest (red-team-adjacent field defect; see spec 038 FR-038-31).

## Requirements

| FR | Requirement | Named test case(s) | Acceptance mode |
|---|---|---|---|
| FR-034-01 | Every job MUST be launched with `SYNCYARD_PROGRESS_FILE` set to a per-job path, and the runner MUST create the parent directory before payload start. | T034-01 progress-env-set-on-all-venues | test |
| FR-034-02 | The progress grammar MUST be exactly STARTED / MARKER / HEARTBEAT / PROGRESS k/n / COMPLETED as defined above; consumers MUST ignore unknown lines and MUST count malformed PROGRESS lines without failing the job. | T034-02 grammar-accept-and-ignore; T034-03 malformed-progress-counted | test |
| FR-034-03 | No progress line, including COMPLETED, MUST ever be rendered, forwarded, or ledgered as a payload verdict; the verdict is the payload exit code from the terminal record only. | T034-04 completed-line-is-not-a-verdict; T034-05 progress-never-ledgers-verdict | test |
| FR-034-04 | The gatekeeper MUST refresh each running job's progress snapshot at most every ~60 s and MUST display snapshot age; a snapshot older than 180 s MUST render as "progress stale", never as current. | T034-06 snapshot-cadence-and-staleness | test |
| FR-034-05 | A job that writes no progress MUST display as "no progress reported" with elapsed time; no component MAY synthesize a marker, percentage, or heartbeat on its behalf. | T034-07 silent-job-stays-silent | test |
| FR-034-06 | PROGRESS k/n MUST be monotonic within a run as displayed: a later snapshot with smaller k MUST be shown with a regression note, never silently clamped. | T034-08 progress-regression-surfaced | test |
| FR-034-07 | Progress transport failure MUST degrade to "progress unknown" and MUST NOT affect dispatch, harvest, or the verdict path. | T034-09 transport-failure-isolated | test |
| FR-034-08 | Harvest MUST attribute marker/progress lines to a job only when run id and log-source identity match that job's record; foreign lines MUST be quarantined and counted, never displayed as the payload's (defect: stray-frun-marker-lines-2026-11-06). | T034-10 foreign-marker-quarantine | test |
| FR-034-09 | The payload contract (markers, exit codes, progress file, verdict rule) MUST have a written contract document and a conformance kit runnable from PowerShell, bash, and Python callers (red-team M3). | T034-11 conformance-kit-three-languages | test |
| FR-034-10 | Door workflows MUST be pinned by commit sha at dispatch, with the sha recorded in the job record (red-team M3 / latency F8). | T034-12 door-workflow-pinned-sha | test |
| FR-034-11 | The CLI/daemon contract MUST be versioned; CI MUST fail when the CLI contract and the daemon contract diverge (red-team M3). | T034-13 cli-daemon-contract-compat | gate |
| FR-034-12 | Streaming progress MUST be visible on the tray and via the MCP status surface with identical values, both read from the gatekeeper record, never from independent polls. | T034-14 tray-mcp-progress-parity | test |

## Falsifiers

- A job whose payload writes `COMPLETED` and then exits non-zero is displayed anywhere as passed: this spec has failed.
- A mirror or sweep record displays a marker line that its own run's log does not contain: this spec has failed.
- A silent 2-hour job displays any progress value other than "no progress reported": this spec has failed.

## Tasks

- T001: This spec.
- T002: Progress grammar + contract document + conformance kit (after ratification).
- T003: Runner wiring (env var, per-job path) on all venues.
- T004: Gatekeeper snapshot store, ~60 s refresh, status/MCP/tray surfaces.
- T005: Harvest attribution + foreign-line quarantine.
- T006: Battery per the FR table; traceability entries per spec 036.

## Out of scope

Verdict semantics (spec 020, unchanged). Dispatch policy (spec 030 / spec 038). Tray layout (spec 035).

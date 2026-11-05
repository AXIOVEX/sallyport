# Configuration

One file is the source of truth: `syncyard.yaml`. The
gatekeeper looks for it beside its own code, or at the path
named by the `SYNCYARD_CONFIG` (then `SYNCARD_CONFIG`)
environment variable. Every key is optional — an absent key
falls back to a built-in default, and the defaults are the
founding fleet's proven values, so a fleet with no config
file behaves exactly as the original constants did.

The loader is deliberately small and **fails closed**: it
supports a documented subset of YAML (nested block maps,
block and inline lists, scalars, comments) and raises a
named error for anything outside it — an invalid file stops
the gatekeeper loudly at load, never silently falls back.
Validation is likewise named-error: config version must be
1, lane names unique, every lane's host must exist, watermark
pairs ordered (warn < escalate), budget critical below
standdown, thresholds positive.

The checked-in example — `config/syncyard.example.yaml` —
expresses the founding fleet exactly and is the reference
this page annotates. Copy it to `syncyard.yaml` and edit.

## Repositories

- `door_repo` — the **private** repository whose workflows
  dispatch to your runners. The gatekeeper's dispatch and
  watch calls go here.
- `project_repo` — this project (`AXIOVEX/syncyard`), used
  by `status --repo` for CI and Dependabot health.

## Budget

The GitHub REST quota is one account-wide bucket; the sweep
stands down rather than starve it.

- `standdown_remaining` (1000) — below this many core calls
  remaining, no dispatch.
- `critical_remaining` (500) — below this, not even the
  batched run list is fetched; completion-critical work only.
- `reserve_for_completion` (0) — extra headroom held back
  for harvests.

## Dispatch

- `max_active_dispatched` (2) — the global cap on
  gatekeeper-active runs.
- `stale_dispatching_min` (10) — a job stuck in the
  dispatching state longer than this is flagged for a human.
- `lock_stale_min` (10) — the sweep lock's staleness bound.
- `duplicate_policy` (`refuse`) — what a second submit of an
  already-active `--key` does.
- `soft_cap_min` (30) — jobs expected to run longer than
  this are announced to the coordination point first.

## Transport

- `max_script_bytes` (49152) — the script size ceiling for
  workflow-input transport. Workflow inputs have a practical
  ceiling (~54 KB observed); larger payloads use the chunked
  transport rather than a bigger number here.

## Markers

- `pattern` — the regular expression a completed run's
  verdict lines must match at line start
  (`^[A-Z][A-Z0-9_]{2,}([ :]|$)` by default). Verdicts come
  from these lines, never from the run's success color.

## Liveness and reboot

- `liveness.down_after_s` (900) — a host whose heartbeat is
  older than this is shown DOWN. DOWN means it stopped
  reporting; nothing more is claimed.
- `reboot.grace_s` (60) — the grace delay `reboot --host`
  gives a host before it goes down, unless `--delay`
  overrides; the going-down record is always written first.

## Hosts

Each host names:

- `type` — `windows`, `wsl2`, `macos`, `linux`, or `docker`.
- `workflows` — the transports it serves, each `execute`
  (run a script) or `grab` (return a file).
- `identity` — the marker names its venues prove themselves
  with (for example `[LID_HOST, LID_WINUSER, LID_OS]`).
- `cwd` — default working directory per workflow.
- A `wsl2` host adds `via` (the Windows host it is reached
  through) and `distro`; a `docker` host adds a default
  `image` (a job may override with `submit --image`).

A job addressed to a lane runs on that lane's host or is
refused at submit time — never silently rerouted.

## Workflows

The list of dispatchable workflow file names (`run.yml`,
`wsl.yml`, `grab.yml` in the founding fleet; the example
config shows how `linux.yml` / `macos.yml` / `docker.yml`
join when their hosts are declared).

## Lanes

Count and names are configuration. Each lane names its
`host`, its `runner_label` (the label the runner registered
with), and its watermarks in minutes:

- `queued: [warn, escalate]` — how long a job may wait
  before the breach is recorded, then escalated.
- `executing` — either `mode: fixed` with
  `values: [warn, escalate]`, or `mode: expectation`, where
  the job's declared `--expected-min` is scaled and floored
  by `floor: [warn, escalate]`.
- `concurrency` — 1 (serial) unless a lane says otherwise;
  the GPU lane is serial on purpose.

## Endpoints and retention

- `endpoints` — named notification endpoints programs
  register against on submit (type `chat`, `webhook`,
  `file`, or `command`); the outbox delivers per type. The
  founding fleet's map is empty in the example because its
  endpoints are its own.
- `retention.human_log_max_bytes` (1000000) — the
  gatekeeper's human-readable log rotates at this size; the
  append-only `ledger.jsonl` is the machine record and is
  archived by `purge --ledger`, never rotated silently.

# The door repository: ownership & versioning

The door is the set of GitHub Actions workflows the
gatekeeper dispatches through — `run.yml`, `wsl.yml`,
`grab.yml` (and the `transport-smoke.yml` battery that
guards them). This record names their owner and their
versioning rule (spec 038 section 13, FR-038-68).

## Owner

The Syncyard program (Desktop Access chat) owns the door:
its workflows, its version bumps, and its contract battery
(`runner/test_workflow_contract.py`). No other program
edits door workflows; changes arrive as requests to the
owning chat.

## Versioning

- Each workflow carries its version in a header comment
  (`# version X.Y.Z` — `run.yml` is at 0.1.1).
- A behavior change bumps the version in the same commit;
  the header and the behavior never drift.
- Actions are pinned by commit SHA, never by tag
  (`actions/checkout@3d3c42e5...`), and a pin change is a
  reviewed diff like any other.
- Dispatch pins door workflows by commit SHA at dispatch
  time and records the SHA in the job record (spec 034
  FR-034-10); the branch is never the dispatch target.

## The contract

The workflow files' inputs, job names, and artifact names
are a contract with the gatekeeper and the runners. The
contract battery runs in CI on every change; a red battery
blocks the merge, and the local substitution rule (run the
battery locally, record the verdict) applies when hosted
CI is billing-blocked.

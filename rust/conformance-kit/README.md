# Conformance kit (spec 034 section 7)

Sample payloads for venue bring-up: when a new venue or host
comes online, run these three payloads through it. Each one
emits the full spec 034 progress line set to
`$SYNCYARD_PROGRESS_FILE` and exits 0:

- `payload.sh` — bash
- `payload.py` — Python 3
- `payload.ps1` — PowerShell

The venue conforms when all three harvest back with the
IDENTICAL parsed event stream (STARTED, MARKER kit-start,
PROGRESS 1/2 first-half, HEARTBEAT, PROGRESS 2/2 second-half,
COMPLETED done) and the completion report carries the
payload's own exit code (0). A `COMPLETED` line is display
data only; the verdict is the exit code (spec 020).

The bash and Python payloads are exercised by the agent test
suite on every run (`cargo test -p syncyard-agent`, test
`t034_11_conformance_kit_three_languages`). The PowerShell
payload's execution leg runs on Windows hosts (the field
leg); where `pwsh` exists the same test executes it too.

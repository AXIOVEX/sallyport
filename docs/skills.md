# Skills pack

Syncyard ships an **agent skill**: the operating contract an
AI agent needs to drive the gatekeeper correctly — the exact
commands, lane routing, the marker-verdict rule, API
discipline, watermark escalation etiquette, and the security
invariant — written so an agent can pick the project up cold
from that one file.

## One source, eight editions

Every edition is generated from a single shared source
(`skills/_source/contract.md`) by `skills/build_skills.py`.
No edition is hand-edited, and CI fails if any edition drifts
from the source, from the repository VERSION, or from the
verbatim security invariant. Behavior changes update the
source in the same change — a change that leaves the skill
stale is incomplete by the project's own rule.

Editions are ranked by how agents actually reach this
project:

1. **Meta Muse** — the primary, first-class edition.
2. **Claude Code**
3. **GitHub Copilot**
4. **OpenAI Codex / ChatGPT**
5. **Cursor**
6. **Google Gemini / Antigravity**
7. **Generic agent** — plain Markdown for any host that loads
   skill or instruction files.
8. **CLI-only fallback** — no agent runtime assumed; the
   contract read as operating instructions for a human or a
   script at a plain terminal.

## The fleet's own copy

The repository root `SKILL.md` is the founding fleet's
operator copy — the same contract with fleet specifics
(coordination points, live operational detail) that the
adopter editions deliberately generalize away. It follows
the same currency rule and is updated in the same changes.

## What the contract teaches, in one paragraph

Submit through the gatekeeper, never around it; declare a
lane and a unique key for every job; write marker lines and
read verdicts from them, never from run colors; never poll —
the sweep is the only watcher, and push notifications replace
polling; check API headroom before any burst and treat a 403
as a hard stop; respect watermarks as escalation triggers,
never as cancellation authority; and hold the security
invariant verbatim: runners are reachable only via
`workflow_dispatch` from the gatekeeper, no workflow may bind
self-hosted labels to `pull_request`, `pull_request_target`,
or issue events, the door repository is private, and the
backchannel repository is private.

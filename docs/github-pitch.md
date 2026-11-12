# Pitch brief: Syncyard as a `gh` extension

**Status: PREPARED ONLY — NOT SENT.** This brief was written
under spec 010 FR-011. It is sent to GitHub (or anyone else)
only on the owner's explicit word, which has not been given.
Everything below is the draft text and the reasoning behind
it, kept in the repo so the send, when authorized, is a
reviewed document rather than an improvisation.

## The problem, as GitHub would see it

AI agent fleets are becoming a real source of self-hosted
runner load, and they drive runners badly by default:

- **Quota collisions.** Many autonomous programs sharing one
  GitHub account each dispatch and poll their own jobs. The
  REST quota (5,000 calls/hour) is one shared bucket; a fleet
  of well-meaning pollers can exhaust it in a morning, and
  then *every* program stops — including the ones that
  behaved. GitHub sees this as support tickets and abuse
  heuristics; owners experience it as "Actions randomly
  broke."
- **Unsafe reachability.** The naive bridge from an agent to
  a self-hosted runner is an event trigger — and a
  self-hosted runner bound to `pull_request` or issue events
  is remote code execution on the owner's machine for anyone
  who can open a PR. The ecosystem is one popular tutorial
  away from a pattern that gets people's desktops owned.
- **No queue discipline.** A self-hosted runner runs one job
  at a time. Without lanes, a two-minute probe waits an hour
  behind a render; without a gatekeeper, duplicate dispatches
  and runaway retries multiply quota burn.

There is currently no blessed pattern for "an agent fleet
may run work on my machines" — every fleet reinvents it, and
most reinventions are either unsafe or quota-hostile.

## The adoption shape being proposed

**A `gh` extension first — not a core merge, not a platform
change.** This already exists and works:

- `gh extension install electrohire/gh-syncyard` installs
  `gh syncyard`, a thin Go dispatcher over the Syncyard
  gatekeeper (the engine stays Python; the extension is a
  front door). Released as gh-syncyard v0.1.0 with
  precompiled binaries for the standard platforms.
- The engine is [electrohire/syncyard](https://github.com/electrohire/syncyard)
  (Apache-2.0), at v0.3.0: a config-driven gatekeeper, lane
  routing across Windows / WSL2 / Linux / macOS / Docker
  hosts, a failsafe backchannel for recovery, and a ranked
  skills pack that teaches eight agent tools (Meta Muse,
  Claude Code, GitHub Copilot, OpenAI Codex, Cursor, Google
  Gemini, plus generic and CLI-only editions) one shared,
  drift-checked contract.

What "adoption" would realistically mean, in increasing
order of commitment — the brief asks only for the first:

1. **Listing / discoverability**: gh-syncyard in the
   extension ecosystem's public listings, so the pattern is
   findable by the next fleet that needs it.
2. **Pattern recognition**: the dispatch-only invariant and
   the fleet polling standard written up where GitHub
   documents self-hosted runner safety — Syncyard's
   `docs/STANDARD.md` and `SECURITY.md` are drafted to be
   liftable for this.
3. **Eventually, upstream interest in the gatekeeper shape
   itself** (budget-aware, lane-aware dispatch as a
   first-class concept) — named here as a direction, not an
   ask.

## Design constraints Syncyard already satisfies

These are the properties a GitHub reviewer would check
first; each is enforced by a test in CI, not just written
down:

- **Dispatch-only reachability.** Runners are reachable only
  via `workflow_dispatch` from the gatekeeper; no workflow
  binds self-hosted labels to `pull_request`,
  `pull_request_target`, or issue events; the door
  repository is private; the backchannel repository is
  private. This sentence is carried verbatim in every skill
  edition and in SECURITY.md, and a CI battery fails if any
  edition drops it.
- **Quota citizenship.** One watcher per fleet, batched
  status reads, 3–5 minute minimum poll intervals, a
  headroom check before bursts, stand-down thresholds, and a
  hard stop on 403 — the fleet standard is a shipped
  document and the gatekeeper enforces it mechanically.
- **Exactly-once, verifiable dispatch.** Job state is
  written with read-back verification *before* dispatch;
  duplicates are refused; verdicts come from markers in the
  jobs' own logs, never from run colors.
- **Hosted-only CI.** This repo's CI runs exclusively on
  GitHub-hosted runners; self-hosted machines never execute
  untrusted event payloads.
- **Honest limits.** The design states what it cannot do
  (it cannot prevent a forced shutdown — spec 011's
  protection level is "delay + drain + checkpoint + notify,
  never prevent"; it does not fail jobs over between hosts)
  in the README itself. No marketing claim in the repo lacks
  a test or a field record behind it.

## What would need to change

For steps 1–2 above: essentially nothing in the code. The
gaps are presentational and procedural, listed honestly:

- **Single-operator evidence base.** Syncyard's field record
  is one owner's fleet (a busy one — hundreds of jobs across
  research, build, and bench programs — but one). Wider
  adoption evidence needs outside adopters; the per-host
  quickstarts and the skills pack are built to make that
  possible, and their feedback would be the next input.
- **Windows-first field history.** The host matrix (Linux,
  macOS, Docker) is implemented and transport-tested on
  hosted runners, but the long field history is Windows +
  WSL2. The brief should not claim otherwise.
- **Extension polish for a general audience**: the
  dispatcher's gatekeeper discovery order and error messages
  assume the Syncyard layout; a general listing would want a
  `gh syncyard setup` walkthrough. Small, well-scoped work.
- **Governance**: a public repo taking outside contributions
  needs the contribution path exercised at least once
  end-to-end (branch protection and the required-check gate
  are already in place and have carried 30+ PRs).

## The one-paragraph version (for a first contact)

> Agent fleets are driving self-hosted GitHub Actions
> runners today, mostly unsafely and with no quota
> discipline. Syncyard (Apache-2.0, v0.3.0) is a working,
> field-proven pattern for doing it right: a gatekeeper that
> is the only door to the runners (workflow_dispatch only,
> never event triggers), lane routing across an owner's
> Windows/WSL2/Linux/macOS/Docker machines, an enforced
> fleet polling standard that keeps one account's quota
> healthy, and a skills pack that teaches eight agent tools
> the same contract. It already ships as a working gh
> extension — `gh extension install electrohire/gh-syncyard` —
> and the ask is modest: discoverability for the extension,
> and consideration of the dispatch-only pattern where
> GitHub documents self-hosted runner safety.

---

*Prepared 2026-10-31 under spec 010 FR-011 / T021. Not
sent. The owner's explicit word is required before any part
of this brief leaves the repo.*

# Security policy and posture

## Reporting a vulnerability

Please report security issues privately, not in public issues:

- Use GitHub's **private vulnerability reporting** for this repository
  (the "Report a vulnerability" button under the Security tab), or
- Contact the maintainers at ElectroHire, Inc. directly.

We will acknowledge reports, investigate, and coordinate a fix and a
disclosure timeline with the reporter. Please give us a reasonable
window to fix an issue before publishing details.

## Supported versions

Only the latest release is supported. The version in the `VERSION` file
on the default branch is the current revision; fixes land there first,
with a version bump and a `CHANGELOG.md` entry.

## The invariant

Everything in Syncyard's security posture hangs off one sentence. It is
stated verbatim in every agent skill edition this project ships, and
the skills battery fails the build if the text drifts:

> Runners are reachable only via `workflow_dispatch` from the gatekeeper; no workflow may bind self-hosted labels to `pull_request`, `pull_request_target`, or issue events; the door repository is private; the backchannel repository is private.

What that means in plain language:

- **Self-hosted runners sit behind exactly one trigger.**
  `workflow_dispatch` requires write access to the repository that
  holds the workflows (the *door* repository). No event a stranger can
  cause — opening a pull request, filing an issue, commenting — can
  start a job on your machines. The door repository is **private**:
  the public project repository holds the code and the documentation,
  never a live trigger surface pointed at your hardware.
- **CI never touches your machines.** Continuous integration for this
  project runs on GitHub-hosted runners only. It checks workflow
  files, runs the test batteries, and builds releases; it does not
  check out or execute anything on the self-hosted fleet.
- **The gatekeeper is the only routine way in.** Programs hand job
  requests to the gatekeeper; it checks the shared API budget,
  refuses duplicates, records the job, and only then dispatches.
  Two of its rules are security rules, not just reliability rules:
  - *Verified writes*: every queue, ledger,
    and control-file write is written, read back, and verified
    byte-for-byte, and a record update lands only as an atomic swap
    of a fully verified file — a failed save never destroys the
    standing record, and the gatekeeper never dispatches a job it
    could not record first.
  - *Exactly-once dispatch*: a job is recorded in `dispatching`
    state before the dispatch call is made, and a second submission
    with the same key while one is active is refused — a retry or a
    double-click can never run the same job twice.
- **The backchannel carries no work.** The failsafe backchannel
   is a development and recovery channel between the
  operator and the host — health heartbeats in, recovery commands
  out. It lives in its own **private** repository, its agent executes
  only freshly-issued command ids, and a STOP file halts it. Program
  jobs never travel it; the gatekeeper is the only door for work.

## The rules the invariant rests on

1. **Dispatch only.** Every workflow that binds self-hosted runners
   (`run`, `wsl`, `grab`, `linux`, `macos`, `docker`) is triggered
   **only** by `workflow_dispatch`.
2. **No unprivileged triggers, ever.** No workflow may bind
   self-hosted runners to `pull_request`, `pull_request_target`,
   `issues`, `issue_comment`, `fork`, or any other event a user
   without write access can cause. This holds for edits, additions,
   and "temporary" experiments. A contract battery in CI checks every
   workflow file for these combinations.
3. **CI is hosted-only.** See above; the CI workflow never binds
   self-hosted labels.
4. **Repo-scoped registration.** Runners register to one repository
   only — never to an organization or enterprise — so a configuration
   mistake cannot expose runners to other repositories.
5. **No secrets in any repository.** No tokens, credentials, keys, or
   registration material is committed, in any file, at any revision.
   Runner registration tokens are short-lived (about an hour),
   fetched at setup time through the API, passed to the setup script
   once, and never written to disk or echoed by it. If a token or
   credential is ever found in a repository's history, treat it as
   compromised: revoke it first, then report.
6. **Jobs run as a normal user, in the open.** Runners execute as
   user-session processes under the operator's own account (not as a
   service, not as administrator), and every job's script arrives as
   a workflow input whose content appears in the run log. There is no
   hidden execution path: if it ran, it is in the log, with the
   marker lines the job printed.

## Adopter checklist — token scoping

Syncyard needs very little credential surface. Keep it that way:

- [ ] **One credential per purpose.** The gatekeeper dispatches
      through the operator's `gh` CLI sign-in (or a token with the
      same reach). The backchannel agent uses the host's own git
      credential for its private channel repository. CI uses the
      workflow's built-in `GITHUB_TOKEN` on GitHub-hosted runners.
      Do not share one token across these jobs.
- [ ] **Scope to the door, nothing more.** The dispatch credential
      needs write access to the private door repository (to call
      `workflow_dispatch` and read run logs) — not organization
      admin, not access to your other repositories. If you use a
      personal access token instead of the `gh` CLI sign-in, scope
      it to the door repository alone.
- [ ] **Registration tokens are minted, used once, and gone.** Fetch
      a runner registration token at setup time, use it immediately,
      never write it to disk, a script, or a ticket. It expires in
      about an hour on its own.
- [ ] **No tokens inside job scripts.** A job script is printed in
      the run log. Anything a job needs beyond the workflow's own
      `GITHUB_TOKEN` is a design smell — restructure the job instead
      of embedding a credential.
- [ ] **Revoke on suspicion, rotate on schedule.** A token that may
      have leaked is revoked first and investigated second.

## Adopter checklist — runner isolation

- [ ] **Dedicated user, ordinary privileges.** Runners run as a
      normal user in that user's own session — never as
      administrator/root, never as a shared service account. Jobs
      inherit exactly that user's reach; give the account no more
      reach than the work needs.
- [ ] **Treat the host as a workbench, not a vault.** Keep no
      production secrets on the runner host beyond what the jobs
      themselves require. Anything readable by the runner's user is
      readable by any dispatched job.
- [ ] **The door repository stays private.** Never move the dispatch
      workflows into a public repository, and never register these
      runners on a public repository — that combination is the
      hazard this design exists to remove.
- [ ] **Never add event triggers "just for testing".** The fastest
      way to break the invariant is a temporary `pull_request`
      binding on a self-hosted workflow. Test dispatch by dispatch.
- [ ] **Repo-scoped registration only.** Register runners to the
      door repository, never organization-wide.
- [ ] **Review write access like a key list.** Anyone with write
      access to the door repository can run arbitrary code on your
      machines. That list should be short, named, and reviewed.

## What this model does not claim

- Anyone with write access to the door repository can dispatch
  arbitrary code to the runners. Write access *is* the authorization
  boundary; grant it accordingly.
- Jobs run with the operator account's permissions on the runner
  machine. Syncyard does not sandbox job scripts; dispatch only code
  you trust, from people you trust.
- The frozen emergency admin path the operator keeps outside this
  repository is out of scope for this policy and carries no job
  traffic.

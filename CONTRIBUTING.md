# Contributing to Syncyard

Thank you for your interest in improving Syncyard. This document explains
how to propose changes so they can be reviewed and landed quickly.

## Ground rules

- **Be respectful.** Participation is governed by the
  [Code of Conduct](CODE_OF_CONDUCT.md).
- **Security first.** If your change touches anything security-sensitive,
  read [SECURITY.md](SECURITY.md) first. In particular, the project's core
  safety invariant is absolute:

  > No workflow that runs on self-hosted runners may ever be triggered by
  > `pull_request`, `pull_request_target`, `issues`, or any other event an
  > untrusted party can create. Self-hosted runners are reachable only
  > through `workflow_dispatch`, which requires write access to the
  > repository. CI runs on GitHub-hosted runners only.

  A pull request that weakens this invariant will be declined regardless of
  its other merits.
- **Keep changes focused.** One topic per pull request; unrelated cleanups
  go in separate pull requests.

## Tests and documentation first

Every behavior change lands with its tests and its evidence in the
same pull request: a change with no test that would fail without it
is not ready to merge.

- **SKILL.md is updated in the same change as any change to behavior,
  commands, lanes, workflows, or operations.** A pull request that
  leaves SKILL.md stale is incomplete.

## Development setup

The gatekeeper is a single-file Python 3 program using only the standard
library — there is nothing to install to work on it:

```sh
python3 gatekeeper/gk.py --help
```

The runner components are PowerShell (Windows) and Bash (clients). Please
run the checks that CI runs before opening a pull request:

- Python syntax: `python3 -m py_compile gatekeeper/gk.py`
- Shell scripts: [ShellCheck](https://www.shellcheck.net/) must report no
  findings on `clients/*.sh`
- PowerShell: scripts must parse cleanly (CI parses every `.ps1`)
- The CI security guard must pass: it scans the workflows for forbidden
  trigger combinations (self-hosted labels bound to untrusted events)

## Submitting a change

1. Fork the repository (or branch, if you have write access) and make your
   change on a topic branch.
2. Add an entry to [CHANGELOG.md](CHANGELOG.md) under **Unreleased**, in
   [Keep a Changelog](https://keepachangelog.com) format. User-visible
   behavior changes always get an entry; internal-only refactors are
   encouraged to as well.
3. Open a pull request against `main`. Fill in the pull request template:
   what changed, why, and how you verified it.
4. A maintainer review and a green CI run are required before merge. Branch
   protection enforces both; please do not ask for the checks to be waived.

## Versioning

Syncyard follows [Semantic Versioning](https://semver.org). Maintainers cut
releases from the changelog; contributors do not bump `VERSION` in pull
requests unless the pull request is itself the release.

## Reporting bugs and requesting features

Use the issue templates. For anything security-related, do **not** open a
public issue — follow the private reporting path in
[SECURITY.md](SECURITY.md).

## A note on scope

Syncyard is deliberately small: a gatekeeper, a set of dispatch workflows,
runner setup tooling, and a written fleet standard. Features that require a
server, a database, or a hosted service are out of scope by design — the
whole system runs from a repository and a cron line.

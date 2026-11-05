# Feature Specification: Standalone Distribution — PyPI Package, Read the Docs, Skill Currency

**Feature Branch**: `spec/017-standalone-distribution`

**Created**: 2026-11-02

**Status**: **Implemented in this change, up to two named owner
gates.** The package builds, installs, and runs from a clean
virtual environment; the release workflow is in place and inert
until a version tag is pushed; the documentation site builds
strict locally and in CI. Nothing in this change publishes
anything: the first PyPI publish waits on FR-005 (the owner
registers the trusted publisher on pypi.org) and the public
docs site waits on FR-007 (the owner imports the project on
readthedocs.org). Both gates are one-time owner acts on
third-party sites; everything up to them is built and proven
here. This spec is deliberately independent of the stacked
feature branches (013/015/016): packaging, docs, and workflows
do not touch gatekeeper feature code, so it is based on `main`
and merges on its own schedule.

**Direction (owner, 2026-11-02)**: "remember to keep a good
skills.md updated for syncyard too. and keep a standalone
syncyard along with the extension. This should have pypi and
rtd too."

## The gap

Syncyard can be driven three ways today, and only two of them
are real distribution routes:

- From a **checkout**: `python3 gatekeeper/gk.py …` — the
  route the founding fleet uses, and the only one that works
  without further setup.
- Through the **gh extension** (spec 010 FR-012, shipped
  2026-10-31): `gh extension install AXIOVEX/gh-syncyard`,
  then `gh syncyard …` — a thin Go dispatcher that finds a
  gatekeeper on disk and forwards to it. It presupposes a
  gatekeeper installation exists somewhere for it to find.
- There is **no standalone install**: no `pip install
  syncyard`, no `pipx install syncyard`, no package index
  presence at all, and no hosted documentation site. An
  adopter who does not use `gh` — or who wants the engine
  itself versioned and pinned like any other Python tool —
  has nothing to install.

The skills pack has the matching gap: the shared contract and
the fleet SKILL.md describe the checkout route and the `gh
syncyard` route wherever a command form is named, but no
standalone route exists to describe. The owner's standing
rule (this skill is updated in the same change as any change
to behavior, commands, or operations) therefore makes the
skill refresh part of this spec, not an afterthought.

## The boundary, stated plainly

**The fleet's live gatekeeper is plain files, and stays
plain files.** The founding fleet runs
`~/workspace/desktop-access-migration/gatekeeper/gk.py` by
path, with its state (queue records, ledger, pause/stop
files) in files beside it and its config resolved beside it.
Packaging must not move, rename, or change the behavior of
`gatekeeper/gk.py` or `gatekeeper/config.py` in the checkout:
the pip package and the checkout are the same code, or the
package does not ship. This change therefore edits neither
file (FR-010) and builds the package by mapping the existing
directories into the distribution (FR-003).

Two consequences are accepted, and stated here so they are
decisions rather than surprises:

- **State location follows the code.** `gk.py` anchors its
  state directory to its own file location. In a checkout
  that is the checkout's `gatekeeper/` directory, as today.
  In a pip install it is the installed package directory —
  inside the virtual environment for the recommended `pipx`
  / venv routes, which is writable and self-contained.
  Configuration still resolves by the documented chain
  (`SYNCYARD_CONFIG` / `SYNCARD_CONFIG`, then a
  `syncyard.yaml` beside the code, then built-in defaults),
  so an installed gatekeeper is pointed at a fleet config by
  environment variable, exactly as a checkout can be.
- **The extension stays the thin dispatcher.** The standalone
  does not replace `gh syncyard`; it gives the extension (and
  everyone else) one more, standard place for the engine to
  be installed. Whether the extension's doctor lookup should
  also resolve a pip-installed `syncyard` is a change in
  `AXIOVEX/gh-syncyard`, out of this repo's boundary: it is
  flagged as a follow-up in this change's PR, not built here.

## User scenarios

1. **Standalone install.** An adopter runs `pipx install
   syncyard` (or `pip install syncyard` in a venv) and drives
   the full gatekeeper surface as `syncyard submit …`,
   `syncyard status`, `syncyard pause …` — the same verbs,
   arguments, output, and exit codes as `gk.py`, because it
   is the same code (FR-001, FR-002, FR-003).
2. **Pinned, versioned engine.** The adopter pins
   `syncyard==0.3.0`; the version they get is the repository
   VERSION for that release, because the package version is
   read from the VERSION file at build time and from nowhere
   else (FR-001).
3. **A release the owner can trust.** The maintainer pushes a
   `v*` tag; the release workflow builds the sdist and wheel,
   checks them, installs the wheel into a clean environment
   and smokes the console command, and only then publishes —
   with a short-lived OIDC credential minted for that run,
   never a stored token (FR-004). A dry run of the same
   pipeline, minus publishing, is one manual dispatch away.
4. **Docs that build or fail loudly.** A reader lands on the
   documentation site (after FR-007): what Syncyard is, both
   install routes, a quickstart, the concepts at the level
   the shipped code supports, the configuration reference,
   the skills pack, and the changelog. The same site builds
   with `mkdocs build --strict` in CI, so a broken page
   fails the build instead of reaching a reader (FR-006).
   Features that exist only in unmerged specs are marked as
   specified, not shipped — or omitted (FR-009).
5. **Skills that name every route.** An agent reading any
   edition of the skill pack sees the checkout route, the
   `gh syncyard` route, and the standalone route wherever a
   command form is given, and the editions still cannot
   drift from their shared source (FR-008).

## Requirements

Each requirement carries a behavior statement, an acceptance
mode (**test** = an automated battery decides; **oracle** =
the owner or operator judges from presented evidence;
**gate** = a named human decision/act must precede the
outcome), and a *this does not establish* line bounding what
passing it proves.

### Part A — The standalone package

- **FR-001 — One distribution, versioned from one source.**
  *Behavior*: the repository root carries a `pyproject.toml`
  defining exactly one distribution, named `syncyard`,
  licensed Apache-2.0, with zero runtime dependencies (the
  gatekeeper and the backchannel client are Python standard
  library only, and the package adds nothing). The package
  version is read dynamically at build time from the repo
  `VERSION` file — the same file the skills pack and the CI
  changelog check already treat as the version source — so
  a later version bump (for example the 0.4.0 the spec 013
  stack owns) flows into the package with no packaging edit.
  `requires-python` is `>=3.9`, the floor the shipped code's
  syntax supports; the proven interpreter is 3.12 (CI, RTD,
  and the founding fleet).
  *Acceptance*: test — the packaging battery
  (`packaging/test_packaging.py`) builds the distribution
  and asserts the built metadata version equals the VERSION
  file's content, the name is `syncyard`, and the wheel
  declares no runtime dependencies.
  *This does not establish* that the code runs on every
  interpreter `>=3.9` claims — only 3.12 is exercised by the
  batteries; the floor is a syntax-level compatibility
  statement, not a tested matrix.

- **FR-002 — The console command is the gatekeeper, not a
  re-implementation.**
  *Behavior*: the package installs one console script,
  `syncyard`, whose entry point is the gatekeeper module's
  own `main()` — the identical argparse surface (`submit`,
  `adopt`, `status`, `cancel`, `sweep`, `pause`, `resume`,
  `start`, `stop`, `purge`, `outbox`, `host-resume`,
  `reboot`). No verb is re-implemented, wrapped with changed
  semantics, or subsetted: arguments, output, and exit codes
  are whatever `gk.py` produces, because the entry point
  calls it directly. The backchannel client ships in the
  package as the importable module
  `syncyard.backchannel.bc` (stdlib only, no import side
  effects), runnable as `python3 -m syncyard.backchannel.bc`.
  *Acceptance*: test — the packaging battery installs the
  built wheel into a fresh virtual environment with no
  network and no dependencies, then runs `syncyard --help`
  and `syncyard status` (both must exit 0) and imports the
  packaged config module and loads the default
  configuration.
  *This does not establish* end-to-end dispatch from an
  installed copy against a live fleet — that needs a door
  repository, runners, and credentials; the battery proves
  the command surface and configuration load, and the fleet
  itself keeps running the checkout copy (FR-010).

- **FR-003 — The packaged code is the checkout code,
  byte for byte.**
  *Behavior*: the distribution maps the existing directories
  into the package — `gatekeeper/` becomes the `syncyard`
  package directory and `backchannel/` becomes
  `syncyard.backchannel` (setuptools `package-dir` mapping
  of PEP 420 namespace packages; neither directory gains an
  `__init__.py` and no file is copied, generated, or
  transformed at build time). This works without touching
  the engine because `gk.py` already loads `config.py` by
  file path from its own directory: siblings on disk in the
  checkout stay siblings on disk in the install. The
  installed `syncyard/gk.py`, `syncyard/config.py`, and
  `syncyard/backchannel/bc.py` are byte-identical to the
  checkout files they were built from.
  *Acceptance*: test — the packaging battery compares the
  wheel's copies of those three files byte-for-byte against
  the checkout's, and does the same for the sdist by
  building the wheel from the sdist.
  *This does not establish* that a stray directory named
  `syncyard` elsewhere on a user's `sys.path` can never
  merge with or shadow the namespace package — PEP 420
  merging is the accepted trade-off of not adding files to
  the fleet's directories (see plan.md); the recommended
  isolated routes (`pipx`, venv) make it a non-issue in
  practice.

### Part B — The PyPI release pipeline

- **FR-004 — Tag-triggered, tokenless, smoke-tested
  publishing.**
  *Behavior*: `.github/workflows/release.yml` runs on
  GitHub-hosted runners only. On a pushed `v*` tag it builds
  the sdist and wheel, runs `twine check` over both,
  installs the wheel into a clean virtual environment and
  smokes the console command (`syncyard --help`,
  `syncyard status`, and a configuration load), and only
  then publishes with `pypa/gh-action-pypi-publish` using
  OIDC trusted publishing (`id-token: write` scoped to the
  publish job only). No PyPI password or API token is
  stored anywhere. A `workflow_dispatch` dry run executes
  every step except publishing. Until a tag is pushed the
  workflow does nothing at all.
  *Acceptance*: test — the packaging battery asserts the
  workflow's structural invariants (tag trigger present,
  dry-run dispatch present, `id-token: write` present, the
  trusted-publishing action present, and no stored-credential
  fields — no `password:`, no token secret references);
  the build + check + smoke steps are the same commands the
  battery itself runs locally.
  *This does not establish* that a real publish succeeds —
  that is exactly FR-005's gate; the pipeline is proven up
  to the publish step, and the first tagged release is the
  publish's own proof.

- **FR-005 — OWNER GATE: the PyPI trusted publisher.**
  *Behavior*: before the first publish can succeed, the
  owner registers a **pending trusted publisher** for the
  project name `syncyard` on pypi.org, naming this
  repository (`AXIOVEX/syncyard`), the workflow
  `release.yml`, and the environment `pypi`. The project
  name was checked on 2026-11-02: `GET
  https://pypi.org/pypi/syncyard/json` returns 404 (and the
  same on test.pypi.org) — no project named `syncyard`
  exists, so the name is available for the pending-publisher
  flow as of this change. Registering is a one-time act on
  the owner's own PyPI account; no agent can or should do
  it, and no credential changes hands when it is done.
  *Acceptance*: gate — the owner's registration on
  pypi.org; the first `v*` tag's publish job is the
  evidence it was done correctly.
  *This does not establish* anything about later releases:
  each publish is still its own tagged, smoke-tested run
  under FR-004.

### Part C — The documentation site (Read the Docs)

- **FR-006 — A docs tree that builds strict, in CI and on
  RTD.**
  *Behavior*: the repository carries `.readthedocs.yaml`
  (configuration version 2, Python 3.12, `fail_on_warning:
  true`) and a MkDocs site (`mkdocs.yml`, strict mode on)
  whose source lives in `docs/`: an index (what Syncyard is;
  the two install routes), installation (standalone
  pip/pipx and the gh extension), quickstart, concepts,
  the configuration reference, the skills page, the fleet
  standard and migration notes (the existing `docs/` pages),
  and the changelog page (FR-009). MkDocs is the only docs
  dependency (pinned in `docs/requirements.txt`); the theme
  is the stock MkDocs theme — no theme packages, no plugins.
  The unpublished GitHub pitch brief (`docs/github-pitch.md`)
  is excluded from the site: it is a prepared, unsent
  document (spec 010 FR-011) and a public docs build is not
  its venue. CI runs `mkdocs build --strict`, so a docs
  change that breaks the site fails the same build as a
  code change.
  *Acceptance*: test — `mkdocs build --strict` passes
  locally and as a CI step on this change.
  *This does not establish* that the public site exists —
  that is FR-007's gate; until then the proof is the strict
  build itself, which is the same build RTD will run.

- **FR-007 — OWNER GATE: the Read the Docs project.**
  *Behavior*: the owner imports this repository on
  readthedocs.org under his own account, claiming the
  `syncyard` project slug (`syncyard.readthedocs.io`).
  RTD then builds from the committed `.readthedocs.yaml`
  with no further repository changes. Like FR-005, this is
  a one-time act on the owner's account on a third-party
  site; the repository side is complete in this change.
  *Acceptance*: gate — the owner's import on
  readthedocs.org; the first successful RTD build of the
  default branch is the evidence.
  *This does not establish* a custom domain, versioned-docs
  policy, or any RTD setting beyond the committed config —
  those are later, owner-visible choices on the RTD side.

- **FR-009 — Docs honesty: shipped is shipped, specified is
  labelled.**
  *Behavior*: the docs describe the behavior `main`
  carries. Concepts that exist only in unmerged specs —
  priorities and focus (spec 013), lane locks (spec 015),
  adaptive lanes (spec 016) — are presented as *specified,
  not yet shipped*, in one clearly-marked place, never as
  current behavior. The changelog page is a byte-for-byte
  copy of the repo `CHANGELOG.md` committed beside it; the
  packaging battery fails if the two differ, so the site
  can never quietly carry a different history than the
  repository.
  *Acceptance*: test — the battery's byte comparison of
  `docs/changelog.md` against `CHANGELOG.md`; the
  spec-stage labelling is reviewed as part of this change's
  PR (oracle for wording, test for the changelog).
  *This does not establish* that the docs are complete —
  they are the adopter-facing minimum (install, quickstart,
  concepts, configuration, skills, changelog) over the
  already-public README and standard, not a rewrite of
  either.

### Part D — Skill currency

- **FR-008 — Every route, in every edition, from one
  source.**
  *Behavior*: wherever the skills pack names a command form
  or an install/setup route, the standalone route stands
  beside the checkout and `gh syncyard` routes: the shared
  source `skills/_source/contract.md` names `syncyard`
  (the standalone command) in its intro and in the Commands
  preamble, and the CLI-only edition's note names
  `pipx install syncyard` alongside the extension install.
  All eight editions are regenerated by
  `skills/build_skills.py` from the updated source — no
  edition is hand-edited — and the fleet's own root
  `SKILL.md` gains the matching standalone note. The
  existing currency battery (`skills/test_skills.py`,
  including its generator drift check, the VERSION match,
  and the verbatim security invariant) passes unchanged in
  what it checks.
  *Acceptance*: test — `skills/test_skills.py` passes on
  the regenerated editions.
  *This does not establish* that agents will prefer the
  standalone route — the pack presents the routes; the
  choice stays with the operator and the agent's host.

### Part E — Non-interference

- **FR-010 — The fleet's door does not move.**
  *Behavior*: this change adds files and edits
  documentation, the skills source, the changelog, the
  README, and CI. It does not edit `gatekeeper/gk.py`,
  `gatekeeper/config.py`, or any other gatekeeper, runner,
  tray, or backchannel **code** file, and it does not change
  how the fleet invokes the gatekeeper (by path, from its
  own tree). The full pre-existing battery set passes
  exactly as before (config, FR-013, golden, workflow
  contract, transport smoke, tray source, backchannel unit
  + chaos, skills, gatekeeper offline smoke), plus the new
  packaging battery.
  *Acceptance*: test — the full battery set on this change
  (reported verbatim in the PR), and the change's own diff
  shows no modification to the named code files.
  *This does not establish* anything about the stacked
  feature branches (013/015/016): when they merge, their
  own changes to the gatekeeper flow into the next package
  build automatically, because the package is the checkout
  code (FR-003) at whatever version VERSION then names
  (FR-001).

## Out of scope, named

- **Merging, tagging, or publishing anything.** This change
  lands as a PR for the owner's review. The first tag and
  first publish follow FR-005, on the owner's word.
- **Changes in `AXIOVEX/gh-syncyard`.** Making the
  extension's doctor resolution also find a pip-installed
  `syncyard` is flagged in the PR as a follow-up for that
  repo; nothing here touches it.
- **A docs custom domain or RTD settings beyond the
  committed config** (FR-007).
- **Windows/macOS package channels** (winget, Homebrew,
  installers): the pip/pipx route is the standalone
  distribution for this spec; other channels are later
  specs if the owner wants them.

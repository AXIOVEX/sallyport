# Tasks — 017 Standalone Distribution

All build tasks land in one repo change on
`spec/017-standalone-distribution`, based on `main`.
**Publishing is NOT a task here**: the first PyPI publish
waits on the FR-005 owner gate, the public docs site on the
FR-007 owner gate, and the merge itself on the owner's
review of the PR. Those are listed at the end, unchecked,
as gates — not as work this change performs.

## Phase 0 — Feasibility (before any repo file)

- [x] T001 PyPI name check: `GET
      https://pypi.org/pypi/syncyard/json` → 404 (same on
      test.pypi.org), 2026-11-02. The name `syncyard` is
      available for the pending-publisher flow.
- [x] T002 Read the engine's seams: `gk.py` loads
      `config.py` by file path from its own directory
      (importlib), never by module name; `bc.py` is
      stdlib-only with no import side effects. Both ship
      unedited.
- [x] T003 Packaging prototype in a scratch tree: wheel
      builds with version from `VERSION`; `packages.find`
      variant discarded (empty wheel — it ignores
      per-package `package-dir` roots); explicit `packages`
      list + `package-dir` mapping verified; wheel installs
      into a clean venv offline; `syncyard --help` and
      `syncyard status` exit 0; installed `gk.py`/`bc.py`
      sha256-identical to the checkout.

## Phase 1 — Spec (FR-001 – FR-010)

- [x] T004 `specs/017-standalone-distribution/spec.md`:
      FRs with behavior, acceptance mode, and a
      *this does not establish* line each; the two owner
      gates as gate-type FRs (FR-005, FR-007).
- [x] T005 `plan.md` (approach, rejected alternatives,
      evidence) and this `tasks.md`.

## Phase 2 — The package (FR-001, FR-002, FR-003)

- [x] T006 `pyproject.toml`: distribution `syncyard`,
      dynamic version from `VERSION`, Apache-2.0,
      `requires-python >=3.9`, zero runtime dependencies,
      console script `syncyard = syncyard.gk:main`,
      setuptools `package-dir` mapping
      (`syncyard`→`gatekeeper/`,
      `syncyard.backchannel`→`backchannel/`), explicit
      namespace package list.
- [x] T007 `MANIFEST.in`: `VERSION`, `LICENSE`,
      `README.md`, `CHANGELOG.md` included so sdist builds
      resolve the dynamic version.

## Phase 3 — Release pipeline (FR-004)

- [x] T008 `.github/workflows/release.yml`: tag (`v*`) +
      `workflow_dispatch` triggers; build job (`python -m
      build`, `twine check`); smoke job (clean venv install,
      console + config smokes); publish job gated to tag
      pushes only, OIDC trusted publishing
      (`id-token: write`, `environment: pypi`,
      `pypa/gh-action-pypi-publish`), no stored credentials
      anywhere.

## Phase 4 — Documentation site (FR-006, FR-009)

- [x] T009 `mkdocs.yml` (stock theme, `strict: true`,
      nav, `exclude_docs` for the unsent pitch brief) and
      `docs/requirements.txt` (`mkdocs>=1.5,<2`).
- [x] T010 `.readthedocs.yaml` (config v2, Python 3.12,
      `fail_on_warning: true`).
- [x] T011 New docs pages: `index.md`, `installation.md`,
      `quickstart.md`, `concepts.md` (spec-stage features
      labelled, per FR-009), `configuration.md` (prose
      annotation of the example config — no file copy),
      `skills.md`; existing `STANDARD.md`/`MIGRATION.md`
      join the nav unmoved.
- [x] T012 `docs/changelog.md` committed as a byte-for-byte
      copy of `CHANGELOG.md` (after T016's entry lands);
      the battery pins the equality.

## Phase 5 — Skills currency (FR-008)

- [x] T013 `skills/_source/contract.md`: standalone route
      named in the intro's command-form parenthetical and
      the Commands preamble; battery-pinned strings
      untouched.
- [x] T014 `skills/build_skills.py`: CLI-only edition note
      names `pipx install syncyard` beside the extension
      install; all eight editions regenerated with
      `python3 skills/build_skills.py`.
- [x] T015 Root `SKILL.md`: spec 017 section recording the
      standalone route and the two pending owner gates, in
      the style of the spec 010/011 sections.

## Phase 6 — Docs currency in-change

- [x] T016 `CHANGELOG.md` [Unreleased] entry for spec 017
      (before T012's copy, so the copy carries it);
      VERSION deliberately not bumped.
- [x] T017 `README.md`: Quickstart gains the standalone
      route beside the gh extension route; layout table
      gains the packaging/docs rows.

## Phase 7 — Battery and CI wiring

- [x] T018 `packaging/test_packaging.py`: pyproject
      invariants; clean-copy build (sdist → wheel from
      sdist); wheel byte-identity for `gk.py`, `config.py`,
      `bc.py`; fresh-venv offline install + console/config
      smokes + version equality; changelog-copy equality;
      release-workflow structural invariants.
- [x] T019 `.github/workflows/ci.yml`: packaging step
      (installs `build`, runs the battery) and a
      `mkdocs build --strict` step (installs
      `docs/requirements.txt`) added after the existing
      batteries; no existing step changed.
- [x] T020 Full verification on the final tree: the whole
      pre-existing battery set + the packaging battery +
      `mkdocs build --strict`, results reported verbatim
      in the PR.

## Gates — NOT tasks of this change

- [ ] G1 **FR-005 (owner)**: register the pending trusted
      publisher for project `syncyard` on pypi.org
      (repo `AXIOVEX/syncyard`, workflow `release.yml`,
      environment `pypi`).
- [ ] G2 **FR-007 (owner)**: import the project on
      readthedocs.org and claim the `syncyard` slug.
- [ ] G3 **Owner review**: merge this PR; the first `v*`
      tag afterwards is the first publish (FR-004) and the
      first live RTD build.
- [ ] G4 **Follow-up (other repo)**: teach
      `AXIOVEX/gh-syncyard`'s doctor lookup to resolve a
      pip-installed `syncyard` — flagged in the PR, built
      in that repo when the owner wants it.

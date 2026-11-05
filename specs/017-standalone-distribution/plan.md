# Plan — 017 Standalone Distribution

Companion to `spec.md`. This records the approach actually
taken, the evidence gathered while taking it, and the
alternatives that were considered and rejected — so a
reviewer can re-derive every choice without re-running the
exploration.

## 1. The one hard problem, and why it turned out not to be one

The hard constraint (spec, "The boundary"): the package and
the checkout must be the same code, and packaging must not
edit `gatekeeper/gk.py` or `gatekeeper/config.py`. The risk
was the gatekeeper's configuration load: if `gk.py` did a
plain `import config`, a packaged layout would have to put a
module named `config` on the top-level path (a collision
hazard — half the Python world has a `config` module) or
edit the import.

Reading the code first settled it. `gk.py` loads its config
module **by file path**: `importlib.util.spec_from_file_location(
"syncyard_config", os.path.join(BASE, "config.py"))`, where
`BASE` is the directory containing `gk.py` itself. It never
imports `config` by name. So the only requirement packaging
has is: **`gk.py` and `config.py` must be siblings on disk
in the install, exactly as they are in the checkout.** Any
layout that preserves the sibling relationship works with
zero code changes.

`backchannel/bc.py` was checked the same way: standard
library only, no import-time side effects, a `main(argv=None)`
returning an exit code under a `__main__` guard. It imports
cleanly as a module, so it ships (spec FR-002); no exclusion
was needed.

## 2. Chosen layout: package-dir mapping over namespace packages

`pyproject.toml` (setuptools backend, the only build
dependency):

```toml
[tool.setuptools]
package-dir = {syncyard = "gatekeeper", "syncyard.backchannel" = "backchannel"}
packages = ["syncyard", "syncyard.backchannel"]
```

Neither source directory contains an `__init__.py`, so both
install as PEP 420 **namespace packages**. Setuptools copies
the literal `.py` files into the wheel — `syncyard/gk.py`,
`syncyard/config.py`, `syncyard/backchannel/bc.py` (plus the
directories' test modules, which are checkout code too and
harmless to ship). The console script is a direct entry
point, no shim file anywhere:

```toml
[project.scripts]
syncyard = "syncyard.gk:main"
```

`gk.main(argv=None)` already returns the process exit code
and parses `sys.argv` when called with no arguments, which
is exactly the console-script calling convention — the
entry point is a reference, not a wrapper.

### Alternatives considered

- **Top-level `py-modules` (`gk`, `config` from
  `gatekeeper/`)**: works mechanically, rejected on hygiene
  — installing a top-level module named `config` into every
  user's environment invites import collisions with
  unrelated software, and a top-level `gk` is nearly as
  rude. The namespaced layout keeps everything under
  `syncyard.*`.
- **Adding `gatekeeper/__init__.py`** (regular packages):
  rejected to keep the change's footprint out of the
  fleet's live directories entirely. An `__init__.py` would
  be inert for the fleet, but "packaging adds no files to
  the engine's directories" is a cleaner invariant to state
  and to diff-check (FR-010). Namespace packages buy the
  same layout without it. Accepted trade-off (stated in
  FR-003): PEP 420 packages merge across `sys.path`
  entries, so a stray user directory literally named
  `syncyard` could merge with the installed package;
  isolated installs (pipx/venv) — the documented routes —
  are not exposed to this in practice.
- **`packages.find` with `namespaces = true`**: tried
  first; it does **not** honor per-package `package-dir`
  mappings as search roots, and produced a wheel containing
  only dist-info. The explicit `packages` list above is the
  form that works; the packaging battery's byte checks
  would have caught the empty wheel even without the manual
  prototype (see §4).

### State and config in an install (the accepted trade-off)

`gk.py` anchors state (`queue/`, `ledger.jsonl`, pause/stop
files, its log) to its own directory. Installed under pipx,
that directory is the app venv's
`site-packages/syncyard/` — writable, self-contained, and
removed with the app. This is stated in the user docs
(installation page) rather than hidden: an installed
gatekeeper is a *separate* gatekeeper with its own state,
pointed at a fleet config through `SYNCYARD_CONFIG` /
`SYNCARD_CONFIG` or a `syncyard.yaml` placed beside the
installed code. Nothing in this spec tries to make an
install share the checkout's live state — that sharing is
the fleet's own arrangement and stays untouched.

## 3. Version flow

`VERSION` (repo root) is already the project's single
version source: the skills generator stamps it into every
edition and CI greps the changelog for it. The package joins
the same flow:

```toml
[project]
dynamic = ["version"]
[tool.setuptools.dynamic]
version = {file = "VERSION"}
```

`MANIFEST.in` includes `VERSION` (plus `LICENSE`,
`README.md`, `CHANGELOG.md`) so a build **from the sdist**
still finds it — the battery proves this by letting
`python -m build` build the wheel from the sdist it just
made, and comparing versions. When the spec 013 stack bumps
VERSION to 0.4.0, the next package build is 0.4.0 with no
packaging edit (FR-001). This change does not bump VERSION:
release mechanics ride the next feature version.

`requires-python = ">=3.9"`: a syntax scan of the three
shipped modules shows nothing newer than f-strings and
standard 3.9-era stdlib; the honest bound is recorded in
FR-001 (proven on 3.12, floor is syntax-level).

## 4. Evidence gathered before writing the real files

A throwaway prototype (the exact `pyproject.toml` shape
above, against a copy of the two directories) was built and
exercised before any repository file was written:

- Wheel built: `syncyard-0.3.0-py3-none-any.whl`, version
  taken from `VERSION` untouched.
- Wheel contents: `syncyard/gk.py`, `syncyard/config.py`,
  `syncyard/backchannel/bc.py` present (after the
  `packages.find` variant was discarded for producing an
  empty wheel — see §2).
- Installed into a fresh venv with `--no-index --no-deps`:
  `syncyard --help` exit 0 (the gatekeeper's own argparse),
  `syncyard status` exit 0 against empty state, packaged
  `load_config()` returned the defaults, and sha256 of the
  installed `gk.py` / `bc.py` equalled the checkout files.

The packaging battery (next section) turns each of these
into a repeatable check so the prototype's result cannot
silently rot.

## 5. Test strategy

New battery: `packaging/test_packaging.py`, in the house
style (named PASS/FAIL checks, exit 1 on any failure),
wired into CI after the existing batteries. It:

1. Parses `pyproject.toml` (stdlib `tomllib`): distribution
   name `syncyard`, version declared dynamic from
   `VERSION`, console script `syncyard` →
   `syncyard.gk:main`, zero runtime dependencies,
   Apache-2.0 license.
2. Copies the build-relevant tree to a temp directory
   (never builds in the checkout), runs `python -m build`
   (sdist, then wheel from sdist), and asserts both
   artifacts exist with version == VERSION content.
3. Asserts the wheel contains `syncyard/gk.py`,
   `syncyard/config.py`, `syncyard/backchannel/bc.py`, and
   that each is **byte-identical** to the checkout file.
4. Creates a fresh venv, installs the wheel offline
   (`--no-index`), and smokes: `syncyard --help` exit 0,
   `syncyard status` exit 0, import + `load_config()`
   returns the documented default door repo,
   `importlib.metadata.version("syncyard")` == VERSION.
5. Asserts `docs/changelog.md` is byte-identical to
   `CHANGELOG.md` (FR-009's synced copy).
6. Asserts the release workflow's structural invariants
   (FR-004): tag trigger `v*`, `workflow_dispatch`,
   `id-token: write`, the trusted-publishing action, and
   the absence of stored-credential fields.

The battery needs the `build` package and `venv` support;
CI installs `build` in the packaging step. Everything else
is stdlib. `mkdocs build --strict` is a separate CI step
(and a tasks.md checklist item locally), using
`docs/requirements.txt`.

The full pre-existing battery set is re-run on this change
and reported verbatim in the PR (FR-010).

## 6. Release workflow shape

`.github/workflows/release.yml` — three jobs on
GitHub-hosted runners:

- **build**: checkout → setup-python 3.12 → `pip install
  build twine` → `python -m build` → `twine check dist/*`
  → upload `dist/` as an artifact. Runs for both triggers.
- **smoke**: download the artifact → fresh venv → install
  the wheel → `syncyard --help`, `syncyard status`,
  config-load import check, version print. Runs for both
  triggers.
- **publish**: `if: github.event_name == 'push'` (i.e. only
  a real tag push, never the manual dry run) → download →
  `pypa/gh-action-pypi-publish@release/v1`, job-level
  `permissions: id-token: write`, `environment: pypi`.

The workflow is inert until a `v*` tag exists (the push
trigger is tags-only; the dispatch trigger requires a
maintainer's hand). No secrets are referenced by any job.

## 7. Docs shape

- `mkdocs.yml`: stock `mkdocs` theme, `strict: true`,
  explicit nav, `exclude_docs` for `github-pitch.md`
  (spec 010 FR-011: prepared, unsent — not a docs page).
  The pre-existing `docs/STANDARD.md` and `docs/MIGRATION.md`
  join the nav; nothing already in `docs/` is moved.
- `.readthedocs.yaml`: config v2, `build.os: ubuntu-22.04`,
  Python 3.12 tool, `mkdocs.configuration: mkdocs.yml`,
  installs `docs/requirements.txt`, `fail_on_warning: true`
  (the local strict build is warning-free, so the setting
  is achievable rather than aspirational).
- `docs/requirements.txt`: `mkdocs>=1.5,<2` — the one docs
  dependency, shared by RTD, CI, and local builds.
- New pages: `index.md`, `installation.md`, `quickstart.md`,
  `concepts.md`, `configuration.md`, `skills.md`,
  `changelog.md` (synced copy). Concepts are written at
  main's level: lanes, the gate, marker verdicts, pause/
  stop are shipped behavior; priorities/focus (013), locks
  (015), adaptive lanes (016) appear once, under a
  "specified, not yet shipped" heading (FR-009).
  Configuration annotates `config/syncyard.example.yaml`
  section by section in prose — deliberately **not** a
  copy of the file, so there is no second drift surface.

## 8. Skills refresh mechanics

Edits land only in the source of truth
(`skills/_source/contract.md`) and the generator's
edition data (`skills/build_skills.py`, CLI-only note);
editions are regenerated with `python3 skills/build_skills.py`
and `skills/test_skills.py` (drift check included) is the
acceptance. The fleet root `SKILL.md` is hand-maintained
and gets its standalone note directly. Contract edits,
kept minimal: the intro's command-form parenthetical and
the Commands preamble line gain the standalone form; the
FR-009 invariant, the marker-verdict wording, and the
FR-005 duty heading — all pinned by the battery — are
untouched.

## 9. Follow-ups flagged, not built

- **`AXIOVEX/gh-syncyard` doctor resolution**: teach the
  extension to find a pip-installed `syncyard` (e.g. via
  `python3 -c "import syncyard.gk"` or the `syncyard`
  console script's location) as one more lookup after its
  existing chain. Flagged in the PR body; that repo is out
  of this change's boundary.
- **First release**: after FR-005, the owner's first `v*`
  tag exercises FR-004 end to end; until then the pipeline
  is proven only to the publish step, and the docs say so.

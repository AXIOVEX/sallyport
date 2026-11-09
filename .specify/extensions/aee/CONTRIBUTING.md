# Contributing

See [development setup](DEVELOPMENT.md) for this repository's own Spec Kit/AEE
installation, dependency names, and the distinction between installed releases
and the product source under test.

Use issues and pull requests. Keep domain logic in
[applied-epistemic-engineering](https://github.com/electrohire/applied-epistemic-engineering)
and this repository a thin adapter to
[Evaluator Contract](https://github.com/electrohire/spec-kit-evaluator).
Preserve distinctions among observed, inferred, asserted, contradicted, and
unsupported evidence. Add regression tests for changed execution or path behavior.

## Reproduce CI

Use Python 3.11 or later in an activated virtual environment:

```bash
python -m pip install --require-hashes -r requirements/dev.txt
python -m ruff check scripts tests
python -m pytest -q
python scripts/python/run_aee.py --help
```

The lock includes the real AEE engine and Spec Kit manifest parser. Tests require
`aee` on PATH and fail when it is missing; integration coverage must not silently
skip because a runtime dependency was omitted. Linux and macOS CI exercise the
symlink and POSIX stub-engine tests; Windows skips those (no shebang stubs, and
symlinks may be disallowed). The CI matrix runs every step on Ubuntu, Windows,
and macOS with Python 3.11, 3.12, and 3.13, the same targets as the engine and
the Evaluator.

For a clean install smoke, clone the Evaluator dependency at the commit pinned
in the CI workflow (currently v1.1.2,
`40413aef4951f4eea947ed2e2291a7b123a6bac3`), then run:

```bash
python scripts/check_install.py --evaluator-dir /path/to/spec-kit-evaluator
```

This uses Spec Kit's own CLI to initialize a disposable project, installs both
extensions, checks registered commands/placeholders and hooks, and runs an
assessment through the installed adapter. It performs no model inference.
The test workflow runs it against the same pinned Evaluator commit. Its
`acceptance` job is the sandbox check on every target. It installs only
`requirements/acceptance.txt` (Spec Kit) into a fresh environment, then runs
`check_install.py --from-url <this commit's GitHub archive> --clean-interpreter`:
specify installs AEE from the immutable archive, and the runner, on a
package-free Python, must install the engine, jsonschema, and the pinned
Evaluator itself (network required). CodeQL and
dependency review run on PRs. OpenSSF Scorecard runs on main/schedule; its latest
main result is not a PR-head analysis.

Regenerate the hash lock after deliberately editing `requirements/dev.in`:

```bash
uv pip compile requirements/dev.in --universal --generate-hashes --python-version 3.11 --output-file requirements/dev.txt
```

Do not commit generated assessment data, credentials, or private evidence.

The suite includes reproducible generated-path fuzzing of the runner's output
boundary. Run it with the full suite, lint, and CodeQL before major releases.
Preserve minimized regression cases for discovered issues. Confirmed exploitable
static/dynamic-analysis findings must be fixed promptly under SECURITY.md.

## Metadata, release, and catalog corrections

Follow Spec Kit's [extension API reference](https://github.com/github/spec-kit/blob/main/extensions/EXTENSION-API-REFERENCE.md)
and [publishing guide](https://github.com/github/spec-kit/blob/main/extensions/EXTENSION-PUBLISHING-GUIDE.md).
Use documented `requires.tools` entries for Python, the `aee` executable, and the
Evaluator extension. The entries are informational; `requires.commands` is not a
supported dependency-enforcement mechanism. Keep the README dependency table and
minimum versions synchronized with commands the adapter actually invokes.

Not following Spec Kit's current procedure means a new submission issue has to
be filed. Release checklist:

1. **Dependencies first.** If this release needs a new spec-kit-evaluator
   release, publish that first (its CONTRIBUTING.md has the same checklist).
   Replace any release-candidate pin in `scripts/python/run_aee.py`
   (`EVALUATOR_VERSION`, `EVALUATOR_ARCHIVE_URL`, `EVALUATOR_ARCHIVE_SHA256`)
   and the CI evaluator checkout `ref` with the published
   `archive/refs/tags/vX.Y.Z.zip`, that archive's SHA-256, and its tag commit.
   Never release with a commit-archive (candidate) pin.
2. **Branch and PR.** Set `extension.version`, date the CHANGELOG entry, and
   keep the README install tag equal to the manifest version (a test enforces
   this). Merge only when `validate` is green, which requires every test and
   acceptance target (Ubuntu, Windows, macOS × Python 3.11-3.13), CodeQL, and
   dependency review.
3. **Tag and publish.** Tag the merge commit `vX.Y.Z` (matching
   `extension.yml`) and **publish** the GitHub release (not a draft; #4516
   failed on a draft release). Never move or reuse a published tag; fix
   forward with a new version.
4. **Test the exact public archive.** Publishing the release runs the
   `Acceptance` workflow against `archive/refs/tags/vX.Y.Z.zip` on every target,
   asserting the installed version (rerun it with **Run workflow**, passing
   `archive_url` and `expected_version`). Continue only when its `gate` job is
   green. Locally:
   `python scripts/check_install.py --from-url https://github.com/electrohire/spec-kit-aee/archive/refs/tags/vX.Y.Z.zip --clean-interpreter --expect-aee-version X.Y.Z`.
   Record the archive SHA-256. If the release is broken, fix forward with a new
   patch release.
5. **File a new
   [Extension Submission](https://github.com/github/spec-kit/issues/new?template=extension_submission.yml)
   issue** stating that it updates the existing `aee` entry. Use the current
   form unchanged and fill in every section: Testing Checklist, Submission
   Requirements, and Proposed Catalog Entry (#4642 failed on a missing
   section). Version, download URL, and Proposed Catalog Entry must name the
   same version (#4447 failed on a mismatch). Commands and hooks counts must
   match `extension.yml` (currently 11 commands, 5 hooks). List the engine
   and Evaluator under Required Tools with their versions, give truthful
   testing details, and name the AI tool used.
6. **Wait for triage.** Do not open a pull request against
   `extensions/catalog.community.json`, and do not request labels. A
   maintainer applies `extension-submission` (you cannot), which runs
   validation and opens the draft catalog PR. If validation fails only because
   its runner could not fetch the archive (#4642, #4661), say so on the issue
   rather than refiling.

For a metadata-only correction to the current release, keep its version and
download URL and explain that the archive is unchanged (#4626).

Prior cases: original submission
[#4565](https://github.com/github/spec-kit/issues/4565) (after #4447 and
#4516 failed validation); updates #4681 (1.0.1), #4890 (1.2.0), #4935 (1.3.0),
[#4937](https://github.com/github/spec-kit/issues/4937) (1.4.0, the current
catalog entry).

By contributing, you agree to the MIT license.

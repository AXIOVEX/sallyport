# Dependency and onboarding audit — 2026-09-17

For the current dependency names, installation policies, and repository tooling,
see [development setup](../DEVELOPMENT.md). The version-specific findings below
are historical evidence, not current installation instructions.

> **Update 2026-10-02:** this audit describes the 1.0.1 release surface
> (six commands), which shipped 2026-09-23. The 1.1.0 branch adds two
> commands (`speckit.aee.route-evidence`, `speckit.aee.report-savings`)
> and four optional tools for a total of eight commands; the findings
> below are retained as the 1.0.1 record, not a description of 1.1.0.

This audit accompanies the unreleased 1.0.1 patch. It does not claim exhaustive
proof of correctness or security. No release tag is moved, no PR is merged, and
no inference is performed by these checks.

## Findings and corrections

| Finding | Correction / validation |
| --- | --- |
| Engine minimum 1.0.0 did not include `gaps` | Require `aee>=1.0.2,<2`; real 1.0.2 engine exercises all operations. |
| Catalog only named `aee`; Evaluator and its source were easy to miss | README dependency table and manifest tool entry name Evaluator; catalog correction uses the official submission process. |
| `requires.commands` implied enforcement the installer does not provide | Remove the unsupported field; explicitly explain informational dependencies and manual installation order. |
| Missing engine silently skipped the only end-to-end test | Hash-lock the engine and Spec Kit parser in dev dependencies; missing engine now fails the integration test. |
| Starter JSON contained an invented observed load-test result | Default to a draft claim with no evidence; test that it requests recovery. |
| YAML template appeared to configure execution | Document that CLI flags are authoritative and YAML is not loaded. |
| Gaps documentation promised a default output file and automatic closure preservation | Document stdout default and explicit `--output` / `--existing`; manual closure does not verify evidence. |
| Graph wording implied automated reference verification | Explain that rendering uses declared relationships; references require inspection. |
| README implied writes only went to extension-owned paths | Disclose explicit in-project gap output and recursive evidence-directory limitations. |
| Existing protected branch requires `validate` | Keep an aggregate `validate` check that fails unless all matrix tests pass. |

Reviewed all six command documents, both templates, README, CONTRIBUTING,
CHANGELOG, SECURITY, runner, tests, manifest, dependency locks, and all four
workflows. Relative documentation links resolve. The benchmark link points to
immutable measured results and explicitly states that no general advantage was
established. Main's latest CodeQL/Scorecard runs passed; no open code-scanning
alerts were returned at audit time. Historical failed runs remain in history.

## Official sources and compatibility

- [Extension API reference](https://github.com/github/spec-kit/blob/5e952140659287106580d32f242ba71365a3862b/extensions/EXTENSION-API-REFERENCE.md)
- [Publishing and update process](https://github.com/github/spec-kit/blob/5e952140659287106580d32f242ba71365a3862b/extensions/EXTENSION-PUBLISHING-GUIDE.md#updating-an-existing-extension)
- [Installer compatibility implementation](https://github.com/github/spec-kit/blob/5e952140659287106580d32f242ba71365a3862b/src/specify_cli/extensions/__init__.py)
- [Original AEE catalog submission](https://github.com/github/spec-kit/issues/4565)

The inspected installer validates Spec Kit version compatibility but does not
install or enforce extension tool dependencies. Since 1.5.0 the AEE runner
therefore installs them itself: the engine (and jsonschema) into a private
runtime environment, and the Evaluator extension through `specify` (see the
README). `spec-kit-evaluator` in
`requires.tools` is an informational extension dependency, not an executable.
Both dependency repositories are linked in the README and manifest comments.

Local validation uses Python 3.12.6, Spec Kit 1.0.8, AEE engine 1.0.2, and Evaluator
1.0.0 at commit `e6e86e1ca2c99c167375953ad8d682913f3de244`. CI now repeats tests,
installation, and sandbox acceptance (Spec Kit alone, then AEE installing its own
dependencies) on Linux, macOS, and Windows with Python 3.11, 3.12, and 3.13. This is tested
coverage, not a claim to have exercised every Spec Kit/agent version allowed by
the compatibility range. The installation smoke uses the Claude integration;
other agents' rendering is outside this run's coverage.

## Verification commands

See CONTRIBUTING for environment setup. Local checks:

```bash
python -m ruff check scripts tests
python -m pytest -q
python scripts/check_install.py --evaluator-dir ../spec-kit-evaluator
```

19 local tests pass. Installation smoke initializes a clean project, installs both
real extensions, verifies six rendered AEE commands, five hooks and resolved
cross-extension placeholders, then executes all six adapter operations. Tests
also verify recovery outcomes, source preservation, tampered-ledger rejection,
and gap generation from empty and passing synthetic test fixtures.

The existing public AEE v1.0.0 archive was downloaded without authentication:
SHA-256 `7be7e11549ae7f9ebfee740af7f61d5b16098f68d89cc454ce51d0134cc18399`.
The downloaded release also passed the six-operation installation smoke with the current engine. Its tag and content remain unchanged. A catalog-only dependency correction can
point at this release; the source patch needs a later reviewed 1.0.1 release.
Consult the PR checks for the final commit's CI, CodeQL, and dependency review.
OpenSSF Scorecard is a main/scheduled workflow, not a PR-head check.

## Remaining limitations

Configuration loading, timestamp collision handling, recursive engine file
validation, automatic dependency installation, and manual gap closure policy are
not newly implemented by this documentation/metadata patch. They are documented
so new users can make informed choices. Generated files must be inspected and
protected appropriately. Green tests and static scans do not certify correctness
of claims, independence of evidence, or absence of every defect.

The first expanded CI run caught a Linux-only executable-bit lint failure on the new smoke script. Its Git executable mode was corrected; no check was removed or weakened. Python dependency update monitoring and the bug-report dependency version prompts were also reviewed.

The final release smoke also installed both dependencies directly with the documented `specify extension add ... --from <published-url>` commands and exercised all six operations. The catalog-only correction is [Spec Kit issue #4626](https://github.com/github/spec-kit/issues/4626); the source patch is [AEE PR #6](https://github.com/electrohire/spec-kit-aee/pull/6). The walkthrough distinguishes the legacy release template from the pending safer template.

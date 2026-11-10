# Applied Epistemic Engineering for Spec Kit

[![CI](https://github.com/electrohire/spec-kit-aee/actions/workflows/test.yml/badge.svg)](https://github.com/electrohire/spec-kit-aee/actions/workflows/test.yml)
[![CodeQL](https://github.com/electrohire/spec-kit-aee/actions/workflows/codeql.yml/badge.svg)](https://github.com/electrohire/spec-kit-aee/actions/workflows/codeql.yml)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/electrohire/spec-kit-aee/badge)](https://securityscorecards.dev/viewer/?uri=github.com/electrohire/spec-kit-aee)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15599/badge)](https://www.bestpractices.dev/en/projects/15599)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

OpenSSF Best Practices Passing criteria: 100% met (self-assessment, verified 2026-10-16).

`spec-kit-aee` applies evidence-centered claim engineering to Spec-Driven Development. It turns explicit requirements, assumptions, hypotheses, decisions, and compliance claims into a challengeable claim graph; distinguishes observations from assertions; preserves contradictions; propagates weakest-link uncertainty; proposes bounded recovery work; and emits results that conform to ElectroHire's [Evaluator Contract](https://github.com/electrohire/spec-kit-evaluator).

The implementation is an original ElectroHire design split into two deliberate layers:

- [`applied-epistemic-engineering`](https://github.com/electrohire/applied-epistemic-engineering) owns the Python model, deterministic engine, CLI, and ledger.
- `spec-kit-aee` is the thin lifecycle adapter, command surface, hooks, and evaluator-result integration.

## What it does

### Names used by this project

| Surface | Name |
| --- | --- |
| This repository / GitHub archive | `electrohire/spec-kit-aee` / `spec-kit-aee` |
| Spec Kit extension ID | `aee` (used by `specify extension add aee`) |
| Engine PyPI distribution | `applied-epistemic-engineering` |
| Engine Python import and CLI | `aee` (`from aee import AEESession`; `aee --version`) |
| Companion repository / extension ID | `electrohire/spec-kit-evaluator` / `evaluator` |

The shared short name does not make the engine and adapter the same package.
Do not use `pip install aee` to install the engine. For this repository's own
Spec Kit/AEE setup, see [development setup](DEVELOPMENT.md).

1. Extracts only explicitly identified claims; ordinary prose is never silently promoted into evidence.
2. Challenges claim atomicity, observability, boundaries, falsifiability, dependencies, contradictions, and evidence independence.
3. Scores evidence using published weights and caps dependent confidence at the weakest supporting claim.
4. Records recovery actions instead of manufacturing certainty.
5. Writes a rich AEE assessment and a provider-neutral Evaluator Contract result.
6. Optionally appends assessments to a tamper-evident SHA-256 ledger.

AEE is a decision aid. It does **not** prove truth, certify compliance, replace domain review, or make a model's self-attestation into observed evidence.

A passing gate means the configured policy accepted the recorded assessment.
A score is not a calibrated probability of truth, and a valid ledger does not
make its contents true. Formal verification can establish a precisely stated
property under explicit assumptions; AEE does not currently provide a theorem
prover or independent certification authority. See the engine's
[names and assurance boundaries](https://applied-epistemic-engineering.readthedocs.io/en/latest/names-and-assurance/).

## Installation

In an initialized Spec Kit project, install AEE by name:

```bash
specify extension add aee
```

**First-time catalog setup:** Spec Kit's default community catalog is
discovery-only, including in Specify 1.2.2. Before the command above, review the
[ElectroHire catalog](catalog.json), which contains only the published AEE and
Evaluator releases, and register it once per project:

```bash
specify extension catalog add https://raw.githubusercontent.com/electrohire/spec-kit-aee/main/catalog.json --name electrohire --install-allowed
```

This explicitly selects ElectroHire's catalog as an installation source; it does
not enable installation from the full community catalog. An existing organization
catalog can instead include the AEE entry. The project catalog configuration
overrides the default catalog stack; teams with custom catalogs should add the
entry to their existing configuration. Then `specify extension add aee` needs no
`--from` URL. Python >=3.11 and Specify must already be installed.

The first AEE operation installs the compatible engine and Python dependencies;
the first `assess` or `compose` also installs the Evaluator companion. No separate
pip or evaluator command is needed. See [installation verification](docs/installation.md)
for the tested versions and exact dependency timing.

### Let your agent handle the workflow

Ask your agent: “Set up AEE for this Spec Kit project, assess the spec's explicit
claims, and explain missing evidence.” After setup, it reads the installed
operation `SKILL.md` files and runs the commands for you. Spec Kit renders eleven
AEE skills and four evaluator skills into the selected integration's directory
(for example `.agents/skills/` for Codex or `.github/skills/` for Copilot).
Reload the agent session if newly installed skills are not discovered yet.

The source [onboarding skill](skills/spec-kit-aee/SKILL.md) helps an agent set up
and find those operation skills. It is included from extension v1.5.2;
operation skills are generated during installation. For direct agent use
without Spec Kit, use the engine's
[standalone skill and three-interface guide](https://applied-epistemic-engineering.readthedocs.io/en/latest/agent/).
The terminal instructions below are also usable by the agent; end users do not
need to memorize CLI or slash-command syntax.

Start with an initialized [Spec Kit](https://github.com/github/spec-kit) project and
Python 3.11 or later. Supported and tested on Linux, macOS, and Windows with Python
3.11, 3.12, and 3.13. Run shell commands below in the project root. Agent slash
commands belong in your coding agent's chat, not in PowerShell or Bash.

| Required component | Install name / version | Repository and purpose |
| --- | --- | --- |
| Spec Kit | `specify-cli>=1.0.0` | [github/spec-kit](https://github.com/github/spec-kit): project and command installation |
| Python | `python>=3.11` | Runs the adapter and engine |
| AEE engine | `applied-epistemic-engineering>=1.6.0,<2`, exposes `aee` | [electrohire/applied-epistemic-engineering](https://github.com/electrohire/applied-epistemic-engineering): deterministic assessment, graph, ledger, and gaps; installed automatically by the runner when missing |
| Evaluator extension (required companion) | extension ID `evaluator`, version `>=1.0.0,<2` | [electrohire/spec-kit-evaluator](https://github.com/electrohire/spec-kit-evaluator): the common evaluator this adapter's results are composed through — composition, reporting, and routing over the engine-produced result; installed automatically by `assess` when missing |
| This adapter | extension ID `aee` | [electrohire/spec-kit-aee](https://github.com/electrohire/spec-kit-aee): eleven agent commands and optional lifecycle hooks |

The engine needs at least 1.6.0: the first engine whose lane-1 outcome is worked out from lane-1 facts alone instead of copying the headline (methodology 2.1), and whose gate can require a lane-1 pass on its own. The runner enforces the range itself by checking `aee --version` against `>=1.6.0,<2`. The Evaluator Contract result is produced by the engine itself, and the evaluator extension is a **required companion** to this adapter: AEE is one evaluator among many by design, and its results are composed through that common evaluator so every evaluator's output is handled the same way. The evaluator extension is a Spec Kit extension, **not** a PyPI package or shell executable.

Spec Kit's `requires.tools` metadata documents dependencies; it does not install them or verify their versions. The runner (`scripts/python/run_aee.py`, which every AEE command calls) therefore installs both itself:

- **Engine.** If the Python running the adapter lacks an in-range `aee` command or importable `aee` package, the first AEE command creates a private environment at `.specify/extensions/aee/.runtime/venv`, installs `applied-epistemic-engineering>=1.6.0,<2` and the evaluator composer's `jsonschema>=4.0` from PyPI into it, and uses it from then on. Each later run checks that environment's `aee` version and imports its packages on its own interpreter; if that check fails (for example after damage), the environment is rebuilt from scratch. Removing the extension removes it. An engine you install yourself in the active Python takes precedence.
- **Evaluator extension.** If `.specify/extensions/evaluator/` is absent, `assess` (or `compose`) downloads the pinned spec-kit-evaluator v1.1.2 archive, verifies its SHA-256, and installs it with `specify extension add evaluator` (the `specify` command must be on PATH).

Set `SPECKIT_AEE_AUTO_INSTALL=0` to disable both installs; an already provisioned runtime environment is still used. When something is missing, the runner then refuses with exit 2 and names the manual install command instead, as it does when an automatic install fails (for example, offline).

For a new project, install Spec Kit using its [official installation
instructions](https://github.com/github/spec-kit#-get-started), then run
`specify init my-project` and `cd my-project`. Choose your agent during setup.
After the one-time catalog setup above, install the adapter in the project;
its runtime dependencies follow automatically on first use:

```bash
specify extension add aee
specify extension list
```

The first AEE command provisions the engine; assess/compose installs the
evaluator companion when missing. Installation needs network access once.
The user does not need a separate pip or evaluator installation. Deterministic
AEE operations make no model API calls; your chosen agent has its own setup.
For offline preparation, standalone composer prerequisites, immutable archive
fallbacks, or testing unreleased source, see [development setup](DEVELOPMENT.md).

## First run and configuration

### Use cases in agent loops

Place AEE after specification to challenge unbounded requirements, after planning
to expose unsupported assumptions, and after implementation to compare completion
claims with actual evidence. Handoffs retain provenance, contradictions, and
uncertainty instead of turning repeated assertions into corroboration. Recovery
work identifies the next bounded check. These controls can help catch unsupported
claims before they propagate; no guaranteed or measured hallucination-reduction
rate is claimed. See the engine's
[use-case and evaluation guide](https://applied-epistemic-engineering.readthedocs.io/en/latest/use-cases/).

The [step-by-step walkthrough](docs/usage.md) covers all eleven commands, expected
exit codes, output files, gap evidence format, and recovery. Start with the
unsupported-claim template; replace its example requirement with your own and add
real evidence only after inspecting it. The published template ships with
draft status and empty evidence. Copying the template is not evidence.

The runner currently reads **CLI flags, not `aee-config.yml` or environment
configuration overrides**. The installed YAML is a reference for agent/operator
choices. See the [configuration table](docs/usage.md#configuration) for exact flags
and defaults. Optional hooks depend on the host workflow emitting their events;
`after_verify` is not guaranteed in every Spec Kit workflow.

## Brief example

Use stable IDs in a specification:

```markdown
## REQ-LATENCY-001 — Search responds within 250 ms

- **Boundary:** Production, p95, 50 requests/second
- **Falsification test:** A 30-minute load test observes p95 above 250 ms
```

Then run:

```text
/speckit.aee.assess phase=after_specify artifact=specs/001-search/spec.md
```

The assessment is stored under `.specify/extensions/aee/assessments/`; the shared result is stored under `.specify/extensions/evaluator/results/`. A claim without inspectable evidence remains unsupported even when generated prose says it passed.

## Reading the result: two lanes, one headline

Every assessment reports three readings together, and the runner's `assess` JSON carries them as a `lane_pair` with a one-line `display`:

```text
headline: iterate — lane 1: pass, lane 2: open (4 draft claim(s); 4 claim(s) below stage 'explained')
```

- **Lane 1 is what the machine checks proved** — scores against the threshold, and any failures the checks found.
- **Lane 2 is where recorded acceptance stands** — draft claims, claims not yet explained or accepted, and similar review state. Acceptance is not a human act by default: in the normal path it is the frozen requirement tests passing, recorded as a checking event (a *test verdict*). A *human act* is recorded only where the work calls for one — an explanation, an acceptance, or a digestion by a named person. When the assessment records such acts, `lane_pair.acceptance_basis` shows each one's basis alongside the pair, with a count line such as `acceptance basis: 1 test verdict(s), 0 human act(s)`.
- **The headline stays cautious until both are done.** A lane-1 `pass` under an `iterate` headline is not a contradiction: it means the machine checks passed and people are still reviewing. Before engine 1.6.0, lane 1 simply copied the headline, so that distinction could not be seen at all.

A CI step that needs the machine checks to have passed on their own — even while review is still open — can gate on lane 1 alone: `run_aee.py gate --input <assessment> --require-lane1-pass` exits 0 when lane 1 passes, 1 when lane 1 needs recovery work, and 2 when the engine blocks or refuses on integrity grounds (for example, a recorded lane-1 outcome the recorded facts do not support). Without the option, `gate` applies the headline outcome as before.

## Runner exit codes

The adapter (`run_aee.py`) uses one code for every error so an exit code alone never mistakes a failure for a scored verdict:

| Code | Meaning |
| --- | --- |
| 0 | Engine outcome `pass`/`warn`, or a successful non-scored operation |
| 1 | Engine scored outcome `iterate`, `clarify`, or `gather_evidence` — a verdict, not an error |
| 2 | An error: runner refusals (missing/out-of-root/symlinked paths, invalid phase, missing `aee`, or an `aee` version outside `>=1.6.0,<2`) and engine-reported input/usage errors. (`gate` also returns 2 for an engine `block` verdict.) |

Runner refusals print `refused: ...` to stderr and never a traceback. JSON summaries always carry `aee_exit_code` and `ok`, and name an output path only when that file was actually written during the run (otherwise the field is `null`).

Assessments produced through the runner carry `metadata.extension_version` (the engine stamps `metadata.engine_version` itself; bare-CLI assessments omit the extension key). The runner stamps before it ledgers — through the engine's own `aee.ledger` — so the ledger's SHA-256 covers the stamped file, and the assess summary reports the post-append chain verification as `ledger_verification` (`null` with `--no-ledger`, never a fabricated verdict). Drafting (`draft-claims`) requires the engine package importable by the runner's Python and an engine build providing `aee.extract.draft_claims_from_spec` (engine builds after 1.4.0); the assess stamp/ledger step likewise uses the importable `aee.ledger`, and refuses cleanly when the package is not importable.

## Commands

| Command | Purpose |
| --- | --- |
| `speckit.aee.draft-claims` | Draft atomic claims from a spec's requirements for human ratification (born `draft`, never upgraded by the tool) |
| `speckit.aee.assess` | Run the complete deterministic AEE pipeline |
| `speckit.aee.challenge` | Focus on breakpoints and recovery work |
| `speckit.aee.trace` | Render dependencies and review provenance |
| `speckit.aee.verify` | Verify ledger integrity |
| `speckit.aee.gate` | Return CI-friendly exit status from an assessment; with `--require-lane1-pass`, require lane 1 (machine verification) to pass on its own |
| `speckit.aee.review` | Diff two assessments into a materiality-filtered review queue |
| `speckit.aee.gaps` | Generate or update the gap register from a verification matrix and test evidence |
| `speckit.aee.route-evidence` | Route oversized logs and sources into raw evidence slices for AEE gates |
| `speckit.aee.report-savings` | Report measured RTK and Headroom token savings, never estimated |
| `speckit.aee.message-security` | Check a spec's authority surface and run the planted-injection battery gate (deterministic screener, no engine or model calls) |

The message-security component is documented in [message security](docs/message-security.md): the authority-surface section every spec for a system that acts on read content must carry, the planted-injection battery gate and its bars, and the challenge record format.

Compose AEE with other evaluators using `python .specify/extensions/aee/scripts/python/run_aee.py compose --phase <phase>`. It runs the evaluator's Python composer with dependencies AEE provisions. `/speckit.evaluator.compose` gives the same composition, but its Bash variant needs `jq` and its Python variant needs `jsonschema` in the project's or PATH's Python, which AEE does not install. Render the result with `/speckit.evaluator.report`, or use `/speckit.evaluator.route` for the next-phase model recommendation. Invocation separators vary by integration; Spec Kit renders the installed command files appropriately.

## Files written

```text
.specify/extensions/aee/
├── assessments/aee-<phase>-<timestamp>.json
├── challenges/aee-<phase>-<timestamp>.json
├── graphs/aee-claims-<timestamp>.mmd
├── evidence/route notes with file paths, line ranges, and backend used
├── reports/measured token-savings reports
├── telemetry/shell-calls.jsonl
├── message-security/battery-report.json and challenge-records.jsonl (when the battery gate is run with output paths)
└── ledger/epistemic-ledger.jsonl

.specify/extensions/evaluator/results/
└── aee-<phase>-<timestamp>.json
```

The command adapter rejects paths outside the project root and symlinked path components supplied to it. Source artifacts are read-only. Assessments, challenges, graphs, and ledgers use extension-owned directories; `gaps --output` can write another explicitly chosen in-project path. Use a trusted evidence directory: the adapter does not recursively validate every file the engine discovers inside it.

## Benchmarks and limitations

The [benchmark repository](https://github.com/electrohire/spec-kit-aee-benchmark)
now uses established HaluEval/RAGTruth labels and FeatureBench/SWE-EVO coding
graders. Its completed model-only detector subsets scored83.3%/80.8%; these do
not establish an AEE/Spec Kit treatment effect or token savings. Matched treatment
and coding economics results remain pending. Earlier convenience-task experiments
and failed runs are preserved as historical evidence, not actively maintained
benchmark suites. See [BENCHMARKS](BENCHMARKS.md) for the harness I/O diagram,
interpretation limits, rebuild requests and exact-build release gating.

AEE scores declared structured evidence; it does not independently run a load test,
fetch every evidence reference, or prove an assertion true. Markdown extraction
collects explicit IDs, boundaries, dependencies, and falsifiers; use JSON to supply
structured evidence. Review findings and references before acting on an outcome.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `aee` is missing or out of range | Check that automatic installation is enabled and has network access (the refusal names the failure), or install engine `>=1.6.0,<2` into the Python that runs the adapter and confirm with `aee --version`. |
| Evaluator commands are missing | Run `assess` once with `specify` on PATH, or install `evaluator` from the explicit archive URL. Then run `specify extension list` and restart the coding agent if needed. |
| No project / unknown slash command | Run `specify init` first and use the command spelling rendered for your selected agent. |
| Exit 1 from an assessment | Read the result: `iterate`, `clarify`, and `gather_evidence` are expected recovery outcomes, not installation errors. |
| Editing YAML has no effect | Pass the corresponding CLI flag; the adapter does not load that YAML. |
| No gap file appears | `gaps` prints to stdout unless `--output` is supplied. |
| A claim is unsupported | Inspect its ID, boundary, falsification test, and evidence; do not relabel generated assertions as observations. |
| Path is refused | Use a real file inside the project, with no symlinked path components. |

## Development

Follow [CONTRIBUTING.md](CONTRIBUTING.md) for the hash-locked environment, real-engine
integration tests, Spec Kit installation smoke, and release/catalog update process.
See [SECURITY.md](SECURITY.md) for the trust boundary and [CHANGELOG.md](CHANGELOG.md)
for released versus pending changes.

## License

MIT © 2026 ElectroHire Inc.


## Benchmark evidence and releases

[Established benchmarks and release gates](BENCHMARKS.md) explain the upstream suites, separate reliability/coding/economics findings and exact-build promotion. Current completed detector results are model-only; no AEE treatment benefit or token payback is established yet. Rebuilds queue content-bound requests; releases remain blocked without complete audited matched evidence.

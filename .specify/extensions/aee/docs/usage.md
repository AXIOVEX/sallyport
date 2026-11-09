# First assessment, recovery, and verification

Agent route: ask “Use AEE to assess this feature and explain what evidence is
missing.” The installed assessment `SKILL.md` guides the agent through the same
steps below; it executes the CLI and reports artifacts for you. The adapter's
[onboarding skill](../skills/spec-kit-aee/SKILL.md) explains skill discovery and
dependency setup. For a standalone engine agent workflow without Spec Kit, see
the [engine's guide](https://applied-epistemic-engineering.readthedocs.io/en/latest/agent/).

Complete the README installation, including the one-time publisher catalog setup, first (`specify extension add aee`; the runner
installs the engine and the Evaluator extension itself on first use). All shell
commands here run from your initialized project root. No paid inference is needed
for these deterministic adapter operations.

## Start with an unsupported claim

Copy `.specify/extensions/aee/templates/aee-claims.json` to `claims.json`. Use
`cp` on macOS/Linux or `Copy-Item` in PowerShell. Edit the stable ID, requirement,
boundary, and falsification test for your project. The shipped template is born
`draft` with empty evidence and `insufficient_evidence` uncertainty by design.
Do not retain the sample load-test text as your own result. The resulting unsupported claim should ask for recovery; a passing outcome is not the goal of this
first smoke check.

```bash
python .specify/extensions/aee/scripts/python/run_aee.py assess --input claims.json --phase after_specify
```

The printed JSON lists `assessment`, `evaluator_result`, `ledger`,
`aee_exit_code`, and `lane_pair` — the two lanes shown as a pair
alongside the headline, in one line (`display`): lane 1 is what the
machine checks proved, lane 2 is where human review and acceptance
stand (`open`, with the recorded reasons, or `complete`), and the
headline stays cautious until both are done. When the assessment
records stage events, the pair also carries `acceptance_basis`: the
basis each recorded act stands on — a test verdict (the frozen
requirement tests passed, recorded by the tool) or a human act (a
named person recorded an explanation, acceptance, or digestion).
Acceptance is not a human act by default; human acts are the
reserved cases. A lane-1 `pass` under
an `iterate` headline means the machine checks passed while people
are still reviewing; it is not a contradiction. The printed JSON
also lists — after a ledgered assess — `ledger_verification`, the
runner's own post-append verification of the ledger chain (`valid`, `entries`,
`head_hash`, `errors`; see "Verify the ledger" below). The assessment's
`metadata` carries `engine_version` (stamped by the engine) and
`extension_version` (stamped by the runner before ledgering, so the ledger's
SHA-256 covers the stamped file). Open both result files and inspect their `outcome`, findings, and
recovery actions. AEE scores the evidence you supply; it does not independently
verify the content of every evidence reference. Do not invent an observed result
to make an assessment pass. Keep source artifacts unchanged during assessment.

Agent equivalent: `/speckit.aee.assess artifact=claims.json phase=after_specify`.
Use your installed agent's rendered command spelling if it uses a different separator.

## Draft claims from a spec

Hand-writing a claims bundle is the slow way in. Draft them from the spec's
own requirements instead:

```bash
python .specify/extensions/aee/scripts/python/run_aee.py draft-claims --spec specs/001-search/spec.md
```

`--spec` also accepts the feature directory (its `spec.md` is used). The
bundle lands as `aee-claims.json` beside the spec unless `--output <path>`
says otherwise. Every drafted claim is born `status: draft` with a single
`asserted`/`model` evidence item — the requirement text itself, as modelled
by the engine's drafting helper — and a proposed falsification test. A
requirement with nothing testable to anchor a falsifier to gets an explicit
`TODO` falsifier, and strict loading (`aee validate`, any strict assess)
rejects the bundle until a human replaces every TODO with a real test.
Where one requirement's text explicitly references another, the draft
carries a `depends_on` proposal; the runner's JSON summary lists them under
`depends_on_proposals` with the source references they came from.

Nothing in this flow ratifies a claim: no path in the runner or the command
sets a draft above `draft` or upgrades its evidence. Ratification is your
recorded edit to the bundle — completing falsifiers, confirming or removing
`depends_on` proposals, and changing statuses by hand. Drafting is
deterministic, makes no model calls, and requires an engine build whose
`aee.extract` provides the drafting helper (builds after 1.4.0); an older
engine makes the runner refuse with exit 2 rather than improvise claims.

## Challenge, repair, and reassess

```bash
python .specify/extensions/aee/scripts/python/run_aee.py challenge --input claims.json --phase after_specify
python .specify/extensions/aee/scripts/python/run_aee.py graph --input claims.json
```

Challenge writes failure modes and bounded recovery proposals under
`.specify/extensions/aee/challenges/`; graph writes Mermaid under `graphs/`.
Choose a recovery, gather independently inspectable evidence, and update the
claims JSON explicitly. Preserve counterevidence. Reassess with the same phase and
compare outcomes and evidence, not just confidence scores. Graph edges represent
declared relationships and are not proof of causality or reference validity.

For example, an actual test report can be represented as an evidence entry with
`ref`, `kind`, `direction`, `source_quality`, `description`, and `source_id`. Use
`kind: observed` only for an observation you actually inspected; model-written
claims remain `asserted`. See the [engine documentation](https://github.com/electrohire/applied-epistemic-engineering)
for its JSON model and scoring rules. Markdown extraction does not populate
structured observed evidence from ordinary prose.

## Verify the ledger and apply a gate

```bash
python .specify/extensions/aee/scripts/python/run_aee.py verify
python .specify/extensions/aee/scripts/python/run_aee.py gate --input "<assessment-path-from-first-command>"
python .specify/extensions/aee/scripts/python/run_aee.py gate --latest after_specify
python .specify/extensions/aee/scripts/python/run_aee.py gate --input "<assessment-path-from-first-command>" --require-lane1-pass
```

`gate --require-lane1-pass` asks a narrower question than the plain
gate: did lane 1 — the machine checks — pass on its own facts,
whatever lane 2 (human review and acceptance) still has open? The
runner forwards the option to the engine and preserves its exit
codes exactly: 0 when lane 1 passes, 1 when lane 1 is in the
`iterate` family, and 2 when the engine blocks or refuses on
integrity grounds (for example, a lane-1 outcome recorded in the
file that the recorded facts do not support). Without the option,
the gate applies the headline outcome, which stays cautious until
both lanes are done.

Replace the angle-bracket placeholder with the actual rich assessment path.
`gate --latest <phase>` gates the assessment named by the pointer `assess`
maintains (`assessments/latest-<phase>.json`); it refuses when no assessment
exists for the phase and prints the assessment's age to stderr — a stale
assessment gates exactly as readily as a fresh one, so read the age before
trusting the verdict.
Verify reports chain validity, entry count, head hash, and errors. A valid chain
establishes continuity of retained records, not truth, identity, trusted time, or
completeness before the first record.

The assess run already performs this verification itself: the documented loop
is assess → verify-ledger, and the assess JSON summary's `ledger_verification`
field is the verdict from immediately after the append (FR-8.2). The standalone
`verify` above stays useful for re-checking the chain at any later time. With
`assess --no-ledger`, `ledger_verification` is `null` — no verdict is reported
for a ledgering that did not happen. The assessment JSON is a working copy; the
ledger entry recording its SHA-256 is the record of what was assessed.

Assessment/gate exit codes:

| Code | Meaning |
| --- | --- |
| 0 | `pass` or `warn`; inspect warnings before proceeding |
| 1 | `iterate`, `clarify`, or `gather_evidence`; recovery is needed |
| 2 | `block` (gate), or an error — never a scored assess/challenge verdict |

Errors include runner refusals (missing, out-of-root, or symlinked paths; an invalid phase; a missing or out-of-range `aee` engine, or a missing evaluator extension for `assess`, when the runner's automatic installation of it is disabled with `SPECKIT_AEE_AUTO_INSTALL=0` or fails) and engine-reported input/usage errors. Runner refusals print `refused: ...` to stderr and never a Python traceback. Do not interpret every nonzero process code as a scored
assessment outcome. Do not globally suppress failures in CI to accommodate an
expected recovery outcome: assert the expected outcome and inspect the artifacts.

The JSON summaries printed by `assess`, `challenge`, `graph`, and
`gaps --output` always include `aee_exit_code`, `timed_out`, and `ok`. An output path
is named only when the file was actually written during that run;
otherwise the field is `null` (the runner never emits a phantom path).
`assess` also names its `latest_pointer` (see the gate section above).
`assess` also reports `evaluator_extension_present`: the Evaluator
Contract result itself is produced by the engine, and the separate
evaluator extension is a required companion, because composing
results through the common evaluator is part of the standard flow.
When it is not installed in the project, `assess` installs the pinned
release through `specify extension add evaluator` first, and refuses
(exit 2, naming the missing extension) only if that is disabled or
fails.

For multiple evaluator results, run
`python .specify/extensions/aee/scripts/python/run_aee.py compose --phase after_specify --strategy strict`
(the evaluator's Python composer, using the jsonschema AEE provisions), or
`/speckit.evaluator.compose phase=after_specify strategy=strict` if your project's
script variant's prerequisites (`jq` for Bash, `jsonschema` for Python) are met; then `/speckit.evaluator.report phase=after_specify format=terminal`.
These agent commands come from the evaluator extension. AEE does not
silently compose results or run every other evaluator for you.

## Message security: authority surface and the injection battery

For a system that acts on content it reads, the spec carries an
`## Authority Surface` section — who may instruct the system, through
which channels, which actions and destinations are authorized —
completed from the shipped template
(`.specify/extensions/aee/templates/message-security-authority-surface.md`).
That section is the principal record the message-security checks grade
against: an action or destination not listed there is not authorized.

The planted-injection battery is the component's assessment gate. It
runs the vendored deterministic screener (no engine, no model calls)
over 106 fixtures — 56 planted instructions in real carrier text and
50 legitimate controls:

```bash
python .specify/extensions/aee/scripts/python/message_security_battery.py --output .specify/extensions/aee/message-security/battery-report.json --records-output .specify/extensions/aee/message-security/challenge-records.jsonl
```

Exit codes: 0 the gate passes, 1 a bar is not met (a verdict), 2 an
error such as a missing or malformed corpus (`refused: ...`, never a
traceback). The bars, stated in the runner: the epistemic layer
challenges at least 95% of the injections (55 of 56 when vendored)
and zero legitimate controls (0 of 50); notes (29 of 50 controls) are
reported, never gated. The records file holds one challenge record
per noted or challenged fixture, in the format defined by
`templates/message-security-challenge-record.schema.json` — span
hash, source class and id, the claim, the check that fired, the
verdict, the action taken, and the timestamp; records are appended,
never edited. See [message security](message-security.md) for the
full method, how to re-run the gate, how to add fixtures, and what
the gate does not establish.

Agent equivalent: `/speckit.aee.message-security`.

## Generate a gap register

Create `docs/verification-matrix.md` with engine-compatible test IDs:

```markdown
| Test ID | Requirements | Layer | Gate |
| --- | --- | --- | --- |
| T-API-001 | Valid request returns 200 | integration | gate-1 |
```

Create an `evidence/` directory. After actually running your test, a JSON report
can use this shape (the `pass` below is a format illustration, not test evidence):

```json
{"results": [{"test_id": "T-API-001", "status": "pass"}]}
```

```bash
python .specify/extensions/aee/scripts/python/run_aee.py gaps --matrix docs/verification-matrix.md --evidence evidence --output GAPS.md
```

Inspect open and closed entries. An empty evidence directory leaves the example
gap open. Without `--output`, the engine prints the register and creates no gap
file. To retain closed entries on regeneration, explicitly add `--existing GAPS.md`
and review whether their original evidence still applies. The register tracks
coverage; it does not rerun tests or certify their results.

Manual `--close GAP-001` changes the existing register selected by `--output`.
The engine does not validate new passing evidence in this mode; inspect evidence
before choosing manual closure. Prefer generation from recorded test results.
(Engine 1.0.3 and later refuse to close a gap ID that does not exist; earlier
releases reported success anyway.)

## Route evidence and report token savings

Two commands support the optional token-economy tools (`rtk`, `headroom`,
`token-router`, `ollama` — see `docs/token-economy.md`). Neither is required;
both degrade gracefully when a tool is absent.

`speckit.aee.route-evidence` triages an oversized log or source file into raw
evidence slices before an assessment consumes it:

```bash
python .specify/extensions/aee/scripts/python/aee_token_economy.py --project-root . route --file build.log --mode error_log
```

Gates always reason over the raw slices, never over router prose or compressed
summaries. Without a router backend, deterministic line ranges are used.

`speckit.aee.report-savings` reports **measured** savings from RTK and
Headroom, each on its own line, never estimated and never double-counted:

```bash
python .specify/extensions/aee/scripts/python/aee_token_economy.py --project-root . report
```

## Configuration

`aee-config.yml` is currently a reference for the agent/operator, not an automatically
loaded runner configuration. The runner does not merge local YAML or `SPECKIT_*`
environment overrides. Use explicit flags:

| Reference setting | Runner setting | Default / scope |
| --- | --- | --- |
| `threshold` | `assess/challenge --threshold` | `0.70` |
| `ledger.enabled` | `assess --no-ledger` disables it | Enabled for assess only |
| `ledger.actor` | `assess/challenge --actor` | `spec-kit-aee` |
| `phases.*` | Host workflow/extension hook configuration | Optional hooks; not read by the runner |
| `gap_register.matrix_path` | `gaps --matrix` | Required |
| `gap_register.evidence_dir` | `gaps --evidence` | Required |
| `gap_register.output_path` | `gaps --output` | Omitted means stdout |

`--project-root` is a global option and goes **before** the operation, for example
`python <runner-path> --project-root <project-dir> assess --input claims.json`.
Input and output paths are resolved relative to that root. Generated
assessment/challenge/graph filenames carry a UTC timestamp with microsecond
and random suffixes, so two runs in the same second never overwrite each
other's files. Archive results before force-reinstalling an extension. Do
not commit private evidence or generated ledger data merely because it is
under `.specify/`.

`--timeout <seconds>` is also global (default 600): every engine subprocess
runs under it, and on expiry the runner kills the engine and returns exit 2
with `timed_out: true` in the JSON summary (`aee_exit_code: null`) — a
timeout is an error, never a scored verdict.

## Hook expectations

Hooks are optional prompts; each names the command it runs but binds no
arguments, so the agent supplies them from this table:

| Hook | Command | Expected artifact | Phase / inputs |
| --- | --- | --- | --- |
| `after_specify` | `speckit.aee.assess` | the feature's claims bundle (or spec, via `draft-claims` first) | `--phase after_specify` |
| `after_plan` | `speckit.aee.assess` | the plan's claims bundle | `--phase after_plan` |
| `after_tasks` | `speckit.aee.assess` | the task breakdown's claims bundle | `--phase after_tasks` |
| `after_implement` | `speckit.aee.assess` | the implementation claims bundle | `--phase after_implement` |
| `after_verify` | `speckit.aee.gaps` | verification matrix (default `docs/verification-matrix.md`) + evidence dir (default `evidence/`) | stop when the matrix is absent; evidence must be real in-project files (symlinks are refused) |

After any assess hook, the freshest verdict for the phase is the one the
`latest-<phase>` pointer names; gate with `gate --latest <phase>` rather
than guessing a filename.

See the README troubleshooting table and [security boundary](../SECURITY.md).

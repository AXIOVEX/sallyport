# Changelog

All notable changes follow Semantic Versioning.

## 1.7.0 - 2026-10-29

- Add the message-security component (specs/007-message-security):
  everything a system reads is data, and only principals instruct.
  It ships an authority-surface template
  (`templates/message-security-authority-surface.md`) — the principal
  record a project completes per spec: who may instruct the system,
  through which channels, which actions and destinations are
  authorized — wired into the `speckit.aee.assess` command as a gap
  check and into the new command below.
- The planted-injection battery becomes an assessment gate: 106
  fixtures (56 planted instructions in real carrier text, 50
  legitimate controls) vendored under
  `assets/message-security/`, the deterministic screener (structural
  layer L1, epistemic layer L2) at
  `scripts/python/message_security_screen.py`, and a gate runner at
  `scripts/python/message_security_battery.py`. Bars, stated in the
  runner: L2 challenges at least 95% of injections (measured 55 of
  56) and zero legitimate controls (measured 0 of 50); notes
  (29 of 50 controls) are reported, never gated. Exit codes 0 pass,
  1 bar not met, 2 error as a clean refusal.
- The challenge record format is standardized:
  `templates/message-security-challenge-record.schema.json` (JSON
  Schema) plus a worked example record. Fields: span hash, source
  class and id, the claim as extracted, the check that fired, the
  verdict (`note`/`challenge`), the action taken, and the timestamp.
  Records are append-only, one JSON object per line; the battery
  runner emits them with `--records-output`.
- New command `speckit.aee.message-security` (eleven commands
  total): checks the active spec's authority surface, runs the
  battery gate, and reports missed fixtures and challenged controls
  by name. `scripts/check_install.py` exercises the installed
  battery gate end to end.
- The gate measures the deterministic screener offline; it does not
  measure whether a model in context would refuse a planted
  instruction, and the changelog entry claims nothing more.

## 1.6.0 - 2026-10-20

- Assessments now show the two lanes as a pair (specs/006-lane-outcomes).
  The runner's `assess` JSON gains a `lane_pair` block with a one-line
  reading such as `headline: iterate — lane 1: pass, lane 2: open
  (4 draft claim(s); 4 claim(s) below stage 'explained')`. Lane 1 is
  what the machine checks proved; lane 2 is where human review and
  acceptance stand; the headline stays cautious until both are done.
  Before this change the headline was the only reading shown, so work
  the machine checks had passed still read as a lone `iterate`
  whenever lane 2 was open.
- The lane pair also shows the basis of acceptance (Spec 006
  amendment, autonomous-execution doctrine v2). When an assessment
  records stage events, `lane_pair.acceptance_basis` lists each
  recorded act with the basis it stands on — a test verdict (the
  frozen requirement tests passed, recorded by the tool) or a human
  act (a named person recorded an explanation, acceptance, or
  digestion) — with a count line. Acceptance is not presented as a
  human act by default; human acts are the reserved cases. An
  assessment recording no acts carries `acceptance_basis: null`.
- `gate` gains `--require-lane1-pass`: the runner forwards the option
  to the engine's gate, which exits 0 when lane 1 passes on its own
  facts, 1 when lane 1 needs recovery work, and 2 when it blocks or
  refuses on integrity grounds. Without the option the gate applies
  the headline outcome exactly as before.
- The engine requirement moves to `applied-epistemic-engineering
  >=1.6.0,<2`. Engine 1.6.0 is the first release whose lane-1 outcome
  is worked out from lane-1 facts alone instead of copying the
  headline (methodology 2.1), and the first whose gate offers the
  lane-1 option above. The runner refuses older engines with exit 2,
  as before.
- `scripts/check_install.py` now checks the lane pair end to end: the
  installed `assess` summary must carry a `lane_pair` that matches
  the installed assessment's own recorded lanes.
- Document installation by name after one-time publisher catalog registration;
  clarify that the current community catalog remains discovery-only and that
  AEE provisions runtime dependencies on first use.

## 1.5.2 - 2026-10-16

- Pin evaluator 1.1.2, including its fail-closed invalid-encoding fix, with
  verified public archive checksum; add the live OpenSSF assessment badge.

- Add reproducible input fuzzing of the runner's output-path boundary on every
  supported platform, and document the pre-release analysis requirement.

- Add an agent onboarding/router skill, document ordinary-language workflows,
  and verify both extensions' generated agent skills in installation acceptance.

- Clarify the engine distribution/import/CLI and extension IDs, assurance
  boundaries, and this repository's current Spec Kit/AEE development setup.

- `check_install.py` prints the engine and jsonschema versions the runner
  provisioned and the Evaluator version it installed, so acceptance logs
  record the resolved dependency versions (test harness only).

## 1.5.1 - 2026-10-15

Documentation only; the runner and its dependency pins are unchanged.

- Fix: the `speckit.aee.draft-claims` command document still told the agent
  to confirm `aee` was available and importable before running. An agent
  following it would stop before the runner could install the engine itself
  (the same issue 1.5.0 fixed for assess, gaps, and review). It now matches
  those documents.
- `docs/usage.md` no longer asks for an "engine environment" to be active;
  `docs/dependency-audit.md` records that AEE installs its own dependencies
  and the current CI coverage.
- README states the supported platforms (Linux, macOS, Windows; Python
  3.11-3.13).

## 1.5.0 - 2026-10-15

- Self-provisioned dependencies: `specify extension add aee` is now
  the only install step. Spec Kit's `requires.tools` is informational,
  so the runner installs what the manifest requires itself. When the
  invoking Python lacks an in-range engine (CLI and importable package),
  the runner builds a private environment at
  `.specify/extensions/aee/.runtime/venv`, installs
  `applied-epistemic-engineering>=1.5.0,<2` into it, and re-runs itself
  there. When `assess` finds the evaluator extension missing, it
  downloads the pinned spec-kit-evaluator v1.1.1 archive, verifies its
  SHA-256, and installs it through `specify extension add evaluator`
  (served over loopback, because the community catalog is
  discovery-only). `SPECKIT_AEE_AUTO_INSTALL=0` restores the previous
  refusals (exit 2), which now name the manual command.
- The runtime environment also carries `jsonschema>=4.0`, the evaluator
  Python composer's hard dependency. A new `compose` operation runs that
  composer (the evaluator's own code, output and exit codes unchanged) on
  the runner's interpreter. Composition then needs neither `jq` (the
  Bash variant) nor jsonschema in a project or global Python, which
  Spec Kit's rendered `speckit.evaluator.compose` would require, and the
  assess command document routes composition through it.
- Runtime reuse probes the environment's interpreter, importing
  `aee.ledger` and `jsonschema` and checking both versions, not only the
  CLI version. An environment failing the probe is rebuilt with
  `venv --clear`. With `SPECKIT_AEE_AUTO_INSTALL=0` a provisioned runtime
  that passes the probe is still used; the switch stops installs only.
- Evaluator pin: spec-kit-evaluator
  [v1.1.1](https://github.com/electrohire/spec-kit-evaluator/releases/tag/v1.1.1)
  release archive with its verified SHA-256. That release fixes the composer
  failing to parse on Python 3.11, which made `compose` fail there with the
  1.1.0 pin.
- CI `acceptance` job on all targets: Spec Kit alone (new hashed
  `requirements/acceptance.txt`), AEE installed by `specify` from this
  commit's GitHub archive (`check_install.py --from-url`), and the runner on a
  package-free Python installing the engine, jsonschema, and the pinned
  Evaluator, whose version the check asserts. The required `validate` gate
  needs both the test and acceptance matrices. The same reusable
  `acceptance.yml` runs on every published release against the exact public
  tag archive (asserting the version via `--expect-aee-version`) and can be
  dispatched with any archive URL.
- `check_install.py --clean-interpreter` runs the installed runner on a
  fresh package-free Python with no `aee` on PATH (only `specify`), so
  the engine, jsonschema, and the evaluator must all be installed by
  AEE itself. CI now runs the same 3 OS x Python 3.11/3.12/3.13 matrix as
  the engine (macOS included), with every test and install check on
  each target.
- Fix: the evaluator refusal and the assess command document told
  users to run `specify extension add spec-kit-evaluator`. That ID does
  not exist; they now give the working `specify extension add evaluator
  --from <archive>` command.
- The gaps, review, and assess command documents no longer tell the
  agent to stop when `aee` is absent; that stop prevented automatic
  provisioning.
- `check_install.py` takes `--evaluator-dir` as optional and also checks
  that `assess` reinstalls a removed evaluator extension. CI pins
  spec-kit-evaluator v1.1.1.
- Dev lock: specify-cli 1.2.0 (from 1.0.13), ruff 0.17.1, hypothesis
  6.169.4, compiled with `--universal` so the hashed lock installs on
  both the Linux and Windows CI legs. Fixed the new ruff FURB162
  finding (Python 3.11+ parses `Z` offsets natively).

## 1.4.0 - 2026-10-12

- Design correction (specs/005-evaluator-required): the
  spec-kit-evaluator extension is now a **required** companion,
  declared `required: true` in `extension.yml` and enforced by the
  runner — `assess` refuses with exit 2, naming the missing extension
  and how to install it, when `.specify/extensions/evaluator/` is
  absent from the project, and writes nothing. In 1.3.0 the extension
  was declared optional and only the contract was required, which
  made consistency opt-in; AEE is one evaluator among many by design,
  and its results are composed through the common evaluator so every
  evaluator's output is handled the same way. The pair is one system.
  `check_install.py` now also exercises the refusal end to end
  (evaluator extension removed → `assess` exits 2 naming it), README,
  the assess command document, and the usage docs teach the pair as
  one system, and the evaluator install reference moves to its
  v1.1.0 release archive.

## 1.3.0 - 2026-10-12

- RT3 remediation (specs/rt3-remediation): `gaps` refuses symlinked or
  out-of-root evidence in a runner-side pre-flight instead of letting it
  close gaps (RT3-05); generated assessment/challenge/graph filenames
  carry microsecond + random suffixes so same-second runs never collide
  (RT3-08); every engine subprocess runs under `--timeout` (default
  600 s) with a distinct timed-out verdict — exit 2, `timed_out: true`,
  never a scored outcome (RT3-11); `assess` maintains a
  `latest-<phase>` pointer and `gate --latest <phase>` gates it with an
  age note (RT3-13); token-router discovery is confined to the project
  root/parent, router ranges are strictly typed, and the route record
  names the router path + SHA-256 (RT3-10); token-economy `shell` runs
  in the project root under a timeout and redacts secret-looking argv
  values from telemetry (RT3-09); `route` refuses files over a 256 MB
  byte cap instead of loading them whole (RT3-18); `_safe_output`
  returns resolved paths in both adapters (RT3-17); command templates
  quote path placeholders verbatim (RT3-12); README/CHANGELOG/usage
  drift corrected and pinned by tests (RT3-14); `check_install.py`
  checks are explicit (survive `python -O`) with an optional
  `--from-archive` mode (RT3-16); the config template header states
  that no script loads it (RT3-19).
- Spec 004 P5 vocabulary alignment (specs/004-vocabulary-alignment):
  the engine floor moves to the realigned engine
  (`applied-epistemic-engineering>=1.5.0,<2` — methodology 2.0, Spec
  004 stage vocabulary) in the runner, `extension.yml`, and README,
  and the runner's startup enforcement refuses older engines by name;
  the `assess` JSON gains a `verdict_display` block copied from the
  stamped assessment (outcome, methodology version, stage-capped
  `reported_statuses`, `stage_caps`, the lane pair, `not_understood`),
  so the extension displays exactly what the bare CLI reports on the
  same bundle; the runner drives the engine's recorded `--lenient`
  compatibility mode for assess/challenge/graph when the installed
  engine advertises it, so pre-realignment bundles (no `stage`
  field) load with the default on the record instead of being
  refused; the assess/gate command documents teach the realigned
  vocabulary (stages, exposition, headline decomposition roles,
  reported-not-declared statuses, lane-paired reporting,
  `not_understood` with every verdict, unjudged distinct from
  abstained).

## 1.2.0 - 2026-10-06

- Spec 002 Phase 4 (extension parts): new `speckit.aee.draft-claims` command
  (ten commands total) and runner `draft-claims` subcommand over the engine's
  `aee.extract` drafting helper — drafts are born `draft` with
  `asserted`/`model` evidence, TODO falsifiers pass through untouched for
  strict loading to reject, and `depends_on` proposals from explicit spec
  cross-references are surfaced in the summary; no tool path ratifies or
  upgrades a draft. The runner now stamps `metadata.extension_version` into
  assessments before ledgering (through the engine's own `aee.ledger`, so
  the ledger SHA-256 covers the stamped file) and reports the post-append
  chain verification as `ledger_verification` in the assess summary
  (`null` under `--no-ledger`).
- Wire the engine's epistemic-ledger ports into the assess flow: the runner's
  `assess` gains `--policy` (per-claim ACCEPT / CHALLENGE / ABSTAIN verdicts,
  with `--min-independent-sources` / `--contested-threshold` overrides) and
  `--reliability <table.json>` (measured per-source reliability blended into
  scoring); a new `speckit.aee.review` command diffs two assessments into a
  materiality-filtered review queue via the runner's `review` subcommand.
- Require `applied-epistemic-engineering>=1.4.0,<2` (dev pin 1.4.0): the CLI
  surface for the gates, reliability, and review landed in engine 1.4.0, on
  top of the 1.1.0-1.3.0 library ports.

## 1.1.0 - Released as part of 1.2.0 (no separate tag)

- Integrate the token-economy policy into AEE core: declare optional `rtk`,
  `headroom`, `token-router`, and `ollama` tools; add `speckit.aee.route-evidence`
  (local triage to raw slices; assess gates reason over slices only, never over
  router prose) and `speckit.aee.report-savings` (measured-only, RTK and Headroom
  reported separately, never estimated or double-counted). The tools stay external
  runtimes; AEE cores the policy and degrades gracefully when a tool is absent.
- Add `scripts/python/aee_token_economy.py` (tool status, evidence routing with
  deterministic fallback, measured savings aggregation, shell telemetry wrapper)
  with fail-closed secret-file refusal and path safety mirroring `run_aee.py`.
- Document headroom integration points for long-horizon sessions
  (`docs/token-economy.md`): proxy/wrap/SharedContext hook-in points and the hard
  rule that gates see raw slices, never compressed summaries.
- Fix the branch's own checks to match the surface it ships: manifest tests and
  `check_install.py` now assert eight commands and the required/optional tool
  split (they still asserted the 1.0.1 surface and failed).
- Token-router discovery anchors on `--project-root`, not the caller's working
  directory; the `shell` wrapper validates `--telemetry` before running the
  wrapped command instead of raising after its side effects.

## 1.0.1 - 2026-09-23

- Declare Python, AEE engine 1.0.2 (needed for `gaps`), and Evaluator Contract dependencies using Spec Kit's documented tool metadata.
- Replace the unsupported `requires.commands` field with explicit installation guidance; dependencies are informational and are not auto-installed by Spec Kit.
- Add newcomer setup, configuration limitations, troubleshooting, and a link to the benchmark's measured results and failures.
- Install the actual engine in hash-locked CI, validate with Spec Kit's own manifest parser, and exercise every adapter operation without silently skipping missing-engine integration tests.
- Document the exact AEE claim field vocabulary in `speckit.aee.assess` so agents emit schema-valid claims on the first attempt.
- Trim the extension description to under 100 characters per the Spec Kit extension publishing guide.
- Add deterministic `extension.yml` and documentation compliance tests.

## 1.0.0 - 2026-09-09

- Add six Spec Kit AEE commands and five optional lifecycle hooks.
- Integrate `applied-epistemic-engineering` 1.x with Evaluator Contract 1.0.
- Add path-safe execution, structured claim template, ledger verification, and CI validation.
- Add the `speckit.aee.gaps` command and `after_verify` hook to generate and maintain the gap register from a verification matrix and test evidence.
- Add dependency review, Dependabot, OpenSSF Scorecard, and hash-locked CI installs.
- Document the private disclosure process and response timeline.

- Add immutable benchmark build requests and audited exact-build release gating; preserve native CI/security controls and unpublished feature branches. No release or empirical improvement is asserted.

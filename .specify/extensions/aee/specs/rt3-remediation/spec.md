# Feature Specification: RT3 Extension Remediation

**Feature Branch**: `impl/rt3-remediation`

**Created**: 2026-10-12

**Status**: Implemented on this branch (2026-10-12). Before: 12 problem
claims at 1.0/high, outcome `iterate` (`assessment.json`, after_specify).
After: 12 fix claims at 1.0/high with all score components 1.0
(`claims-after-implement.json`, `assessment-after-implement.json`,
after_implement) — the outcome remains `iterate` because the challenge
projection still raises decomposition failure modes on the compound
claims (33 after vs 28 before; 0 claims below the 0.70 threshold in
both runs), not because any fix is unscored or failing. Suite: 92 → 119
passed, 3 skipped; `ruff check scripts tests` clean; check_install
passes in local, `--from-archive`, and `python -O` modes. Not merged —
the coordinator merges.

**Input**: The RT3 red-team suite against this extension
(`~/workspace/aee-redteam/rt3/FINDINGS.md`, 21 enumerated findings) and the
Spec 003 Phase 9 close-out re-run (`~/workspace/aee-closeout/results/rt3-verdicts.md`),
which verdicted 9 findings INVERTED and 12 STILL-LIVE on main `527feae`.
Spec 003 cross-referenced RT3 only; it never implemented extension fixes.
Owner directive 2026-10-12 ("Do all that remains") authorizes this remediation.

Every still-live finding was re-verified against current main by the
implementer before any fix: RT3-05, RT3-08, RT3-09, RT3-10, RT3-11 by live
probe (probe scripts run 2026-10-12, outputs quoted in `claims.json`);
RT3-12, RT3-13, RT3-14, RT3-16, RT3-17, RT3-18, RT3-19 by direct code/doc
inspection of the same tree the probes ran against.

## Scope

In scope: the 12 still-live findings RT3-05, RT3-08, RT3-09, RT3-10, RT3-11,
RT3-12, RT3-13, RT3-14, RT3-16, RT3-17, RT3-18, RT3-19.

Out of scope: the 9 inverted findings (no regression may re-open them; the
runner's exit-code contract 0/1/2 and JSON summary contract are preserved);
engine changes (RT3-05's long-term engine-side `GapEngine` symlink discipline
is a companion engine issue, noted in FR-1); Spec 004 P5 vocabulary alignment
(sequenced after this work).

## FR-1 — `gaps` evidence confinement (RT3-05)

**Current state (verified).** `_gaps` validates only the evidence *directory*
chain (`_safe_dir`). A symlinked evidence file inside it
(`evidence/linked.json` → a file outside the project root) is followed by the
engine's `rglob` + `read_text`, and its contents close gaps: live probe —
runner exit 0, register `Open: 0 | Closed: 1` on out-of-root data.

**Requirements.**
- FR-1.1 Before invoking the engine, the runner walks the evidence tree and
  refuses (exit 2, named offenders) any symlink component or file, and any
  entry whose resolved path escapes the project root.
- FR-1.2 `commands/speckit.aee.gaps.md` states the check is runner-side
  pre-flight; the companion engine issue (`GapEngine` should accept a
  no-follow discipline) is recorded in spec notes.

## FR-2 — Collision-proof generated filenames (RT3-08)

**Current state (verified).** Assessment/challenge/graph filenames carry a
second-resolution stamp (`%Y%m%dT%H%M%SZ`). Live probe: two distinct
assessments in one second share one filename; the ledger attests both
hashes while the file on disk holds only the second.

**Requirements.**
- FR-2.1 Every generated filename (assess, challenge, graph) carries a
  collision-proof suffix beyond second resolution (microseconds plus a
  per-process random component), keeping lexicographic sortability by time.
- FR-2.2 Two distinct same-second runs produce two distinct files, each
  matching its ledger record.

## FR-3 — Subprocess timeouts with a distinct timeout verdict (RT3-11)

**Current state (verified).** Every operation subprocess in `run_aee.py`
runs without a timeout (only the 30 s version/help capability probes have
one). Live probe: against an engine stub that sleeps 60 s, the runner did
not return within 8 s.

**Requirements.**
- FR-3.1 The runner takes `--timeout` (default 600 s) and passes it to every
  operation subprocess (assess, challenge, graph, gaps, gate, review,
  verify); the token-economy `shell` gains a timeout as well (FR-5).
- FR-3.2 On expiry the runner kills the child and returns exit 2 — never a
  scored verdict — printing `refused: ... timed out after Ns ...` to stderr;
  JSON-summary subcommands also print their summary with
  `aee_exit_code: null`, `timed_out: true`, `ok: false`, and no path that
  was not actually written.

## FR-4 — Token-router confinement and strict range types (RT3-10)

**Current state (verified).** `_find_token_router` takes the
`TOKEN_ROUTER_HOME` candidate first with no confinement; a fake router
outside the project root was executed via `sys.executable`. Its emitted
range `[[true, 3]]` was accepted as start=1 (`isinstance(v, int)` admits
`bool`). The route record names no router path or hash.

**Requirements.**
- FR-4.1 An env-supplied router home is honored only when its resolved
  `scripts/router.py` sits inside the project root or its parent (the same
  trust domain as the built-in candidates); otherwise it is ignored and
  routing falls back deterministically.
- FR-4.2 Range validation rejects `bool` (and non-`int`) values strictly.
- FR-4.3 The route record includes the resolved router path and its
  SHA-256 when the token-router backend runs.

## FR-5 — Token-economy `shell` confinement (RT3-09)

**Current state (verified).** `_shell` runs the caller's argv with no
`cwd` (caller's directory, not the project root), no timeout, and writes
the full argv — including secret-looking values — verbatim to telemetry.
Live probe: `pwd` printed the caller's directory, an outside-root file was
printed, and `token=SUPERSECRETARG123` sits in `shell-calls.jsonl`.

**Requirements.**
- FR-5.1 `shell` runs with `cwd` = project root and a timeout
  (`--timeout`, default 600 s); on expiry it reports a timeout and
  returns exit 2.
- FR-5.2 Telemetry redacts secret-looking argv values (key=value secrets
  and known secret patterns are replaced with `«redacted»`); byte counts
  and exit codes are still recorded.
- FR-5.3 `docs/token-economy.md` states the trust model honestly: `shell`
  is a telemetry wrapper, not a sandbox — it confines cwd and lifetime,
  not what the command itself may read.

## FR-6 — Resolved output paths (RT3-17)

**Current state (verified).** `_safe_output` in both scripts returns the
unresolved `candidate` after the inside/symlink checks, leaving a
check-then-use window between validation and the engine's later write.

**Requirements.**
- FR-6.1 `_safe_output` returns `candidate.resolve(strict=False)` after
  the final chain check, in `run_aee.py` and `aee_token_economy.py` alike.

## FR-7 — `gate --latest` and hook binding (RT3-13)

**Current state (verified).** `gate --help` lists only `--input`;
`extension.yml` hooks are bare optional prompts with no phase/artifact
binding; nothing checks assessment freshness before gating, and no
latest pointer exists.

**Requirements.**
- FR-7.1 `assess` maintains a pointer file
  `.specify/extensions/aee/assessments/latest-<phase>.json` naming the
  assessment it just wrote (a plain JSON pointer, not a symlink), and the
  summary reports it.
- FR-7.2 `gate` accepts `--latest <phase>` (mutually exclusive with
  `--input`), resolving the pointer, refusing (exit 2) when no pointer
  exists for the phase, and reporting the assessment's age in the gate
  JSON-free passthrough (stderr note) so a stale gate is visible.
- FR-7.3 Each command file documents the artifact + phase its hook
  expects (a per-hook expectation table in `docs/usage.md`, referenced
  from `extension.yml` hook descriptions), including the `after_verify →
  gaps` matrix/evidence defaults and stop condition.

## FR-8 — Command template quoting (RT3-12)

**Current state (verified).** All command `.md` templates interpolate
`<artifact>`/`<path>` placeholders unquoted into bash blocks; the runner
itself is injection-safe (list-form exec), so the risk is word-splitting /
substitution in the agent's shell at the template layer.

**Requirements.**
- FR-8.1 Every path/artifact placeholder in every command template is
  double-quoted, and `speckit.aee.assess.md` carries the guardrail line:
  quote paths verbatim; never eval or split user-supplied arguments.

## FR-9 — Docs and install truth (RT3-14, RT3-16, RT3-18, RT3-19)

**Current state (verified).** README installs `v1.0.1.zip` while
`extension.yml` says 1.2.0 and says "This branch prepares 1.1.0
(unreleased)"; CHANGELOG heads both 1.2.0 and 1.1.0 "Unreleased";
`check_install.py` verifies only the local `--dev` path with bare
`assert`s (stripped under `python -O`); `route` reads the whole target
file into memory before any gate; `route-evidence.md` describes the
deterministic fallback as `rg`/`grep` (the implementation is pure
Python); `report-savings.md` specifies `used=<yes|no|n/a>` while the
helper emits JSON booleans; the config template header names only
`run_aee.py` although neither script loads the file.

**Requirements.**
- FR-9.1 README installs the archive tag matching `extension.yml`
  (1.2.0), drops the stale "1.1.0 (unreleased)" text, and its other
  version mentions (e.g. the v1.0.1 template note) match reality;
  CHANGELOG stops calling shipped work "Unreleased": the 1.2.0 section
  carries its tag date (tag `v1.2.0`, 2026-10-06), and the 1.1.0 section
  is labelled as released as part of 1.2.0 (no separate 1.1.0 tag
  exists). A test asserts the README install tag == manifest version.
- FR-9.2 `check_install.py` replaces every bare `assert` with explicit
  checks that raise with messages (surviving `python -O`) and gains an
  optional `--from-archive <zip>` mode that installs the extension from
  a release archive instead of a local directory.
- FR-9.3 `route` streams the file with a hard byte cap
  (256 MB default): an over-cap file is refused with exit 2 and a
  slicing hint; line counting no longer loads multi-GB files whole.
- FR-9.4 `route-evidence.md` describes the deterministic fallback as
  pure-Python literal match windows; `report-savings.md` documents the
  actual JSON shape (`"used": true/false`, `"saved_tokens": <number|"n/a">`).
- FR-9.5 The config template header states that **no** script loads the
  file — it is operator reference for both adapters.

## Acceptance

Each RT3 probe scenario, re-run against the fixed tree, inverts: symlinked
evidence refuses with exit 2 and no gap closes; same-second distinct
assessments land in distinct files; a hung engine yields the timeout
verdict in bounded time; a fake out-of-root `TOKEN_ROUTER_HOME` is never
executed and bool ranges are rejected; `shell` runs in the project root,
times out, and redacts secrets from telemetry; `gate --latest` gates the
pointer's assessment. Every scenario is also a regression test in the
extension suite, which stays green and grows.

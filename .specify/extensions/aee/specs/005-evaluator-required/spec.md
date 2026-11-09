# Feature Specification: Evaluator Extension Required (Spec 005)

**Feature Branch**: `impl/evaluator-required`

**Created**: 2026-10-12

**Status**: Implemented on this branch (2026-10-12). Claims:
ER-005-01 0.858 → 0.900; ER-005-02 0.808 → 0.850; ER-005-03
0.733 → 0.775; ER-005-04 0.808 → 0.850 (real engine 1.5.0,
before → after implementation); bundle outcome `iterate` at both
assessments — the claims sit at stage `created`, so the engine
reports them `draft` however they are labelled, recorded as-is.
Suite: 127 passed at the 1.3.0 baseline → 133 passed (repo-pinned
layout, engine 1.5.0 from PyPI), `ruff check scripts tests` clean;
`scripts/check_install.py` passes end to end against the real
spec-kit-evaluator v1.1.0 checkout, including the new absence
exercise. Not merged — the coordinator merges.

**Input**: Owner decision, 2026-10-12: the evaluator extension should be
required, not optional — "the whole idea was to have a common evaluator
that other evaluators (which is what AEE is) can use and all be
consistent."

## Why

AEE is one evaluator among many, by design. Its `assess` writes an
Evaluator Contract result so that results from different evaluators can
be composed and compared the same way. In 1.3.0 that consistency was
opt-in: `extension.yml` declared the spec-kit-evaluator extension
`required: false`, and a project could run `assess` without it — nothing
was composed, and nothing said so. The declaration also disagreed with
this repo's own practice: `scripts/check_install.py` has always
installed both extensions and asserted both are enabled.

This spec makes the pair one system in declaration and in enforcement:
the evaluator extension is required, and `assess` — the command that
produces the result meant for composition — refuses without it.

## Requirements

### ER-01 — assess refuses without the evaluator extension

With `.specify/extensions/evaluator/` absent from the project, the
runner's `assess` refuses before running the engine or writing any
output: exit 2 through the runner's established clean-refusal channel
(`refused: ...` on stderr, no traceback, no JSON summary), and the
message names the missing extension and how to install it
(`specify extension add spec-kit-evaluator`). No assessment, evaluator
result, ledger entry, or latest pointer is written. The presence check
keeps the existing RT3-04 sampling point (before any output path is
created, so the runner's own `results/` directory can never fake an
installation).

Scope: `assess` only. It is the result-producing command.
`draft-claims`, `challenge`, `graph`, `gate`, `verify`, `review`, and
`gaps` do not emit Evaluator Contract results and are not blocked —
`gate` works on AEE's own assessments. The refusal message states that
composition through the evaluator extension is part of the standard
flow.

### ER-02 — with the evaluator extension installed, assess is unchanged

With the extension installed, `assess` behaves exactly as in 1.3.0: same
outputs, same exit-code passthrough, same `evaluator_extension_present:
true` in the JSON summary. The assess command document presents
composition through the evaluator extension as the standard next step
when other evaluators ran at the same phase.

### ER-03 — the pair is declared and taught as one system

`extension.yml` declares `spec-kit-evaluator` with `required: true`
(version `>=1.0.0,<2` unchanged), and its comment states why: AEE is one
evaluator among many, results are composed through the common evaluator
so every evaluator's output is handled consistently, and the runner
enforces the requirement. The README requirements table and install
section and the assess command document teach the pair as one system;
no user-facing surface calls the evaluator extension optional or
not-required.

### ER-04 — check_install fails without the evaluator extension

`scripts/check_install.py` keeps asserting both extensions are enabled
after install, and additionally exercises the enforcement end to end:
with the evaluator extension removed from its scratch project, the
installed runner's `assess` must exit 2 naming the missing extension —
otherwise the script fails.

## Version

This correction ships as extension version 1.4.0 (`extension.yml`
`version:` and the README install archive tag move together; the
README↔manifest consistency test pins them equal). The CHANGELOG
records the correction plainly under `## 1.4.0 - 2026-10-12`.

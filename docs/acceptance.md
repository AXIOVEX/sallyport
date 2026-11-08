# Acceptance gates (spec 036)

"100% production stable" is not a declaration anyone makes;
it is the name of a gate set, and the gate is mechanical.
Spec 036 defines it. A release that has not passed every gate
below has not passed, whatever any summary says.

## The traceability matrix

A matrix is **generated from the spec files** (by
`syncyard-tracegen`), never hand-maintained: every functional
requirement in specs 034-039 maps to at least one named test
case and exactly one primary acceptance mode — `test`,
`human-as-oracle`, or `gate`. CI fails if an FR has no test, a
named test does not exist in the suite, a test names no FR, or
an acceptance mode is missing.

The ratified package baseline: **126 FRs, 158 named test
cases** (034: 14, 035: 24, 036: 18, 037: 12, 038: 70,
039: 20), zero FRs without a named test, zero without an
acceptance mode.

## Coverage bars

Rust core line coverage is measured per crate and reported as
measured — never rounded up. The bars (ratchetable only
upward, by owner ruling):

| Crate | Bar |
|---|---|
| syncyard-contract | 95% |
| syncyard-daemon | 90% |
| syncyard-cli | 85% |
| syncyard-agent | 85% |
| syncyard-tray | 80% |

A release below any bar fails the gate regardless of test
pass state.

## Field-simulation suites

The suites simulate field behavior, not just source shape:

- **Tray rendering fixtures** — captured window/icon fixtures
  checked visually, per spec 035.
- **Runner-log noise fixtures** — real runner chatter
  (including cancellation dumps) must never surface as an
  agent error.
- **Ghost-record fixtures** — queue/GitHub pairs reproducing
  the ghost geometry.
- **Wave fixtures** — every state writer's save path under
  simulated write denial.
- **Fault-injection fixtures** — the daemon killed at every
  dispatch-protocol step, proving zero double and zero lost
  dispatches.
- **Mixed-version fixtures** — component pairs at train N and
  N-1 per the compatibility matrix.

## Defect-to-test rule

No field defect closes without a failing-before test added in
the same change, **named after the defect**. A test that never
failed on the defective code does not satisfy the rule.
Defect-named tests already in the package include
`laptop_two_tray_fight_2026_11_06` (the two-tray singleton
proof) and `single-path-publisher-defect-2026-11-06`.

## The release gate

No release — any component, any train — ships unless **all**
of these hold:

1. The traceability matrix is green.
2. All field-simulation suites pass.
3. Coverage bars are met, as measured.
4. The fault-injection suite passes.
5. The release's compatibility matrix is published with it.
6. One owner-manual recovery drill per critical component has
   been run and recorded for the train (owner + written
   runbook, no agent involvement).

Plus the all-OS acceptance the owner set for this program:
the stack proven on the desktop, the laptop, the always-on
server (including its deferred Docker-venue battery legs),
the offsite GPU host, a macOS host per spec 039, and aarch64
release artifacts shipping — and, once green, the fleet's own
programs migrating in their pre-registered order, iSMART
first, under close monitoring.

## Where the gate stands (2026-11-08, measured)

Honest state, from the program's own records:

- Test suite: **125 passed, 0 failed** across the workspace
  (P4 closeout), clippy and fmt clean.
- Traceability: **75 of 158 named tests in suite, 83 missing,
  0 orphans.** The matrix is red, as it must be at this phase;
  the missing cases land with the phases that implement their
  surfaces (transport, macOS host, field verification).
- Coverage bars: **not yet measured** — no coverage tooling
  was wired in the build phases. The bars remain the
  acceptance definition, unmet until measured and passed.
- Not yet done: the live GUI tray shell, the GitHub transport,
  all live-host field verification, the mini-server Docker
  legs, the macOS host, and the cutover rehearsal.

Nothing on this page is a claim that the gate has passed. It
is the definition of what passing means.

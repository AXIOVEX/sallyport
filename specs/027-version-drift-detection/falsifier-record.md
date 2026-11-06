# Spec 027 falsifier record (T004 evidence)

The spec's falsifier has two legs. Both were exercised
locally against the shipped code; the scenario battery
(gatekeeper/test_spec027.py) carries them as standing
coverage, so they re-run in CI on every change.

## Leg 1 — an old build shows behind within one sweep

Scenario battery, behind scenario: a tray beat at build
0.4.2.0 with the manifest stubbed at the module seam to the
published 0.4.4.0. After ONE sweep pass the host record
carries tray_drift state `behind`, `tray_drift` is ledgered
exactly once across repeat sweeps carrying both versions
(build=0.4.2.0, release=0.4.4.0, host=desktop), and the
status host line renders
`tray drift BEHIND (running 0.4.2.0, current 0.4.4.0)`.
The catch-up scenario then moves the beat to 0.4.4.0 and
ledgers exactly one `tray_current`. Result: TEST_SPEC027 OK
(12/12 checks).

## Leg 2 — a broken manifest read never shows current

Scenario battery, unknown scenario: a host first judged
`current` on a good read; the manifest read is then stubbed
to fail. The next sweep moves the host to `unknown` (the
failed read revokes the claim rather than coasting on it),
ledgers no second `tray_current`, ledgers the read failure
as `api_error` (where=tray_release_manifest), and the
status line renders the word `unknown`. The judge uses only
the current sweep's read — the tray-release.json cache is a
record of the last read, never a source judged from.

## FR-004 leg — one stamp moving alone fails the battery

In a scratch copy of runner/tray (outside the repo tree):
- BuildId alone changed 0.4.4.0 → 0.4.5.0:
  `FAIL spec 027: all extracted version stamps are equal
  (one version truth)`, battery verdict TRAY_SOURCE FAILURES.
- All five stamps changed together to 0.4.5.0: the spec 027
  extraction checks PASS. (The battery as a whole still
  reports FAILURES in that scratch state from the
  pre-existing spec 021 literal pins to 0.4.4.0 — a real
  version bump updates those pins in the same commit, as
  every tray release to date has done; the spec 027 check
  itself is value-independent by design.)

## Full local battery at the implementation head

All suites ci.yml runs, foreground, per-suite verdicts:
gatekeeper test_config / test_fr013 / test_golden /
test_spec013 / test_spec011 / test_spec025 / test_spec027 /
test_spec015 / test_spec016 / test_fleet_status /
test_install_publisher / test_dispatch_isolation /
test_per_host_cwd / test_profile_suggest /
test_holds_per_host — ALL PASS / OK; runner
test_workflow_contract, test_transport_smoke,
tray/test_tray_source (TRAY_SOURCE OK), guard source —
ALL PASS / OK; backchannel test_bc, test_chaos
(TEST_CHAOS: ALL PASS), test_agent_source — OK; skills
ALL PASS; the ci.yml gatekeeper offline smoke (submit,
duplicate refused rc=3, adopt, status, cancel) — SMOKE OK.
Not run locally: the spec 017 packaging/docs CI steps (pip
installs; untouched by this change) and the tray-ci compile
(Windows-only; this change touches no tray source, only its
source battery).

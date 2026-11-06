# Spec 028 falsifier record (T008 evidence)

The spec's falsifier has two venue legs and the T008 battery
pins. The pins were exercised locally against the shipped
code by mutation: each mutation was applied to a working
tree, the tray source battery run, the expected check
observed FAILING, and the mutation reverted with the
battery re-verified OK (151 PASS / 0 FAIL, TRAY_SOURCE OK)
after every restore. The two venue legs cannot run on this
Linux VM (they need the Windows tray and a live runner
root); they are named below as deferred, with their venue.

## Pin leg 1 — the atomic save (FR-001)

Battery check inverted to require the pre-028 in-place
write (`File.WriteAllText(FilePath, Serialize())`):
battery exit 1, exactly the check "the settings save is
atomic: temp file, replace, previous kept as .bak" FAILS.
The shipped Save writes FilePath + ".tmp" and File.Replace
keeps the previous file as tray-settings.json.bak.

## Pin leg 2 — the per-machine lane set (FR-002/FR-004)

A `gpu` lane appended to the shipped laptop profile
(runner/tray/profiles/laptop/tray-profile.json): battery
exit 1 on "the laptop profile names only its own lanes
(long and quick) — no gpu on a machine with no GPU". The
mutation was run in both orders (profile edited before and
after the rest of the implementation) and reproduced both
times; the desktop profile's own checks do not catch it,
so the pin is isolated to the laptop entry as designed.

## Pin leg 3 — exact listener identity, tray side (FR-007)

`ListenerInDir` reverted to a StartsWith prefix match on
the listener's executable path: battery exit 1 on "tray
listener identity is the exact runner directory". The
shipped code compares the listener exe's parent directory
to the instance directory with string.Equals.

## Pin leg 4 — exact listener identity, watchdog side (FR-007)

runner/runner-watchdog.ps1's Test-ListenerAlive reverted
to a StartsWith prefix match: battery exit 1 on the
watchdog exact-directory check. The shipped code uses
(Split-Path -Parent $p.ExecutablePath).Equals($Dir,
OrdinalIgnoreCase).

## Deferred venue leg A — kill mid-save (FR-001)

Kill the tray mid-save against a laptop profile; the next
start loads either the old settings whole or the new
settings whole, never a torn file, and the fallback taken
is named in the log via TraySettings.LastNote. Venue: the
tray-ci compile/smoke on this PR covers the compiled-floor
path only (its scratch has no profile); the kill-mid-save
leg itself belongs to the parent's field deployment on
tristen-laptop, where a profile is installed. NOT RUN by
this builder; stated as deferred, not as passed.

## Deferred venue leg B — one of three normal instances (FR-005)

Stop one of the desktop's three normal-lane runner
listeners (normal, normal2, normal3); the tray restarts
exactly that instance, under its own restart cap, and the
other two are undisturbed. Venue: the parent's field
deployment on tristen-desktop after merge. NOT RUN by this
builder; stated as deferred, not as passed.

## Self-caught defect — ParseInto brace (found by tray-ci)

The first pushed head (f7de866) failed the tray-ci compile:
ParseInto, extracted from Load's try block, carried one
closing brace too many (the try block's and the method's),
closing TraySettings a member early (CS1518 from line 863,
CS1022 at the class end). The local tray battery is
text-based and could not see it; the compile leg is the
payload that caught it, which is what it exists for.
Fixed in 39c1c95 by removing the extra brace and dedenting
the method body; verified locally by a full-file
string/comment-aware brace-balance scan (final depth 0,
no premature class close, 13 top-level classes) and the
tray battery (TRAY_SOURCE OK). The tray-ci re-run on the
fixed head is the confirming payload.

## Local battery state

- Baseline (unmodified main, db2ae56, scratch clone):
  23/23 suites PASS, foreground records, committed as
  specs/028-settings-resilience/battery-baseline-main.txt.
- Changed tree: the suites that create no temp files PASS
  (test_tray_source 151 checks, test_workflow_contract,
  test_guard_source, test_skills, test_bc,
  test_agent_source). Tempfile-dependent suites were
  UNVERIFIED LOCALLY on both trees during a VM
  write-denial wave (the fleet's known intermittent
  platform defect): they fail identically on the baseline
  tree with the wave's tempfile FileNotFoundError, so the
  wave, not the change, is the cause. The PR's CI (ci +
  tray-build) is the payload venue for those suites.

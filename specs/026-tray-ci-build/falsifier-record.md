# Spec 026 falsifier record (T004)

Run 2026-11-06 by the spec 026 builder, against the workflow
this spec ships (`.github/workflows/tray-ci.yml`, job
`tray-build`). Rule under test, from the spec: introduce a
deliberate compile error, and separately a build whose
identity stamps disagree with the source, on a branch; if
either PR shows a green tray check, the spec has failed.

## Control: the real PR is green

- PR #78 (`feat/026-tray-ci-build`, head b9683a5): `ci` PASS
  (run 42224213877, 5m17s) and `tray-build` PASS (run
  42224214118, 21 s).
- Compile payload: `TRAY_CI_COMPILE_OK ... bytes=171520`
  (171,520 bytes, matching the field-built 0.4.4.0 exe size).
- Identity payload: `TRAY_CI_IDENTITY_OK version=0.4.4.0
  product=Syncyard Tray company=Axiovex Systems, LLC`.
- Smoke payload: the built exe launched from scratch state
  (pid 996); within about 2 s its log carried the start lines
  and its first heartbeat existed, reading `"host":
  "ci-smoke"`, `"build": "0.4.4.0"`, `"pid": 996`, all four
  lanes present and unfaulted; the job then stopped it:
  `TRAY_CI_SMOKE_OK`.

## Falsifier 1: deliberate compile error

- Branch `falsify/026-compile-error` (deleted after the run),
  PR #79 (closed, never merged). One injected line in
  `runner/tray/SyncyardTray.cs` after the BuildId const:
  `private const string FalsifierBroken = ;`
- Result: `tray-build` FAIL (run 42224254936, 14 s). csc
  reported `SyncyardTray.cs(1289,48): error CS1525: Invalid
  expression term ';'` and `error CS1002: ; expected`; the
  step closed with `TRAY_CI_COMPILE_FAIL csc exited with
  code 1`.
- The same PR's `ci` check was green (run 42224254985): the
  source battery cannot see a compile error, which is the
  gap this spec exists to close. PASS (the falsifier bit).

## Falsifier 2: identity stamps disagree with the source

- Branch `falsify/026-identity-mismatch` (deleted after the
  run), PR #80 (closed, never merged).
  `AssemblyFileVersion` set to `9.9.9.9` while
  `AssemblyVersion` and the `BuildId` stayed `0.4.4.0`.
- Result: `tray-build` FAIL (run 42224761983, 17 s). The
  compile itself succeeded (`TRAY_CI_COMPILE_OK ...
  bytes=171520`); the identity step failed the build with
  `TRAY_CI_IDENTITY_FAIL source stamps disagree:
  AssemblyVersion=0.4.4.0 AssemblyFileVersion=9.9.9.9
  BuildId=0.4.4.0`.
- The same tamper also fails the pre-existing pins on sight:
  the PR's first `ci` run (42224290248) failed in the tray
  source battery ("assembly version is the 0.4.4.0 build
  stamp"; "version truth moves together"), before the
  falsifier branch carried the workflow file. The red
  `tray-build` above is from the run after the workflow was
  added to the branch, so the verdict belongs to this
  spec's own check. PASS (the falsifier bit).

## Notes on method

- Each falsifier branch was based on the spec branch, so the
  workflow under test is byte-identical to the one PR #78
  ships. PR #79 needed no second commit: the spec branch
  already carried the workflow when it was created. PR #80
  was created from main before that ordering was pinned
  down, so its first runs show no `tray-build` check at all;
  the workflow was then added to the branch unchanged and
  the check ran and failed as recorded.
- Neither falsifier PR was merged. Both branches were
  deleted from the remote on close and locally afterwards.

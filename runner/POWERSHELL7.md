# PowerShell 7 is required on every Windows host

Owner order, 2026-11-08: **Syncyard uses PowerShell 7 (`pwsh`) for
everything on Windows. Windows PowerShell 5.1 (`powershell.exe`) is
not supported anywhere in Syncyard.**

## Why a rule, and why enforcement in code

Scripts written for PowerShell 7 behave differently under 5.1
(encoding defaults, error handling, available cmdlets, JSON and
native-command semantics), and a job that silently ran under 5.1
produces evidence nobody can trust at face value. The dangerous
part is that the old setup could fall back silently: GitHub
Actions' `shell: pwsh` falls back to Windows PowerShell 5.1 when
`pwsh` is not installed, so a missing install never announced
itself. Every layer below therefore fails closed and says so.

## The enforcement layers

1. **Transport workflows** (`runner/workflows/run.yml`,
   `runner/workflows/wsl.yml`, and their copies in the dispatch
   door repo `electrohire/desktop-runner`; the retired desktop-label
   copies under `runner/legacy/` are history, not live
   transports):
   - a `shell: cmd` preflight step, which cannot itself run under
     any PowerShell, checks for
     `%ProgramFiles%\PowerShell\7\pwsh.exe` and verifies it reports
     edition Core and major version 7 or later. Missing or wrong:
     the job fails before the payload with a `SYNCYARD_PS7_MISSING`
     marker naming the fix.
   - the Execute step asserts the same about its own shell as its
     first action, resolves the payload shell as the canonical
     install path (`%ProgramFiles%\PowerShell\7\pwsh.exe`) or,
     where the host carries the Store/MSIX install (which registers
     only on the user PATH — the laptop's install is this flavor,
     verified 2026-11-08), the `pwsh` found on PATH, and verifies
     the resolved executable's own major version before invoking
     the payload through it. The executable name `pwsh.exe` is
     itself part of the guarantee: 5.1's executable is
     `powershell.exe`, a different name, so a PATH hit can never
     be 5.1 — and the version verification runs anyway.
2. **Host scripts** (`setup-runner.ps1`, `runner-watchdog.ps1`,
   `guard/install-guard.ps1`, `tray/install-tray.ps1`,
   `tray/tray-watchdog.ps1`, the gatekeeper Windows probe) carry
   `#Requires -Version 7.0` as their first line, so 5.1 refuses
   them with a clear error instead of running them differently.
3. **The tray watchdog scheduled task**
   (`tray/tray-watchdog-task.xml`) invokes `pwsh.exe` directly
   (resolved on the owner's PATH, which covers both the MSI and
   the Store/MSIX install flavors).
4. **Backchannel examples** (README and RUNBOOK) call the
   PowerShell 7 path under `/mnt/c/`. text still names
   `powershell.exe`; that spec is ratified history, and these
   living documents govern current practice.
5. **A source test** (`runner/test_powershell7_policy.py`, run in
   CI with the other runner tests) fails if any workflow gains a
   `shell: powershell`, if a Windows transport loses its preflight
   or its in-step assertion, if the payload invocation goes back
   to a bare `pwsh` PATH lookup, or if `powershell.exe` reappears
   as a launch target in the runner tree.

## Installing: ensure-powershell7.cmd

`runner/ensure-powershell7.cmd` is the bootstrap. It is a cmd
script on purpose: it runs before PowerShell 7 exists, so it
cannot be a.ps1, and it never invokes 5.1 either.

- No arguments: check; if missing, ask (default yes), install,
  re-check.
- `/y`: unattended yes. `/check`: check only, never installs.
- Install route: `winget install --id Microsoft.PowerShell`
  first; if winget is absent or fails, it downloads the pinned
  official MSI from the PowerShell GitHub release, verifies the
  SHA256 against that release's own `hashes.sha256`, and installs
  with `msiexec`. The pinned version is a variable at the top of
  the script; bump it deliberately.
- Verdicts are markers: `SYNCYARD_PS7_OK version=<v> path=<p>`
  (exit 0), `SYNCYARD_PS7_MISSING` (exit 10 in check mode or when
  declined), `SYNCYARD_PS7_INSTALL_FAIL` (exit 2 or 3).

Installs happen owner-side, in the owner's session (an Actions
job never installs the shell it runs under; the installers may
show their own UAC prompt, which is expected there and only
there).

## Scope notes

- Job scripts submitted through the gatekeeper inherit the
  enforced shell from the transport workflows; they do not each
  need their own check. A script may still print
  `$PSVersionTable` when its own evidence needs the version.
- `wsl.yml`'s Windows-side wrapper is under the same rule; the
  payload inside WSL is bash and is not a PowerShell surface.
- `grab.yml` has no shell steps (it is a single upload-artifact
  action) and is not a PowerShell surface.
- The Rust agent executes the command its assignment names; on
  Windows the assignment builder names the canonical pwsh path,
  and the workflow rule above covers every current dispatch path.

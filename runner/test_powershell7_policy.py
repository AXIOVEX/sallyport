#!/usr/bin/env python3
"""PowerShell 7 policy battery (owner order 2026-11-08).

Syncyard requires PowerShell 7 on every Windows host; Windows
PowerShell 5.1 is not supported anywhere. Text-level checks
(stdlib only), in the style of test_workflow_contract.py:

  - no workflow anywhere in this repo uses `shell: powershell`
    (the 5.1 shell) — `shell: pwsh` alone is NOT enforcement,
    because the Actions runner falls back to 5.1 when pwsh is
    missing;
  - every Windows transport workflow (run.yml, wsl.yml, in
    runner/workflows/, in .github/workflows/, and in the door
    repo copies when that checkout is a sibling) carries the
    cmd-shell preflight (SYNCYARD_PS7_PREFLIGHT_OK), the in-step
    assertion (SYNCYARD_PS7_REQUIRED), and, for run.yml, invokes
    the payload through the canonical $pwsh7 path rather than a
    bare `& pwsh` PATH lookup;
  - host-side .ps1 scripts carry `#Requires -Version 7.0` as
    their first line;
  - the tray watchdog task invokes pwsh.exe (a name only
    PowerShell 7 has; 5.1's executable is powershell.exe);
  - `powershell.exe` never appears as a launch target in the
    runner tree or the living backchannel docs (detection lists
    and ratified spec text are out of scope by design).

Run: python3 runner/test_powershell7_policy.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def transport_paths():
    paths = []
    for wf in ("run.yml", "wsl.yml"):
        for base in (os.path.join(HERE, "workflows"),
                     os.path.join(REPO, ".github", "workflows")):
            p = os.path.join(base, wf)
            if os.path.exists(p):
                paths.append(p)
    # The dispatch door repo (tbitcs/desktop-runner) is not part of
    # this repository; when a checkout sits beside this one in the
    # fleet's working trees, hold it to the same contract.
    door = os.path.join(os.path.dirname(REPO), "door",
                        ".github", "workflows")
    for wf in ("run.yml", "wsl.yml"):
        p = os.path.join(door, wf)
        if os.path.exists(p):
            paths.append(p)
    return paths


# 1. No `shell: powershell` anywhere in any workflow in the repo.
wf_roots = [os.path.join(REPO, ".github", "workflows"),
            os.path.join(HERE, "workflows")]
for root in wf_roots:
    if not os.path.isdir(root):
        continue
    for name in sorted(os.listdir(root)):
        if name.endswith((".yml", ".yaml")):
            text = read(os.path.join(root, name))
            check(f"no-51-shell {os.path.relpath(root, REPO)}/{name}",
                  "shell: powershell" not in text)

# 2. Windows transports carry the enforcement layers.
paths = transport_paths()
check("transports found", len(paths) >= 4, f"found {len(paths)}")
for p in paths:
    text = read(p)
    rel = os.path.relpath(p, os.path.dirname(REPO))
    check(f"preflight {rel}",
          "shell: cmd" in text and "SYNCYARD_PS7_PREFLIGHT_OK" in text
          and "SYNCYARD_PS7_MISSING" in text)
    check(f"in-step assert {rel}", "SYNCYARD_PS7_REQUIRED" in text)
    check(f"pwsh resolution {rel}",
          "Get-Command pwsh" in text)
    if p.endswith("run.yml"):
        check(f"payload via canonical path {rel}",
              "& $pwsh7 -NoProfile" in text and "& pwsh " not in text)

# 3. Host-side .ps1 scripts refuse 5.1 via #Requires.
for rel in ("runner/setup-runner.ps1", "runner/runner-watchdog.ps1",
            "runner/guard/install-guard.ps1",
            "runner/tray/install-tray.ps1",
            "runner/tray/tray-watchdog.ps1",
            "gatekeeper/probes/probe-windows.ps1"):
    p = os.path.join(REPO, rel)
    if os.path.exists(p):
        first = read(p).splitlines()[0] if read(p).splitlines() else ""
        check(f"requires-7 {rel}", first.strip() == "#Requires -Version 7.0")

# 4. Tray watchdog task invokes pwsh 7 by canonical path.
task = os.path.join(HERE, "tray", "tray-watchdog-task.xml")
if os.path.exists(task):
    text = read(task)
    check("tray task pwsh7",
          "<Command>pwsh.exe</Command>" in text)
    check("tray task no 5.1", "powershell.exe" not in text.lower().replace(
        "powershell\\7\\pwsh.exe", ""))

# 5. powershell.exe as a launch target: banned in the runner tree
#    and the living backchannel docs. (SyncyardTray.cs names
#    process names for DETECTION, never launches one; ratified
#    spec text is historical and out of scope.)
for root, _dirs, files in os.walk(HERE):
    for name in files:
        if name.endswith((".ps1", ".yml", ".yaml", ".xml", ".cmd")):
            p = os.path.join(root, name)
            text = read(p)
            scrubbed = text.replace(
                "C:\\Program Files\\PowerShell\\7\\pwsh.exe", "")
            check(f"no-51-launch {os.path.relpath(p, REPO)}",
                  "powershell.exe" not in scrubbed.lower())
for rel in ("backchannel/README.md", "backchannel/RUNBOOK.md"):
    p = os.path.join(REPO, rel)
    if os.path.exists(p):
        check(f"no-51-launch {rel}",
              "powershell.exe" not in read(p).lower().replace(
                  "powershell/7/pwsh.exe", "").replace(
                  "powershell\\7\\pwsh.exe", ""))

# 6. The bootstrap exists and is a cmd script (it must run before
#    PowerShell 7 exists, and must never invoke 5.1).
boot = os.path.join(HERE, "ensure-powershell7.cmd")
check("bootstrap exists", os.path.exists(boot))
if os.path.exists(boot):
    text = read(boot)
    check("bootstrap markers",
          "SYNCYARD_PS7_OK" in text and "SYNCYARD_PS7_MISSING" in text)
    code_only = "\n".join(
        ln for ln in text.splitlines()
        if not ln.lstrip().lower().startswith("rem"))
    check("bootstrap never invokes 5.1",
          "powershell.exe" not in code_only.lower())
    check("bootstrap installs",
          "winget install" in text and "msiexec" in text)

print()
if fails:
    print(f"PS7 POLICY: {len(fails)} FAILURES")
    sys.exit(1)
print("PS7 POLICY: ALL PASS")

#!/usr/bin/env python3
"""Transport contract battery for runner/workflows.

Text-level checks (stdlib only, no YAML dependency) pinning the
invariants every Syncyard transport workflow must keep:

  - dispatch-only: the `on:` block triggers on workflow_dispatch
    and on nothing else (never pull_request / issues / push) —
    these workflows bind self-hosted runners; an unprivileged
    trigger would break the security model (SECURITY.md).
  - lane-addressed: a `lane` input exists and the job binds
    `runs-on: [self-hosted, "${{ inputs.lane }}"]` — no hardcoded
    label lists, so any host carrying the lane label can serve.
  - transport inputs: execute workflows take cmd_b64; the grab
    workflow takes path.
  - Linux door parity: runner/workflows/linux.yml and the
    vendored production-door fixture must both carry the
    production-critical Linux properties (timeout of at least
    1440 minutes, isolated checkout git config, hold
    file, running-job registry, and GNU base64 decode).
    linux.yml and macos.yml share the dispatch/lane/input
    contract only; their script bodies are intentionally OS
    specific and are not asserted identical.

Run: python3 runner/test_workflow_contract.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.join(HERE, "workflows")
DOOR_LINUX_FIXTURE = os.path.join(
    HERE, "fixtures", "door-linux.yml")
EXECUTE = ("run.yml", "wsl.yml", "linux.yml", "macos.yml", "docker.yml")
GRAB = ("grab.yml",)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def on_block(text):
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("on:"))
        end = next(i for i, ln in enumerate(lines) if ln.startswith("jobs:"))
    except StopIteration:
        return ""
    return "\n".join(lines[start:end])


for fname in EXECUTE + GRAB:
    path = os.path.join(WF_DIR, fname)
    text = open(path).read()
    block = on_block(text)
    check(f"{fname}: on-block found", bool(block))
    check(f"{fname}: dispatch-only trigger",
          "workflow_dispatch" in block
          and "pull_request" not in block
          and "issues" not in block
          and "push" not in block.replace("workflow_dispatch", ""))
    check(f"{fname}: lane input declared", "\n      lane:" in text)
    check(f"{fname}: runs-on binds the lane input",
          'runs-on: [self-hosted, "${{ inputs.lane }}"]' in text)
    check(f"{fname}: no hardcoded label list",
          "Windows, X64" not in text and "desktop, gpu" not in text)
    if fname in EXECUTE:
        check(f"{fname}: takes cmd_b64", "cmd_b64:" in text)
    else:
        check(f"{fname}: takes path", "\n      path:" in text)

# The checkpoint hold signal: the venue
# workflows that carry a holds_dir declare the hold_file input
# and pass it to the job as SYNCYARD_HOLD_FILE.
for fname in ("run.yml", "wsl.yml", "linux.yml"):
    text = open(os.path.join(WF_DIR, fname)).read()
    check(f"{fname}: takes hold_file", "hold_file:" in text)
    check(f"{fname}: exposes SYNCYARD_HOLD_FILE",
          "SYNCYARD_HOLD_FILE" in text)

# The running-job registry: while a job
# with a hold venue runs, its workflow keeps an entry file in
# the `running` dir beside the holds dir — the host guard's
# local discovery of live checkpoint-capable jobs. The entry
# is written before the job starts and removed when it ends.
rtext = open(os.path.join(WF_DIR, "run.yml")).read()
check("run.yml: registers the running job",
      "$regFile" in rtext and ".entry" in rtext
      and "job_id=" in rtext and "'running'" in rtext)
check("run.yml: unregisters at job end",
      "Remove-Item -Force -ErrorAction SilentlyContinue $regFile"
      in rtext)
wtext = open(os.path.join(WF_DIR, "wsl.yml")).read()
check("wsl.yml: registers the running job over the UNC path "
      "",
      "$regFileWsl" in wtext and "wsl.localhost" in wtext
      and ".entry" in wtext and "hold_file_win=" in wtext)
check("wsl.yml: unregisters at job end",
      "Remove-Item -Force -ErrorAction SilentlyContinue $regFileWsl"
      in wtext)
check("grab.yml: no registry (no hold venue, not "
      "checkpoint-capable)",
      ".entry" not in open(os.path.join(WF_DIR, "grab.yml")).read())
ltext = open(os.path.join(WF_DIR, "linux.yml")).read()
check("linux.yml: registers the running job",
      "reg_file" in ltext and ".entry" in ltext
      and "job_id=" in ltext and "/running" in ltext)
check("linux.yml: unregisters at job end",
      'rm -f "$reg_file"' in ltext)

# The docker transport declares its venue inputs and runs docker.
dtext = open(os.path.join(WF_DIR, "docker.yml")).read()
check("docker.yml: takes image", "\n      image:" in dtext)
check("docker.yml: takes mounts", "\n      mounts:" in dtext)
check("docker.yml: runs the container", "docker run" in dtext)


# Linux door parity. The production door is a separate repo with
# no battery of its own, so a pinned fixture of its linux.yml is
# vendored here. Both the fixture and the repo copy must carry
# the properties production depends on; if either side drifts,
# this battery fails instead of the fleet discovering it.
def timeout_minutes(text):
    m = re.search(r"^\s*timeout-minutes:\s*(\d+)\s*$", text, re.M)
    return int(m.group(1)) if m else None


def check_linux_door_parity(label, text):
    check(f"{label}: timeout is at least 1440 minutes",
          (timeout_minutes(text) or 0) >= 1440)
    check(f"{label}: checkout uses isolated GIT_CONFIG_GLOBAL",
          "actions/checkout@" in text
          and "GIT_CONFIG_GLOBAL:" in text
          and "syncyard-checkout-gitconfig" in text)
    check(f"{label}: hold file input and environment are present",
          "hold_file:" in text and "SYNCYARD_HOLD_FILE" in text)
    check(f"{label}: running-job registry is present",
          ".entry" in text and "/running" in text
          and "job_id=" in text)
    check(f"{label}: payload decode uses GNU base64 -d",
          "base64 -d" in text and "openssl base64" not in text)


check("door linux fixture exists", os.path.isfile(DOOR_LINUX_FIXTURE))
if os.path.isfile(DOOR_LINUX_FIXTURE):
    fixture = open(DOOR_LINUX_FIXTURE).read()
    check("door linux fixture names its source and refresh duty",
          "electrohire/desktop-runner" in fixture
          and ".github/workflows/linux.yml" in fixture
          and "Refresh duty:" in fixture
          and "0b3bf7000d6eda93bd2b2ecaf8eddde5c521ab81" in fixture)
    check_linux_door_parity("door linux fixture", fixture)
check_linux_door_parity("linux.yml", ltext)

# The POSIX pair parity is deliberately narrowed: linux.yml and
# macos.yml share the generic dispatch/lane/cmd_b64 contract
# checked above, but their bodies serve different userlands. The
# macOS file states that boundary and neither file may revive
# the old identical-body claim.
mtext = open(os.path.join(WF_DIR, "macos.yml")).read()
check("linux/macos pair parity is narrowed to the shared contract",
      "intentionally not identical" in mtext
      and "identical to macos.yml" not in ltext
      and "identical to linux.yml" not in mtext)

print()
if fails:
    print(f"TEST_WORKFLOW_CONTRACT: FAIL ({len(fails)} failing)")
    sys.exit(1)
print("TEST_WORKFLOW_CONTRACT: ALL PASS")

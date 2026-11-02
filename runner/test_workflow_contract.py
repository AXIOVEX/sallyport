#!/usr/bin/env python3
"""Transport contract battery for runner/workflows (spec 010 FR-003).

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

Run: python3 runner/test_workflow_contract.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.join(HERE, "workflows")
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

# The checkpoint hold signal (spec 013 FR-007): the two venue
# workflows that carry a holds_dir declare the hold_file input
# and pass it to the job as SYNCYARD_HOLD_FILE.
for fname in ("run.yml", "wsl.yml"):
    text = open(os.path.join(WF_DIR, fname)).read()
    check(f"{fname}: takes hold_file (spec 013)", "hold_file:" in text)
    check(f"{fname}: exposes SYNCYARD_HOLD_FILE (spec 013)",
          "SYNCYARD_HOLD_FILE" in text)

# The running-job registry (spec 011 Phase 3): while a job
# with a hold venue runs, its workflow keeps an entry file in
# the `running` dir beside the holds dir — the host guard's
# local discovery of live checkpoint-capable jobs. The entry
# is written before the job starts and removed when it ends.
rtext = open(os.path.join(WF_DIR, "run.yml")).read()
check("run.yml: registers the running job (spec 011)",
      "$regFile" in rtext and ".entry" in rtext
      and "job_id=" in rtext and "'running'" in rtext)
check("run.yml: unregisters at job end (spec 011)",
      "Remove-Item -Force -ErrorAction SilentlyContinue $regFile"
      in rtext)
wtext = open(os.path.join(WF_DIR, "wsl.yml")).read()
check("wsl.yml: registers the running job over the UNC path "
      "(spec 011)",
      "$regFileWsl" in wtext and "wsl.localhost" in wtext
      and ".entry" in wtext and "hold_file_win=" in wtext)
check("wsl.yml: unregisters at job end (spec 011)",
      "Remove-Item -Force -ErrorAction SilentlyContinue $regFileWsl"
      in wtext)
check("grab.yml: no registry (no hold venue, not "
      "checkpoint-capable)",
      ".entry" not in open(os.path.join(WF_DIR, "grab.yml")).read())

# The docker transport declares its venue inputs and runs docker.
dtext = open(os.path.join(WF_DIR, "docker.yml")).read()
check("docker.yml: takes image", "\n      image:" in dtext)
check("docker.yml: takes mounts", "\n      mounts:" in dtext)
check("docker.yml: runs the container", "docker run" in dtext)


# The POSIX transports share one script body; drift fails here.
def run_block(text):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == "run: |":
            indent = len(ln) - len(ln.lstrip())
            body = []
            for b in lines[i + 1:]:
                if b.strip() and (len(b) - len(b.lstrip())) <= indent:
                    break
                body.append(b)
            pad = min((len(b) - len(b.lstrip())) for b in body if b.strip())
            return "\n".join(b[pad:] for b in body)
    return ""


check("linux.yml body == macos.yml body",
      run_block(open(os.path.join(WF_DIR, "linux.yml")).read())
      == run_block(open(os.path.join(WF_DIR, "macos.yml")).read()))

print()
if fails:
    print(f"TEST_WORKFLOW_CONTRACT: FAIL ({len(fails)} failing)")
    sys.exit(1)
print("TEST_WORKFLOW_CONTRACT: ALL PASS")

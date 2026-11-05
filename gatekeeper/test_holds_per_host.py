#!/usr/bin/env python3
"""Battery for per-host checkpoint holds_dir resolution.

The defect (field case 2026-11-05): checkpoint.holds_dir
mapped run.yml to ONE directory — the desktop's profile tree
C:\\Users\\trist\\Development\\Syncyard\\holds — and
hold_signal_path used it for every Windows job regardless of
host. Laptop jobs were handed a hold path inside another
Windows user's profile; the door wrapper's directory creation
was denied ('Access to the path C:\\Users\\trist\\...') and
every laptop run.yml job died before its payload ran.

The fix: a holds_dir entry is either one directory valid on
every host (unchanged behavior, e.g. wsl.yml) or a per-host
mapping {host: directory} resolved through the job's lane
host (LANE_HOST, the same derivation routing uses).

Pattern follows test_per_host_cwd.py / test_spec013.py: temp
dir, copied gk.py + config.py, a two-machine syncyard.yaml,
subprocess drivers importing gk at the module seam.

Run: python3 gatekeeper/test_holds_per_host.py
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as cfgmod  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


BASE_YAML = r"""
hosts:
  desktop:
    type: windows
    workflows:
      run.yml: execute
      wsl.yml: execute
      grab.yml: grab
    identity: [LID_HOST, LID_WINUSER, LID_OS]
    cwd:
      run.yml: "C:\\Users\\trist"
      grab.yml: null
  desktop-wsl:
    type: wsl2
    via: desktop
    distro: Ubuntu-24.04
    workflows:
      wsl.yml: execute
      grab.yml: grab
    identity: [LID_HOST, LID_WSLUSER, LID_OS]
    cwd:
      wsl.yml: /home/tpierson
  laptop:
    type: windows
    workflows:
      run.yml: execute
      wsl.yml: execute
      grab.yml: grab
    identity: [LID_HOST, LID_WINUSER, LID_OS]
    cwd:
      run.yml: "D:\\Development\\Inductoheat"
      grab.yml: null
  laptop-wsl:
    type: wsl2
    via: laptop
    distro: Ubuntu-24.04
    workflows:
      wsl.yml: execute
      grab.yml: grab
    identity: [LID_HOST, LID_WSLUSER, LID_OS]
    cwd:
      wsl.yml: /home/lapuser

lanes:
  active_profile: two-machine
  auto_default: false
  profiles:
    two-machine:
      quick:
        host: desktop
        runner_label: lane-quick
        queued: [10, 30]
        executing:
          mode: fixed
          values: [5, 15]
      long:
        host: desktop
        runner_label: lane-long
        queued: [45, 120]
        executing:
          mode: expectation
          floor: [90, 180]
      laptop-quick:
        host: laptop
        runner_label: lane-laptop-quick
        queued: [10, 30]
        executing:
          mode: fixed
          values: [5, 15]
      laptop-long:
        host: laptop
        runner_label: lane-laptop-long
        queued: [45, 120]
        executing:
          mode: expectation
          floor: [90, 180]
"""

OVERLAY_YAML = r"""
checkpoint:
  holds_dir:
    run.yml:
      desktop: "E:\\DeskHolds"
      laptop: "E:\\LapHolds"
    wsl.yml: /mnt/holds
"""

PRELUDE = r"""
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location(
    "gk_under_test", os.path.join(BASE_DIR, "gk.py"))
gk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gk)

def hp(jid, workflow, lane):
    return gk.hold_signal_path(
        {"id": jid, "workflow": workflow, "lane": lane})

out = {
    "lap_run": hp("j-lap", "run.yml", "laptop-quick"),
    "desk_run": hp("j-desk", "run.yml", "quick"),
    "lap_wsl": hp("j-lw", "wsl.yml", "laptop-long"),
    "desk_wsl": hp("j-dw", "wsl.yml", "long"),
    "grab": hp("j-g", "grab.yml", "quick"),
    "nolane_run": hp("j-nl", "run.yml", None),
}
print(json.dumps(out))
"""


def run_driver(yaml_text):
    d = tempfile.mkdtemp(prefix="gk-holds-")
    try:
        for f in ("gk.py", "config.py"):
            shutil.copy(os.path.join(HERE, f), d)
        with open(os.path.join(d, "syncyard.yaml"), "w") as fh:
            fh.write(yaml_text)
        driver = "BASE_DIR = %r\n%s" % (d, PRELUDE)
        env = {k: v for k, v in os.environ.items()
               if k not in ("SYNCYARD_CONFIG", "SYNCARD_CONFIG")}
        r = subprocess.run([sys.executable, "-c", driver],
                           capture_output=True, text=True, env=env,
                           cwd=d)
        if r.returncode != 0:
            raise RuntimeError("driver failed: " + r.stderr[-1200:])
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(d, ignore_errors=True)


# --- A: shipped defaults (no checkpoint section in the yaml) -------------
got = run_driver(BASE_YAML)
check("laptop run.yml job gets the laptop holds dir",
      got["lap_run"] == r"D:\Development\Syncyard\holds\j-lap.hold",
      repr(got["lap_run"]))
check("desktop run.yml job keeps the desktop holds dir",
      got["desk_run"]
      == r"C:\Users\trist\Development\Syncyard\holds\j-desk.hold",
      repr(got["desk_run"]))
check("laptop wsl.yml job keeps the shared string holds dir",
      got["lap_wsl"] == "/home/tpierson/.syncyard/holds/j-lw.hold",
      repr(got["lap_wsl"]))
check("desktop wsl.yml job keeps the shared string holds dir",
      got["desk_wsl"] == "/home/tpierson/.syncyard/holds/j-dw.hold",
      repr(got["desk_wsl"]))
check("grab.yml job still has no hold venue",
      got["grab"] is None, repr(got["grab"]))
check("run.yml job with no lane gets no guessed venue",
      got["nolane_run"] is None, repr(got["nolane_run"]))

# --- B: yaml overlay with its own per-host mapping -----------------------
got = run_driver(BASE_YAML + OVERLAY_YAML)
check("overlay: laptop run.yml job gets the overlay laptop dir",
      got["lap_run"] == r"E:\LapHolds\j-lap.hold",
      repr(got["lap_run"]))
check("overlay: desktop run.yml job gets the overlay desktop dir",
      got["desk_run"] == r"E:\DeskHolds\j-desk.hold",
      repr(got["desk_run"]))
check("overlay: wsl.yml string entry still serves both hosts",
      got["lap_wsl"] == "/mnt/holds/j-lw.hold"
      and got["desk_wsl"] == "/mnt/holds/j-dw.hold",
      repr((got["lap_wsl"], got["desk_wsl"])))

# --- C: validation shapes the YAML subset cannot express -----------------
def expect_invalid(name, mutate):
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    mutate(cfg["checkpoint"]["holds_dir"])
    try:
        cfgmod.validate(cfg)
        check(name, False, "validated without error")
    except cfgmod.ConfigError:
        check(name, True)


def expect_valid(name, mutate):
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    mutate(cfg["checkpoint"]["holds_dir"])
    try:
        cfgmod.validate(cfg)
        check(name, True)
    except cfgmod.ConfigError as e:
        check(name, False, str(e))


expect_invalid("reject empty per-host mapping",
               lambda h: h.update({"run.yml": {}}))
expect_invalid("reject empty per-host value (direct)",
               lambda h: h.update({"run.yml": {"laptop": ""}}))
expect_invalid("reject empty host name in per-host mapping",
               lambda h: h.update({"run.yml": {"": "/tmp/holds"}}))
expect_valid("accept per-host entry for an undeclared host (inert)",
             lambda h: h.update(
                 {"run.yml": {"desktop": "/tmp/d", "future": "/tmp/f"}}))

print()
if fails:
    print("BATTERY FAILED: %d check(s): %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("BATTERY OK")

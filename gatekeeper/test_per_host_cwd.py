#!/usr/bin/env python3
"""Battery for per-host default cwd resolution (2026-11-04 fix).

The defect: gk.py built its default cwd by merging every
host's cwd map into ONE global dict in file order. With a
single machine that was invisible. When the laptop host
enrolled as a second Windows host (2026-11-04), its run.yml
cwd (C:\\Users\\you\\Development\\Inductoheat) silently overwrote the
desktop's, and every desktop run.yml job submitted without
--cwd was dispatched into a directory that does not exist
on the desktop, so its payload never ran (field case:
bc-dnsfix-peek-2026-11-04, run 41895695909, pwsh working
directory invalid).

The fix resolves the default per machine: the lane's own
host first, then the hosts declared `via` it (the WSL half
of the same machine), then the built-in table. An explicit
--cwd always wins; an explicit null in a host's map wins
over every fallback; a --lane auto submit records cwd_auto
and resolves against the lane the router assigns.

Pattern follows test_spec016.py layer B: temp dir, copied
gk.py + config.py, a two-machine syncyard.yaml, subprocess
drivers and real submits read back from the queue records.

Run: python3 gatekeeper/test_per_host_cwd.py
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


YAML = r"""
hosts:
  desktop:
    type: windows
    workflows:
      run.yml: execute
      wsl.yml: execute
      grab.yml: grab
    identity: [LID_HOST, LID_WINUSER, LID_OS]
    cwd:
      run.yml: "C:\\Users\\you"
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
      wsl.yml: /home/you
  laptop:
    type: windows
    workflows:
      run.yml: execute
      wsl.yml: execute
      grab.yml: grab
    identity: [LID_HOST, LID_WINUSER, LID_OS]
    cwd:
      run.yml: "C:\\Users\\you\\Development\\Inductoheat"
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


def main():
    d = tempfile.mkdtemp(prefix="cwd-battery-")
    try:
        for f in ("gk.py", "config.py"):
            shutil.copy(os.path.join(HERE, f), os.path.join(d, f))
        cfg = os.path.join(d, "syncyard.yaml")
        with open(cfg, "w") as f:
            f.write(YAML)
        env = dict(os.environ)
        env["SYNCARD_CONFIG"] = cfg
        env.pop("SYNCYARD_CONFIG", None)

        # --- resolver matrix, straight from the module -----
        driver = (
            "import gk, json\n"
            "cases = [\n"
            "  ('quick', 'run.yml'), ('quick', 'wsl.yml'),\n"
            "  ('quick', 'grab.yml'), ('long', 'wsl.yml'),\n"
            "  ('laptop-quick', 'run.yml'),\n"
            "  ('laptop-quick', 'wsl.yml'),\n"
            "  ('laptop-quick', 'grab.yml'),\n"
            "  ('laptop-long', 'wsl.yml'),\n"
            "  (None, 'run.yml'), ('nope', 'run.yml')]\n"
            "print(json.dumps({f'{a}|{b}': gk.default_cwd(a, b)\n"
            "                  for a, b in cases}))\n"
        )
        r = subprocess.run([sys.executable, "-c", driver],
                           cwd=d, env=env, capture_output=True,
                           text=True)
        check("resolver driver runs", r.returncode == 0,
              r.stderr[-400:])
        got = json.loads(r.stdout.strip().splitlines()[-1]) \
            if r.returncode == 0 else {}
        check("desktop lane run.yml cwd is the desktop's",
              got.get("quick|run.yml") == r"C:\Users\you",
              repr(got.get("quick|run.yml")))
        check("laptop lane run.yml cwd is the laptop's (regression)",
              got.get("laptop-quick|run.yml")
              == r"C:\Users\you\Development\Inductoheat",
              repr(got.get("laptop-quick|run.yml")))
        check("desktop wsl.yml cwd via the machine's wsl host",
              got.get("long|wsl.yml") == "/home/you",
              repr(got.get("long|wsl.yml")))
        check("laptop wsl.yml cwd via the laptop's own wsl host",
              got.get("laptop-long|wsl.yml") == "/home/lapuser",
              repr(got.get("laptop-long|wsl.yml")))
        check("grab.yml cwd is null on both machines",
              got.get("quick|grab.yml") is None
              and got.get("laptop-quick|grab.yml") is None)
        check("unknown or absent lane falls back to the built-in",
              got.get("nope|run.yml") == r"C:\Users\you"
              and got.get("None|run.yml") == r"C:\Users\you")

        # --- real submits, read back from queue records ----
        def submit(key, *args):
            sp = os.path.join(d, key + ".ps1")
            with open(sp, "w") as f:
                f.write("Write-Output " + key + "\n")
            cmd = [sys.executable, "gk.py", "submit",
                   "--program", "cwd-battery", "--key", key,
                   "--script", sp, "--expected-min", "1"] + list(args)
            rr = subprocess.run(cmd, cwd=d, env=env,
                                capture_output=True, text=True)
            if rr.returncode != 0:
                return None, rr.stdout + rr.stderr
            recs = glob.glob(os.path.join(d, "queue", "*.json"))
            for p in recs:
                rec = json.load(open(p))
                if rec.get("key") == key:
                    return rec, ""
            return None, "record not found"

        rec, err = submit("cb-desktop-run", "--lane", "quick",
                          "--workflow", "run.yml")
        check("submit on desktop lane records the desktop cwd",
              rec is not None and rec.get("cwd") == r"C:\Users\you",
              err or repr(rec and rec.get("cwd")))
        rec, err = submit("cb-laptop-run", "--lane", "laptop-quick",
                          "--workflow", "run.yml")
        check("submit on laptop lane records the laptop cwd",
              rec is not None
              and rec.get("cwd") == r"C:\Users\you\Development\Inductoheat",
              err or repr(rec and rec.get("cwd")))
        rec, err = submit("cb-explicit", "--lane", "laptop-quick",
                          "--workflow", "run.yml",
                          "--cwd", r"E:\Somewhere\Else")
        check("explicit --cwd always wins",
              rec is not None
              and rec.get("cwd") == r"E:\Somewhere\Else",
              err or repr(rec and rec.get("cwd")))
        rec, err = submit("cb-laptop-wsl", "--lane", "laptop-long",
                          "--workflow", "wsl.yml")
        check("wsl submit on laptop lane records the laptop wsl cwd",
              rec is not None and rec.get("cwd") == "/home/lapuser",
              err or repr(rec and rec.get("cwd")))
        rec, err = submit("cb-auto", "--lane", "auto",
                          "--workflow", "run.yml")
        check("auto submit defers cwd to the routing pass",
              rec is not None and rec.get("cwd") is None
              and rec.get("cwd_auto") is True
              and rec.get("lane_request") == "auto",
              err or repr(rec))
        rec, err = submit("cb-grab", "--lane", "quick",
                          "--workflow", "grab.yml",
                          "--grab-path", r"C:\Users\you\x.txt")
        check("grab submit records null cwd",
              rec is not None and rec.get("cwd") is None
              and not rec.get("cwd_auto"),
              err or repr(rec and rec.get("cwd")))

        # --- the routing-pass hook, pinned by source -------
        src = open(os.path.join(HERE, "gk.py")).read()
        anchor = src.find('job["lane"] = chosen')
        hook = src.find('job["cwd"] = default_cwd(chosen',
                        anchor, anchor + 400) if anchor >= 0 else -1
        check("auto-routing pass resolves cwd_auto on assignment",
              anchor >= 0 and hook > anchor)
        check("no global cwd merge remains in gk.py",
              "DEFAULT_CWD.update" not in src
              and "DEFAULT_CWD = {}" not in src)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("TEST_PER_HOST_CWD: %s"
          % ("ALL PASS" if not fails else f"{len(fails)} FAILURES"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())

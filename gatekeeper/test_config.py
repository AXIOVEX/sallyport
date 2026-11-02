#!/usr/bin/env python3
"""Config loader battery (spec 010 FR-001/FR-002, T005 slice).

Run: python3 test_config.py [example.yaml]
Asserts:
  1. The shipped example config parses and validates.
  2. GOLDEN: the example reproduces the pre-config constants
     exactly (lanes, watermarks, budgets, repos, workflows,
     default cwds, marker pattern, log cap).
  3. No file at all -> defaults equal the same constants.
  4. A partial override merges over defaults.
  5. Invalid files fail closed with named ConfigErrors
     (warn>=escalate, unknown host, bad budget order,
     duplicate key, bad executing mode).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfgmod  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    HERE, "..", "config", "syncyard.example.yaml")

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def expect_error(name, text):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        cfgmod.load_config(path)
        check(name, False, "loaded without error")
    except cfgmod.ConfigError as e:
        check(name, True)
        print(f"     error: {e}")
    finally:
        os.unlink(path)


# 1+2: example parses and is golden against the constants.
cfg = cfgmod.load_config(EXAMPLE)
check("example loads", True)
check("golden door_repo", cfg["door_repo"] == "tbitcs/desktop-runner")
check("golden project_repo", cfg["project_repo"] == "AXIOVEX/syncyard")
check("golden workflows", cfg["workflows"] == ["run.yml", "wsl.yml", "grab.yml"])
check("golden budget", (cfg["budget"]["standdown_remaining"],
                        cfg["budget"]["critical_remaining"]) == (1000, 500))
check("golden marker pattern",
      cfg["markers"]["pattern"] == r"^[A-Z][A-Z0-9_]{2,}([ :]|$)")
check("golden liveness threshold (spec 011 FR-001)",
      cfg["liveness"]["down_after_s"] == 900)
check("golden reboot grace (spec 011 FR-015)",
      cfg["reboot"]["grace_s"] == 60)
check("golden optimizer knobs (spec 013 FR-012)",
      cfg["dispatch"]["optimizer_ceiling"] == 100
      and cfg["dispatch"]["optimizer_aging_step_min"] == 10)
check("golden checkpoint contract (spec 013 FR-007)",
      cfg["checkpoint"]["exit_code"] == 86
      and cfg["checkpoint"]["marker"] == "CHECKPOINT_PAUSED"
      and cfg["checkpoint"]["env_var"] == "SYNCYARD_HOLD_FILE"
      and cfg["checkpoint"]["holds_dir"] == {
          "run.yml": r"C:\Users\trist\Development\Syncyard\holds",
          "wsl.yml": "/home/tpierson/.syncyard/holds"})
check("golden log cap", cfg["retention"]["human_log_max_bytes"] == 1000000)
check("golden cwd windows", cfg["hosts"]["desktop"]["cwd"]["run.yml"] == r"C:\Users\trist")
check("golden cwd grab null", cfg["hosts"]["desktop"]["cwd"]["grab.yml"] is None)
check("golden cwd wsl", cfg["hosts"]["desktop-wsl"]["cwd"]["wsl.yml"] == "/home/tpierson")
check("golden wsl via+distro", cfg["hosts"]["desktop-wsl"]["via"] == "desktop"
      and cfg["hosts"]["desktop-wsl"]["distro"] == "Ubuntu-24.04")
for lane, (q, mode, vals) in {
    "quick": ((10, 30), "fixed", (5, 15)),
    "normal": ((20, 45), "fixed", (45, 90)),
    "long": ((45, 120), "expectation", (90, 180)),
    "gpu": ((30, 90), "expectation", (120, 240)),
}.items():
    L = cfg["lanes"][lane]
    key = "values" if mode == "fixed" else "floor"
    check(f"golden lane {lane}",
          tuple(L["queued"]) == q and L["executing"]["mode"] == mode
          and tuple(L["executing"][key]) == vals
          and L["runner_label"] == f"lane-{lane}" and L["host"] == "desktop")

# 3: defaults (no file) equal the golden example on the fleet surface.
os.environ.pop(cfgmod.ENV_VAR, None)
dflt = cfgmod.load_config("/nonexistent/path.yaml") if False else None
import importlib
# load with an explicit missing default location: point env at a tmp name that does not exist is an error by design;
# instead call with path=None after removing any beside-file config.
beside = os.path.join(HERE, cfgmod.CONFIG_FILENAME)
check("no beside-file config in repo gatekeeper dir", not os.path.exists(beside))
dflt = cfgmod.load_config(None)
check("defaults == example fleet surface",
      dflt["lanes"] == cfg["lanes"] and dflt["hosts"] == cfg["hosts"]
      and dflt["budget"] == cfg["budget"] and dflt["workflows"] == cfg["workflows"]
      and dflt["liveness"] == cfg["liveness"]
      and dflt["reboot"] == cfg["reboot"]
      and dflt["dispatch"] == cfg["dispatch"]
      and dflt["checkpoint"] == cfg["checkpoint"])

# 4: partial override merges.
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
    f.write("budget:\n  standdown_remaining: 2000\nlanes:\n  quick:\n    queued: [5, 15]\n")
    path = f.name
over = cfgmod.load_config(path)
os.unlink(path)
check("override merges", over["budget"]["standdown_remaining"] == 2000
      and over["budget"]["critical_remaining"] == 500
      and over["lanes"]["quick"]["queued"] == [5, 15]
      and over["lanes"]["quick"]["executing"]["values"] == [5, 15])

# 5: invalid files fail closed, named.
expect_error("reject warn>=escalate",
             "lanes:\n  quick:\n    queued: [30, 30]\n")
expect_error("reject unknown lane host",
             "lanes:\n  quick:\n    host: nowhere\n")
expect_error("reject budget order",
             "budget:\n  standdown_remaining: 100\n  critical_remaining: 500\n")
expect_error("reject duplicate key",
             "version: 1\nversion: 1\n")
expect_error("reject bad executing mode",
             "lanes:\n  quick:\n    executing:\n      mode: sideways\n")
expect_error("reject bad version", "version: 2\n")
expect_error("reject host serves undeclared workflow",
             "hosts:\n  desktop:\n    workflows:\n      teleport.yml: execute\n")
expect_error("reject bad workflow kind",
             "hosts:\n  desktop:\n    workflows:\n      run.yml: sideways\n")
expect_error("reject wsl2 workflow not served by via host",
             "hosts:\n  desktop-wsl:\n    workflows:\n      wsl.yml: execute\n      mac.yml: execute\n"
             "workflows:\n  - run.yml\n  - wsl.yml\n  - grab.yml\n  - mac.yml\n")
expect_error("reject workflow served by no host",
             "workflows:\n  - run.yml\n  - wsl.yml\n  - grab.yml\n  - orphan.yml\n")
expect_error("reject empty identity",
             "hosts:\n  desktop:\n    identity: []\n")
expect_error("reject empty host image",
             "hosts:\n  desktop:\n    image: ''\n")
expect_error("reject zero liveness threshold",
             "liveness:\n  down_after_s: 0\n")
expect_error("reject negative liveness threshold",
             "liveness:\n  down_after_s: -5\n")
expect_error("reject non-numeric liveness threshold",
             "liveness:\n  down_after_s: soon\n")
expect_error("reject boolean liveness threshold",
             "liveness:\n  down_after_s: true\n")
expect_error("reject zero reboot grace",
             "reboot:\n  grace_s: 0\n")
expect_error("reject negative reboot grace",
             "reboot:\n  grace_s: -30\n")
expect_error("reject non-numeric reboot grace",
             "reboot:\n  grace_s: soon\n")
expect_error("reject boolean reboot grace",
             "reboot:\n  grace_s: true\n")
expect_error("reject zero optimizer ceiling",
             "dispatch:\n  optimizer_ceiling: 0\n")
expect_error("reject non-integer optimizer ceiling",
             "dispatch:\n  optimizer_ceiling: high\n")
expect_error("reject zero optimizer aging step",
             "dispatch:\n  optimizer_aging_step_min: 0\n")
expect_error("reject zero checkpoint exit code",
             "checkpoint:\n  exit_code: 0\n")
expect_error("reject out-of-range checkpoint exit code",
             "checkpoint:\n  exit_code: 256\n")
expect_error("reject non-integer checkpoint exit code",
             "checkpoint:\n  exit_code: soon\n")
expect_error("reject boolean checkpoint exit code",
             "checkpoint:\n  exit_code: true\n")
expect_error("reject lowercase checkpoint marker",
             "checkpoint:\n  marker: checkpoint_paused\n")
expect_error("reject empty checkpoint env var",
             "checkpoint:\n  env_var: ''\n")
expect_error("reject checkpoint holds_dir for undeclared workflow",
             "checkpoint:\n  holds_dir:\n    teleport.yml: /tmp/holds\n")
expect_error("reject empty checkpoint holds_dir value",
             "checkpoint:\n  holds_dir:\n    run.yml: ''\n")

# 6: a valid liveness override loads and lands.
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
    f.write("liveness:\n  down_after_s: 120\n")
    path = f.name
over = cfgmod.load_config(path)
os.unlink(path)
check("liveness override loads", over["liveness"]["down_after_s"] == 120)

# 7: a valid reboot grace override loads and lands.
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
    f.write("reboot:\n  grace_s: 120\n")
    path = f.name
over = cfgmod.load_config(path)
os.unlink(path)
check("reboot grace override loads", over["reboot"]["grace_s"] == 120)

# 8: a valid checkpoint override loads and lands.
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
    f.write("checkpoint:\n  exit_code: 42\n  marker: HELD_HERE\n")
    path = f.name
over = cfgmod.load_config(path)
os.unlink(path)
check("checkpoint override loads",
      over["checkpoint"]["exit_code"] == 42
      and over["checkpoint"]["marker"] == "HELD_HERE"
      and over["checkpoint"]["env_var"] == "SYNCYARD_HOLD_FILE")

print()
if fails:
    print(f"TEST_CONFIG: FAIL ({len(fails)} failing)")
    sys.exit(1)
print("TEST_CONFIG: ALL PASS")

#!/usr/bin/env python3
"""Battery for `gk profile suggest` (spec 016 follow-on).

The command drafts a lane profile for one host from its
recorded capability snapshot, with the reasoning printed as
comments in the fragment, and applies nothing: it never
edits the config, never writes hosts.json, and writes a file
only at an explicit --out path that neither exists nor is
the live config.

Pattern follows test_spec016.py layer B (temp dir, copied
gk.py + config.py), but the drivers are the real CLI run as
a subprocess, so the argparse wiring and the exit codes are
pinned as the operator meets them. Time is real time: a
fresh fixture is stamped now, a stale one three hours back
(the configured bound is 60 min).

Covers: (a) a large GPU host drafts quick+normal+long+gpu
with the largest VRAM named in the reasoning, and the
fragment loads as valid config; (b) a small CPU-only host
drafts quick+long only, with normal's absence explained by
the measured values; (c) a stale snapshot is refused with
exit 2; (d) unknown GPU fields draft no gpu lane and say
unknown is never treated as a GPU; (e) --out writes once,
refuses an existing file, and refuses the live config path;
plus the missing snapshot refusal, the --suggest flag form,
determinism, and hosts.json left untouched.

Run: python3 gatekeeper/test_profile_suggest.py
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


BIG_CAP = {"platform": "windows", "hostname": "BIGBOX",
           "cpu_cores": 32, "cpu_model": "EPYC", "cpu_load_1m": 0.5,
           "ram_total_mb": 131072, "ram_free_mb": 90000,
           "storage_free_gb": 2000.0, "storage_root": "C:",
           "gpus": [{"name": "NVIDIA GeForce RTX 4090",
                     "vram_mb": 24576, "class": "discrete"},
                    {"name": "NVIDIA T400", "vram_mb": 4096,
                     "class": "discrete"}],
           "venues": ["native", "wsl", "docker"]}

SMALL_CAP = {"platform": "linux", "hostname": "smallbox",
             "cpu_cores": 4, "cpu_model": "i3", "cpu_load_1m": None,
             "ram_total_mb": 8192, "ram_free_mb": 3000,
             "storage_free_gb": 40.0, "storage_root": "/",
             "gpus": [], "venues": ["native"]}

UNKNOWN_GPU_CAP = {"platform": "darwin", "hostname": "macbox",
                   "cpu_cores": 16, "cpu_model": "Apple M3",
                   "cpu_load_1m": None,
                   "ram_total_mb": 32768, "ram_free_mb": 12000,
                   "storage_free_gb": 300.0, "storage_root": "/",
                   "gpus": [{"name": None, "vram_mb": None,
                             "class": "none"}],
                   "venues": ["native"]}

UNKNOWN_CORES_CAP = dict(SMALL_CAP, cpu_cores=None,
                         ram_total_mb=65536, gpus=[])


def make_env(d, cap=None, host="desktop", age_min=0, config_text=None):
    """Temp gatekeeper dir with gk.py + config.py copied in
    and (optionally) one capability snapshot in hosts.json.
    Returns the subprocess env to run the CLI with."""
    shutil.copy(os.path.join(HERE, "gk.py"), d)
    shutil.copy(os.path.join(HERE, "config.py"), d)
    env = {k: v for k, v in os.environ.items()
           if k not in ("SYNCYARD_CONFIG", "SYNCARD_CONFIG")}
    if config_text is not None:
        cfg_path = os.path.join(d, "syncyard.yaml")
        with open(cfg_path, "w") as f:
            f.write(config_text)
        env["SYNCARD_CONFIG"] = cfg_path
    if cap is not None:
        probed = datetime.now(timezone.utc) - timedelta(minutes=age_min)
        snap = dict(cap)
        snap["host"] = host
        snap["probed_at"] = iso(probed)
        state = {host: {"last_seen": None, "last_heartbeat_ts": None,
                        "down": False, "hold": False,
                        "hold_since": None, "going_down": None,
                        "capability": snap}}
        with open(os.path.join(d, "hosts.json"), "w") as f:
            json.dump(state, f)
    return env


def run_cli(d, env, *argv):
    r = subprocess.run([sys.executable, "gk.py"] + list(argv),
                       capture_output=True, text=True, env=env, cwd=d)
    return r.returncode, r.stdout + r.stderr


def file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tempdir():
    return tempfile.mkdtemp(prefix="gk-suggest-")


# --- (a) Large GPU host ------------------------------------------------
d = tempdir()
try:
    env = make_env(d, BIG_CAP)
    hj_before = file_hash(os.path.join(d, "hosts.json"))
    rc, out = run_cli(d, env, "profile", "suggest",
                      "--host", "desktop", "--name", "big-draft")
    check("(a) large GPU host: exit 0", rc == 0, out[-300:])
    check("(a) fragment has the profiles shape",
          "lanes:\n  profiles:\n    big-draft:" in out)
    for lane in ("quick", "normal", "long", "gpu"):
        check("(a) lane %s drafted on the host" % lane,
              ("      %s:\n        host: desktop" % lane) in out)
    check("(a) reasoning names the largest VRAM",
          "gpu: suggested" in out and "vram_mb=24576" in out
          and "RTX 4090" in out)
    check("(a) reasoning names the measured facts",
          "cpu_cores=32" in out and "ram_total_mb=131072" in out
          and "storage_free_gb=2000" in out)
    check("(a) concurrency reasoning present",
          "concurrency: 1 on every suggested lane" in out)
    check("(a) hosts.json untouched by suggest",
          file_hash(os.path.join(d, "hosts.json")) == hj_before)
    rc2, out2 = run_cli(d, env, "profile", "suggest",
                        "--host", "desktop", "--name", "big-draft")
    check("(a) determinism: a second run is byte-identical",
          rc2 == 0 and out2 == out)
    rc3, out3 = run_cli(d, env, "profile", "--suggest",
                        "--host", "desktop")
    check("(a) --suggest flag form works, default name derived",
          rc3 == 0 and "desktop-suggested:" in out3)
    frag = os.path.join(d, "fragment.yaml")
    with open(frag, "w") as f:
        f.write(out)
    spec = importlib.util.spec_from_file_location(
        "cfg_for_suggest", os.path.join(d, "config.py"))
    cfgmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfgmod)
    merged = cfgmod.load_config(frag)
    prof = merged["lane_profiles"]["big-draft"]
    check("(a) fragment loads as valid config, all concurrency 1",
          sorted(prof) == ["gpu", "long", "normal", "quick"]
          and all(l["concurrency"] == 1 for l in prof.values())
          and all(l["host"] == "desktop" for l in prof.values()))
    check("(a) gpu band and watermarks mirror fleet-default",
          prof["gpu"]["duration_band"] == [0, None]
          and prof["gpu"]["queued"] == [30, 90]
          and prof["quick"]["executing"]
          == {"mode": "fixed", "values": [5, 15]})
finally:
    shutil.rmtree(d, ignore_errors=True)

# --- (b) Small CPU-only host -------------------------------------------
d = tempdir()
try:
    env = make_env(d, SMALL_CAP)
    rc, out = run_cli(d, env, "profile", "suggest", "--host", "desktop")
    lanes = [ln.strip()[:-1] for ln in out.splitlines()
             if ln.startswith("      ")
             and ln.rstrip().endswith(":")
             and not ln.startswith("        ")]
    check("(b) small host: exit 0", rc == 0, out[-300:])
    check("(b) only quick and long are drafted",
          lanes == ["quick", "long"], repr(lanes))
    check("(b) normal's absence is explained by measured values",
          "normal: not suggested" in out
          and "cpu_cores=4 is below 8" in out
          and "ram_total_mb=8192 (8 GiB) is below 16 GiB" in out)
    check("(b) gpu's absence is the measured none",
          "gpu: not suggested. The probe measured no GPU" in out)
finally:
    shutil.rmtree(d, ignore_errors=True)

# --- (c) Stale snapshot -------------------------------------------------
d = tempdir()
try:
    env = make_env(d, BIG_CAP, age_min=180)
    rc, out = run_cli(d, env, "profile", "suggest", "--host", "desktop")
    check("(c) stale snapshot refused with exit 2",
          rc == 2 and "stale" in out, "rc=%r out=%s" % (rc, out[-200:]))
    check("(c) refusal names the refresh route",
          "gk probe-host --host desktop" in out)
finally:
    shutil.rmtree(d, ignore_errors=True)

# --- Missing snapshot ---------------------------------------------------
d = tempdir()
try:
    env = make_env(d, cap=None)
    rc, out = run_cli(d, env, "profile", "suggest", "--host", "desktop")
    check("missing snapshot refused with exit 2",
          rc == 2 and "no capability snapshot" in out)
    rc, out = run_cli(d, env, "profile", "suggest", "--host", "nowhere")
    check("unknown host refused with exit 2",
          rc == 2 and "unknown host" in out)
finally:
    shutil.rmtree(d, ignore_errors=True)

# --- (d) Unknown GPU fields ---------------------------------------------
d = tempdir()
try:
    env = make_env(d, UNKNOWN_GPU_CAP)
    rc, out = run_cli(d, env, "profile", "suggest", "--host", "desktop")
    check("(d) unknown-GPU host: exit 0, normal still drafted",
          rc == 0 and "      normal:" in out, out[-300:])
    check("(d) no gpu lane from unknown GPU fields",
          "      gpu:" not in out)
    check("(d) reasoning: unknown is never treated as a GPU",
          "gpu: not suggested" in out
          and "unknown is never treated as a GPU" in out)
finally:
    shutil.rmtree(d, ignore_errors=True)

# --- Unknown cores cannot justify normal --------------------------------
d = tempdir()
try:
    env = make_env(d, UNKNOWN_CORES_CAP)
    rc, out = run_cli(d, env, "profile", "suggest", "--host", "desktop")
    check("unknown cpu_cores: no normal lane, reason says unknown",
          rc == 0 and "      normal:" not in out
          and "cpu_cores is unknown, and unknown never counts"
          in out, out[-300:])
finally:
    shutil.rmtree(d, ignore_errors=True)

# --- (e) --out discipline -------------------------------------------------
d = tempdir()
try:
    env = make_env(d, BIG_CAP, config_text="# operator config\n")
    cfg_path = os.path.join(d, "syncyard.yaml")
    out_path = os.path.join(d, "draft.yaml")
    rc, out = run_cli(d, env, "profile", "suggest", "--host", "desktop",
                      "--out", out_path)
    check("(e) --out writes the draft once",
          rc == 0 and os.path.isfile(out_path)
          and "big-draft" not in out, out[-200:])
    written = file_hash(out_path)
    with open(out_path) as f:
        body = f.read()
    check("(e) written file is the fragment",
          "lanes:\n  profiles:\n    desktop-suggested:" in body
          and "#   gpu: suggested" in body)
    rc, out = run_cli(d, env, "profile", "suggest", "--host", "desktop",
                      "--out", out_path)
    check("(e) --out refuses an existing file, exit 2, file unchanged",
          rc == 2 and "already exists" in out
          and file_hash(out_path) == written)
    rc, out = run_cli(d, env, "profile", "suggest", "--host", "desktop",
                      "--out", cfg_path)
    check("(e) --out refuses the live config path, exit 2",
          rc == 2 and "live config file" in out, out[-200:])
    with open(cfg_path) as f:
        check("(e) the config file itself was never touched",
              f.read() == "# operator config\n")
finally:
    shutil.rmtree(d, ignore_errors=True)

print()
if fails:
    print(f"TEST_PROFILE_SUGGEST: FAIL ({len(fails)} failing)")
    sys.exit(1)
print("TEST_PROFILE_SUGGEST: ALL PASS")

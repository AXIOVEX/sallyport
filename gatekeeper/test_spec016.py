#!/usr/bin/env python3
"""Scenario battery for spec 016 (adaptive lanes).

Two layers, mirroring test_config.py + test_spec015.py:
  A. Config-layer pins, run in-process against config.py:
     profile validation (every named ConfigError), the
     fleet-default equivalence (the resolved lane table and
     watermarks equal today's constants), and the built-in
     profile set.
  B. Gatekeeper scenarios, golden style: temp dir, copied
     gk.py + config.py, subprocess drivers, stub dispatch_one /
     ledger at the module seams, a fixed evaluation instant.
     LANE_CONCURRENCY is raised to 99 in the prelude (spec 015
     battery's fixture choice): lane serialization is spec
     001/013 behavior, pinned by their own batteries; here it
     would mask the routing decisions this battery pins.

Covers: custom profile end-to-end dispatch + workflow pin;
hint recording (incl. flag-alone --needs-gpu) + negative
refusal; auto submit record shape; auto resolution + the
auto_routed snapshot's completeness; scoring determinism;
GPU/VRAM hint satisfaction + auto_no_fit; history-median
routing to the long lane; lock respect in both directions;
focus conjunction across resolution; host-hold exclusion;
staleness degradation + unknown-field neutrality; venue
eligibility (docker requires image, wsl requires a Windows/
WSL host); explicit lane beats auto; probe parsers for all
three platforms against fixtures (incl. the unified-GPU
classification and no wsl venue on darwin); probe ingest +
probe-job ingest; the no-transport refusal; status/profile
rendering; probe script file == constant drift pin.

Run: python3 gatekeeper/test_spec016.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfgmod  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


# === A. Config layer ---------------------------------------------------------

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


def load_text(text):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(text)
        path = f.name
    try:
        return cfgmod.load_config(path)
    finally:
        os.unlink(path)


LANES_HDR = "lanes:\n  profiles:\n    fleet-default:\n"
expect_error("reject bad profile name (FR-002)",
             "lanes:\n  profiles:\n    Bad_Name:\n      quick:\n"
             "        host: desktop\n        queued: [10, 30]\n"
             "        executing:\n          mode: fixed\n"
             "          values: [5, 15]\n")
expect_error("reject bad lane name (FR-002)",
             "lanes:\n  profiles:\n    fleet-default:\n      GPU_Lane:\n"
             "        host: desktop\n        queued: [10, 30]\n"
             "        executing:\n          mode: fixed\n"
             "          values: [5, 15]\n")
expect_error("reject unknown lane host in profile (FR-002)",
             LANES_HDR + "      quick:\n        host: nowhere\n")
expect_error("reject unknown workflow pin (FR-002)",
             LANES_HDR + "      quick:\n        workflow: teleport.yml\n")
expect_error("reject zero concurrency (FR-002)",
             LANES_HDR + "      quick:\n        concurrency: 0\n")
expect_error("reject non-integer concurrency (FR-002)",
             LANES_HDR + "      quick:\n        concurrency: two\n")
expect_error("reject unknown resource class (FR-002)",
             LANES_HDR + "      quick:\n        resource_class: tpu\n")
expect_error("reject inverted duration band (FR-002)",
             LANES_HDR + "      quick:\n        duration_band: [10, 5]\n")
expect_error("reject scalar duration band (FR-002)",
             LANES_HDR + "      quick:\n        duration_band: wide\n")
expect_error("reject negative duration band (FR-002)",
             LANES_HDR + "      quick:\n        duration_band: [-1, 5]\n")
expect_error("reject non-boolean enabled (FR-002)",
             LANES_HDR + "      quick:\n        enabled: yes\n")
expect_error("reject profile with zero enabled lanes (FR-002)",
             "lanes:\n  profiles:\n    empty-prof:\n      quick:\n"
             "        host: desktop\n        enabled: false\n"
             "        queued: [10, 30]\n        executing:\n"
             "          mode: fixed\n          values: [5, 15]\n")
expect_error("reject unknown active profile (FR-002)",
             "lanes:\n  active_profile: nowhere\n")
expect_error("reject non-boolean auto_default (FR-002)",
             "lanes:\n  auto_default: yes\n")
expect_error("reject zero capability max age (FR-002)",
             "lanes:\n  capability_max_age_min: 0\n")
expect_error("reject duplicate lane key in profile (FR-002)",
             LANES_HDR + "      quick:\n        host: desktop\n"
             "      quick:\n        host: desktop\n")

dflt = cfgmod.load_config(None)
check("fleet-default equivalence: resolved lanes (FR-001)",
      {n: (l["host"], l["runner_label"], l["concurrency"],
           tuple(l["queued"]), l["executing"]["mode"],
           tuple(l["executing"].get("values")
                 or l["executing"].get("floor")),
           l["resource_class"], l["duration_band"])
       for n, l in dflt["lanes"].items()} == {
          "quick": ("desktop", "lane-quick", 1, (10, 30), "fixed",
                    (5, 15), "cpu", [0, 10]),
          "normal": ("desktop", "lane-normal", 1, (20, 45), "fixed",
                     (45, 90), "cpu", [0, 45]),
          "long": ("desktop", "lane-long", 1, (45, 120), "expectation",
                   (90, 180), "cpu", [30, None]),
          "gpu": ("desktop", "lane-gpu", 1, (30, 90), "expectation",
                  (120, 240), "gpu", [0, None])})
check("built-in profile set (FR-001)",
      set(dflt["lane_profiles"]) == {"fleet-default",
                                     "single-host-light", "gpu-farm"}
      and dflt["lane_profiles"]["gpu-farm"]["gpu"]["concurrency"] == 2
      and set(dflt["lane_profiles"]["single-host-light"]) == {"quick",
                                                              "long"})

# === B. Gatekeeper scenarios -------------------------------------------------

PRELUDE = r"""
import importlib.util, io, json, os, sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from types import SimpleNamespace as NS
spec = importlib.util.spec_from_file_location(
    "gk_under_test", os.path.join(BASE_DIR, "gk.py"))
gk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gk)
NOW_HOLDER = {"now": datetime(2026, 11, 2, 12, 0,
                              tzinfo=timezone.utc)}
gk.now_dt = lambda: NOW_HOLDER["now"]
os.makedirs(gk.QUEUE_DIR, exist_ok=True)
open(gk.LANES_LIVE_FILE, "w").close()
gk.LANE_CONCURRENCY = {lane: 99 for lane in gk.LANES}
events = []
gk.ledger = lambda event, **kw: events.append(
    dict({"event": event}, **kw))

SCRIPT = os.path.join(BASE_DIR, "job.ps1")
with open(SCRIPT, "w") as f:
    f.write("Write-Output ok\n")

def job(jid, lane, key=None, program="prog", workflow="run.yml",
        state="submitted", created="2026-11-02T11:00:00Z",
        expected=None, maint=False, chat=None, prio=None,
        run_id=None, run_status=None, needs=None, venue=None,
        image=None, dispatched=None, first_seen=None, completed=None):
    j = {"id": jid, "key": key or jid, "program": program,
         "workflow": workflow, "lane": lane, "state": state,
         "created_at": created, "script_sha256": "sha-" + jid,
         "maintenance": maint, "notify_chat": chat,
         "run_id": run_id, "run_status": run_status,
         "run_conclusion": None, "flags": [], "watermarks": {},
         "expected_min": expected, "cwd": "/x"}
    if prio is not None:
        j["priority"], j["priority_source"] = prio
    if needs is not None:
        j["needs"] = needs
    if venue is not None:
        j["venue"] = venue
    if image is not None:
        j["image"] = image
    if dispatched:
        j["dispatched_at"] = dispatched
    if first_seen:
        j["first_seen_in_progress_at"] = first_seen
    if completed:
        j["completed_at"] = completed
    path = os.path.join(gk.QUEUE_DIR, jid + ".json")
    with open(path, "w") as f:
        json.dump(j, f)
    return j

def submit_ns(**kw):
    d = {"program": "prog", "workflow": "run.yml", "lane": "quick",
         "script": SCRIPT, "grab_path": None, "cwd": None,
         "expected_min": None, "key": "k", "maintenance": False,
         "image": None, "mounts": None, "notify_chat": None,
         "priority": None, "needs_cpu": None, "needs_ram_mb": None,
         "needs_storage_mb": None, "needs_gpu": None, "venue": "auto"}
    d.update(kw)
    return NS(**d)

def write_capability(host, snap, probed_at="2026-11-02T12:00:00Z"):
    state = {}
    if os.path.isfile(gk.HOSTS_FILE):
        with open(gk.HOSTS_FILE) as f:
            state = json.load(f)
    rec = state.setdefault(host, {
        "last_seen": None, "last_heartbeat_ts": None, "down": False,
        "hold": False, "hold_since": None, "going_down": None})
    cap = dict(snap)
    cap["host"] = host
    cap["probed_at"] = probed_at
    rec["capability"] = cap
    with open(gk.HOSTS_FILE, "w") as f:
        json.dump(state, f)

DESKTOP_CAP = {"platform": "windows", "hostname": "EXAMPLE-HOST",
               "cpu_cores": 24, "cpu_model": "i9", "cpu_load_1m": None,
               "ram_total_mb": 65536, "ram_free_mb": 40000,
               "storage_free_gb": 900.0, "storage_root": "C:",
               "gpus": [{"name": "RTX 4090", "vram_mb": 24576,
                         "class": "discrete"}],
               "venues": ["native", "wsl", "docker"]}

def read_job(jid):
    with open(os.path.join(gk.QUEUE_DIR, jid + ".json")) as f:
        return json.load(f)

def by_key():
    out = {}
    for fn in os.listdir(gk.QUEUE_DIR):
        with open(os.path.join(gk.QUEUE_DIR, fn)) as f:
            j = json.load(f)
        out[j["key"]] = j
    return out

def decided_order(mod=None):
    mod = mod or gk
    decided = []
    mod.dispatch_one = lambda job, jobs: (
        decided.append(job["id"]), True)[1]
    mod.dispatch_pass(mod.load_jobs(), {})
    return decided

def call(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(args)
    return rc, buf.getvalue()

def evnames():
    return [e["event"] for e in events]

def evfind(name):
    return [e for e in events if e["event"] == name]
"""


def run_driver(body, config_text=None):
    d = tempfile.mkdtemp(prefix="gk-spec016-")
    try:
        shutil.copy(os.path.join(HERE, "gk.py"), d)
        shutil.copy(os.path.join(HERE, "config.py"), d)
        env = {k: v for k, v in os.environ.items()
               if k not in ("SYNCYARD_CONFIG", "SYNCARD_CONFIG")}
        if config_text is not None:
            cfg_path = os.path.join(d, "syncyard.yaml")
            with open(cfg_path, "w") as f:
                f.write(config_text)
            env["SYNCARD_CONFIG"] = cfg_path
        driver = "BASE_DIR = %r\n%s\n%s" % (d, PRELUDE, body)
        r = subprocess.run([sys.executable, "-c", driver],
                           capture_output=True, text=True, env=env,
                           cwd=d)
        if r.returncode != 0:
            raise RuntimeError("driver failed: " + r.stderr[-1200:])
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(d, ignore_errors=True)


RIG_YAML = """lanes:
  active_profile: rig
  profiles:
    rig:
      build:
        host: desktop
        runner_label: lane-build
        resource_class: cpu
        duration_band: [0, 60]
        workflow: run.yml
        queued: [10, 30]
        executing:
          mode: fixed
          values: [5, 15]
      render:
        host: desktop
        runner_label: lane-render
        resource_class: any
        duration_band: [30, null]
        queued: [10, 30]
        executing:
          mode: fixed
          values: [5, 15]
"""

# --- Custom profile: dispatch end-to-end + pin refusal ----------
res = run_driver(r"""
rc1, _ = call(gk.cmd_submit, submit_ns(lane="build", key="cb1"))
rc2, out2 = call(gk.cmd_submit, submit_ns(
    lane="build", key="cb2", workflow="wsl.yml", script=None))
job("aj1", None, program="prog", expected=45)
decided = decided_order()
print(json.dumps({"rc1": rc1, "rc2": rc2, "out2": out2,
                  "decided": decided,
                  "cb1_id": by_key()["cb1"]["id"],
                  "aj1_lane": read_job("aj1").get("lane")}))
""", config_text=RIG_YAML)
check("custom profile dispatches end-to-end (FR-002)",
      res["rc1"] == 0 and res["cb1_id"] in res["decided"])
check("workflow pin refuses other workflows (FR-002)",
      res["rc2"] == 3 and "REFUSED" in res["out2"])
# cb1 waits on build at resolution time, so load pricing (-5)
# tips the band tie to render — deterministic, and recorded.
check("custom profile auto routes within its own lanes (FR-002/FR-006)",
      res["aj1_lane"] == "render")

# --- Hint recording + refusals ----------------------------------
res = run_driver(r"""
rc1, _ = call(gk.cmd_submit, submit_ns(
    key="h1", needs_cpu=4, needs_ram_mb=2048))
rc2, _ = call(gk.cmd_submit, submit_ns(key="h2", needs_gpu=True))
rc3, _ = call(gk.cmd_submit, submit_ns(key="h3", needs_gpu=8192))
rc4, _ = call(gk.cmd_submit, submit_ns(key="h4", needs_ram_mb=-5))
print(json.dumps({"rcs": [rc1, rc2, rc3, rc4],
                  "h1": by_key()["h1"].get("needs"),
                  "h2": by_key()["h2"].get("needs"),
                  "h3": by_key()["h3"].get("needs")}))
""")
check("hints recorded on the record (FR-003)",
      res["rcs"][:3] == [0, 0, 0]
      and res["h1"] == {"cpu_cores": 4, "ram_mb": 2048}
      and res["h2"] == {"gpu": True}
      and res["h3"] == {"gpu": True, "gpu_vram_mb": 8192})
check("negative hint refused (FR-003)", res["rcs"][3] == 2)

# --- Auto submit, resolution, snapshot completeness -------------
res = run_driver(r"""
write_capability("desktop", DESKTOP_CAP)
rc, out = call(gk.cmd_submit, submit_ns(
    key="a1", lane="auto", needs_gpu=True))
before = by_key()["a1"]
decided = decided_order()
after = by_key()["a1"]
snap = after.get("auto_route") or {}
print(json.dumps({
    "rc": rc, "before_lane": before.get("lane"),
    "before_req": before.get("lane_request"),
    "decided": decided, "after_lane": after.get("lane"),
    "snap_keys": sorted(snap.keys()),
    "chosen": snap.get("chosen"), "score": snap.get("score"),
    "degraded": snap.get("degraded"),
    "gpu_eval": (snap.get("lanes") or {}).get("gpu"),
    "events": evnames(),
    "routed_ev": evfind("auto_routed")}))
""")
check("auto submit lands lane-less with lane_request (FR-004)",
      res["rc"] == 0 and res["before_lane"] is None
      and res["before_req"] == "auto")
check("auto resolves to the GPU lane and dispatches (FR-006)",
      res["after_lane"] == "gpu" and len(res["decided"]) == 1
      and res["chosen"] == "gpu")
check("auto_routed snapshot is complete (FR-006)",
      {"evaluated_at", "needs", "venue", "history", "degraded",
       "capability", "lanes", "chosen", "score"} <= set(res["snap_keys"])
      and res["degraded"] is False
      and res["gpu_eval"]["eligible"] is True
      and res["gpu_eval"]["score"] == res["score"])
check("auto_routed ledgered with lane + score (FR-006)",
      len(res["routed_ev"]) == 1
      and res["routed_ev"][0]["lane"] == "gpu")

# --- Scoring determinism (pure function) ------------------------
res = run_driver(r"""
job("x1", "gpu", state="watching", run_status="in_progress")
hist = {"samples": 4, "median_min": 100.0, "dominant_class": "gpu",
        "last_lane": "gpu", "durations_min": [100.0]}
caps = {"desktop": (DESKTOP_CAP, False), "desktop-wsl": (None, True)}
lane_ctx = {n: {"host": gk.LANE_HOST[n],
                "resource_class": gk.LANE_CLASS[n],
                "duration_band": gk.LANE_BAND[n]} for n in gk.LANES}
ctx = {"lanes": lane_ctx,
       "load": {"gpu": {"executing": 1, "waiting": 0}},
       "locks": {}, "held_hosts": set(), "capabilities": caps,
       "history": hist, "now_iso": "2026-11-02T12:00:00Z"}
j = {"program": "det", "workflow": "run.yml", "needs": {"gpu": True},
     "venue": "auto", "expected_min": None, "maintenance": False}
lane1, snap1 = gk.resolve_auto_lane(j, ctx)
lane2, snap2 = gk.resolve_auto_lane(j, ctx)
print(json.dumps({"same_lane": lane1 == lane2,
                  "same_snap": snap1 == snap2, "lane": lane1,
                  "gpu_score": snap1["lanes"]["gpu"]["score"],
                  "quick_eligible": snap1["lanes"]["quick"]["eligible"]}))
""")
check("scoring is deterministic (FR-006)",
      res["same_lane"] and res["same_snap"] and res["lane"] == "gpu")
check("load is priced into the score (FR-006)",
      res["gpu_score"] == 100 - 15 + 20 + 10 + 5
      and res["quick_eligible"] is False)

# --- auto_no_fit: no GPU lane / VRAM too large ------------------
res = run_driver(r"""
job("nf1", None, program="prog", needs={"gpu": True})
decided = decided_order()
rec = read_job("nf1")
print(json.dumps({"decided": decided, "lane": rec.get("lane"),
                  "state": rec["state"],
                  "unmet": (rec.get("auto_wait") or {}).get("unmet"),
                  "nofit": evfind("auto_no_fit")}))
""", config_text="lanes:\n  active_profile: single-host-light\n")
check("gpu need on a GPU-less profile waits with auto_no_fit (FR-006)",
      res["decided"] == [] and res["lane"] is None
      and res["state"] == "submitted"
      and any("gpu" in r for r in (res["unmet"] or []))
      and len(res["nofit"]) == 1)

res = run_driver(r"""
small = dict(DESKTOP_CAP)
small["gpus"] = [{"name": "RTX 4060", "vram_mb": 4096,
                  "class": "discrete"}]
write_capability("desktop", small)
job("nf2", None, program="prog", needs={"gpu": True, "gpu_vram_mb": 8192})
decided = decided_order()
rec = read_job("nf2")
print(json.dumps({"decided": decided, "lane": rec.get("lane"),
                  "unmet": (rec.get("auto_wait") or {}).get("unmet")}))
""")
check("VRAM need above every evidenced GPU waits (FR-006)",
      res["decided"] == [] and res["lane"] is None
      and any("vram" in r for r in (res["unmet"] or [])))

# --- History medians route the undeclared job -------------------
res = run_driver(r"""
for i in range(3):
    job("old%d" % i, "quick", program="slowprog", state="completed",
        dispatched="2026-11-02T06:00:00Z",
        first_seen="2026-11-02T06:05:00Z",
        completed="2026-11-02T09:25:00Z")
job("new1", None, program="slowprog")
decided = decided_order()
rec = read_job("new1")
snap = rec.get("auto_route") or {}
print(json.dumps({"lane": rec.get("lane"), "decided": decided,
                  "median": (snap.get("history") or {}).get("median_min"),
                  "expected_used": snap.get("expected_min_used")}))
""")
check("history median routes a long program to long (FR-006)",
      res["lane"] == "long" and "new1" in res["decided"]
      and res["median"] == 200.0 and res["expected_used"] == 200.0)

# --- Lock respect, both directions -------------------------------
res = run_driver(r"""
def lock_ns(**kw):
    d = {"list": False, "lane": None, "program": None,
         "ttl_min": None, "reason": "", "suspend": False,
         "resume": False, "force": False}
    d.update(kw)
    return NS(**d)
rc, _ = call(gk.cmd_lock, lock_ns(lane="gpu",
                                  program="holder-prog",
                                  ttl_min=120))
job("lk1", None, program="other-prog", expected=200)
job("lk2", None, program="holder-prog", expected=200)
decided = decided_order()
r1, r2 = read_job("lk1"), read_job("lk2")
snap1 = r1.get("auto_route") or {}
print(json.dumps({
    "lock_rc": rc, "lk1_lane": r1.get("lane"),
    "lk2_lane": r2.get("lane"),
    "gpu_eligible_for_other": (snap1.get("lanes") or {}).get(
        "gpu", {}).get("eligible"),
    "gpu_reasons": (snap1.get("lanes") or {}).get(
        "gpu", {}).get("reasons")}))
""")
check("auto never routes a non-holder onto a locked lane (FR-006/015)",
      res["lock_rc"] == 0 and res["lk1_lane"] == "long"
      and res["gpu_eligible_for_other"] is False
      and any("locked" in r for r in (res["gpu_reasons"] or [])))
check("the holder's own auto job may use the locked lane (FR-006/015)",
      res["lk2_lane"] == "gpu")

# --- Focus conjunction across resolution -------------------------
res = run_driver(r"""
with open(gk.PAUSE_FILE, "w") as f:
    json.dump({"mode": "focused", "ts": "2026-11-02T11:30:00Z",
               "reason": "focus test", "except_keys": [],
               "except_programs": ["focus-prog"],
               "except_chats": [], "drain": False}, f)
job("f1", None, program="focus-prog", expected=200)
job("f2", None, program="other-prog", expected=200)
decided = decided_order()
print(json.dumps({"decided": decided,
                  "f1_lane": read_job("f1").get("lane"),
                  "f2_lane": read_job("f2").get("lane")}))
""")
check("focus admission still binds after resolution (FR-007/013)",
      "f1" in res["decided"] and "f2" not in res["decided"]
      and res["f1_lane"] == "gpu" and res["f2_lane"] == "gpu")

# --- Host hold excludes the host's lanes --------------------------
res = run_driver(r"""
state = {"desktop": {"last_seen": None, "last_heartbeat_ts": None,
                     "down": False, "hold": True,
                     "hold_since": "2026-11-02T11:00:00Z",
                     "going_down": None}}
with open(gk.HOSTS_FILE, "w") as f:
    json.dump(state, f)
job("hh1", None, program="prog", expected=200)
decided = decided_order()
rec = read_job("hh1")
print(json.dumps({"decided": decided, "lane": rec.get("lane"),
                  "unmet": (rec.get("auto_wait") or {}).get("unmet")}))
""")
check("a held host's lanes are ineligible for auto (FR-006)",
      res["decided"] == [] and res["lane"] is None
      and any("held" in r for r in (res["unmet"] or [])))

# --- Staleness degradation + unknown-field neutrality -------------
res = run_driver(r"""
stingy = dict(DESKTOP_CAP)
stingy["ram_total_mb"] = 1024
write_capability("desktop", stingy, probed_at="2026-11-02T08:00:00Z")
job("st1", None, program="prog", needs={"ram_mb": 8192}, expected=5)
decided = decided_order()
rec = read_job("st1")
snap = rec.get("auto_route") or {}
print(json.dumps({"lane": rec.get("lane"), "decided": decided,
                  "degraded": snap.get("degraded"),
                  "events": evnames()}))
""")
# quick, normal and gpu all tie on band fit for a 5-minute
# job (the gpu band is open); the deterministic tie-break
# (first lane name) picks gpu.
check("stale snapshot degrades instead of blocking (FR-005/FR-006)",
      res["lane"] == "gpu" and "st1" in res["decided"]
      and res["degraded"] is True and "auto_degraded" in res["events"])

res = run_driver(r"""
unknown = {"platform": "windows", "hostname": "H", "cpu_cores": None,
           "cpu_model": None, "cpu_load_1m": None, "ram_total_mb": None,
           "ram_free_mb": None, "storage_free_gb": None, "gpus": [],
           "venues": ["native"]}
write_capability("desktop", unknown)
job("un1", None, program="prog",
    needs={"ram_mb": 999999, "cpu_cores": 64}, expected=5)
decided = decided_order()
print(json.dumps({"lane": read_job("un1").get("lane"),
                  "decided": decided}))
""")
check("unknown capability fields are neutral, never zero (FR-005)",
      res["lane"] == "gpu" and "un1" in res["decided"])

# --- Venue eligibility ---------------------------------------------
res = run_driver(r"""
write_capability("desktop", DESKTOP_CAP)
job("v1", None, program="prog", venue="docker", expected=5)
job("v2", None, program="prog", venue="docker",
    image="img:1", expected=5)
decided = decided_order()
r1 = read_job("v1")
print(json.dumps({"v1_lane": r1.get("lane"),
                  "v1_unmet": (r1.get("auto_wait") or {}).get("unmet"),
                  "v2_lane": read_job("v2").get("lane"),
                  "decided": decided}))
""")
check("docker venue requires an image; with one it routes (FR-006)",
      res["v1_lane"] is None
      and any("image" in r for r in (res["v1_unmet"] or []))
      and res["v2_lane"] is not None and "v2" in res["decided"])

MAC_YAML = """hosts:
  macmini:
    type: macos
    workflows:
      mac.yml: execute
      grab.yml: grab
    identity: [LID_HOST, LID_USER, LID_OS]
workflows:
  - run.yml
  - wsl.yml
  - grab.yml
  - mac.yml
lanes:
  active_profile: macprof
  profiles:
    macprof:
      maclane:
        host: macmini
        runner_label: lane-maclane
        resource_class: any
        duration_band: [0, null]
        queued: [10, 30]
        executing:
          mode: fixed
          values: [5, 15]
"""
res = run_driver(r"""
job("v3", None, program="prog", workflow="mac.yml", venue="wsl")
decided = decided_order()
rec = read_job("v3")
print(json.dumps({"decided": decided, "lane": rec.get("lane"),
                  "unmet": (rec.get("auto_wait") or {}).get("unmet")}))
""", config_text=MAC_YAML)
check("wsl venue has no candidate on a macOS-only profile (FR-006)",
      res["decided"] == [] and res["lane"] is None
      and any("wsl" in r for r in (res["unmet"] or [])))

# --- Explicit lane beats auto; resolved jobs never re-resolve -----
res = run_driver(r"""
write_capability("desktop", DESKTOP_CAP)
job("e1", "quick", program="prog", needs={"gpu": True}, expected=5)
job("e2", None, program="prog", needs={"gpu": True})
decided = decided_order()
first = read_job("e2")
decided2 = decided_order()
second = read_job("e2")
r_e1 = read_job("e1")
print(json.dumps({
    "e1_lane": r_e1.get("lane"), "e1_auto_route": r_e1.get("auto_route"),
    "e2_lane": first.get("lane"),
    "e2_eval1": (first.get("auto_route") or {}).get("evaluated_at"),
    "e2_eval2": (second.get("auto_route") or {}).get("evaluated_at")}))
""")
check("an explicit lane is never re-routed by auto (FR-007)",
      res["e1_lane"] == "quick" and res["e1_auto_route"] is None)
check("a resolved auto job is never re-resolved (FR-007)",
      res["e2_lane"] == "gpu"
      and res["e2_eval1"] is not None
      and res["e2_eval1"] == res["e2_eval2"])

# --- Probe parsers against fixtures --------------------------------
WINDOWS_REPORT = """SYNCYARD_PROBE 1 platform=windows
## hostname
EXAMPLE-HOST
## cpu


Name                      : Intel(R) Core(TM) i9-13900K
NumberOfCores             : 24
NumberOfLogicalProcessors : 32

## os


TotalVisibleMemorySize    : 33453056
FreePhysicalMemory        : 8102332

## disk


DeviceID                  : C:
FreeSpace                 : 549755813888
Size                      : 1099511627776

## nvidia
NVIDIA GeForce RTX 4090, 24576 MiB
## venues
venue wsl
venue docker
PROBE_END
"""

LINUX_REPORT = """SYNCYARD_PROBE 1 platform=linux
## hostname
example-desktop
## cpuinfo
model name	: AMD Ryzen 9 7950X 16-Core Processor
processors: 32
## meminfo
MemTotal:       65536000 kB
MemAvailable:   40000000 kB
## loadavg
1.25 0.90 0.60 2/1234 9999
## df
/dev/nvme0n1p2  976562500  500000000  476562500  52% /
## nvidia
NVIDIA GeForce RTX 4070, 12282 MiB
## venues
venue docker
PROBE_END
"""

DARWIN_REPORT = """SYNCYARD_PROBE 1 platform=darwin
## hostname
example-mac
## sysctl
Apple M2 Pro
cores: 10
memsize: 34359738368
## vm_stat
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                100000.
Pages active:                             500000.
## loadavg
{ 1.50 1.00 0.75 }
## df
/dev/disk3s1   976490576  500000000  476490576  52% /
## gpu
      Chipset Model: Apple M2 Pro
      Metal Support: Metal 3
## venues
PROBE_END
"""

DARWIN_INTEL_REPORT = DARWIN_REPORT.replace(
    "Apple M2 Pro\ncores: 10", "Intel Core i9\ncores: 8").replace(
    "      Chipset Model: Apple M2 Pro\n      Metal Support: Metal 3",
    "      Chipset Model: AMD Radeon Pro 5500M\n"
    "      VRAM (Total): 4 GB\n      Metal Support: Metal 3")


def parse_in_driver(report, name):
    body = ("snap = gk.parse_probe_report(%r)\n"
            "print(json.dumps(snap))\n" % report)
    return run_driver(body)


snap = parse_in_driver(WINDOWS_REPORT, "windows")
check("windows probe parser (FR-005)",
      snap["platform"] == "windows"
      and snap["hostname"] == "EXAMPLE-HOST"
      and snap["cpu_cores"] == 24
      and snap["cpu_model"].startswith("Intel(R)")
      and snap["ram_total_mb"] == 33453056 // 1024
      and snap["ram_free_mb"] == 8102332 // 1024
      and snap["storage_free_gb"] == round(549755813888 / (1024 ** 3), 1)
      and snap["gpus"] == [{"name": "NVIDIA GeForce RTX 4090",
                            "vram_mb": 24576, "class": "discrete"}]
      and snap["venues"] == ["native", "wsl", "docker"])

snap = parse_in_driver(LINUX_REPORT, "linux")
check("linux probe parser (FR-005)",
      snap["platform"] == "linux" and snap["cpu_cores"] == 32
      and snap["cpu_model"].startswith("AMD Ryzen")
      and snap["ram_total_mb"] == 65536000 // 1024
      and snap["ram_free_mb"] == 40000000 // 1024
      and snap["cpu_load_1m"] == 1.25
      and snap["storage_free_gb"] == round(476562500 / 1048576, 1)
      and snap["gpus"][0]["vram_mb"] == 12282
      and snap["venues"] == ["native", "docker"])

snap = parse_in_driver(DARWIN_REPORT, "darwin")
check("darwin probe parser: unified GPU, no wsl venue (FR-005)",
      snap["platform"] == "darwin" and snap["cpu_cores"] == 10
      and snap["ram_total_mb"] == 34359738368 // 1048576
      and snap["ram_free_mb"] == 100000 * 16384 // 1048576
      and snap["cpu_load_1m"] == 1.5
      and snap["gpus"] == [{"name": "Apple M2 Pro",
                            "vram_mb": 34359738368 // 1048576,
                            "class": "unified", "vram_shared": True}]
      and "wsl" not in snap["venues"])

snap = parse_in_driver(DARWIN_INTEL_REPORT, "darwin-intel")
check("darwin probe parser: Intel discrete VRAM (FR-005)",
      snap["gpus"] == [{"name": "AMD Radeon Pro 5500M",
                        "vram_mb": 4096, "class": "discrete"}])

res = run_driver("print(json.dumps(gk.parse_probe_report('garbage')))\n")
check("an unparseable report yields no snapshot (FR-005)", res is None)

# --- Probe routes: ingest, dispatch + watch ingest, refusal -------
res = run_driver(r"""
report_path = os.path.join(BASE_DIR, "report.txt")
with open(report_path, "w") as f:
    f.write(%r)
rc, out = call(gk.cmd_probe_host, NS(host="desktop", ingest=report_path))
with open(gk.HOSTS_FILE) as f:
    state = json.load(f)
cap = (state.get("desktop") or {}).get("capability") or {}
print(json.dumps({"rc": rc, "platform": cap.get("platform"),
                  "cpus": cap.get("cpu_cores"),
                  "events": evnames()}))
""" % WINDOWS_REPORT)
check("probe ingest stores the snapshot (FR-005)",
      res["rc"] == 0 and res["platform"] == "windows"
      and res["cpus"] == 24 and "capability_probed" in res["events"])

res = run_driver(r"""
rc, out = call(gk.cmd_probe_host, NS(host="desktop-wsl", ingest=None))
jobrec = None
for fn in os.listdir(gk.QUEUE_DIR):
    with open(os.path.join(gk.QUEUE_DIR, fn)) as f:
        j = json.load(f)
    if str(j.get("key") or "").startswith("gk-probe-"):
        jobrec = j
jobrec["log_tail"] = %r.splitlines()
gk._maybe_ingest_probe(jobrec)
with open(gk.HOSTS_FILE) as f:
    state = json.load(f)
cap = (state.get("desktop-wsl") or {}).get("capability") or {}
print(json.dumps({"rc": rc, "key": (jobrec or {}).get("key"),
                  "probe_host": (jobrec or {}).get("probe_host"),
                  "workflow": (jobrec or {}).get("workflow"),
                  "ingested": (jobrec or {}).get("capability_ingested"),
                  "cap_platform": cap.get("platform")}))
""" % LINUX_REPORT)
check("probe dispatch rides wsl.yml and watch-ingests (FR-005)",
      res["rc"] == 0 and res["workflow"] == "wsl.yml"
      and res["probe_host"] == "desktop-wsl"
      and res["ingested"] is True and res["cap_platform"] == "linux")

res = run_driver(r"""
rc, out = call(gk.cmd_probe_host, NS(host="linhost", ingest=None))
print(json.dumps({"rc": rc, "out": out}))
""", config_text="hosts:\n  linhost:\n    type: linux\n    workflows:\n"
                 "      run.yml: execute\n"
                 "    identity: [LID_HOST, LID_USER, LID_OS]\n")
check("no-transport host refused with the ingest route named (FR-005)",
      res["rc"] == 3 and "--ingest" in res["out"])

# --- Status + profile rendering --------------------------------------
res = run_driver(r"""
write_capability("desktop", DESKTOP_CAP)
job("s1", None, program="prog", needs={"gpu": True,
                                      "gpu_vram_mb": 999999})
decided = decided_order()
rc1, out1 = call(gk.cmd_status, NS(repo=False))
rc2, out2 = call(gk.cmd_profile, NS(list=True, show=False, name=None))
rc3, out3 = call(gk.cmd_profile, NS(list=False, show=True,
                                    name="gpu-farm"))
rc4, out4 = call(gk.cmd_profile, NS(list=False, show=True,
                                    name="nowhere"))
print(json.dumps({"status": out1, "list": out2, "show": out3,
                  "rc4": rc4}))
""")
check("status renders profile + capability (FR-008)",
      "profile: fleet-default" in res["status"]
      and "platform=windows" in res["status"]
      and "(fresh)" in res["status"])
check("status shows an unplaced auto job's why (FR-008)",
      "[auto: no fit yet" in res["status"])
check("profile --list / --show render (FR-001)",
      "fleet-default (active)" in res["list"]
      and "gpu-long" in res["show"] and "class=gpu" in res["show"]
      and res["rc4"] == 2)

# --- Probe script files == shipped constants (drift pin) --------------
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("gk_files", os.path.join(HERE, "gk.py"))
_gkmod = _ilu.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_gkmod)
    _drift = {plat: (open(os.path.join(HERE, "probes", fname)).read() == body)
              for plat, (fname, body) in _gkmod.PROBE_SCRIPTS.items()}
except SystemExit:
    _drift = {}
check("probe script files match the shipped constants (FR-005)",
      len(_drift) == 3 and all(_drift.values()), str(_drift))

print()
if fails:
    print(f"TEST_SPEC016: FAIL ({len(fails)} failing)")
    sys.exit(1)
print("TEST_SPEC016: ALL PASS")

#!/usr/bin/env python3
"""Scenario battery for spec 027 (version drift detection, H3):
the gatekeeper drift judge.

Golden style: temp dir, copied gk.py + config.py, subprocess
drivers, the backchannel heartbeat and the release-manifest
read stubbed at the module seams (the spec 025 battery's
pattern).

Covers:
  - a host whose tray beat matches the published release is
    judged `current`: tray_current ledgered exactly once
    across repeat sweeps, and the status host line shows the
    build with its drift state;
  - a host behind the release is judged `behind`: tray_drift
    ledgered exactly once across repeat sweeps, carrying BOTH
    versions, and the status line shows BEHIND with both;
  - a behind host that catches up transitions to `current`
    with one tray_current (the falsifier's positive leg: an
    old build shows behind within one sweep of its beat);
  - a failed manifest read judges `unknown`, never `current`
    (the falsifier's negative leg) — including a host that was
    current on the previous sweep: the failed read revokes
    the claim rather than coasting on it, and the status line
    renders the word unknown;
  - a host with no tray beat is never judged and costs no
    manifest read (FR-005: the read rides the sweep and only
    fires when there is something to judge).

Run: python3 gatekeeper/test_spec027.py
"""
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
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


PRELUDE = r"""
import importlib.util, io, json, os, sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from types import SimpleNamespace as NS
spec = importlib.util.spec_from_file_location(
    "gk_under_test", os.path.join(BASE_DIR, "gk.py"))
gk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gk)
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
gk.now_dt = lambda: NOW
os.makedirs(gk.QUEUE_DIR, exist_ok=True)
open(gk.LANES_LIVE_FILE, "w").close()
events = []
gk.ledger = lambda event, **kw: events.append(
    {"event": event, "kw": kw})
FETCH = {"result": ("0.4.4.0", None), "calls": 0}
def fake_fetch():
    FETCH["calls"] += 1
    return FETCH["result"]
gk.fetch_tray_release_version = fake_fetch

def hosts_state():
    if not os.path.isfile(gk.HOSTS_FILE):
        return {}
    with open(gk.HOSTS_FILE) as f:
        return json.load(f)

def release_cache():
    if not os.path.isfile(gk.TRAY_RELEASE_FILE):
        return {}
    with open(gk.TRAY_RELEASE_FILE) as f:
        return json.load(f)

def sweep_with(hb):
    gk.fetch_backchannel_heartbeat = lambda: (hb, None)
    gk.sweep_liveness([])
    gk.sweep_tray_drift()

def ev(name):
    return [e for e in events if e["event"] == name]
"""


def run_driver(body):
    d = tempfile.mkdtemp(prefix="gk-spec027-")
    try:
        shutil.copy(os.path.join(HERE, "gk.py"), d)
        shutil.copy(os.path.join(HERE, "config.py"), d)
        driver = "BASE_DIR = %r\n%s\n%s" % (d, PRELUDE, body)
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


def tray(build):
    return ('{"host": "desktop", "build": "%s", "pid": 4242,'
            ' "session": 1, "started_at": "2026-11-06T11:58:00Z",'
            ' "ts": "2026-11-06T12:00:45Z", "tick": 130,'
            ' "lanes": []}' % build)


HB = ('{"ts": "2026-11-06T12:01:00Z", "host": "EXAMPLE-HOST",'
      ' "stopped": False, "tray": %s}')

# --- Current: beat equals the release --------------------------------------
res = run_driver(r"""
hb = %s
sweep_with(hb)
sweep_with(hb)
st = hosts_state()["desktop"]
lines = gk.host_status_lines()
print(json.dumps({
    "drift": st.get("tray_drift"),
    "current_events": len(ev("tray_current")),
    "drift_events": len(ev("tray_drift")),
    "fetch_calls": FETCH["calls"],
    "cache_version": release_cache().get("version"),
    "line": [ln for ln in lines if ln.startswith("host desktop: ")],
}))
""" % (HB % tray("0.4.4.0")))
check("current: a beat at the release version is judged "
      "current, with the judged versions on the record",
      (res["drift"] or {}).get("state") == "current"
      and (res["drift"] or {}).get("build") == "0.4.4.0"
      and (res["drift"] or {}).get("release") == "0.4.4.0",
      str(res))
check("current: tray_current ledgered exactly once across "
      "repeat sweeps, tray_drift never",
      res["current_events"] == 1 and res["drift_events"] == 0,
      str(res))
check("current: one manifest read per sweep, cached at "
      "sweep cadence (FR-005)",
      res["fetch_calls"] == 2 and res["cache_version"] == "0.4.4.0",
      str(res))
check("current: the status line shows the build and its "
      "drift state (FR-003)",
      len(res["line"]) == 1 and "build 0.4.4.0" in res["line"][0]
      and "tray drift current" in res["line"][0], str(res))

# --- Behind: the falsifier's positive leg ----------------------------------
res = run_driver(r"""
hb = %s
sweep_with(hb)
sweep_with(hb)
st = hosts_state()["desktop"]
lines = gk.host_status_lines()
print(json.dumps({
    "drift": st.get("tray_drift"),
    "drift_events": ev("tray_drift"),
    "current_events": len(ev("tray_current")),
    "line": [ln for ln in lines if ln.startswith("host desktop: ")],
}))
""" % (HB % tray("0.4.2.0")))
check("behind: an old build is judged behind within one "
      "sweep of its beat arriving",
      (res["drift"] or {}).get("state") == "behind", str(res))
check("behind: tray_drift ledgered exactly once across "
      "repeat sweeps, carrying BOTH versions (FR-001)",
      len(res["drift_events"]) == 1
      and res["drift_events"][0]["kw"].get("build") == "0.4.2.0"
      and res["drift_events"][0]["kw"].get("release") == "0.4.4.0"
      and res["drift_events"][0]["kw"].get("host") == "desktop"
      and res["current_events"] == 0, str(res))
check("behind: the status line shows BEHIND with the running "
      "and current versions",
      len(res["line"]) == 1
      and "tray drift BEHIND (running 0.4.2.0, current 0.4.4.0)"
      in res["line"][0], str(res))

# --- Behind, then caught up -------------------------------------------------
res = run_driver(r"""
sweep_with(%s)
sweep_with(%s)
st = hosts_state()["desktop"]
print(json.dumps({
    "drift": st.get("tray_drift"),
    "drift_events": len(ev("tray_drift")),
    "current_events": len(ev("tray_current")),
}))
""" % (HB % tray("0.4.2.0"), HB % tray("0.4.4.0")))
check("catch-up: behind then current ledgers one tray_drift "
      "and one tray_current, ending current",
      (res["drift"] or {}).get("state") == "current"
      and res["drift_events"] == 1 and res["current_events"] == 1,
      str(res))

# --- Unknown: the falsifier's negative leg ----------------------------------
res = run_driver(r"""
hb = %s
sweep_with(hb)
st1 = hosts_state()["desktop"]
FETCH["result"] = (None, "release read exploded")
sweep_with(hb)
st2 = hosts_state()["desktop"]
lines = gk.host_status_lines()
print(json.dumps({
    "state1": (st1.get("tray_drift") or {}).get("state"),
    "state2": (st2.get("tray_drift") or {}).get("state"),
    "current_events": len(ev("tray_current")),
    "api_errors": len([e for e in ev("api_error")
                        if e["kw"].get("where")
                        == "tray_release_manifest"]),
    "line": [ln for ln in lines if ln.startswith("host desktop: ")],
}))
""" % (HB % tray("0.4.4.0")))
check("unknown: a failed manifest read revokes a previous "
      "current — the state is unknown, never current (FR-002)",
      res["state1"] == "current" and res["state2"] == "unknown",
      str(res))
check("unknown: the failed read ledgers no second "
      "tray_current and is itself ledgered as an api_error",
      res["current_events"] == 1 and res["api_errors"] == 1,
      str(res))
check("unknown: the status line renders the word unknown",
      len(res["line"]) == 1
      and "tray drift unknown" in res["line"][0], str(res))

# --- No beat: no judgement, no manifest read --------------------------------
res = run_driver(r"""
hb = {"ts": "2026-11-06T12:01:00Z", "host": "EXAMPLE-HOST",
      "stopped": False}
sweep_with(hb)
st = hosts_state().get("desktop", {})
print(json.dumps({
    "drift": st.get("tray_drift"),
    "fetch_calls": FETCH["calls"],
    "events": len(events),
}))
""")
check("no beat: no drift record, no events, and no manifest "
      "read spent (FR-005)",
      res["drift"] is None and res["fetch_calls"] == 0
      and res["events"] == 0, str(res))

print()
if fails:
    print(f"TEST_SPEC027: FAIL ({len(fails)} failing)")
    sys.exit(1)
print("TEST_SPEC027: OK")

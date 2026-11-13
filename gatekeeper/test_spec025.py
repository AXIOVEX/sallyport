#!/usr/bin/env python3
"""Scenario battery for (tray liveness heartbeat, H1):
the gatekeeper judge.

Golden style: temp dir, copied gk.py + config.py, subprocess
drivers, the backchannel heartbeat stubbed at the module seam
(the battery's pattern).

Covers:
  - a tray beat riding the agent heartbeat is stored on the
    host's liveness record (build, pid, lanes) and is fresh
    against its carrying heartbeat;
  - a beat lagging its carrier past liveness.tray_stale_after_s
    is judged STALE (agent alive, tray dead): tray_stale
    ledgered exactly once across repeat sweeps, and the status
    host line marks it TRAY STALE;
  - a fresh beat clears the staleness and ledgers
    tray_recovered exactly once;
  - a heartbeat with no tray record makes no tray claim at
    all (no record field set, no events).

Run: python3 gatekeeper/test_spec025.py
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
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
gk.now_dt = lambda: NOW
os.makedirs(gk.QUEUE_DIR, exist_ok=True)
open(gk.LANES_LIVE_FILE, "w").close()
events = []
gk.ledger = lambda event, **kw: events.append(event)

def hosts_state():
    if not os.path.isfile(gk.HOSTS_FILE):
        return {}
    with open(gk.HOSTS_FILE) as f:
        return json.load(f)

def sweep_with(hb):
    gk.fetch_backchannel_heartbeat = lambda: (hb, None)
    gk.sweep_liveness([])
"""


def run_driver(body):
    d = tempfile.mkdtemp(prefix="gk-spec025-")
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


TRAY = """{"host": "desktop", "build": "0.4.4.0", "pid": 4242,
           "session": 1, "started_at": "2026-11-05T11:58:00Z",
           "ts": "2026-11-05T12:00:45Z", "tick": 130,
           "lanes": [{"lane": "long", "running": True,
                      "faulted": False},
                     {"lane": "gpu", "running": False,
                      "faulted": True}]}"""

# --- Fresh beat: stored, not stale, shown without a stale mark -----------
res = run_driver(r"""
tray = %s
hb = {"ts": "2026-11-05T12:01:00Z", "host": "EXAMPLE-HOST",
      "stopped": False, "tray": tray}
sweep_with(hb)
st = hosts_state()["desktop"]
lines = gk.host_status_lines()
print(json.dumps({
    "bound": gk.TRAY_STALE_AFTER_S,
    "build": (st.get("tray") or {}).get("build"),
    "pid": (st.get("tray") or {}).get("pid"),
    "faulted_gpu": [l for l in (st.get("tray") or {}).get(
        "lanes", []) if l.get("lane") == "gpu"],
    "stale": st.get("tray_stale"),
    "stale_events": events.count("tray_stale"),
    "line": [ln for ln in lines if ln.startswith("host desktop: ")],
}))
""" % TRAY)
check("config: liveness.tray_stale_after_s defaults to 300",
      res["bound"] == 300, str(res))
check("tray beat: stored on the host record, fresh against "
      "its carrying heartbeat",
      res["build"] == "0.4.4.0" and res["pid"] == 4242
      and res["stale"] is False and res["stale_events"] == 0,
      str(res))
check("tray beat: per-lane state rides verbatim (faulted lane "
      "included)",
      res["faulted_gpu"] == [{"lane": "gpu", "running": False,
                              "faulted": True}], str(res))
check("tray beat: the status line shows the beat and its build "
      "with no stale mark",
      len(res["line"]) == 1 and "tray beat" in res["line"][0]
      and "0.4.4.0" in res["line"][0]
      and "TRAY STALE" not in res["line"][0], str(res))

# --- Lagging beat: stale, ledgered once, shown; then recovery ------------
res = run_driver(r"""
tray = %s
hb1 = {"ts": "2026-11-05T12:01:00Z", "host": "EXAMPLE-HOST",
       "stopped": False, "tray": tray}
sweep_with(hb1)
hb2 = dict(hb1, ts="2026-11-05T12:10:00Z")
sweep_with(hb2)
st2 = hosts_state()["desktop"]
sweep_with(hb2)
lines_stale = gk.host_status_lines()
stale_events = events.count("tray_stale")
tray2 = dict(tray, ts="2026-11-05T12:10:20Z", tick=131)
hb3 = dict(hb1, ts="2026-11-05T12:10:30Z", tray=tray2)
sweep_with(hb3)
st3 = hosts_state()["desktop"]
print(json.dumps({
    "stale2": st2.get("tray_stale"),
    "stale_events": stale_events,
    "recovered": events.count("tray_recovered"),
    "stale3": st3.get("tray_stale"),
    "line_stale": [ln for ln in lines_stale
                   if ln.startswith("host desktop: ")],
}))
""" % TRAY)
check("tray beat: a beat 555 s older than its carrier is STALE "
      "— ledgered exactly once across repeat sweeps",
      res["stale2"] is True and res["stale_events"] == 1, str(res))
check("tray beat: the status line names the agent-alive / "
      "tray-dead state while stale",
      len(res["line_stale"]) == 1
      and "TRAY STALE" in res["line_stale"][0], str(res))
check("tray beat: a fresh beat clears the staleness and "
      "ledgers tray_recovered once",
      res["recovered"] == 1 and res["stale3"] is False, str(res))

# --- No tray record: no claim --------------------------------------------
res = run_driver(r"""
hb = {"ts": "2026-11-05T12:01:00Z", "host": "EXAMPLE-HOST",
      "stopped": False}
sweep_with(hb)
st = hosts_state().get("desktop", {})
lines = gk.host_status_lines()
print(json.dumps({
    "tray_field": st.get("tray"),
    "tray_events": events.count("tray_stale")
                   + events.count("tray_recovered"),
    "line": [ln for ln in lines if ln.startswith("host desktop: ")],
}))
""")
check("no tray record: nothing stored, no events, no tray "
      "claim on the status line (the honest unknown)",
      res["tray_field"] is None and res["tray_events"] == 0
      and len(res["line"]) == 1
      and "tray" not in res["line"][0], str(res))

print()
if fails:
    print(f"TEST_SPEC025: FAIL ({len(fails)} failing)")
    sys.exit(1)
print("TEST_SPEC025: OK")

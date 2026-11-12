#!/usr/bin/env python3
"""Scenario battery for spec 011 Phases 2-3 (host drain + the
going-down writer contract + guard beats).

Golden style: temp dir, copied gk.py + config.py, subprocess
drivers, stubs at the module seams (ledger, gh_json,
fetch_log_once, dispatch_one).

Covers:
  - `gk drain --host`: CLEAN with nothing running (and the
    drain hold it leaves standing), a checkpointed job
    (identical held_at_checkpoint harvest to a sweep's), a
    job that finishes inside the window, an ignoring job
    named UNCHECKPOINTED at its window's expiry, a grab job
    that cannot checkpoint (no hold venue);
  - the drain hold in dispatch: program jobs and plain
    maintenance wait, the gatekeeper's own gk-hold-* signal
    jobs flow (the drain can deliver its own signals);
  - reconciliation with spec 013: the global pause record is
    untouched by a host drain, `gk resume` does not clear
    the host's drain hold, `gk host-resume` does (and clears
    a going-down hold in the same act);
  - the writer/ingest contract: a record in the exact shape
    the host guard writes (runner/guard/SyncyardGuard.cs)
    ingests through the shipped Phase 1 path, exactly once
    per (host, ts) — the same record again is a no-op, a
    new ts ingests again;
  - guard beats riding the heartbeat: stored on the host
    record, stale judged against the carrying heartbeat,
    guard_stale ledgered exactly once per transition and
    guard_recovered on the fresh beat.

Run: python3 gatekeeper/test_spec011.py
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
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
gk.now_dt = lambda: NOW
gk.DRAIN_POLL_S = 0
os.makedirs(gk.QUEUE_DIR, exist_ok=True)
open(gk.LANES_LIVE_FILE, "w").close()
events = []
gk.ledger = lambda event, **kw: events.append(event)

def job(jid, lane, key=None, program="prog", workflow="wsl.yml",
        state="submitted", created="2026-11-02T11:00:00Z",
        expected=None, maint=False, chat=None,
        run_id=None, run_status=None):
    j = {"id": jid, "key": key or jid, "program": program,
         "workflow": workflow, "lane": lane, "state": state,
         "created_at": created, "script_sha256": "sha-" + jid,
         "maintenance": maint, "notify_chat": chat,
         "run_id": run_id, "run_status": run_status,
         "run_conclusion": None, "flags": [], "watermarks": {},
         "expected_min": expected, "cwd": "/home/you"}
    path = os.path.join(gk.QUEUE_DIR, jid + ".json")
    with open(path, "w") as f:
        json.dump(j, f)
    return j

def write_pause(rec):
    with open(gk.PAUSE_FILE, "w") as f:
        json.dump(rec, f)

def read_job(jid):
    with open(os.path.join(gk.QUEUE_DIR, jid + ".json")) as f:
        return json.load(f)

def hosts_state():
    if not os.path.isfile(gk.HOSTS_FILE):
        return {}
    with open(gk.HOSTS_FILE) as f:
        return json.load(f)

def decided_order():
    decided = []
    gk.dispatch_one = lambda job, jobs: (decided.append(job["id"]),
                                         True)[1]
    gk.dispatch_pass(gk.load_jobs(), {})
    return decided

def call(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(args)
    return rc, buf.getvalue()

def drain(window=None):
    return call(gk.cmd_drain, NS(host="desktop", window=window,
                                 requested_by="test-chat"))
"""


def run_driver(body):
    d = tempfile.mkdtemp(prefix="gk-spec011-")
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


# --- Drain: nothing running -> CLEAN, and the hold stands ---------------
res = run_driver(r"""
job("q", "quick", key="queued-job")
rc, out = drain()
hold_after_drain = hosts_state().get("desktop", {}).get("drain_hold")
decided_held = decided_order()
sig = job("sig", "normal", key="gk-hold-abc", maint=True)
plain = job("pm", "long", key="plain-maint", maint=True)
decided2 = decided_order()
rc2, _ = call(gk.cmd_host_resume, NS(host="desktop"))
decided_after = decided_order()
print(json.dumps({
    "rc": rc, "clean": "DRAIN CLEAN" in out,
    "hold": hold_after_drain,
    "decided_held": decided_held, "decided2": decided2,
    "rc_resume": rc2, "decided_after": decided_after,
    "events": events}))
""")
check("drain: no running jobs -> CLEAN (rc 0), drain hold set",
      res["rc"] == 0 and res["clean"] and res["hold"] is True
      and "host_drain_started" in res["events"]
      and "host_drain_verdict" in res["events"], str(res))
check("drain hold: a queued program job waits under it",
      res["decided_held"] == [], str(res))
check("drain hold: the gatekeeper's own gk-hold-* signal job "
      "flows, a plain maintenance job does not",
      res["decided2"] == ["sig"], str(res))
check("host-resume clears the drain hold and dispatch flows",
      res["rc_resume"] == 0
      and set(res["decided_after"]) == {"q", "sig", "pm"},
      str(res))

# --- Drain: a checkpointed job ------------------------------------------
res = run_driver(r"""
job("w", "quick", key="worker", state="dispatched", run_id=9,
    run_status="in_progress")
gk.gh_json = lambda args, timeout=120: (
    {"status": "completed", "conclusion": "failure",
     "createdAt": "2026-11-02T11:30:00Z"}, None)
def fetch(job):
    job["markers"] = ["CHECKPOINT_PAUSED at unit 3"]
    job["log_fetched_at"] = "x"
    return True
gk.fetch_log_once = fetch
rc, out = drain()
w = read_job("w")
keys = {j["key"] for j in gk.load_jobs()}
print(json.dumps({
    "rc": rc, "clean": "DRAIN CLEAN" in out,
    "named": "CHECKPOINTED" in out, "state": w["state"],
    "flag": w.get("hold_requested"),
    "signal_queued": any(k.startswith("gk-hold-") for k in keys),
    "events": events}))
""")
check("drain: a job honoring the hold is CHECKPOINTED — the "
      "record lands held_at_checkpoint through the same watch "
      "machinery a sweep runs",
      res["rc"] == 0 and res["clean"] and res["named"]
      and res["state"] == "held_at_checkpoint"
      and res["flag"] is True and res["signal_queued"]
      and "hold_requested" in res["events"]
      and "held_at_checkpoint" in res["events"], str(res))

# --- Drain: a job that finishes inside the window ------------------------
res = run_driver(r"""
job("f", "normal", key="finisher", state="watching", run_id=10,
    run_status="in_progress")
gk.gh_json = lambda args, timeout=120: (
    {"status": "completed", "conclusion": "success",
     "createdAt": "2026-11-02T11:30:00Z"}, None)
def fetch(job):
    job["markers"] = ["ALL_DONE=1"]
    job["log_fetched_at"] = "x"
    return True
gk.fetch_log_once = fetch
rc, out = drain()
print(json.dumps({"rc": rc, "clean": "DRAIN CLEAN" in out,
                  "named": "FINISHED" in out,
                  "state": read_job("f")["state"]}))
""")
check("drain: a job completing in the window is FINISHED, "
      "not interrupted — verdict stays CLEAN",
      res["rc"] == 0 and res["clean"] and res["named"]
      and res["state"] == "completed", str(res))

# --- Drain: an ignoring job is named at expiry ---------------------------
res = run_driver(r"""
job("i", "quick", key="ignorer", state="dispatched", run_id=11,
    run_status="in_progress")
gk.gh_json = lambda args, timeout=120: (
    {"status": "in_progress", "conclusion": None,
     "createdAt": "2026-11-02T11:30:00Z"}, None)
rc, out = drain(window=1)
print(json.dumps({"rc": rc, "interrupting": "DRAIN INTERRUPTING" in out,
                  "named": "UNCHECKPOINTED" in out,
                  "why": "did not honor the checkpoint hold" in out,
                  "events_tail": events[-2:]}))
""")
check("drain: a job still running at its window's expiry is "
      "UNCHECKPOINTED and the verdict is INTERRUPTING (rc 1)",
      res["rc"] == 1 and res["interrupting"] and res["named"]
      and res["why"], str(res))

# --- Drain: a grab job cannot checkpoint ---------------------------------
res = run_driver(r"""
job("g", "quick", key="grabber", workflow="grab.yml",
    state="dispatched", run_id=12, run_status="in_progress")
gk.gh_json = lambda args, timeout=120: (
    {"status": "in_progress", "conclusion": None,
     "createdAt": "2026-11-02T11:30:00Z"}, None)
rc, out = drain(window=1)
print(json.dumps({"rc": rc, "named": "UNCHECKPOINTED" in out,
                  "why": "no checkpoint hold venue" in out,
                  "flag": read_job("g").get("hold_requested")}))
""")
check("drain: a grab job is never signaled (no hold venue) "
      "and is named UNCHECKPOINTED with that reason",
      res["rc"] == 1 and res["named"] and res["why"]
      and res["flag"] is None, str(res))

# --- Drain: unknown host is a usage error --------------------------------
res = run_driver(r"""
rc, out = call(gk.cmd_drain, NS(host="nowhere", window=None,
                                requested_by=None))
print(json.dumps({"rc": rc, "events": events}))
""")
check("drain: unknown host -> rc 2, nothing ledgered but the "
      "caller's own record", res["rc"] == 2 and res["events"] == [],
      str(res))

# --- Reconciliation with the spec 013 operator drain ---------------------
res = run_driver(r"""
write_pause({"ts": "2026-11-02T11:00:00Z", "reason": "ops",
             "drain": True})
rc, out = drain()
pause_after_drain = os.path.isfile(gk.PAUSE_FILE)
hold_after_drain = hosts_state()["desktop"].get("drain_hold")
rc2, _ = call(gk.cmd_resume, NS(job=None))
pause_after_resume = os.path.isfile(gk.PAUSE_FILE)
hold_after_resume = hosts_state()["desktop"].get("drain_hold")
print(json.dumps({"rc": rc, "pause_after_drain": pause_after_drain,
                  "hold_after_drain": hold_after_drain,
                  "pause_after_resume": pause_after_resume,
                  "hold_after_resume": hold_after_resume}))
""")
check("reconciliation: a host drain leaves the global pause "
      "record untouched, and `gk resume` clears the global "
      "record only — the host's drain hold stands until "
      "`gk host-resume`",
      res["rc"] == 0 and res["pause_after_drain"]
      and res["hold_after_drain"] is True
      and res["pause_after_resume"] is False
      and res["hold_after_resume"] is True, str(res))

# --- Per-lane window resolution ------------------------------------------
res = run_driver(r"""
base = gk.drain_window_for_lane("gpu")
gk.DRAIN_PER_LANE_WINDOW_S = {"gpu": 300}
over = gk.drain_window_for_lane("gpu")
other = gk.drain_window_for_lane("quick")
print(json.dumps({"base": base, "over": over, "other": other}))
""")
check("drain windows: default 120 for every lane, per-lane "
      "override wins for its lane only",
      res == {"base": 120, "over": 300, "other": 120}, str(res))

# --- The writer/ingest contract (the guard's record shape) ---------------
res = run_driver(r"""
job("w", "quick", key="worker", state="watching", run_id=42,
    run_status="in_progress")
# The exact record the host guard writes (SyncyardGuard.cs):
# same keys, same types, trigger from the shipped vocabulary,
# supervised entries as {id, checkpoint}.
record = {"host": "desktop", "ts": "2026-11-02T12:00:00Z",
          "trigger": "shutdown", "active_run_ids": [42],
          "supervised": [{"id": "camp-1", "checkpoint": "unit-7"}],
          "uncheckpointed": ["20261102T110000Z-worker"]}
r1 = gk.ingest_going_down(record)
r2 = gk.ingest_going_down(record)
record2 = dict(record, ts="2026-11-02T12:05:00Z")
r3 = gk.ingest_going_down(record2)
w = read_job("w")
st = hosts_state()["desktop"]
print(json.dumps({
    "r1": bool(r1), "r2_none": r2 is None, "r3": bool(r3),
    "annotated": w.get("interrupted_by"),
    "hold": st.get("hold"),
    "stored_supervised": st["going_down"]["supervised"],
    "gd_events": events.count("host_going_down"),
    "interrupted_events": events.count("interrupted")}))
""")
check("writer contract: the guard-shaped record ingests — "
      "affected job annotated, host hold set, supervised "
      "entries stored verbatim",
      res["r1"] and res["annotated"] == "host-shutdown"
      and res["hold"] is True
      and res["stored_supervised"] == [
          {"id": "camp-1", "checkpoint": "unit-7"}], str(res))
check("writer contract: exactly once per (host, ts) — the "
      "same record is a no-op, a new ts ingests again",
      res["r2_none"] and res["r3"]
      and res["gd_events"] == 2
      and res["interrupted_events"] == 2, str(res))

# --- Guard beats via the heartbeat ---------------------------------------
res = run_driver(r"""
job("w", "quick", key="worker", state="watching", run_id=42,
    run_status="in_progress")
hb1 = {"ts": "2026-11-02T12:01:00Z", "host": "EXAMPLE-HOST",
       "stopped": False,
       "guard": {"host": "desktop", "ts": "2026-11-02T12:00:30Z",
                 "active": 2, "draining": False}}
gk.fetch_backchannel_heartbeat = lambda: (hb1, None)
gk.sweep_liveness(gk.load_jobs())
st1 = hosts_state()["desktop"]
hb2 = dict(hb1, ts="2026-11-02T12:10:00Z")
gk.fetch_backchannel_heartbeat = lambda: (hb2, None)
gk.sweep_liveness(gk.load_jobs())
st2 = hosts_state()["desktop"]
lines_stale = gk.host_status_lines()
gk.sweep_liveness(gk.load_jobs())
stale_events = events.count("guard_stale")
hb3 = dict(hb1, ts="2026-11-02T12:10:30Z",
           guard={"host": "desktop", "ts": "2026-11-02T12:10:20Z",
                  "active": 0, "draining": False})
gk.fetch_backchannel_heartbeat = lambda: (hb3, None)
gk.sweep_liveness(gk.load_jobs())
st3 = hosts_state()["desktop"]
lines = lines_stale
print(json.dumps({
    "active1": (st1.get("guard") or {}).get("active"),
    "stale1": st1.get("guard_stale"), "stale2": st2.get("guard_stale"),
    "stale_events": stale_events,
    "recovered": events.count("guard_recovered"),
    "stale3": st3.get("guard_stale"),
    "line_stale": any("guard beat" in ln and "STALE" in ln
                      for ln in lines)}))
""")
check("guard beat: stored on the host record, fresh against "
      "its carrying heartbeat",
      res["active1"] == 2 and res["stale1"] is False, str(res))
check("guard beat: a beat 570 s older than its carrier is "
      "STALE — ledgered exactly once across repeat sweeps and "
      "shown in the status lines",
      res["stale2"] is True and res["stale_events"] == 1
      and res["line_stale"], str(res))
check("guard beat: a fresh beat clears the staleness and "
      "ledgers guard_recovered once",
      res["recovered"] == 1 and res["stale3"] is False, str(res))

print()
if fails:
    print(f"TEST_SPEC011: FAIL ({len(fails)} failing)")
    sys.exit(1)
print("TEST_SPEC011: ALL PASS")

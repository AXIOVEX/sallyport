#!/usr/bin/env python3
"""Scenario battery for spec 015 (lane priority lock).

Golden style (as test_spec013.py): temp dir, copied gk.py +
config.py, subprocess drivers, stub dispatch_one / ledger at
the module seams, fixed evaluation instant with a rebindable
clock for the expiry scenarios. The ledger stub captures full
events (name + fields), because several pins are about what
an event *carries* (the holder's notify chat on an expiry or
an override release).

Fixture choice: LANE_CONCURRENCY is raised to 99 in the
prelude. Lane serialization is spec 001/013 behavior, pinned
by their own batteries; here it would mask the lock gate
(two same-lane jobs are the lock's whole subject), so the
fixture removes it as a variable — the lock scenarios then
decide on lock state alone.

Covers: acquire + holder-only dispatch; the second-holder
refusal naming holder, state, expiry; renewal (TTL extended,
state preserved); priority-200 non-holder waiting while a
priority-0 holder dispatches; release at the sweep boundary
(and the non-holder release refusal); a running run untouched
by acquire/release; expiry as release semantics + ledger +
notice, for ACTIVE and SUSPENDED locks; suspend -> lane flows
+ new acquires still blocked; resume restores enforcement;
global pause preserving lock state; focus ^ lock conjunction
in both directions; maintenance bypass + lock_bypassed;
drain/host-hold leaving lock state byte-identical;
persistence across a simulated restart; legacy no-file
behavior; TTL bounds; the FR-013 write-failure invariant;
status / --list / outbox visibility.

Run: python3 gatekeeper/test_spec015.py
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
NOW_HOLDER = {"now": datetime(2026, 11, 2, 12, 0,
                              tzinfo=timezone.utc)}
gk.now_dt = lambda: NOW_HOLDER["now"]
os.makedirs(gk.QUEUE_DIR, exist_ok=True)
open(gk.LANES_LIVE_FILE, "w").close()
gk.LANE_CONCURRENCY = {lane: 99 for lane in gk.LANES}
events = []
gk.ledger = lambda event, **kw: events.append(
    dict({"event": event}, **kw))

def set_now(s):
    NOW_HOLDER["now"] = datetime.strptime(
        s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

def reload_gk():
    # A simulated gatekeeper restart: a fresh module instance
    # over the same state directory (spec 015 FR-010).
    s2 = importlib.util.spec_from_file_location(
        "gk_restarted", os.path.join(BASE_DIR, "gk.py"))
    m = importlib.util.module_from_spec(s2)
    s2.loader.exec_module(m)
    m.now_dt = lambda: NOW_HOLDER["now"]
    m.ledger = gk.ledger
    m.LANE_CONCURRENCY = {lane: 99 for lane in m.LANES}
    return m

def job(jid, lane, key=None, program="prog", workflow="wsl.yml",
        state="submitted", created="2026-11-02T11:00:00Z",
        expected=None, maint=False, chat=None, prio=None,
        run_id=None, run_status=None):
    j = {"id": jid, "key": key or jid, "program": program,
         "workflow": workflow, "lane": lane, "state": state,
         "created_at": created, "script_sha256": "sha-" + jid,
         "maintenance": maint, "notify_chat": chat,
         "run_id": run_id, "run_status": run_status,
         "run_conclusion": None, "flags": [], "watermarks": {},
         "expected_min": expected, "cwd": "/home/you"}
    if prio is not None:
        j["priority"], j["priority_source"] = prio
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

def read_locks():
    if not os.path.isfile(gk.LOCKS_FILE):
        return None
    with open(gk.LOCKS_FILE) as f:
        return json.load(f)

def read_locks_raw():
    if not os.path.isfile(gk.LOCKS_FILE):
        return None
    with open(gk.LOCKS_FILE, "rb") as f:
        return f.read().decode("utf-8")

def decided_order(mod=None):
    mod = mod or gk
    decided = []
    mod.dispatch_one = lambda job, jobs: (
        decided.append(job["id"]), True)[1]
    mod.dispatch_pass(mod.load_jobs(), {})
    return decided

def lock_ns(**kw):
    d = {"list": False, "lane": None, "program": None,
         "ttl_min": None, "reason": "", "suspend": False,
         "resume": False, "force": False}
    d.update(kw)
    return NS(**d)

def unlock_ns(**kw):
    d = {"lane": None, "program": None, "force": False}
    d.update(kw)
    return NS(**d)

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


def run_driver(body):
    d = tempfile.mkdtemp(prefix="gk-spec015-")
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


# --- Acquire + holder-only dispatch + second-holder refusal ---------------
res = run_driver(r"""
job("h1", "gpu", program="holder-prog", chat="chat-holder")
job("o1", "gpu", program="other-prog")
job("q1", "quick", program="other-prog")
rc, _ = call(gk.cmd_lock, lock_ns(
    lane="gpu", program="holder-prog", ttl_min=120,
    reason="campaign"))
locks = read_locks()
rc2, out2 = call(gk.cmd_lock, lock_ns(
    lane="gpu", program="other-prog"))
print(json.dumps({
    "rc": rc, "rec": locks.get("gpu") if locks else None,
    "rc_refuse": rc2, "refusal": out2,
    "decided": decided_order(), "events": evnames()}))
""")
rec = res["rec"] or {}
check("acquire: record lands in LOCKS.json (holder, ACTIVE, "
      "ttl, expiry, reason, holder chat resolved from its jobs)",
      res["rc"] == 0 and rec.get("program") == "holder-prog"
      and rec.get("state") == "active"
      and rec.get("ttl_min") == 120
      and rec.get("expires_at") == "2026-11-02T14:00:00Z"
      and rec.get("acquired_at") == "2026-11-02T12:00:00Z"
      and rec.get("reason") == "campaign"
      and rec.get("notify_chat") == "chat-holder"
      and "lock_acquired" in res["events"], str(res))
check("acquire: holder-only dispatch on the locked lane; "
      "other lanes flow",
      res["decided"] == ["h1", "q1"], str(res))
check("acquire: second holder REFUSED (3) naming the holder, "
      "its state, and its expiry",
      res["rc_refuse"] == 3 and "holder-prog" in res["refusal"]
      and "ACTIVE" in res["refusal"]
      and "2026-11-02T14:00:00Z" in res["refusal"], str(res))

# --- Priority never trumps a lock -------------------------------------------
res = run_driver(r"""
job("big", "gpu", program="other-prog", prio=(200, "human"),
    created="2026-11-02T10:00:00Z")
job("small", "gpu", program="holder-prog", prio=(0, "human"),
    created="2026-11-02T11:30:00Z")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
decided = decided_order()
big = read_job("big")
print(json.dumps({"decided": decided,
                  "big_state": big["state"],
                  "big_prio": big.get("priority")}))
""")
check("priority never trumps: the human-200 non-holder waits "
      "while the human-0 holder dispatches; the waiting job's "
      "record is untouched (not refused, not reordered)",
      res["decided"] == ["small"] and res["big_state"] == "submitted"
      and res["big_prio"] == 200, str(res))

# --- Release at the sweep boundary ------------------------------------------
res = run_driver(r"""
job("o1", "gpu", program="other-prog")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
before = decided_order()
rc_no, _ = call(gk.cmd_unlock, unlock_ns(
    lane="gpu", program="other-prog"))
still = decided_order()
rc_ok, _ = call(gk.cmd_unlock, unlock_ns(
    lane="gpu", program="holder-prog"))
after = decided_order()
rel = evfind("lock_released")
print(json.dumps({"before": before, "rc_no": rc_no,
                  "still": still, "rc_ok": rc_ok,
                  "after": after, "locks": read_locks(),
                  "released": rel}))
""")
check("release: a job submitted before release waits until "
      "the release lands, then dispatches on the next pass",
      res["before"] == [] and res["still"] == []
      and res["after"] == ["o1"], str(res))
check("release: non-holder release refused (3); holder "
      "release ledgers lock_released with the actor and "
      "clears the record",
      res["rc_no"] == 3 and res["rc_ok"] == 0
      and res["locks"] == {}
      and len(res["released"]) == 1
      and res["released"][0].get("actor") == "holder-prog",
      str(res))

# --- Operator override release ------------------------------------------------
res = run_driver(r"""
job("hj", "gpu", program="holder-prog", chat="chat-holder")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
rc, _ = call(gk.cmd_unlock, unlock_ns(
    lane="gpu", program="ops", force=True))
ovr = evfind("lock_released_override")
rc2, out2 = call(gk.cmd_unlock, unlock_ns(lane="gpu"))
print(json.dumps({"rc": rc, "ovr": ovr, "rc_unlocked": rc2,
                  "unlocked_msg": "no lock in effect" in out2}))
""")
check("override release: --force by a non-holder releases, "
      "ledgers lock_released_override carrying the holder's "
      "notify chat; unlocking an unlocked lane is a no-op (0)",
      res["rc"] == 0 and len(res["ovr"]) == 1
      and res["ovr"][0].get("program") == "holder-prog"
      and res["ovr"][0].get("actor") == "ops"
      and res["ovr"][0].get("notify_chat") == "chat-holder"
      and res["rc_unlocked"] == 0 and res["unlocked_msg"],
      str(res))

# --- Renewal ------------------------------------------------------------------
res = run_driver(r"""
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog",
                          ttl_min=60))
first = read_locks()["gpu"]
set_now("2026-11-02T12:30:00Z")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
renewed = read_locks()["gpu"]
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog",
                          suspend=True))
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog",
                          ttl_min=30))
after_susp = read_locks()["gpu"]
print(json.dumps({"first_exp": first["expires_at"],
                  "renewed": renewed,
                  "after_susp": after_susp,
                  "events": evnames()}))
""")
check("renew: same-holder re-acquire extends the TTL from "
      "now (stored TTL reused) and ledgers lock_renewed",
      res["first_exp"] == "2026-11-02T13:00:00Z"
      and res["renewed"]["expires_at"] == "2026-11-02T13:30:00Z"
      and res["renewed"]["ttl_min"] == 60
      and "lock_renewed" in res["events"], str(res))
check("renew: a suspended lock stays suspended across a "
      "renewal (renewal never silently re-activates)",
      res["after_susp"]["state"] == "suspended"
      and res["after_susp"]["ttl_min"] == 30
      and res["after_susp"]["expires_at"] == "2026-11-02T13:00:00Z",
      str(res))

# --- Running runs are untouched -------------------------------------------------
res = run_driver(r"""
job("r1", "gpu", program="other-prog", state="watching",
    run_id=42, run_status="in_progress")
before = read_job("r1")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
call(gk.cmd_unlock, unlock_ns(lane="gpu",
                              program="holder-prog"))
after = read_job("r1")
print(json.dumps({"same": before == after,
                  "state": after["state"],
                  "events": evnames()}))
""")
check("running run untouched: acquire + release leave a "
      "watching job's record byte-for-byte the job it was; "
      "no cancellation-shaped event exists",
      res["same"] and res["state"] == "watching"
      and not any("cancel" in e for e in res["events"]), str(res))

# --- Expiry: ACTIVE -------------------------------------------------------------
res = run_driver(r"""
job("hj", "gpu", program="holder-prog", chat="chat-holder")
job("o1", "gpu", program="other-prog")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog",
                          ttl_min=60))
before = decided_order()
set_now("2026-11-02T13:00:01Z")
after = decided_order()
exp = evfind("lock_expired")
print(json.dumps({"before": before, "after": after,
                  "expired": exp, "locks": read_locks()}))
""")
check("expiry: before the TTL the non-holder waits; on the "
      "first pass after it, the lane flows (release semantics)",
      res["before"] == ["hj"] and res["after"] == ["hj", "o1"],
      str(res))
check("expiry: lock_expired ledgers with the holder and its "
      "registered chat, and the record is gone",
      len(res["expired"]) == 1
      and res["expired"][0].get("program") == "holder-prog"
      and res["expired"][0].get("state") == "active"
      and res["expired"][0].get("notify_chat") == "chat-holder"
      and res["locks"] == {}, str(res))

# --- Expiry: SUSPENDED expires on the same clock --------------------------------
res = run_driver(r"""
job("o1", "gpu", program="other-prog")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog",
                          ttl_min=60))
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog",
                          suspend=True))
set_now("2026-11-02T13:00:01Z")
decided = decided_order()
exp = evfind("lock_expired")
print(json.dumps({"decided": decided, "expired": exp,
                  "locks": read_locks()}))
""")
check("expiry: a SUSPENDED lock expires on the same clock "
      "(a forgotten suspended claim cannot squat the lane)",
      res["decided"] == ["o1"] and len(res["expired"]) == 1
      and res["expired"][0].get("state") == "suspended"
      and res["locks"] == {}, str(res))

# --- Suspend / resume -------------------------------------------------------------
res = run_driver(r"""
job("h1", "gpu", program="holder-prog")
job("o1", "gpu", program="other-prog")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
rc_no, _ = call(gk.cmd_lock, lock_ns(
    lane="gpu", program="other-prog", suspend=True))
rc_s, _ = call(gk.cmd_lock, lock_ns(
    lane="gpu", program="holder-prog", suspend=True))
state_s = read_locks()["gpu"]["state"]
rc_acq, out_acq = call(gk.cmd_lock, lock_ns(
    lane="gpu", program="other-prog"))
flows = decided_order()
rc_r, _ = call(gk.cmd_lock, lock_ns(
    lane="gpu", program="holder-prog", resume=True))
state_r = read_locks()["gpu"]["state"]
enforced = decided_order()
print(json.dumps({"rc_no": rc_no, "rc_s": rc_s,
                  "state_s": state_s, "rc_acq": rc_acq,
                  "acq_names": "holder-prog" in out_acq
                  and "SUSPENDED" in out_acq,
                  "flows": flows, "rc_r": rc_r,
                  "state_r": state_r, "enforced": enforced,
                  "events": evnames()}))
""")
check("suspend: holder-only without --force; the lane flows "
      "while suspended, but a new acquire by another program "
      "is still refused (the claim is held)",
      res["rc_no"] == 3 and res["rc_s"] == 0
      and res["state_s"] == "suspended"
      and res["rc_acq"] == 3 and res["acq_names"]
      and res["flows"] == ["h1", "o1"], str(res))
check("resume: enforcement returns without re-competing; "
      "both transitions ledgered",
      res["rc_r"] == 0 and res["state_r"] == "active"
      and res["enforced"] == ["h1"]
      and "lock_suspended" in res["events"]
      and "lock_resumed" in res["events"], str(res))

# --- Global pause preserves lock state --------------------------------------------
res = run_driver(r"""
job("h1", "gpu", program="holder-prog")
job("o1", "gpu", program="other-prog")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
raw_before = read_locks_raw()
write_pause({"ts": "2026-11-02T12:00:00Z", "reason": "night"})
held = decided_order()
rc, _ = call(gk.cmd_resume, NS(job=None))
raw_after = read_locks_raw()
flows = decided_order()
print(json.dumps({"held": held, "rc": rc,
                  "same_bytes": raw_before == raw_after,
                  "flows": flows}))
""")
check("pause interplay: while paused nothing dispatches; "
      "pause + resume leave LOCKS.json byte-identical and "
      "the lock still enforces on resume",
      res["held"] == [] and res["rc"] == 0
      and res["same_bytes"] and res["flows"] == ["h1"],
      str(res))

# --- Focus ^ lock conjunction -------------------------------------------------------
res = run_driver(r"""
job("h1", "gpu", program="holder-prog")
job("o1", "gpu", program="other-prog")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
write_pause({"ts": "2026-11-02T12:00:00Z", "reason": "f",
             "except_programs": ["other-prog"]})
d_focus_other = decided_order()
write_pause({"ts": "2026-11-02T12:00:00Z", "reason": "f",
             "except_programs": ["holder-prog"]})
d_focus_holder = decided_order()
print(json.dumps({"focus_other": d_focus_other,
                  "focus_holder": d_focus_holder}))
""")
check("conjunction: focus-admitted non-holder still waits "
      "on the locked lane, and the lock holder the focus "
      "excludes still waits — both gates must pass",
      res["focus_other"] == []
      and res["focus_holder"] == ["h1"], str(res))

# --- Maintenance bypass ---------------------------------------------------------------
res = run_driver(r"""
job("m1", "gpu", program="ops", maint=True,
    key="gk-reboot-desktop-1")
job("o1", "gpu", program="other-prog")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
decided = decided_order()
byp = evfind("lock_bypassed")
print(json.dumps({"decided": decided, "bypassed": byp}))
""")
check("maintenance bypass: a maintenance job dispatches on "
      "an ACTIVE-locked lane held by another program, and "
      "the bypass ledgers lock_bypassed with job key + holder",
      res["decided"] == ["m1"] and len(res["bypassed"]) == 1
      and res["bypassed"][0].get("key") == "gk-reboot-desktop-1"
      and res["bypassed"][0].get("holder") == "holder-prog",
      str(res))

# --- Drain / host hold leaves locks untouched -------------------------------------------
res = run_driver(r"""
job("h1", "gpu", program="holder-prog")
job("o1", "gpu", program="other-prog")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
raw_before = read_locks_raw()
gk.ingest_going_down({"host": "desktop",
                      "ts": "2026-11-02T11:30:00Z",
                      "trigger": "shutdown",
                      "active_run_ids": [], "supervised": [],
                      "uncheckpointed": []})
call(gk.cmd_host_resume, NS(host="desktop"))
write_pause({"ts": "2026-11-02T12:00:00Z", "reason": "d",
             "drain": True})
settled = gk.maybe_settle_drain()
import os as _os
_os.unlink(gk.PAUSE_FILE)
raw_after = read_locks_raw()
enforced = decided_order()
print(json.dumps({"same_bytes": raw_before == raw_after,
                  "settled": settled, "enforced": enforced}))
""")
check("drain interplay: going-down ingest + host-resume + "
      "a pipeline drain settle leave LOCKS.json "
      "byte-identical, and the lock still enforces after",
      res["same_bytes"] and res["settled"] is True
      and res["enforced"] == ["h1"], str(res))

# --- Persistence across a simulated restart ----------------------------------------------
res = run_driver(r"""
job("h1", "gpu", program="holder-prog")
job("o1", "gpu", program="other-prog")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
gk2 = reload_gk()
locks2 = gk2.load_locks()
decided2 = decided_order(mod=gk2)
print(json.dumps({"locks2": locks2, "decided2": decided2}))
""")
check("persistence: a fresh module load over the same state "
      "dir (a restart) sees the lock and enforces it",
      (res["locks2"].get("gpu") or {}).get("program")
      == "holder-prog"
      and res["decided2"] == ["h1"], str(res))

# --- Legacy: no LOCKS.json ---------------------------------------------------------------
res = run_driver(r"""
import os as _os
job("a", "quick")
job("b", "normal")
decided = decided_order()
gk.backchannel_status_line = lambda: "bc stub"
gk.host_status_lines = lambda: []
rc, out = call(gk.cmd_status, NS(repo=False))
print(json.dumps({"decided": decided, "rc": rc,
                  "locks_line": "LOCKS:" in out
                  and "  none" in out,
                  "file_created": _os.path.isfile(
                      gk.LOCKS_FILE)}))
""")
check("legacy: with no LOCKS.json every job dispatches as "
      "before, status renders an empty LOCKS section, and "
      "no file is created by reading",
      res["decided"] == ["a", "b"] and res["rc"] == 0
      and res["locks_line"] and not res["file_created"],
      str(res))

# --- TTL bounds ----------------------------------------------------------------------------
res = run_driver(r"""
rc0, _ = call(gk.cmd_lock, lock_ns(
    lane="gpu", program="p", ttl_min=0))
rc_big, out_big = call(gk.cmd_lock, lock_ns(
    lane="gpu", program="p", ttl_min=1441))
rc_max, _ = call(gk.cmd_lock, lock_ns(
    lane="gpu", program="p", ttl_min=1440))
rc_min, _ = call(gk.cmd_lock, lock_ns(
    lane="quick", program="p", ttl_min=1))
print(json.dumps({"rc0": rc0, "rc_big": rc_big,
                  "bounds_named": "1440" in out_big,
                  "rc_max": rc_max, "rc_min": rc_min}))
""")
check("TTL bounds: 0 and max+1 refused (3, bounds named); "
      "1 and max accepted",
      res["rc0"] == 3 and res["rc_big"] == 3
      and res["bounds_named"] and res["rc_max"] == 0
      and res["rc_min"] == 0, str(res))

# --- FR-013 write failure leaves prior state intact ------------------------------------------
res = run_driver(r"""
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
raw_before = read_locks_raw()
n_events = len(events)
gk._write_verified_bytes = lambda path, data, attempts=8: False
rc_unlock, _ = call(gk.cmd_unlock, unlock_ns(
    lane="gpu", program="holder-prog"))
rc_lock, _ = call(gk.cmd_lock, lock_ns(
    lane="quick", program="other-prog"))
raw_after = read_locks_raw()
print(json.dumps({"rc_unlock": rc_unlock, "rc_lock": rc_lock,
                  "same_bytes": raw_before == raw_after,
                  "new_events": evnames()[n_events:]}))
""")
check("write failure: a failed verified write leaves the "
      "prior LOCKS.json byte-identical, the commands exit "
      "non-zero, and nothing is ledgered as done",
      res["rc_unlock"] == 1 and res["rc_lock"] == 1
      and res["same_bytes"] and res["new_events"] == [],
      str(res))

# --- Status, --list, outbox ---------------------------------------------------------------------
res = run_driver(r"""
job("hj", "gpu", program="holder-prog", chat="chat-holder")
job("o1", "gpu", program="other-prog")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog",
                          reason="campaign-x"))
gk.backchannel_status_line = lambda: "bc stub"
gk.host_status_lines = lambda: []
rc, out = call(gk.cmd_status, NS(repo=False))
rc2, out2 = call(gk.cmd_lock, lock_ns(list=True))
line = ("lock lane=gpu holder=holder-prog state=ACTIVE "
        "acquired=2026-11-02T12:00:00Z "
        "expires=2026-11-02T16:00:00Z reason=campaign-x")
print(json.dumps({"rc": rc, "status_line": line in out,
                  "mark": "[lock-held by holder-prog]" in out,
                  "list_line": line in out2}))
""")
check("status: the LOCKS section renders lane, holder, "
      "state, acquired, expires, reason; the waiting list "
      "marks the non-holder [lock-held by ...]; --list "
      "prints the same section standalone",
      res["rc"] == 0 and res["status_line"] and res["mark"]
      and res["list_line"], str(res))

res = run_driver(r"""
job("hj", "gpu", program="holder-prog", chat="chat-holder")
call(gk.cmd_lock, lock_ns(lane="gpu", program="holder-prog"))
call(gk.cmd_unlock, unlock_ns(lane="gpu",
                              program="holder-prog"))
call(gk.cmd_lock, lock_ns(lane="long", program="holder-prog"))
call(gk.cmd_unlock, unlock_ns(lane="long", program="ops",
                              force=True))
lines = [json.dumps(e) for e in events]
lines.append(json.dumps({
    "ts": "2026-11-02T12:05:00Z", "event": "lock_expired",
    "program": "holder-prog", "lane": "quick",
    "state": "active", "expires_at": "2026-11-02T12:05:00Z",
    "notify_chat": "chat-holder"}))
with open(gk.LEDGER, "w") as f:
    f.write("\n".join(lines) + "\n")
with open(gk.OUTBOX_CURSOR, "w") as f:
    json.dump({"line": 0}, f)
rc, out = call(gk.cmd_outbox, NS(ack=False))
print(json.dumps({"rc": rc, "out": out}))
""")
o = res["out"]
check("outbox: the override release and the expiry notify "
      "the holder's chat; the holder's own release produces "
      "no group (no self-notification)",
      res["rc"] == 0 and o.count("NOTIFY ") == 1
      and "NOTIFY chat-holder" in o
      and "RELEASED BY OPERATOR" in o
      and "EXPIRED" in o, o[:400])

print()
if fails:
    print(f"TEST_SPEC015: FAIL ({len(fails)} failing)")
    sys.exit(1)
print("TEST_SPEC015: ALL PASS")

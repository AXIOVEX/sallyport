#!/usr/bin/env python3
"""Scenario battery for (live priority and pause control).

Golden style: temp dir, copied gk.py + config.py, subprocess
drivers, stub dispatch_one / ledger / fetch_log_once at the
module seams. Fixed evaluation instant for the optimizer, so
aging arithmetic is exact.

Covers: focus by key / program / chat; focus + maintenance
exemption; legacy fleet-shaped PAUSED.json (single except_key)
normalization; live priority (reorder, refusal for dispatched
and completed jobs, submit --priority stamping); the optimizer
(determinism, aging/starvation bound, human pin, equal-nominal
human precedence, inertness under focus); drain settle (plain
and focus); the checkpoint hold (signal job + refusal shapes),
the held_at_checkpoint harvest, resume --job release, and
pipeline resume clearing a focused, draining record.

Run: python3 gatekeeper/test_spec013.py
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
os.makedirs(gk.QUEUE_DIR, exist_ok=True)
open(gk.LANES_LIVE_FILE, "w").close()
events = []
gk.ledger = lambda event, **kw: events.append(event)

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

def read_pause():
    if not os.path.isfile(gk.PAUSE_FILE):
        return None
    with open(gk.PAUSE_FILE) as f:
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
"""


def run_driver(body):
    d = tempfile.mkdtemp(prefix="gk-spec013-")
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


# --- Focus: one scenario per selector kind -------------------------------
FOCUS_BODY = r"""
sel = json.loads(SEL_JSON)
job("m", "quick", key="keep-me", program="keep-prog", chat="chat-1")
job("x", "normal", key="other-job", program="other-prog",
    chat="chat-2")
job("t", "long", key="maint-job", maint=True)
rc, out = call(gk.cmd_focus, NS(except_key=sel.get("k"),
                                except_program=sel.get("p"),
                                except_chat=sel.get("c"),
                                reason="t", drain=False))
print(json.dumps({"rc": rc, "decided": decided_order(),
                  "pause": read_pause()}))
"""
for tag, sel in (("key", {"k": ["keep"]}),
                 ("program", {"p": ["keep-prog"]}),
                 ("chat", {"c": ["chat-1"]})):
    body = "SEL_JSON = %r\n%s" % (json.dumps(sel), FOCUS_BODY)
    res = run_driver(body)
    check("focus by %s: admits matching + maintenance, holds rest"
          % tag,
          res["rc"] == 0 and res["decided"] == ["m", "t"],
          str(res))
check("focus: record carries the superset schema",
      res["pause"] is not None
      and res["pause"].get("except_chats") == ["chat-1"]
      and res["pause"].get("except_keys") == []
      and res["pause"].get("except_programs") == [], str(res))

# --- Legacy fleet-shaped PAUSED.json -------------------------------------
res = run_driver(r"""
write_pause({"ts": "2026-11-01T16:28:00Z",
             "reason": "fleet pause",
             "except_key": "gpu-unlock-step4-build-probe2-2026-11-01",
             "amendment": "release only on owner permission"})
job("e", "quick", key="gpu-unlock-step4-build-probe2-2026-11-01")
job("o", "normal", key="some-other-job")
job("t", "long", key="maint-job", maint=True)
print(json.dumps({"decided": decided_order(),
                  "mode": gk.pause_record()["mode"],
                  "keys": gk.pause_record()["except_keys"]}))
""")
check("legacy PAUSED.json: single except_key normalizes to focus",
      res["mode"] == "focused"
      and res["keys"] == ["gpu-unlock-step4-build-probe2-2026-11-01"],
      str(res))
check("legacy PAUSED.json: named exception + maintenance flow, "
      "others hold",
      res["decided"] == ["e", "t"], str(res))

# --- Live priority ---------------------------------------------------------
res = run_driver(r"""
job("lo", "quick", prio=(1, "human"))
job("hi", "normal", prio=(5, "human"),
    created="2026-11-02T11:30:00Z")
print(json.dumps({"decided": decided_order()}))
""")
check("priority: later higher human priority dispatches first",
      res["decided"] == ["hi", "lo"], str(res))

res = run_driver(r"""
job("w", "quick")
job("d", "normal", state="dispatched", run_status="in_progress")
job("c", "long", state="completed")
rc1, _ = call(gk.cmd_priority, NS(job="w", value=9))
after_ok = read_job("w")
rc2, out2 = call(gk.cmd_priority, NS(job="d", value=9))
rc3, _ = call(gk.cmd_priority, NS(job="c", value=9))
rc4, _ = call(gk.cmd_priority, NS(job="no-such", value=9))
print(json.dumps({"rc_ok": rc1, "rc_disp": rc2, "rc_done": rc3,
                  "rc_missing": rc4, "named": "state=dispatched" in out2,
                  "w": [after_ok.get("priority"),
                        after_ok.get("priority_source")],
                  "d_prio": read_job("d").get("priority"),
                  "events": events}))
""")
check("priority: change on a waiting job lands + stamps human",
      res["rc_ok"] == 0 and res["w"] == [9, "human"]
      and "priority_changed" in res["events"], str(res))
check("priority: refused for dispatched (3, names state) and "
      "completed (3); missing job is 2",
      res["rc_disp"] == 3 and res["named"] and res["rc_done"] == 3
      and res["rc_missing"] == 2 and res["d_prio"] is None, str(res))

res = run_driver(r"""
script = os.path.join(BASE_DIR, "s.sh")
open(script, "w").write("echo hi\n")
def sub(key, prio):
    return call(gk.cmd_submit, NS(
        program="t", workflow="wsl.yml", lane="quick",
        script=script, grab_path=None, cwd=None, image=None,
        mounts=None, expected_min=None, key=key, maintenance=False,
        notify_chat=None, priority=prio))
rc1, _ = sub("with-prio", 4)
rc2, _ = sub("no-prio", None)
jobs = {j["key"]: j for j in gk.load_jobs()}
print(json.dumps({"rcs": [rc1, rc2],
                  "wp": [jobs["with-prio"].get("priority"),
                         jobs["with-prio"].get("priority_source")],
                  "np": [jobs["no-prio"].get("priority"),
                         jobs["no-prio"].get("priority_source")]}))
""")
check("submit --priority stamps human; plain submit stays auto",
      res["rcs"] == [0, 0] and res["wp"] == [4, "human"]
      and res["np"] == [0, "auto"], str(res))

# --- The optimizer ----------------------------------------------
res = run_driver(r"""
job("a", "quick", expected=600, created="2026-11-02T03:40:00Z")
job("b", "normal", expected=1, created="2026-11-02T11:59:00Z")
job("c", "long", expected=25, created="2026-11-02T06:00:00Z")
job("d", "gpu", prio=(3, "human"), created="2026-11-02T11:00:00Z")
jobs = {j["id"]: j for j in gk.load_jobs()}
def order():
    ws = [j for j in gk.load_jobs() if j["state"] == "submitted"]
    return [j["id"] for j in sorted(
        ws, key=lambda j: gk.dispatch_sort_key(j, NOW))]
print(json.dumps({"order1": order(), "order2": order(),
                  "decided": decided_order(),
                  "eff": {i: gk.effective_priority(jobs[i], NOW)
                          for i in ("a", "b", "c", "d")}}))
""")
# a: base 10 + aging 50 (500 min) = 60; b: 50 + fit 5 + 0 = 55;
# c: base 30 + fit 5 + aging 36 (360 min) = 71; d: human pin 3.
check("optimizer: effective values match the policy arithmetic",
      res["eff"] == {"a": [60, "auto"], "b": [55, "auto"],
                     "c": [71, "auto"], "d": [3, "human"]}, str(res))
check("optimizer: deterministic — same state, same order, and "
      "the pass follows it",
      res["order1"] == res["order2"] == ["c", "a", "b", "d"]
      and res["decided"] == res["order1"], str(res))

res = run_driver(r"""
job("old", "quick", expected=600, created="2026-11-02T03:40:00Z")
job("new", "normal", expected=1, created="2026-11-02T12:00:00Z")
job("ancient", "long", expected=600,
    created="2026-10-01T00:00:00Z")
jobs = {j["id"]: j for j in gk.load_jobs()}
print(json.dumps({
    "decided": decided_order(),
    "old": gk.auto_priority(jobs["old"], NOW),
    "new": gk.auto_priority(jobs["new"], NOW),
    "ceiling": gk.auto_priority(jobs["ancient"], NOW)}))
""")
check("optimizer: aging bound — an old base-10 job (60) outranks "
      "a fresh base-55 job; the ceiling caps at 100",
      res["old"] == 60 and res["new"] == 55
      and res["ceiling"] == 100
      and res["decided"] == ["ancient", "old", "new"], str(res))

res = run_driver(r"""
job("h60", "quick", prio=(60, "human"),
    created="2026-11-02T11:00:00Z")
job("a60", "normal", expected=600, created="2026-11-02T03:40:00Z")
job("a61", "long", expected=600, created="2026-11-02T03:30:00Z")
job("h5", "gpu", prio=(5, "human"), created="2026-10-01T00:00:00Z")
jobs = {j["id"]: j for j in gk.load_jobs()}
print(json.dumps({
    "decided": decided_order(),
    "h5_eff": gk.effective_priority(jobs["h5"], NOW),
    "a60": gk.auto_priority(jobs["a60"], NOW),
    "a61": gk.auto_priority(jobs["a61"], NOW)}))
""")
check("optimizer: human wins the equal-nominal tie (60 before "
      "auto 60), auto 61 outranks human 60, and a human pin "
      "never ages (ancient human 5 stays 5)",
      res["a60"] == 60 and res["a61"] == 61
      and res["h5_eff"] == [5, "human"]
      and res["decided"] == ["a61", "h60", "a60", "h5"], str(res))

res = run_driver(r"""
write_pause({"ts": "2026-11-02T11:00:00Z", "reason": "f",
             "except_programs": ["keep-prog"]})
job("hot", "quick", program="other-prog", expected=600,
    created="2026-10-01T00:00:00Z")
job("in", "normal", program="keep-prog")
print(json.dumps({"decided": decided_order()}))
""")
check("optimizer: inert under focus — a ceiling-hot held job "
      "still waits; the admitted job flows",
      res["decided"] == ["in"], str(res))

# --- Drain ---------------------------------------------------------
res = run_driver(r"""
write_pause({"ts": "2026-11-02T11:00:00Z", "reason": "d",
             "drain": True})
job("e", "quick", state="dispatched", run_id=7,
    run_status="in_progress")
job("m", "normal", state="dispatched", run_id=8, maint=True,
    run_status="in_progress")
gk.fetch_log_once = lambda job: True
settle1 = gk.maybe_settle_drain()
runs = {"7": {"status": "completed", "conclusion": "success",
              "createdAt": "2026-11-02T11:30:00Z"},
        "8": {"status": "completed", "conclusion": "success",
              "createdAt": "2026-11-02T11:30:00Z"}}
gk.watch_pass(gk.load_jobs(), runs)
settle2 = gk.maybe_settle_drain()
print(json.dumps({"settle_blocked": settle1,
                  "settle_after": settle2,
                  "pause": read_pause(),
                  "events": events}))
""")
check("drain: settle blocked while a program job executes "
      "(maintenance does not block), settles when it completes",
      res["settle_blocked"] is False and res["settle_after"] is True
      and res["pause"]["drain"] is False
      and "drain_settled_at" in res["pause"]
      and "drain_settled" in res["events"], str(res))

res = run_driver(r"""
write_pause({"ts": "2026-11-02T11:00:00Z", "reason": "f",
             "except_programs": ["keep"], "drain": True})
job("out", "quick", program="other", state="dispatched",
    run_id=7, run_status="in_progress")
job("in", "normal", program="keep", state="dispatched",
    run_id=8, run_status="in_progress")
gk.fetch_log_once = lambda job: True
s0 = gk.maybe_settle_drain()
runs = {"7": {"status": "completed", "conclusion": "success",
              "createdAt": "2026-11-02T11:30:00Z"}}
gk.watch_pass(gk.load_jobs(), runs)
s1 = gk.maybe_settle_drain()
print(json.dumps({"while_outside_runs": s0,
                  "after_outside_done": s1}))
""")
check("drain + focus: a non-admitted job executing blocks the "
      "settle; once it completes the settle lands even with an "
      "admitted job still running (the drain converged to the "
      "focus set)",
      res == {"while_outside_runs": False,
              "after_outside_done": True},
      str(res))

# --- Checkpoint hold lifecycle ----------------------------------------------
res = run_driver(r"""
t = job("t1", "quick", key="target-job")
g = job("g1", "normal", workflow="grab.yml", key="grab-job")
c = job("c1", "long", state="completed", key="done-job")
rc_t, _ = call(gk.cmd_hold, NS(job="target-job"))
rc_g, _ = call(gk.cmd_hold, NS(job="grab-job"))
rc_c, _ = call(gk.cmd_hold, NS(job="done-job"))
jobs = {j["key"]: j for j in gk.load_jobs()}
sig = jobs.get("gk-hold-t1")
script = ""
if sig:
    with open(sig["script_path"]) as f:
        script = f.read()
flag_before = read_job("t1").get("hold_requested")
rc_r, _ = call(gk.cmd_resume, NS(job="target-job"))
jobs2 = {j["key"]: j for j in gk.load_jobs()}
print(json.dumps({
    "rc_hold": rc_t, "rc_grab": rc_g, "rc_done": rc_c,
    "flag": flag_before,
    "sig_maint": sig.get("maintenance") if sig else None,
    "sig_wf": sig.get("workflow") if sig else None,
    "script_names_path": "/home/you/.syncyard/holds/t1.hold"
                         in script,
    "events": events,
    "rc_release": rc_r,
    "flag_after": read_job("t1").get("hold_requested"),
    "unhold_exists": "gk-unhold-t1" in jobs2}))
""")
check("hold: request lands on the record + a maintenance signal "
      "job carries the venue path",
      res["rc_hold"] == 0 and res["flag"] is True
      and res["sig_maint"] is True and res["sig_wf"] == "wsl.yml"
      and res["script_names_path"]
      and "hold_requested" in res["events"], str(res))
check("hold: refused for grab (3) and completed (3) jobs",
      res["rc_grab"] == 3 and res["rc_done"] == 3, str(res))
check("resume --job: clears the flag and queues the withdrawal",
      res["rc_release"] == 0 and res["flag_after"] is False
      and res["unhold_exists"]
      and "hold_released" in res["events"], str(res))

res = run_driver(r"""
job("w", "quick", state="watching", run_id=9,
    run_status="in_progress")
job("v", "normal", state="watching", run_id=10,
    run_status="in_progress")
def fetch(job):
    job["markers"] = (["CHECKPOINT_PAUSED at step 3"]
                      if job["id"] == "w" else ["ALL_DONE=1"])
    job["log_fetched_at"] = "x"
    return True
gk.fetch_log_once = fetch
runs = {"9": {"status": "completed", "conclusion": "failure",
              "createdAt": "2026-11-02T11:30:00Z"},
        "10": {"status": "completed", "conclusion": "success",
               "createdAt": "2026-11-02T11:30:00Z"}}
gk.watch_pass(gk.load_jobs(), runs)
w, v = read_job("w"), read_job("v")
# The host-raised case: job w never carried a
# gatekeeper hold request — its signal was raised host-side,
# so all the harvest ever saw was the marker. Releasing it
# must still queue the withdrawal.
rc_rel, _ = call(gk.cmd_resume, NS(job="w"))
keys = {j["key"] for j in gk.load_jobs()}
print(json.dumps({
    "w_state": w["state"], "w_conc": w.get("run_conclusion"),
    "w_held_at": bool(w.get("held_at_checkpoint_at")),
    "w_no_flag": w.get("hold_requested") is None,
    "v_state": v["state"], "events": events,
    "rc_release_host": rc_rel, "unhold_queued": "gk-unhold-w" in keys}))
""")
check("harvest: CHECKPOINT_PAUSED becomes held_at_checkpoint "
      "(conclusion kept verbatim), marker-less control completes",
      res["w_state"] == "held_at_checkpoint"
      and res["w_conc"] == "failure" and res["w_held_at"]
      and res["v_state"] == "completed"
      and "held_at_checkpoint" in res["events"], str(res))
check("host-raised hold: harvests identically with no "
      "gatekeeper request on the record, and resume --job "
      "still releases it",
      res["w_no_flag"] and res["rc_release_host"] == 0
      and res["unhold_queued"]
      and "hold_released" in res["events"], str(res))

# --- Resume clears focus; status renders the mode ---------------------------
res = run_driver(r"""
write_pause({"ts": "2026-11-02T11:00:00Z", "reason": "f",
             "except_programs": ["keep"], "drain": True})
rc, _ = call(gk.cmd_resume, NS(job=None))
print(json.dumps({"rc": rc, "pause_gone": read_pause() is None,
                  "events": events}))
""")
check("resume: clears a focused, draining record entirely",
      res["rc"] == 0 and res["pause_gone"]
      and "resumed" in res["events"], str(res))

res = run_driver(r"""
write_pause({"ts": "2026-11-02T11:00:00Z", "reason": "f",
             "except_programs": ["keep"]})
job("hw", "quick", key="human-wait", prio=(7, "human"))
job("aw", "normal", key="auto-wait", expected=1)
job("hc", "long", key="held-job", state="held_at_checkpoint",
    run_id=5)
gk.backchannel_status_line = lambda: "bc stub"
gk.host_status_lines = lambda: []
rc, out = call(gk.cmd_status, NS(repo=False))
print(json.dumps({"rc": rc, "out": out}))
""")
o = res["out"]
wsec = o.split("waiting (dispatch order):")[-1]
check("status: FOCUSED banner names the selectors",
      res["rc"] == 0 and "FOCUSED" in o and "program=keep" in o,
      o[:400])
check("status: waiting shows effective priority + source + held "
      "marks, in effective order (the fresh short auto job's 61 "
      "ahead of the human-set 7 — precedence is a tie rule)",
      "pri=7" in wsec and "pri=61" in wsec
      and "(human)" in wsec and "(auto)" in wsec
      and "[held]" in wsec
      and wsec.index("auto-wait") < wsec.index("human-wait"),
      o[:600])
check("status: held-at-checkpoint section lists the job",
      "held at checkpoint:" in o and "held-job" in o, o[:600])

print()
if fails:
    print(f"TEST_SPEC013: FAIL ({len(fails)} failing)")
    sys.exit(1)
print("TEST_SPEC013: ALL PASS")

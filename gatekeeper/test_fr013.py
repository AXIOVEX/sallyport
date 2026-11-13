#!/usr/bin/env python3
"""test battery for gk.py: atomic-swap save_job +
sweep resilience. Runs against a throwaway queue dir; the live
gatekeeper is never touched. Fault injection: os.replace forced to
fail — the pre-code destroyed the standing record in exactly
this situation (direct-write fallback truncating the final path)."""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gk

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


tmpd = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "test-tmp", "fr013-run")
shutil.rmtree(tmpd, ignore_errors=True)
os.makedirs(tmpd)


def wait_for_lull(label, budget_s=1800):
    """The VM write restriction drops writes in waves; a verified
    write either lands or it does not. Steps that need one real
    write wait for a lull instead of failing spuriously — a battery
    that cannot get a single verified write in the whole budget is
    SETUP-BLOCKED (an environment verdict, not a code verdict)."""
    import time as _t
    probe = os.path.join(tmpd, ".lull-probe")
    deadline = _t.time() + budget_s
    while _t.time() < deadline:
        if gk._write_verified_bytes(probe, b"lull-probe\n"):
            print("LULL OK (%s)" % label)
            return True
        print("... waiting for a write lull (%s)" % label)
        _t.sleep(10)
    print("SETUP_BLOCKED_BY_WAVE (%s): no verified write landed in "
          "%ds; the battery's fault-injection steps were not reached"
          % (label, budget_s))
    sys.exit(2)
gk.QUEUE_DIR = tmpd
gk.LEDGER = os.path.join(tmpd, "ledger.jsonl")
gk.HLOG = os.path.join(tmpd, "gk.log")

job = {"id": "20260101T000000Z-test-job-abc123", "key": "test-job",
       "state": "submitted", "program": "test", "flags": []}

def save_with_lull(j, label):
    """Happy-path saves ride out the wave; fault-injection saves
    (below) deliberately do NOT use this — their failure is the
    assertion being tested."""
    import time as _t
    for _ in range(30):
        try:
            gk.save_job(j)
            return
        except IOError:
            _t.sleep(10)
    print("SETUP_BLOCKED_BY_WAVE (%s)" % label)
    sys.exit(2)


# 1. happy path: create + update land byte-exact
wait_for_lull("create")
save_with_lull(job, "create")
path = gk.job_path(job["id"])
disk = open(path, "rb").read()
check("create lands byte-exact",
      disk == json.dumps(job, indent=2, sort_keys=True).encode())
job["state"] = "dispatching"
save_with_lull(job, "update")
disk2 = open(path, "rb").read()
check("update lands byte-exact",
      disk2 == json.dumps(job, indent=2, sort_keys=True).encode())

# 2. the claim: with every rename refused, the update fails
#    loudly and the STANDING record is untouched, byte for byte
real_replace = os.replace
os.replace = lambda a, b: (_ for _ in ()).throw(OSError("EPERM (injected)"))
try:
    job["state"] = "dispatched"
    try:
        gk.save_job(job)
        raised = False
    except IOError:
        raised = True
    check("save_job raises when no swap lands", raised)
    check("standing record untouched byte-for-byte",
          open(path, "rb").read() == disk2)
    # 3. save_job_safe swallows into a loud False (sweep continues)
    check("save_job_safe returns False, does not raise",
          gk.save_job_safe(job, "test") is False)
    check("record still untouched after safe-save failure",
          open(path, "rb").read() == disk2)
    # 4. dispatch_one must not dispatch what it could not record
    calls = []
    real_run_cmd = gk.run_cmd
    gk.run_cmd = lambda *a, **k: (calls.append(a), (0, "", ""))[1]
    job2 = dict(job)
    job2["state"] = "submitted"
    job2["workflow"] = "run.yml"
    job2["lane"] = "quick"
    job2["script_path"] = __file__
    res = gk.dispatch_one(job2, [job2])
    gk.run_cmd = real_run_cmd
    check("dispatch_one returns False when pre-dispatch save fails",
          res is False)
    check("gh was never called", len(calls) == 0)
finally:
    os.replace = real_replace

# 5. recovery: with renames working again, the pending update lands
wait_for_lull("recovery")
save_with_lull(job, "recovery")
check("update lands after the fault clears",
      json.loads(open(path, "rb").read())["state"] == "dispatched")

# 6. a brand-new record whose save can never land leaves NO file
os.replace = lambda a, b: (_ for _ in ()).throw(OSError("EPERM (injected)"))
try:
    job3 = dict(job)
    job3["id"] = "20260101T000000Z-new-job-def456"
    try:
        gk.save_job(job3)
        raised = False
    except IOError:
        raised = True
    check("new-record save raises under total fault", raised)
    check("no husk at the final path",
          not os.path.exists(gk.job_path(job3["id"])))
finally:
    os.replace = real_replace

print("RESULT:", "ALL PASS" if not failures else "FAILURES: %s" % failures)
sys.exit(1 if failures else 0)

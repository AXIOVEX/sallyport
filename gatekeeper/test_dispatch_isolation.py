#!/usr/bin/env python3
"""Battery for dispatch isolation: a missing or unreadable job
script must fail that one job, never the sweep.

Field case (2026-11-04): job spec053-f0-build-repair-2026-11-04 was
submitted with its script staged at /tmp/build053b.sh; a VM
replacement wiped /tmp before dispatch, dispatch_one raised
FileNotFoundError, the whole sweep exited 1, and the record was
left wedged in state=dispatching for an operator to repair by hand.

Golden style: temp dir, copied gk.py + config.py, subprocess
driver, ledger captured at the module seam, gh stubbed at run_cmd.

Covers:
  1. dispatch_one on a missing-script job returns False, does not
     raise, and leaves the record submitted with the error on it
     plus a dispatch_failed_retryable ledger event.
  2. dispatch_pass continues past the broken job and dispatches
     the next waiting job in the same lane in the same pass.
  3. Restaging the script revives the job on a later pass with no
     operator surgery on the record.

Run: python3 gatekeeper/test_dispatch_isolation.py
"""
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
spec = importlib.util.spec_from_file_location(
    "gk_under_test", os.path.join(BASE_DIR, "gk.py"))
gk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gk)
NOW = datetime(2026, 11, 4, 17, 30, tzinfo=timezone.utc)
gk.now_dt = lambda: NOW
os.makedirs(gk.QUEUE_DIR, exist_ok=True)
open(gk.LANES_LIVE_FILE, "w").close()
events = []
gk.ledger = lambda event, **kw: events.append((event, kw))

def job(jid, lane, script_path, created, sha):
    j = {"id": jid, "key": jid, "program": "prog",
         "workflow": "wsl.yml", "lane": lane, "state": "submitted",
         "created_at": created, "script_path": script_path,
         "script_sha256": sha, "maintenance": False,
         "notify_chat": None, "run_id": None, "run_status": None,
         "run_conclusion": None, "flags": [], "watermarks": {},
         "expected_min": 5, "cwd": "/home/you"}
    path = os.path.join(gk.QUEUE_DIR, jid + ".json")
    with open(path, "w") as f:
        json.dump(j, f)
    return j

def read_job(jid):
    with open(os.path.join(gk.QUEUE_DIR, jid + ".json")) as f:
        return json.load(f)

RESULTS = {}
"""

BODY = r"""
missing = os.path.join(BASE_DIR, "no-such-script.sh")
good = os.path.join(BASE_DIR, "good.sh")
with open(good, "w") as f:
    f.write("echo ok\n")
job("job-a-broken", "quick", missing, "2026-11-04T17:00:00Z", "sha-a")
job("job-b-good", "quick", good, "2026-11-04T17:01:00Z", "sha-b")

# 1. dispatch_one alone: no raise, False, record back to submitted.
jobs = gk.load_jobs()
a = next(j for j in jobs if j["id"] == "job-a-broken")
raised = False
try:
    ret = gk.dispatch_one(a, jobs)
except Exception:
    raised = True
    ret = None
RESULTS["one_no_raise"] = not raised
RESULTS["one_returns_false"] = (ret is False)
a_disk = read_job("job-a-broken")
RESULTS["one_state_submitted"] = a_disk["state"] == "submitted"
RESULTS["one_error_recorded"] = str(
    a_disk.get("last_dispatch_error", "")).startswith(
        "script unreadable")
RESULTS["one_no_dispatching_at"] = "dispatching_at" not in a_disk
RESULTS["one_ledgered"] = any(
    e == "dispatch_failed_retryable" and kw.get("key") == "job-a-broken"
    for e, kw in events)

# 2. A full pass: the broken job fails, the good job in the same
# lane still dispatches in the same pass.
run_ids = iter([424242, 434343])
gk.run_cmd = lambda args, timeout=60: (
    0, "https://github.com/x/y/actions/runs/%d" % next(run_ids), "")
n = gk.dispatch_pass(gk.load_jobs(), {})
RESULTS["pass_count_one"] = (n == 1)
b_disk = read_job("job-b-good")
RESULTS["pass_b_dispatched"] = (
    b_disk["state"] == "dispatched" and b_disk["run_id"] == 424242)
a_disk = read_job("job-a-broken")
RESULTS["pass_a_still_submitted"] = a_disk["state"] == "submitted"

# 3. Restage the script: the job revives on the next pass with no
# operator edit to its record. (The good job is marked completed
# first so the lane is free, as it would be once its run ends.)
b_done = read_job("job-b-good")
b_done["state"] = "completed"
b_done["run_status"] = "completed"
with open(os.path.join(gk.QUEUE_DIR, "job-b-good.json"), "w") as f:
    json.dump(b_done, f)
with open(missing, "w") as f:
    f.write("echo revived\n")
n2 = gk.dispatch_pass(gk.load_jobs(), {})
RESULTS["revive_count_one"] = (n2 == 1)
a_disk = read_job("job-a-broken")
RESULTS["revive_dispatched"] = (
    a_disk["state"] == "dispatched" and a_disk["run_id"] == 434343)

import json as _json
print("RESULTS_JSON:" + _json.dumps(RESULTS))
"""


def main():
    d = tempfile.mkdtemp(prefix="gk-dispatch-isolation-")
    try:
        shutil.copy(os.path.join(HERE, "gk.py"), d)
        shutil.copy(os.path.join(HERE, "config.py"), d)
        driver = "BASE_DIR = %r\n%s\n%s" % (d, PRELUDE, BODY)
        env = {k: v for k, v in os.environ.items()
               if k not in ("SYNCYARD_CONFIG", "SYNCARD_CONFIG")}
        p = subprocess.run([sys.executable, "-c", driver],
                           capture_output=True, text=True, env=env,
                           timeout=120)
        results = {}
        for line in p.stdout.splitlines():
            if line.startswith("RESULTS_JSON:"):
                import json
                results = json.loads(line[len("RESULTS_JSON:"):])
        if not results:
            print(p.stdout)
            print(p.stderr, file=sys.stderr)
            check("driver produced results", False,
                  "rc=%s" % p.returncode)
            return 1
        names = {
            "one_no_raise": "dispatch_one missing script does not raise",
            "one_returns_false": "dispatch_one missing script returns False",
            "one_state_submitted": "record returned to submitted",
            "one_error_recorded": "last_dispatch_error records script unreadable",
            "one_no_dispatching_at": "dispatching_at cleared",
            "one_ledgered": "dispatch_failed_retryable ledgered",
            "pass_count_one": "pass dispatches exactly the good job",
            "pass_b_dispatched": "good job dispatched with its run id",
            "pass_a_still_submitted": "broken job waits submitted after the pass",
            "revive_count_one": "restaged job dispatches on the next pass",
            "revive_dispatched": "restaged job holds its run id",
        }
        for key, label in names.items():
            check(label, results.get(key) is True,
                  "got %r" % results.get(key))
        return 1 if fails else 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

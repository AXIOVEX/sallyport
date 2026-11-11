#!/usr/bin/env python3
"""Audit 2026-11-10 gatekeeper robustness regressions.

Every section is written against a behavior introduced by the
audit fix branch. Run against the pre-fix gk.py, the corresponding
checks fail (or the function under test is absent); run against
the fixed gk.py, the whole battery passes. All state lives in a
throwaway directory and every network-facing helper is faked.
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gk

failures = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name +
          ((" :: " + str(detail)[:500]) if (detail and not cond) else ""))
    if not cond:
        failures.append(name)


def capture(fn, *args, **kwargs):
    """Call fn capturing (exception, return, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = fn(*args, **kwargs)
        return None, rc, out.getvalue(), err.getvalue()
    except Exception as exc:  # the pre-fix shape for several sections
        return exc, None, out.getvalue(), err.getvalue()


ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "test-tmp", "audit-fixes-run")
shutil.rmtree(ROOT, ignore_errors=True)
os.makedirs(ROOT)

ORIG = {}
for _name in (
        "BASE", "QUEUE_DIR", "LEDGER", "HLOG", "LOCK_DIR", "PAUSE_FILE",
        "OUTBOX_CURSOR", "load_jobs", "save_job", "save_job_safe",
        "ledger", "gh_json", "run_cmd", "_append_verified",
        "_write_text_file", "staging_pass", "fetch_run_by_id",
        "acquire_lock", "stop_state", "rate_limit_remaining", "list_runs",
        "sweep_liveness", "sweep_tray_drift", "dispatch_pass",
        "watch_pass", "maybe_settle_drain", "pause_record", "hlog"):
    ORIG[_name] = getattr(gk, _name, None)

OUTBOX_READ = os.path.join(ROOT, "outbox-read.json")
if hasattr(gk, "OUTBOX_READ"):
    ORIG["OUTBOX_READ"] = gk.OUTBOX_READ
gk.OUTBOX_READ = OUTBOX_READ


def ledger_capture_factory(store):
    def fake_ledger(event, **kw):
        rec = {"event": event}
        rec.update(kw)
        store.append(rec)
        return rec
    return fake_ledger


# ---------------------------------------------------------------------------
# F1/F2/F3: outbox ack semantics, best-effort read marker, refused ack
# ---------------------------------------------------------------------------

outbox_dir = os.path.join(ROOT, "outbox")
os.makedirs(outbox_dir)
gk.LEDGER = os.path.join(outbox_dir, "ledger.jsonl")
gk.OUTBOX_CURSOR = os.path.join(outbox_dir, "outbox-cursor.json")
gk.OUTBOX_READ = OUTBOX_READ


def outbox_job(job_id, key):
    return {"id": job_id, "key": key, "program": "audit",
            "state": "completed", "run_status": "completed",
            "expected_min": 1, "notify_chat": "chat-audit",
            "markers": ["AUDIT_VERDICT PASS"], "flags": []}


JOB_O1 = outbox_job("20260101T000000Z-outbox-one-aaa111", "outbox-one")
JOB_O2 = outbox_job("20260101T000001Z-outbox-two-bbb222", "outbox-two")
gk.load_jobs = lambda: [JOB_O1, JOB_O2]


def ledger_line(job):
    return json.dumps({
        "ts": "2026-01-01T00:00:00Z", "event": "state_change",
        "job_id": job["id"], "key": job["key"], "run_id": 101,
        "from_status": "in_progress", "to_status": "completed",
        "conclusion": "success"}) + "\n"


with open(gk.LEDGER, "w", encoding="utf-8") as f:
    f.write(ledger_line(JOB_O1))
with open(gk.OUTBOX_CURSOR, "w", encoding="utf-8") as f:
    json.dump({"line": 0}, f)
if os.path.exists(OUTBOX_READ):
    os.unlink(OUTBOX_READ)

exc, rc, out, err = capture(gk.cmd_outbox, SimpleNamespace(ack=False))
check("F1 outbox: first read lists the owed completion",
      exc is None and rc == 0 and "NOTIFY chat-audit" in out
      and "outbox-one" in out, (exc, rc, out, err))
marker_ok = False
try:
    with open(OUTBOX_READ, encoding="utf-8") as f:
        marker_ok = int(json.load(f)["line"]) == 1
except (OSError, ValueError, KeyError, TypeError):
    marker_ok = False
check("F1 outbox: plain read records the observed ledger line",
      marker_ok)

with open(gk.LEDGER, "a", encoding="utf-8") as f:
    f.write(ledger_line(JOB_O2))
exc, rc, out, err = capture(
    gk.cmd_outbox, SimpleNamespace(ack=True, force_ack=False))
cursor_line = None
try:
    with open(gk.OUTBOX_CURSOR, encoding="utf-8") as f:
        cursor_line = int(json.load(f)["line"])
except (OSError, ValueError, KeyError, TypeError):
    pass
check("F1 outbox: ack advances only to the last read line, "
      "not the ledger end",
      exc is None and rc == 0 and cursor_line == 1,
      (exc, rc, cursor_line, out, err))

exc, rc, out, err = capture(gk.cmd_outbox, SimpleNamespace(ack=False))
check("F1 outbox: an event landing between read and ack stays owed",
      exc is None and rc == 0 and "outbox-two" in out
      and "outbox-one" not in out, (exc, rc, out, err))

# F3: corrupt marker refuses; explicit force overrides.
with open(OUTBOX_READ, "w", encoding="utf-8") as f:
    f.write("not-json")
exc, rc, out, err = capture(
    gk.cmd_outbox, SimpleNamespace(ack=True, force_ack=False))
with open(gk.OUTBOX_CURSOR, encoding="utf-8") as f:
    cursor_after_refusal = int(json.load(f)["line"])
check("F3 outbox: ack with a corrupt read marker refuses clearly "
      "and does not move the cursor",
      exc is None and rc == 2 and cursor_after_refusal == 1
      and "REFUSED" in (out + err) and "read marker" in (out + err),
      (exc, rc, cursor_after_refusal, out, err))
exc, rc, out, err = capture(
    gk.cmd_outbox, SimpleNamespace(ack=True, force_ack=True))
with open(gk.OUTBOX_CURSOR, encoding="utf-8") as f:
    cursor_after_force = int(json.load(f)["line"])
check("F3 outbox: --force-ack explicitly overrides a bad marker",
      exc is None and rc == 0 and cursor_after_force == 2,
      (exc, rc, cursor_after_force, out, err))

# F3: missing marker refuses too.
try:
    os.unlink(OUTBOX_READ)
except OSError:
    pass
exc, rc, out, err = capture(
    gk.cmd_outbox, SimpleNamespace(ack=True, force_ack=False))
check("F3 outbox: ack with no read marker refuses",
      exc is None and rc == 2 and "REFUSED" in (out + err),
      (exc, rc, out, err))

# F2: a failed marker write warns but never fails the listing.
with open(gk.LEDGER, "a", encoding="utf-8") as f:
    f.write(ledger_line(JOB_O1))
real_write_text_file = gk._write_text_file


def flaky_write(path, text):
    if os.path.abspath(path) == os.path.abspath(OUTBOX_READ):
        raise IOError("injected marker write failure")
    return real_write_text_file(path, text)


gk._write_text_file = flaky_write
exc, rc, out, err = capture(gk.cmd_outbox, SimpleNamespace(ack=False))
gk._write_text_file = real_write_text_file
check("F2 outbox: failed read-marker write warns and the listing "
      "still succeeds",
      exc is None and rc == 0 and "NOTIFY chat-audit" in out
      and "WARNING" in err and "read marker" in err,
      (exc, rc, out, err))


# ---------------------------------------------------------------------------
# F1/F8: artifact-delete verb (ported from the live gatekeeper)
# ---------------------------------------------------------------------------

artifact_dir = os.path.join(ROOT, "artifact")
os.makedirs(artifact_dir)
gk.BASE = artifact_dir
artifact_events = []
gk.ledger = ledger_capture_factory(artifact_events)
saved_jobs = []
gk.save_job = lambda job: saved_jobs.append(dict(job))

PAYLOAD = b"grabbed payload for the audit fixture\n"
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
ART = {"id": 4242, "name": "grabbed-file", "expired": False,
       "size_in_bytes": len(PAYLOAD)}
grab_job = {"id": "20260101T000002Z-grab-one-ccc333", "key": "grab-one",
            "program": "audit", "workflow": "grab.yml", "run_id": 777,
            "state": "completed"}
gk.load_jobs = lambda: [grab_job]

gh_responses = []
download_content = [PAYLOAD]
download_rc = [0]


def fake_gh_json(args, timeout=120):
    return gh_responses.pop(0), None


def fake_run_cmd(args, timeout=120, input_text=None, **kw):
    if "download" in args:
        if download_rc[0] != 0:
            return download_rc[0], "", "injected download failure"
        dest = args[args.index("-D") + 1]
        with open(os.path.join(dest, "grabbed.bin"), "wb") as f:
            f.write(download_content[0])
        return 0, "", ""
    if "DELETE" in args:
        return 0, "", ""
    return 1, "", "unexpected run_cmd call"


gk.gh_json = fake_gh_json
gk.run_cmd = fake_run_cmd
cmd_artifact_delete = getattr(gk, "cmd_artifact_delete", None)
check("F1 artifact-delete: verb exists on main",
      callable(cmd_artifact_delete))

if callable(cmd_artifact_delete):
    def art_call(job_ref, sha):
        return capture(cmd_artifact_delete,
                       SimpleNamespace(job=job_ref, landed_sha256=sha))

    exc, rc, out, err = art_call("no-such-job", PAYLOAD_SHA)
    check("F1 artifact-delete: unknown job refused (2)",
          exc is None and rc == 2 and "REFUSED" in out,
          (exc, rc, out, err))

    saved_workflow = grab_job["workflow"]
    grab_job["workflow"] = "run.yml"
    exc, rc, out, err = art_call(grab_job["id"], PAYLOAD_SHA)
    check("F1 artifact-delete: non-grab job refused (2)",
          exc is None and rc == 2 and "REFUSED" in out,
          (exc, rc, out, err))
    grab_job["workflow"] = saved_workflow

    gh_responses[:] = [{"artifacts": []}]
    exc, rc, out, err = art_call(grab_job["id"], PAYLOAD_SHA)
    check("F1 artifact-delete: missing artifact is ARTIFACT_ABSENT (3)",
          exc is None and rc == 3 and "ARTIFACT_ABSENT" in out,
          (exc, rc, out, err))

    gh_responses[:] = [{"artifacts": [ART, dict(ART, id=4243)]}]
    exc, rc, out, err = art_call(grab_job["id"], PAYLOAD_SHA)
    check("F1 artifact-delete: multiple artifacts refused (2)",
          exc is None and rc == 2 and "REFUSED" in out,
          (exc, rc, out, err))

    gh_responses[:] = [{"artifacts": [ART]}]
    exc, rc, out, err = art_call(grab_job["id"], " , ")
    check("F1 artifact-delete: empty landed sha set refused (2)",
          exc is None and rc == 2 and "REFUSED" in out,
          (exc, rc, out, err))

    gh_responses[:] = [{"artifacts": [ART]}]
    download_rc[0] = 9
    exc, rc, out, err = art_call(grab_job["id"], PAYLOAD_SHA)
    check("F1 artifact-delete: download failure refused (2)",
          exc is None and rc == 2 and "REFUSED" in out,
          (exc, rc, out, err))
    download_rc[0] = 0

    artifact_events.clear()
    gh_responses[:] = [{"artifacts": [ART]}]
    exc, rc, out, err = art_call(grab_job["id"], "f" * 64)
    check("F1 artifact-delete: hash mismatch refused (4), "
          "refusal ledgered, nothing deleted",
          exc is None and rc == 4 and "REFUSED" in out
          and any(e["event"] == "artifact_delete_refused"
                  for e in artifact_events),
          (exc, rc, out, err, artifact_events))

    gh_responses[:] = [{"artifacts": [ART]}, {"artifacts": [ART]}]
    exc, rc, out, err = art_call(grab_job["id"], PAYLOAD_SHA)
    check("F1 artifact-delete: still listed after DELETE is "
          "UNVERIFIED (5)",
          exc is None and rc == 5 and "UNVERIFIED" in out,
          (exc, rc, out, err))

    artifact_events.clear()
    saved_jobs.clear()
    gh_responses[:] = [{"artifacts": [ART]}, {"artifacts": []}]
    exc, rc, out, err = art_call(grab_job["id"], PAYLOAD_SHA)
    check("F1 artifact-delete: happy path deletes and verifies (0), "
          "record stamped, deletion ledgered",
          exc is None and rc == 0 and "ARTIFACT_DELETED" in out
          and grab_job.get("artifact_id") == 4242
          and grab_job.get("landed_sha256") == [PAYLOAD_SHA]
          and any(e["event"] == "artifact_deleted"
                  for e in artifact_events) and saved_jobs,
          (exc, rc, out, err, artifact_events))
    scratch_parent = os.path.join(artifact_dir, "grab-staging")
    check("F8 artifact-delete: missing scratch parent is created",
          exc is None and os.path.isdir(scratch_parent)
          and not os.listdir(scratch_parent),
          (exc, scratch_parent))

    # F8: a scratch parent that cannot be created refuses cleanly.
    shutil.rmtree(scratch_parent)
    with open(scratch_parent, "w", encoding="utf-8") as f:
        f.write("a file, not a directory")
    gh_responses[:] = [{"artifacts": [ART]}]
    exc, rc, out, err = art_call(grab_job["id"], PAYLOAD_SHA)
    check("F8 artifact-delete: uncreatable scratch dir refuses "
          "cleanly with a printed reason (no traceback)",
          exc is None and rc == 2 and "REFUSED" in out
          and "scratch" in out,
          (exc, rc, out, err))
    os.unlink(scratch_parent)


# ---------------------------------------------------------------------------
# F4: ledger_soft on the sweep watch path; F5: bounded per-id lookup
# ---------------------------------------------------------------------------

gk.ledger = ORIG["ledger"]
gk.gh_json = ORIG["gh_json"]
gk.run_cmd = ORIG["run_cmd"]
gk.save_job = ORIG["save_job"]
gk.LEDGER = os.path.join(ROOT, "ledger-f4.jsonl")
gk.HLOG = os.path.join(ROOT, "gk-f4.log")
gk.save_job_safe = lambda job, context: True
ledger_soft = getattr(gk, "ledger_soft", None)
check("F4 ledger_soft: function exists", callable(ledger_soft))

real_append_verified = gk._append_verified
gk._append_verified = lambda *a, **k: False
exc, rc, out, err = capture(gk.ledger, "audit_fault_probe")
hard_raises = exc is not None and isinstance(exc, IOError)
soft_result = None
if callable(ledger_soft):
    exc2, soft_result, out2, err2 = capture(ledger_soft,
                                            "audit_fault_probe")
else:
    exc2 = None
check("F4 ledger_soft: ledger() still raises on an unverifiable "
      "append while ledger_soft returns False",
      hard_raises and exc2 is None and soft_result is False,
      (exc, exc2, soft_result))

watch_job = {"id": "20260101T000003Z-watch-one-ddd444", "key": "watch-one",
             "program": "audit", "state": "dispatched", "run_id": 987654,
             "run_status": None, "run_conclusion": None, "flags": [],
             "watermarks": {}, "lane": gk.LANES[0],
             "not_in_list_noted": False}
exc, rc, out, err = capture(gk.watch_pass, [watch_job], {})
check("F4 watch: an advisory ledger append failure does not abort "
      "the watch pass",
      exc is None and rc == (0, 0) and watch_job["not_in_list_noted"],
      (exc, rc, out, err))
gk._append_verified = real_append_verified

# F5: a watched run missing from the 50-run window is resolved by a
# bounded per-id lookup (one call per missing watched id, none for
# ids already in the window).
f5_events = []
gk.ledger = ledger_capture_factory(f5_events)
if hasattr(gk, "ledger_soft"):
    # ledger_soft resolves gk.ledger at call time; keep the capture.
    pass
lookup_calls = []


def fake_fetch_run(run_id):
    lookup_calls.append(run_id)
    return {"databaseId": run_id, "status": "in_progress",
            "conclusion": None, "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:01:00Z"}


fetch_run_by_id = getattr(gk, "fetch_run_by_id", None)
check("F5 watch: per-id run lookup helper exists",
      callable(fetch_run_by_id))
if callable(fetch_run_by_id):
    gk.fetch_run_by_id = fake_fetch_run


def watch_fixture(job_id, run_id):
    return {"id": job_id, "key": job_id, "program": "audit",
            "state": "dispatched", "run_id": run_id,
            "run_status": "queued", "run_conclusion": None,
            "flags": [], "watermarks": {}, "lane": gk.LANES[0],
            "run_created_at": gk.now_iso(),
            "first_seen_in_progress_at": gk.now_iso(),
            "not_in_list_noted": True}


present = watch_fixture("20260101T000004Z-watch-two-eee555", 111)
missing = watch_fixture("20260101T000005Z-watch-three-fff666", 222)
runs_by_id = {"111": {"databaseId": 111, "status": "in_progress",
                      "conclusion": None,
                      "createdAt": "2026-01-01T00:00:00Z",
                      "updatedAt": "2026-01-01T00:01:00Z"}}
exc, rc, out, err = capture(gk.watch_pass, [present, missing],
                            runs_by_id)
changes = rc[0] if isinstance(rc, tuple) else None
check("F5 watch: run missing from the window is found by per-id "
      "lookup and watched normally",
      exc is None and changes == 2
      and missing["run_status"] == "in_progress"
      and not missing["not_in_list_noted"],
      (exc, rc, out, err, missing))
check("F5 watch: lookup is bounded (one call, only for the "
      "missing id)",
      lookup_calls == [222], lookup_calls)


# ---------------------------------------------------------------------------
# F9: staging defer escalates after a bounded number of attempts
# ---------------------------------------------------------------------------

f9_events = []
gk.ledger = ledger_capture_factory(f9_events)
defer_job = {"id": "20260101T000006Z-stage-one-aaa777", "key": "stage-one",
             "run_id": 31337}
defer_entry = {"name": "deferred.bin", "status": "pending",
               "attempts": 0, "detail": None}
for _ in range(4):
    gk._staging_defer(defer_job, defer_entry, "transport_down")
check("F9 staging: entry stays pending below the escalation bound",
      defer_entry["status"] == "pending"
      and defer_entry["attempts"] == 4,
      defer_entry)
gk._staging_defer(defer_job, defer_entry, "transport_down")
escalations = [e for e in f9_events
               if e["event"] == "staging_deferred_escalated"]
check("F9 staging: the bounded attempt escalates to a named "
      "ledger event and leaves the pending set",
      defer_entry["attempts"] == 5
      and defer_entry["status"] == "deferred_escalated"
      and len(escalations) == 1
      and escalations[0].get("attempts") == 5,
      (defer_entry, f9_events))


# ---------------------------------------------------------------------------
# F6: cmd_staging_run takes the sweep lock
# ---------------------------------------------------------------------------

gk.LOCK_DIR = os.path.join(ROOT, "staging-run.lock")
gk.load_jobs = lambda: []
staging_calls = []
gk.staging_pass = lambda jobs, only_job_id=None, limit=1: (
    staging_calls.append((list(jobs), only_job_id, limit)), 0)[1]
os.makedirs(gk.LOCK_DIR)
exc, rc, out, err = capture(
    gk.cmd_staging_run, SimpleNamespace(job=None, all=False))
check("F6 staging-run: refuses to run while the sweep lock is held",
      exc is None and rc == 0 and not staging_calls
      and "lock" in (out + err),
      (exc, rc, out, err, staging_calls))
os.rmdir(gk.LOCK_DIR)
exc, rc, out, err = capture(
    gk.cmd_staging_run, SimpleNamespace(job=None, all=False))
check("F6 staging-run: runs under the lock and releases it",
      exc is None and rc == 0 and len(staging_calls) == 1
      and not os.path.exists(gk.LOCK_DIR),
      (exc, rc, out, err, staging_calls))


# ---------------------------------------------------------------------------
# F7: log archive through run_cmd (bytes mode); rm fallbacks never raise
# ---------------------------------------------------------------------------

exc, rc, out, err = capture(
    gk.run_cmd,
    [sys.executable, "-c",
     "import sys; sys.stdout.buffer.write(b'\\x00\\xffok')"],
    binary=True)
check("F7 run_cmd: binary mode returns bytes untouched",
      exc is None and isinstance(rc, tuple) and rc[0] == 0
      and rc[1] == b"\x00\xffok" and rc[2] == b"",
      (exc, rc, out, err))

zip_buf = io.BytesIO()
with zipfile.ZipFile(zip_buf, "w") as zf:
    zf.writestr("0_job.txt", "first line\n")
    zf.writestr("1_job.txt", "second line\n")
    zf.writestr("not-text.bin", "ignored")
zip_bytes = zip_buf.getvalue()
archive_calls = []


def fake_archive_run_cmd(args, timeout=120, input_text=None, **kw):
    archive_calls.append((args, kw))
    return 0, zip_bytes, b""


real_run_cmd_for_archive = gk.run_cmd
gk.run_cmd = fake_archive_run_cmd
exc, archive_text, out, err = capture(gk.fetch_log_archive, 123456)
gk.run_cmd = real_run_cmd_for_archive
check("F7 fetch_log_archive: archive fetched through run_cmd in "
      "binary mode and text files combined",
      exc is None and archive_text is not None
      and "first line" in archive_text
      and "second line" in archive_text
      and archive_calls
      and archive_calls[0][1].get("binary") is True,
      (exc, archive_text, archive_calls))

real_unlink = os.unlink
real_subprocess_run = subprocess.run


def raising_unlink(path):
    raise OSError("injected unlink failure")


def raising_subprocess_run(*a, **k):
    raise subprocess.CalledProcessError(1, a[0] if a else "rm")


os.unlink = raising_unlink
subprocess.run = raising_subprocess_run
exc, rc, out, err = capture(gk._clear_marker,
                            os.path.join(ROOT, "marker.json"))
os.unlink = real_unlink
subprocess.run = real_subprocess_run
check("F7 _clear_marker: a failing rm fallback does not raise "
      "out of the caller",
      exc is None, (exc, rc, out, err))

gk.PAUSE_FILE = os.path.join(ROOT, "PAUSED.json")
gk.pause_state = lambda: {"ts": "2026-01-01T00:00:00Z", "reason": "test"}
resume_events = []
gk.ledger = ledger_capture_factory(resume_events)
gk.hlog = lambda msg: None


def selective_unlink(path):
    if os.path.abspath(path) == os.path.abspath(gk.PAUSE_FILE):
        raise OSError("injected unlink failure")
    return real_unlink(path)


os.unlink = selective_unlink
subprocess.run = raising_subprocess_run
exc, rc, out, err = capture(gk.cmd_resume, SimpleNamespace(job=None))
os.unlink = real_unlink
subprocess.run = real_subprocess_run
check("F7 cmd_resume: a failing rm fallback does not raise out "
      "of the caller",
      exc is None and rc == 0
      and any(e["event"] == "resumed" for e in resume_events),
      (exc, rc, out, err, resume_events))


# ---------------------------------------------------------------------------
# Submit size guard
# ---------------------------------------------------------------------------

submit_dir = os.path.join(ROOT, "submit")
os.makedirs(os.path.join(submit_dir, "queue"))
gk.QUEUE_DIR = os.path.join(submit_dir, "queue")
gk.LEDGER = os.path.join(submit_dir, "ledger.jsonl")
gk.load_jobs = ORIG["load_jobs"]
gk.save_job = ORIG["save_job"]
big_script = os.path.join(submit_dir, "big-script.ps1")
with open(big_script, "wb") as f:
    f.write(b"#" * 40000)
chosen_lane = None
for lane in gk.LANES:
    if gk.route_refusal(lane, "run.yml") is None:
        chosen_lane = lane
        break
submit_args = SimpleNamespace(
    program="audit", workflow="run.yml", lane=chosen_lane,
    script=big_script, grab_path=None, cwd=".", image=None,
    mounts=None, needs_cpu=None, needs_ram_mb=None,
    needs_storage_mb=None, needs_gpu=None, venue="auto",
    expected_min=1, key="oversize-audit", maintenance=False,
    notify_chat=None, priority=None)
exc, rc, out, err = capture(gk.cmd_submit, submit_args)
check("submit guard: an oversize script is refused before any "
      "job is recorded, with the gzip bootstrap named",
      exc is None and rc == 2 and "gzip" in (out + err)
      and not os.listdir(gk.QUEUE_DIR),
      (exc, rc, out, err, os.listdir(gk.QUEUE_DIR)))


# ---------------------------------------------------------------------------
# E1: cmd_sweep loads the queue once and threads the same list
# ---------------------------------------------------------------------------

sweep_jobs = [{"id": "20260101T000007Z-sweep-one-bbb888",
               "key": "sweep-one", "state": "submitted"}]
load_calls = []
gk.load_jobs = lambda: (load_calls.append(1), sweep_jobs)[1]
gk.acquire_lock = lambda: True
gk.stop_state = lambda: None
gk.rate_limit_remaining = lambda: (10000, None)
gk.list_runs = lambda limit: ([], None)
gk.sweep_liveness = lambda jobs: None
gk.sweep_tray_drift = lambda: None
gk.dispatch_pass = lambda jobs, runs: 0
e1_watch_seen = []
gk.watch_pass = lambda jobs, runs: (e1_watch_seen.append(jobs), (0, 0))[1]
gk.maybe_settle_drain = lambda jobs=None: False
e1_staging_seen = []
gk.staging_pass = lambda jobs, only_job_id=None, limit=1: (
    e1_staging_seen.append(jobs), 0)[1]
gk.pause_record = lambda: None
gk.hlog = lambda msg: None
exc, rc, out, err = capture(gk.cmd_sweep, SimpleNamespace())
check("E1 sweep: the queue is loaded exactly once per sweep",
      exc is None and rc == 0 and len(load_calls) == 1,
      (exc, rc, load_calls, out, err))
check("E1 sweep: watch and staging passes receive the same "
      "loaded list",
      e1_watch_seen and e1_watch_seen[0] is sweep_jobs
      and e1_staging_seen and e1_staging_seen[0] is sweep_jobs,
      (e1_watch_seen, e1_staging_seen))
check("E1 sweep: summary counts come from the threaded list",
      "active=0 waiting=1" in out, out)


print()
if failures:
    print("TEST_AUDIT_FIXES: FAIL (%d failing)" % len(failures))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("TEST_AUDIT_FIXES: ALL PASS")

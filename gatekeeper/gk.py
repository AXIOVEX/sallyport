#!/usr/bin/env python3
"""gk.py - Desktop Access gatekeeper: the single dispatch/watch door for the
tbitcs/desktop-runner self-hosted runner fleet.

Implements the fleet standard (github-polling-census-and-standard-2026-10-27,
Parts 2 and 4):
  - one watcher (this tool, via a 5-minute cron sweep), batched reads
  - gh api rate_limit headroom check before any dispatch; hard stand-down
    below 1,000 core remaining, completion-critical only below 500
  - duplicate-dispatch pre-check (local --key + same script hash in flight)
  - lane serialization on this side until real lanes stand up
  - Part 4 watermarks are RECORDED, never acted on: this tool never cancels
    any run, ever.

State lives in files next to this script:
  queue/<job-id>.json   one JSON per job
  ledger.jsonl          append-only event log

Python 3 stdlib only.
"""

import argparse
import base64
import hashlib
import json
import statistics
import os
import re
import subprocess
import sys
import tempfile
import time
import types
import uuid
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
QUEUE_DIR = os.path.join(BASE, "queue")
LEDGER = os.path.join(BASE, "ledger.jsonl")
LOCK_DIR = os.path.join(BASE, ".sweep.lock")
PAUSE_FILE = os.path.join(BASE, "PAUSED.json")
# Staged hold-signal writer scripts (spec 013 FR-009); durable
# under BASE like every other gatekeeper script (never /tmp).
HOLDS_DIR = os.path.join(BASE, "holds")
STOP_FILE = os.path.join(BASE, "STOPPED.json")
OUTBOX_CURSOR = os.path.join(BASE, "outbox-cursor.json")
HOSTS_FILE = os.path.join(BASE, "hosts.json")   # host liveness (spec 011)
LOCKS_FILE = os.path.join(BASE, "LOCKS.json")   # lane locks (spec 015)
LANES_LIVE_FILE = os.path.join(BASE, "LANES_LIVE")

REPO = "tbitcs/desktop-runner"
PROJECT_REPO = "AXIOVEX/syncyard"   # canonical project repo (status --repo)
BC_REPO = "tbitcs/syncyard-backchannel"  # failsafe backchannel (spec 009)
BC_HEARTBEAT_PATH = "channels/syncyard-dev/heartbeat.json"
BC_STALE_S = 300                         # heartbeat older = stale alarm
HLOG = os.path.join(BASE, "gatekeeper.log")   # human-readable rotating log
HLOG_MAX_BYTES = 1000000

# --- Config constants (edit here, nowhere else) ---------------------------
MAX_ACTIVE_DISPATCHED = 2        # total gatekeeper-active runs cap, pre-lanes
STANDDOWN_REMAINING = 1000       # below this: no dispatch (standard Part 2.3)
CRITICAL_REMAINING = 500         # below this: no list call either
STALE_DISPATCHING_MIN = 10       # dispatching state older than this = human flag
LOCK_STALE_MIN = 10
WORKFLOWS = ("run.yml", "wsl.yml", "grab.yml")
LANES = ("quick", "normal", "long", "gpu")
DEFAULT_CWD = {"run.yml": r"C:\Users\trist", "wsl.yml": "/home/tpierson",
               "grab.yml": None}

# Part 4 watermark tables, minutes: lane -> (W1, W2)
QUEUED_WM = {"quick": (10, 30), "normal": (20, 45), "long": (45, 120),
             "gpu": (30, 90)}
EXEC_WM_FIXED = {"quick": (5, 15), "normal": (45, 90)}
# long/gpu executing: declared expectation wins, floored.
EXEC_WM_FLOOR = {"long": (90, 180), "gpu": (120, 240)}

# States in which a job blocks a duplicate --key and holds its lane.
ACTIVE_STATES = ("submitted", "dispatching", "dispatched", "watching")
# States holding a live run on the runner (lane gate / active cap).
RUN_HOLDING_STATES = ("dispatching", "dispatched", "watching")

MARKER_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}([ :]|$)")

# --- Effective configuration (spec 010 FR-001/FR-002/FR-007) ---------------
# The literals above are the pre-config constants. config.py's built-in
# defaults ARE those constants verbatim, so loading the effective config
# (syncyard.yaml beside this file, or $SYNCYARD_CONFIG / $SYNCARD_CONFIG,
# deep-merged over the defaults) and rebinding every name below changes
# nothing when no config file exists — golden-proven by test_golden.py.
# A config that fails validation exits loudly here (fail closed), never
# a silent fall-back to defaults.
import importlib.util as _ilu

_cfg_spec = _ilu.spec_from_file_location(
    "syncyard_config", os.path.join(BASE, "config.py"))
_cfg_mod = _ilu.module_from_spec(_cfg_spec)
_cfg_spec.loader.exec_module(_cfg_mod)
try:
    _CFG = _cfg_mod.load_config(os.environ.get("SYNCYARD_CONFIG") or None)
except _cfg_mod.ConfigError as _e:
    print("CONFIG_ERROR %s" % _e, file=sys.stderr)
    raise SystemExit(2)

REPO = _CFG["door_repo"]
PROJECT_REPO = _CFG["project_repo"]
HLOG_MAX_BYTES = _CFG["retention"]["human_log_max_bytes"]
MAX_ACTIVE_DISPATCHED = _CFG["dispatch"]["max_active_dispatched"]
STANDDOWN_REMAINING = _CFG["budget"]["standdown_remaining"]
CRITICAL_REMAINING = _CFG["budget"]["critical_remaining"]
STALE_DISPATCHING_MIN = _CFG["dispatch"]["stale_dispatching_min"]
LOCK_STALE_MIN = _CFG["dispatch"]["lock_stale_min"]
WORKFLOWS = tuple(_CFG["workflows"])
LANES = tuple(_CFG["lanes"].keys())
LANE_LABEL = {n: l["runner_label"] for n, l in _CFG["lanes"].items()}
LANE_CONCURRENCY = {n: int(l.get("concurrency", 1))
                    for n, l in _CFG["lanes"].items()}
QUEUED_WM = {n: tuple(l["queued"]) for n, l in _CFG["lanes"].items()}
EXEC_WM_FIXED = {n: tuple(l["executing"]["values"])
                 for n, l in _CFG["lanes"].items()
                 if l["executing"]["mode"] == "fixed"}
EXEC_WM_FLOOR = {n: tuple(l["executing"]["floor"])
                 for n, l in _CFG["lanes"].items()
                 if l["executing"]["mode"] == "expectation"}
HOST_CWD = {name: dict(h.get("cwd") or {})
            for name, h in _CFG["hosts"].items()}


def default_cwd(lane, workflow):
    """The default working directory for a job on `lane`.

    Resolution is per machine, never global: the lane's own
    host first, then the hosts that hang off it (a WSL host
    declared `via` its Windows host is the same machine's
    other half), then the built-in DEFAULT_CWD table. A host
    that declares a workflow's cwd explicitly, including
    null, wins over every fallback.

    History: until 2026-11-04 this was one merged dict built
    by updating a global with every host's cwd map in file
    order, so the LAST host in the config silently set the
    default for all of them. With one machine that was
    invisible; the day the laptop enrolled as a second
    Windows host, its run.yml directory (D:\\Development\\
    Inductoheat) overwrote the desktop's and every desktop
    run.yml job submitted without --cwd was dispatched into
    a directory that does not exist there, so its payload
    never ran (field case: bc-dnsfix-peek-2026-11-04).
    """
    host = LANE_HOST.get(lane) if lane else None
    if host is not None:
        chain = [host] + sorted(
            name for name, h in _CFG["hosts"].items()
            if h.get("via") == host)
        for name in chain:
            cwd_map = HOST_CWD.get(name) or {}
            if workflow in cwd_map:
                return cwd_map[workflow]
    return DEFAULT_CWD.get(workflow)
MARKER_RE = re.compile(_CFG["markers"]["pattern"])
LIVENESS_DOWN_AFTER_S = _CFG["liveness"]["down_after_s"]
REBOOT_GRACE_S = _CFG["reboot"]["grace_s"]
# Checkpoint hold (spec 013 FR-007): the cooperative job-level
# pause contract — per-job venue hold file, the marker a held
# job prints, the exit code it stops with, the env var the
# transport workflows export the path under.
CHECKPOINT_EXIT_CODE = int(_CFG["checkpoint"]["exit_code"])
CHECKPOINT_MARKER = _CFG["checkpoint"]["marker"]
CHECKPOINT_ENV_VAR = _CFG["checkpoint"]["env_var"]
CHECKPOINT_HOLDS_DIR = dict(_CFG["checkpoint"]["holds_dir"])
# Host drain (spec 011 FR-005/FR-007): the checkpoint window a
# deliberate host drain waits out (per lane when overridden),
# and the staleness bound for the host guard's heartbeat beats.
DRAIN_WINDOW_S = _CFG["drain"]["window_s"]
DRAIN_PER_LANE_WINDOW_S = dict(_CFG["drain"]["per_lane_window_s"])
DRAIN_GUARD_STALE_S = _CFG["drain"]["guard_stale_after_s"]
DRAIN_POLL_S = 2  # drain wait-loop poll cadence (tests may lower)
# Priority optimizer (spec 013 FR-012): auto-assigned effective
# priorities are capped at the ceiling (a human-set priority
# above it is unreachable by any auto job) and age +1 per step.
OPTIMIZER_CEILING = int(_CFG["dispatch"]["optimizer_ceiling"])
OPTIMIZER_AGING_STEP_MIN = _CFG["dispatch"]["optimizer_aging_step_min"]
# Lane priority lock (spec 015 FR-007/FR-011): a lock taken
# without --ttl-min gets the default TTL; no lock may exceed
# the maximum. Expiry is what keeps a forgotten claim from
# squatting a lane forever.
LOCK_DEFAULT_TTL_MIN = int(_CFG["lock"]["default_ttl_min"])
LOCK_MAX_TTL_MIN = int(_CFG["lock"]["max_ttl_min"])
# Adaptive lanes (spec 016): the resolved profile surface —
# per-lane resource class and duration band for auto routing,
# and the routing knobs. _CFG["lanes"] is already the ACTIVE
# profile's enabled lanes (config resolution, FR-001).
LANE_CLASS = {n: l.get("resource_class", "any")
              for n, l in _CFG["lanes"].items()}
LANE_BAND = {n: tuple(l.get("duration_band") or (None, None))
             for n, l in _CFG["lanes"].items()}
LANE_PIN = {n: l.get("workflow") for n, l in _CFG["lanes"].items()}
ACTIVE_PROFILE = _CFG.get("active_lane_profile", "fleet-default")
LANES_AUTO_DEFAULT = bool(_CFG.get("lanes_auto_default", False))
CAPABILITY_MAX_AGE_MIN = int(_CFG.get("lanes_capability_max_age_min", 60))

# Host routing (spec 010 FR-003/FR-004): a job names a lane; the
# lane's host is the venue. A workflow the host does not serve is
# refused at submit/adopt — never silently rerouted to another
# venue.
LANE_HOST = {n: l["host"] for n, l in _CFG["lanes"].items()}
HOST_TYPE = {n: h["type"] for n, h in _CFG["hosts"].items()}
HOST_VIA = {n: h.get("via") for n, h in _CFG["hosts"].items()}
HOST_WORKFLOWS = {n: dict(h.get("workflows") or {})
                  for n, h in _CFG["hosts"].items()}
HOST_IDENTITY = {n: list(h.get("identity")
                         or _gk_config.IDENTITY_BY_TYPE[h["type"]])
                 for n, h in _CFG["hosts"].items()}
HOST_IMAGE = {n: h.get("image") for n, h in _CFG["hosts"].items()}


def route_check(lane, workflow):
    """(host, kind) if the lane's host serves this workflow, else None.

    Spec 016: a lane may pin itself to one workflow
    (lanes.profiles.<p>.<lane>.workflow); a pinned lane serves
    no other workflow, exactly like a host that does not serve
    it — the job is refused, never silently rerouted."""
    host = LANE_HOST.get(lane)
    if host is None:
        return None
    pin = LANE_PIN.get(lane)
    if pin is not None and pin != workflow:
        return None
    kind = HOST_WORKFLOWS.get(host, {}).get(workflow)
    return (host, kind) if kind else None


# --- Small helpers ---------------------------------------------------------

def now_dt():
    return datetime.now(timezone.utc)


def now_iso():
    return now_dt().strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None


def minutes_between(a, b):
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 60.0


def hlog(msg):
    """Append one line to the human-readable gatekeeper log, rotating at
    HLOG_MAX_BYTES (one backup kept). Best-effort by design: a logging
    failure must never break a sweep, so every error is swallowed."""
    try:
        if os.path.isfile(HLOG) and os.path.getsize(HLOG) > HLOG_MAX_BYTES:
            os.replace(HLOG, HLOG + ".1")
        with open(HLOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (now_iso(), msg))
    except OSError:
        pass


def _write_verified_bytes(path, data, attempts=8):
    """WRITE-VERIFY-RETRY (spec 001 FR-012): write data to path with
    unbuffered syscalls, fsync, then READ BACK and byte-compare.
    Retries with backoff (0.25s doubling, capped at 8s). Returns True
    only when the bytes on disk are exactly the bytes given - the VM
    write restriction can drop a buffered write silently and leave a
    0-byte file standing, so an unverified success is never trusted.
    Returns False after the bounded retries are exhausted."""
    for i in range(attempts):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                         0o644)
            try:
                view = memoryview(data)
                while view:
                    n = os.write(fd, view)
                    view = view[n:]
                os.fsync(fd)
            finally:
                os.close(fd)
            with open(path, "rb") as f:
                back = f.read()
            if back == data and len(back) > 0:
                return True
        except OSError:
            pass
        if i + 1 < attempts:
            time.sleep(min(0.25 * (2 ** i), 8.0))
    return False


def _append_verified(path, line, attempts=8):
    """Append one line and verify it landed (file size grows by
    exactly len(line)); same retry discipline as
    _write_verified_bytes. Returns True only when verified."""
    data = line.encode("utf-8")
    for i in range(attempts):
        try:
            before = os.path.getsize(path) if os.path.isfile(path) else 0
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                         0o644)
            try:
                view = memoryview(data)
                while view:
                    n = os.write(fd, view)
                    view = view[n:]
                os.fsync(fd)
            finally:
                os.close(fd)
            if os.path.getsize(path) == before + len(data):
                return True
        except OSError:
            pass
        if i + 1 < attempts:
            time.sleep(min(0.25 * (2 ** i), 8.0))
    return False


def ledger(event, **kw):
    rec = {"ts": now_iso(), "event": event}
    rec.update(kw)
    line = json.dumps(rec, sort_keys=True) + "\n"
    if not _append_verified(LEDGER, line):
        raise IOError("ledger append could not be verified after "
                      "retries: %s" % event)
    return rec


def job_path(job_id):
    return os.path.join(QUEUE_DIR, job_id + ".json")


def pause_state():
    """The pause record, or None when not paused. The file's mere
    existence means paused (a corrupt record still pauses — the
    operator's intent is the safe side)."""
    if not os.path.isfile(PAUSE_FILE):
        return None
    try:
        with open(PAUSE_FILE, encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else {"ts": None, "reason": ""}
    except (OSError, ValueError):
        return {"ts": None, "reason": ""}


def pause_record():
    """The normalized pause/focus record (spec 013 FR-001), or
    None when not paused. Every field of the stored record is
    preserved; the normalized view adds the superset-schema
    fields with defaults. A legacy record carrying a single
    `except_key` string (the fleet's 2026-11-01 pause shape)
    normalizes to focused mode with that key as a one-element
    key list, so the exception the record itself names is
    effective under the new reader."""
    rec = pause_state()
    if rec is None:
        return None
    out = dict(rec)
    keys = [str(k) for k in (rec.get("except_keys") or [])]
    legacy = rec.get("except_key")
    if legacy and str(legacy) not in keys:
        keys.append(str(legacy))
    out["except_keys"] = keys
    out["except_programs"] = [str(p)
                              for p in (rec.get("except_programs") or [])]
    out["except_chats"] = [str(c)
                           for c in (rec.get("except_chats") or [])]
    out["drain"] = bool(rec.get("drain"))
    focused = bool(keys or out["except_programs"] or out["except_chats"])
    out["mode"] = "focused" if focused else "paused"
    return out


def focus_match(job, rec):
    """The selector admitting this job under a pause/focus
    record (spec 013 FR-002), or None. A key selector matches
    on equality or prefix, so a chat can focus a whole job
    family with one selector."""
    if rec is None:
        return None
    key = str(job.get("key") or "")
    for sel in rec.get("except_keys") or []:
        if key == sel or key.startswith(sel):
            return "key:" + sel
    program = job.get("program")
    if program and program in (rec.get("except_programs") or []):
        return "program:" + str(program)
    chat = job.get("notify_chat")
    if chat and chat in (rec.get("except_chats") or []):
        return "chat:" + str(chat)
    return None


def selectors_text(rec):
    """A compact rendering of a focus record's selector set,
    for status banners and command output."""
    parts = (["key=%s" % k for k in rec.get("except_keys") or []]
             + ["program=%s" % p
                for p in rec.get("except_programs") or []]
             + ["chat=%s" % c for c in rec.get("except_chats") or []])
    return ", ".join(parts) if parts else "(none)"


# --- Live priority + the optimizer (spec 013 FR-003/FR-004/FR-012)

def _prio(job):
    """The record's nominal priority as an int (absent or
    malformed reads as 0 — pre-spec-013 records carry none)."""
    try:
        return int(job.get("priority") or 0)
    except (TypeError, ValueError):
        return 0


def _auto_base(job):
    """Optimizer base from the declared expectation (FR-012):
    short jobs first; an undeclared expectation counts as the
    conservative 180 minutes."""
    exp = job.get("expected_min")
    e = 180 if exp is None else exp
    if e <= 2:
        return 50
    if e <= 10:
        return 40
    if e <= 30:
        return 30
    if e <= 120:
        return 20
    return 10


def auto_priority(job, now):
    """The optimizer's effective priority for an auto job
    (FR-012): base + lane-fit bonus + aging, capped at the
    ceiling. A pure function of the record and the evaluation
    instant — it writes nothing, so it can never go stale and
    never overrides a human value (it is not applied to
    human-set jobs at all)."""
    exp = job.get("expected_min")
    fit = 0
    if exp is not None:
        w2 = QUEUED_WM.get(job.get("lane"), (0, 0))[1]
        if w2 and exp <= w2:
            fit = 5
    waited = minutes_between(parse_iso(job.get("created_at")), now)
    aging = 0
    if waited is not None and waited > 0:
        aging = int(waited // OPTIMIZER_AGING_STEP_MIN)
    return min(OPTIMIZER_CEILING, _auto_base(job) + fit + aging)


def effective_priority(job, now):
    """(value, source) used for ordering: a human job's pinned
    nominal value; an auto job's optimizer-assigned value."""
    if job.get("priority_source") == "human":
        return _prio(job), "human"
    return auto_priority(job, now), "auto"


def dispatch_sort_key(job, now):
    """The unified dispatch order (FR-012): effective priority
    descending, then human before auto at equal value, then
    created_at. Admission (pause/focus/drain, host holds) is
    decided before ordering and is never overridden by it."""
    value, source = effective_priority(job, now)
    return (-value, 0 if source == "human" else 1,
            job.get("created_at") or "")


def _write_text_file(path, text):
    """Write a small text file through the write-verify-retry path
    (spec 001 FR-012), landed by atomic swap (FR-013): the payload
    is verified in a temp file in the same directory and renamed
    over the target, so the target is never opened for writing and
    a failed write leaves the prior content standing. Raises
    IOError when the write cannot be verified - a pause/stop record
    that silently did not land is a safety failure, not a soft
    one."""
    data = text.encode("utf-8")
    directory = os.path.dirname(path) or "."
    tmp = os.path.join(directory, ".%s.%d.tmp"
                       % (os.path.basename(path), os.getpid()))
    try:
        if _write_verified_bytes(tmp, data):
            try:
                os.replace(tmp, path)
                with open(path, "rb") as f:
                    if f.read() == data:
                        _fsync_dir(directory)
                        return
            except OSError:
                pass
        raise IOError("could not write verified file: %s" % path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _write_pause_record(rec):
    """Write the pause/focus record through the same verified
    write as every other control file; False (with the error
    printed) when the write did not verify."""
    try:
        _write_text_file(PAUSE_FILE,
                         json.dumps(rec, indent=2, sort_keys=True))
    except (OSError, subprocess.SubprocessError) as exc:
        print("ERROR: could not write the pause file (%s). The "
              "change is NOT in effect." % exc)
        return False
    return True


def cmd_pause(args):
    cur = pause_state()
    drain = bool(getattr(args, "drain", False))
    if cur is not None:
        if drain and not cur.get("drain"):
            # Spec 013 FR-005: an immediate pause becomes a
            # draining one in place; every other field of the
            # record (focus selectors included) is preserved.
            cur["drain"] = True
            cur["drain_since"] = now_iso()
            if not _write_pause_record(cur):
                return 1
            ledger("drain_started", reason=cur.get("reason", ""))
            hlog("DRAINING at %s (pause record updated in place)"
                 % cur["drain_since"])
            print("DRAINING at %s — executing jobs finish, "
                  "nothing new dispatches, then the pause "
                  "settles." % cur["drain_since"])
            return 0
        print("ALREADY PAUSED since %s reason=%s"
              % (cur.get("ts"), cur.get("reason") or ""))
        return 0
    rec = {"ts": now_iso(), "reason": args.reason or ""}
    if drain:
        rec["drain"] = True
        rec["drain_since"] = now_iso()
    if not _write_pause_record(rec):
        return 1
    ledger("paused", reason=rec["reason"], drain=drain)
    hlog("PAUSED at %s reason=%s" % (rec["ts"], rec["reason"]))
    if drain:
        print("PAUSED at %s — DRAINING: executing jobs finish, "
              "nothing new dispatches, then the pause settles."
              % rec["ts"])
    else:
        print("PAUSED at %s — dispatch of program jobs is held; "
              "maintenance jobs still dispatch; the watch pass "
              "continues." % rec["ts"])
    return 0


def cmd_focus(args):
    keys = list(args.except_key or [])
    programs = list(args.except_program or [])
    chats = list(args.except_chat or [])
    if not (keys or programs or chats):
        print("REFUSED: focus needs at least one selector "
              "(--except-key, --except-program or --except-chat); "
              "an empty selector set would hold everything, "
              "which is plain 'gk pause'.")
        return 2
    rec = pause_state() or {}
    rec.setdefault("ts", now_iso())
    if args.reason:
        rec["reason"] = args.reason
    rec.setdefault("reason", "")
    rec["except_keys"] = keys
    rec["except_programs"] = programs
    rec["except_chats"] = chats
    if getattr(args, "drain", False) and not rec.get("drain"):
        rec["drain"] = True
        rec["drain_since"] = now_iso()
    if not _write_pause_record(rec):
        return 1
    norm = pause_record()
    ledger("focused", selectors=selectors_text(norm),
           reason=rec.get("reason", ""))
    hlog("FOCUSED at %s selectors=%s"
         % (now_iso(), selectors_text(norm)))
    print("FOCUSED at %s — only jobs matching [%s] dispatch; "
          "maintenance jobs still flow%s."
          % (rec["ts"], selectors_text(norm),
             "; draining toward the focus set" if rec.get("drain")
             else ""))
    return 0


# --- Lane priority lock (spec 015) -------------------------------------------
#
# LOCKS.json beside PAUSED.json: a flat mapping of lane ->
# {program, state, acquired_at, expires_at, ttl_min, reason,
# notify_chat}. One record per lane makes "one holder per lane"
# structural. Stored states are lowercase ("active" /
# "suspended"); status renders them uppercase. Every mutation
# goes through the FR-013 verified write; a failed write leaves
# the prior file byte-identical and nothing is ledgered as done.
#
# Actor discipline is uniform across the lock verbs: --program
# names the ACTOR. Actor == holder is the normal path; actor !=
# holder is refused (exit 3, naming holder, state, expiry)
# unless --force — the operator override — which is ledgered
# distinctly and noticed to the holder's chat.

LOCK_STATES = ("active", "suspended")


def load_locks():
    """The lane-lock state (spec 015 FR-001/FR-010). A missing
    file means no locks — the pre-015 world, behaviorally
    identical. A corrupt file also reads as no locks rather
    than crashing every command (the load_hosts_state
    tolerance): lock claims are re-acquirable, a wedged reader
    helps no one. An unreadable state value normalizes to
    "active" — fail-closed: a lock whose state cannot be read
    still enforces."""
    try:
        with open(LOCKS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for lane, rec in raw.items():
        if not isinstance(rec, dict) or not rec.get("program"):
            continue
        r = dict(rec)
        r["state"] = r.get("state") if r.get("state") in LOCK_STATES \
            else "active"
        out[str(lane)] = r
    return out


def _write_locks_record(state):
    """Write LOCKS.json through the same verified write as
    every other control file; False (with the error printed)
    when the write did not verify."""
    try:
        _write_text_file(LOCKS_FILE,
                         json.dumps(state, indent=2, sort_keys=True))
    except (OSError, subprocess.SubprocessError) as exc:
        print("ERROR: could not write the locks file (%s). The "
              "change is NOT in effect." % exc)
        return False
    return True


def _holder_chat(program, jobs=None):
    """The notify chat a program most recently registered on
    one of its jobs, or None — the address lock notices
    (expiry, override release, override suspend/resume) are
    owed to (spec 015 FR-009)."""
    if jobs is None:
        jobs = load_jobs()
    best = None
    for j in jobs:
        if j.get("program") == program and j.get("notify_chat"):
            if best is None or (j.get("created_at") or "") \
                    >= (best.get("created_at") or ""):
                best = j
    return best.get("notify_chat") if best else None


def _lock_expiry_iso(minutes):
    return (now_dt() + timedelta(minutes=float(minutes))).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _ttl_value(minutes):
    """A TTL as stored: an int when integral (the common
    case), else the float as given."""
    f = float(minutes)
    return int(f) if f == int(f) else f


def _lock_refusal(lane, rec):
    return ("REFUSED: lane %s is locked by %s (state=%s, "
            "expires %s)" % (lane, rec.get("program"),
                             str(rec.get("state") or "active").upper(),
                             rec.get("expires_at") or "?"))


def expire_locks(jobs=None):
    """Remove past-due locks (spec 015 FR-007) and return the
    effective lock map. Evaluated at the head of the dispatch
    pass and on entry to the lock/unlock commands — never by
    a separate timer. SUSPENDED locks expire on the same
    clock as ACTIVE ones. The removal persists BEFORE
    `lock_expired` ledgers; if the persist write cannot be
    verified, the expiry does not take effect (the unexpired
    map is returned and the lock keeps enforcing) — an
    unlanded expiry must not silently free a lane."""
    locks = load_locks()
    if not locks:
        return locks
    now = now_dt()
    expired = [lane for lane in sorted(locks)
               if parse_iso(locks[lane].get("expires_at")) is not None
               and parse_iso(locks[lane].get("expires_at")) <= now]
    if not expired:
        return locks
    remaining = {k: v for k, v in locks.items() if k not in expired}
    if not _write_locks_record(remaining):
        hlog("LOCK_EXPIRE_WRITE_FAILED lanes=%s — the locks stand"
             % ",".join(expired))
        return locks
    for lane in expired:
        rec = locks[lane]
        kw = {"lane": lane, "program": rec.get("program"),
              "state": rec.get("state"),
              "expires_at": rec.get("expires_at")}
        chat = rec.get("notify_chat") or _holder_chat(
            rec.get("program"), jobs)
        if chat:
            kw["notify_chat"] = chat
        ledger("lock_expired", **kw)
        hlog("LOCK EXPIRED lane=%s holder=%s"
             % (lane, rec.get("program")))
    return remaining


def locks_section_lines(locks=None):
    """The LOCKS section (spec 015 FR-009): one line per lock
    — lane, holder, state, acquired, expires, reason — or
    `none`. Shared by `gk status` (all modes) and
    `gk lock --list`."""
    if locks is None:
        locks = load_locks()
    lines = ["LOCKS:"]
    if not locks:
        lines.append("  none")
        return lines
    for lane in sorted(locks):
        rec = locks[lane]
        lines.append(
            "  lock lane=%s holder=%s state=%s acquired=%s "
            "expires=%s reason=%s"
            % (lane, rec.get("program"),
               str(rec.get("state") or "active").upper(),
               rec.get("acquired_at") or "?",
               rec.get("expires_at") or "?",
               rec.get("reason") or ""))
    return lines


def _lock_set_state(lane, locks, existing, actor, target, force):
    """Suspend/resume one lock (spec 015 FR-005) under the
    uniform actor discipline."""
    verb = "suspend" if target == "suspended" else "resume"
    if existing is None:
        print("lane %s: no lock in effect — nothing to %s"
              % (lane, verb))
        return 0
    holder = existing.get("program")
    override = actor != holder
    if override and not force:
        print(_lock_refusal(lane, existing)
              + " — only the holder may %s it (or pass --force "
                "as the operator override)." % verb)
        return 3
    if existing.get("state") == target:
        print("lane %s: the lock held by %s is already %s"
              % (lane, holder, target.upper()))
        return 0
    rec = dict(existing)
    rec["state"] = target
    locks[lane] = rec
    if not _write_locks_record(locks):
        return 1
    kw = {"lane": lane, "program": holder, "actor": actor,
          "override": override}
    if override:
        chat = existing.get("notify_chat") or _holder_chat(holder)
        if chat:
            kw["notify_chat"] = chat
    ledger("lock_suspended" if target == "suspended"
           else "lock_resumed", **kw)
    hlog("LOCK %s lane=%s holder=%s actor=%s"
         % (target.upper(), lane, holder, actor))
    print("LOCK %s lane=%s holder=%s%s"
          % (target.upper(), lane, holder,
             " (operator override by %s)" % actor if override
             else ""))
    return 0


def cmd_lock(args):
    """Acquire, renew, suspend, resume, or list lane locks
    (spec 015 FR-001/FR-005/FR-009)."""
    if getattr(args, "list", False):
        for line in locks_section_lines(expire_locks()):
            print(line)
        return 0
    lane = getattr(args, "lane", None)
    if not lane:
        print("ERROR: --lane is required (or use --list)")
        return 2
    if lane not in LANES:
        print("ERROR: unknown lane %r (lanes: %s)"
              % (lane, ", ".join(LANES)))
        return 2
    suspend = bool(getattr(args, "suspend", False))
    resume = bool(getattr(args, "resume", False))
    if suspend and resume:
        print("REFUSED: --suspend and --resume are "
              "alternatives; pass one.")
        return 2
    locks = expire_locks()
    existing = locks.get(lane)
    actor = getattr(args, "program", None)
    if suspend or resume:
        return _lock_set_state(
            lane, locks, existing, actor,
            "suspended" if suspend else "active",
            bool(getattr(args, "force", False)))
    # Acquire / renew.
    if not actor:
        print("ERROR: --program is required to acquire a lock "
              "(it names the holder)")
        return 2
    ttl_given = getattr(args, "ttl_min", None)
    if ttl_given is not None and not (
            1 <= float(ttl_given) <= LOCK_MAX_TTL_MIN):
        print("REFUSED: --ttl-min %s is outside [1, %s] "
              "(lock.max_ttl_min)" % (ttl_given, LOCK_MAX_TTL_MIN))
        return 3
    if existing is not None and existing.get("program") != actor:
        print(_lock_refusal(lane, existing)
              + " — a lock is held in both the ACTIVE and "
                "SUSPENDED states; the holder's release, an "
                "operator override, or expiry ends it.")
        return 3
    chat = _holder_chat(actor)
    if existing is not None:
        # Same-holder re-acquire = renewal (FR-001): the TTL
        # extends from now, the state is preserved (a
        # suspended lock stays suspended), the reason is
        # replaced only when a new one is given.
        ttl = ttl_given if ttl_given is not None \
            else existing.get("ttl_min") or LOCK_DEFAULT_TTL_MIN
        rec = dict(existing)
        rec["ttl_min"] = _ttl_value(ttl)
        rec["expires_at"] = _lock_expiry_iso(ttl)
        rec["renewed_at"] = now_iso()
        if getattr(args, "reason", ""):
            rec["reason"] = args.reason
        if chat:
            rec["notify_chat"] = chat
        locks[lane] = rec
        if not _write_locks_record(locks):
            return 1
        ledger("lock_renewed", lane=lane, program=actor,
               ttl_min=rec["ttl_min"], expires_at=rec["expires_at"],
               state=rec.get("state"))
        print("LOCK RENEWED lane=%s holder=%s state=%s "
              "expires=%s" % (lane, actor,
                              str(rec.get("state") or "active").upper(),
                              rec["expires_at"]))
        return 0
    ttl = ttl_given if ttl_given is not None \
        else LOCK_DEFAULT_TTL_MIN
    rec = {"program": actor, "state": "active",
           "acquired_at": now_iso(),
           "expires_at": _lock_expiry_iso(ttl),
           "ttl_min": _ttl_value(ttl),
           "reason": getattr(args, "reason", "") or "",
           "notify_chat": chat}
    locks[lane] = rec
    if not _write_locks_record(locks):
        return 1
    ledger("lock_acquired", lane=lane, program=actor,
           ttl_min=rec["ttl_min"], expires_at=rec["expires_at"],
           reason=rec["reason"])
    hlog("LOCKED lane=%s holder=%s expires=%s"
         % (lane, actor, rec["expires_at"]))
    print("LOCKED lane=%s holder=%s state=ACTIVE expires=%s "
          "ttl_min=%s" % (lane, actor, rec["expires_at"],
                          rec["ttl_min"]))
    return 0


def cmd_unlock(args):
    """Release a lane lock (spec 015 FR-003): the holder
    releases; anyone else needs --force (the operator
    override, ledgered distinctly and noticed to the
    holder). Effective at the next dispatch decision; a
    running run is never touched."""
    lane = args.lane
    locks = expire_locks()
    existing = locks.get(lane)
    if existing is None:
        print("lane %s: no lock in effect" % lane)
        return 0
    holder = existing.get("program")
    actor = getattr(args, "program", None)
    override = actor != holder
    if override and not getattr(args, "force", False):
        print(_lock_refusal(lane, existing)
              + " — only the holder may release it (or pass "
                "--force as the operator override).")
        return 3
    del locks[lane]
    if not _write_locks_record(locks):
        return 1
    if override:
        kw = {"lane": lane, "program": holder, "actor": actor}
        chat = existing.get("notify_chat") or _holder_chat(holder)
        if chat:
            kw["notify_chat"] = chat
        ledger("lock_released_override", **kw)
        hlog("LOCK RELEASED (override) lane=%s holder=%s "
             "actor=%s" % (lane, holder, actor))
        print("LOCK RELEASED lane=%s holder=%s (operator "
              "override by %s)" % (lane, holder, actor))
    else:
        ledger("lock_released", lane=lane, program=holder,
               actor=actor)
        hlog("LOCK RELEASED lane=%s holder=%s" % (lane, holder))
        print("LOCK RELEASED lane=%s holder=%s" % (lane, holder))
    return 0


def _find_job(jobs, ref):
    """Any job (any state) by id or key — the cmd_cancel lookup
    shape, shared by the spec 013 record-targeting commands."""
    for j in jobs:
        if j["id"] == ref or j.get("key") == ref:
            return j
    return None


def _resume_job(jobref):
    """Job-level resume (spec 013 FR-009): clear the job's hold
    request and withdraw its hold signal on the venue. The job
    keeps its state — continuation is the owning chat's new
    submission, per the checkpoint contract. A hold raised
    host-side (FR-007) leaves no request on the record; a
    `held_at_checkpoint` job is releasable either way, so the
    withdrawal is queued regardless of which side raised the
    signal."""
    job = _find_job(load_jobs(), jobref)
    if job is None:
        print("no job %r (active or recent)" % jobref)
        return 4
    if not job.get("hold_requested") \
            and job["state"] != "held_at_checkpoint":
        print("job %s (key=%s, state=%s) has no hold requested — "
              "nothing to release"
              % (job["id"], job.get("key"), job["state"]))
        return 0
    job["hold_requested"] = False
    job["hold_released_at"] = now_iso()
    save_job(job)
    ledger("hold_released", job_id=job["id"], key=job.get("key"))
    sid, err = _submit_signal_job(job, hold=False)
    if err:
        print("hold released on the record for %s, but the signal "
              "withdrawal could not be queued: %s" % (job["id"], err))
        return 4
    print("HOLD RELEASED for %s (key=%s); withdrawal queued as "
          "%s. The job stays %s — resubmit to continue."
          % (job["id"], job.get("key"), sid, job["state"]))
    return 0


def cmd_resume(args):
    if getattr(args, "job", None):
        return _resume_job(args.job)
    if pause_state() is None:
        print("NOT PAUSED")
        return 0
    try:
        os.unlink(PAUSE_FILE)
    except OSError:
        subprocess.run(["/bin/sh", "-c", 'rm -f "$1"', "sh", PAUSE_FILE],
                       check=True)
    ledger("resumed")
    hlog("RESUMED at %s" % now_iso())
    print("RESUMED — dispatch is flowing again.")
    return 0


def stop_state():
    """The stop record, or None when not stopped. Like the pause, the
    file's mere existence means stopped."""
    if not os.path.isfile(STOP_FILE):
        return None
    try:
        with open(STOP_FILE, encoding="utf-8") as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else {"ts": None, "reason": ""}
    except (OSError, ValueError):
        return {"ts": None, "reason": ""}


def _clear_marker(path):
    try:
        os.unlink(path)
    except OSError:
        subprocess.run(["/bin/sh", "-c", 'rm -f "$1"', "sh", path],
                       check=True)


def cmd_start(_args):
    """Start: clear any stop and any pause — fully running."""
    cleared = []
    for path, name in ((STOP_FILE, "stop"), (PAUSE_FILE, "pause")):
        if os.path.isfile(path):
            _clear_marker(path)
            cleared.append(name)
    if not cleared:
        print("ALREADY RUNNING (no stop or pause in effect)")
        return 0
    ledger("started", cleared=cleared)
    hlog("STARTED at %s (cleared: %s)" % (now_iso(), ", ".join(cleared)))
    print("RUNNING — cleared %s. Dispatch and watch are live."
          % " + ".join(cleared))
    return 0


def cmd_stop(args):
    """Stop: the hard halt. Sweeps no-op (no budget spent, no watch,
    no dispatch) until `gk start`. Stronger than pause; jobs already
    dispatched are untouched, as always."""
    cur = stop_state()
    if cur is not None:
        print("ALREADY STOPPED since %s reason=%s"
              % (cur.get("ts"), cur.get("reason") or ""))
        return 0
    rec = {"ts": now_iso(), "reason": args.reason or ""}
    try:
        _write_text_file(STOP_FILE,
                         json.dumps(rec, indent=2, sort_keys=True))
    except (OSError, subprocess.SubprocessError) as exc:
        print("ERROR: could not write the stop file (%s). The stop is "
              "NOT in effect." % exc)
        return 1
    ledger("stopped", reason=rec["reason"])
    hlog("STOPPED at %s reason=%s" % (rec["ts"], rec["reason"]))
    print("STOPPED at %s — sweeps will no-op until `gk start`."
          % rec["ts"])
    return 0


def cmd_purge(args):
    """Purge housekeeping: remove terminal (completed/cancelled) job
    records, optionally only those older than N days from creation,
    and optionally archive the ledger to a fresh one. DRY RUN unless
    --yes. Active and submitted records are never touched."""
    jobs = load_jobs()
    targets = []
    for j in jobs:
        if j["state"] not in ("completed", "cancelled"):
            continue
        if args.older_than_days is not None:
            created = parse_iso(j.get("created_at"))
            age_min = minutes_between(created, now_dt())
            if age_min is None or age_min < args.older_than_days * 1440:
                continue
        targets.append(j)
    verb = "purged" if args.yes else "would purge"
    for j in targets:
        print("  %s %s key=%s state=%s"
              % (verb, j["id"], j["key"], j["state"]))
    if args.ledger:
        print("  %s the ledger (archive ledger.jsonl, start fresh)"
              % verb)
    if not targets and not args.ledger:
        print("PURGE: nothing matches (terminal states: completed, "
              "cancelled%s)"
              % ("" if args.older_than_days is None
                 else "; older than %s day(s) from creation"
                      % args.older_than_days))
        return 0
    if not args.yes:
        print("DRY RUN — nothing deleted. Re-run with --yes to purge "
              "%d job record(s)%s."
              % (len(targets), " and archive the ledger" if args.ledger
                 else ""))
        return 0
    removed = 0
    for j in targets:
        _clear_marker(os.path.join(QUEUE_DIR, j["id"] + ".json"))
        removed += 1
    archived = False
    if args.ledger and os.path.isfile(LEDGER):
        stamp = now_iso().replace(":", "").replace("-", "")
        dest = os.path.join(BASE, "ledger-archived-%s.jsonl" % stamp)
        os.replace(LEDGER, dest)
        open(LEDGER, "a", encoding="utf-8").close()
        archived = True
        print("  ledger archived to %s" % os.path.basename(dest))
    ledger("purged", jobs=removed, ledger_archived=archived)
    hlog("PURGED %d job record(s) at %s" % (removed, now_iso()))
    print("PURGED %d job record(s)%s."
          % (removed, "; ledger archived" if archived else ""))
    return 0


def cmd_outbox(args):
    """Push-notification outbox. Reads ledger events past the stored
    cursor and prints, grouped by each job's notify_chat, the pushes
    owed: STARTED (with the job's expected length), COMPLETED
    (conclusion plus verdict highlights from the job's harvested
    markers), ESCALATED (W2 watermark). The cursor starts at the
    current ledger end the first time — history is never replayed.
    --ack advances the cursor once the caller has delivered the
    pushes; delivery itself is the caller's act (the coordination
    chat forwards each group into the named chat)."""
    try:
        with open(LEDGER, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        lines = []
    cursor = None
    if os.path.isfile(OUTBOX_CURSOR):
        try:
            with open(OUTBOX_CURSOR, encoding="utf-8") as f:
                cursor = int(json.load(f)["line"])
        except (OSError, ValueError, KeyError, TypeError):
            cursor = None
    if cursor is None:
        _write_text_file(OUTBOX_CURSOR, json.dumps({"line": len(lines)}))
        print("OUTBOX initialized at line %d (history not replayed)"
              % len(lines))
        return 0
    if args.ack:
        _write_text_file(OUTBOX_CURSOR, json.dumps({"line": len(lines)}))
        print("OUTBOX ACK line=%d" % len(lines))
        return 0
    jobs = {j["id"]: j for j in load_jobs()}
    groups = {}
    for raw in lines[cursor:]:
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        # Lane-lock notices (spec 015 FR-009): lock events
        # carry their notice address on the event itself —
        # there is no single job to look up — and are owed
        # exactly on expiry, on an override release, and on
        # an override suspend/resume. Same-holder actions
        # carry no address, so nothing self-notifies.
        evname = ev.get("event")
        if evname in ("lock_expired", "lock_released_override",
                      "lock_suspended", "lock_resumed"):
            chat = ev.get("notify_chat")
            owed = bool(chat) and (
                evname in ("lock_expired", "lock_released_override")
                or ev.get("override"))
            if owed:
                head = "[%s] lock lane=%s" % (
                    ev.get("program") or "?", ev.get("lane") or "?")
                if evname == "lock_expired":
                    msg = ("%s EXPIRED at %s (was %s) — the lane "
                           "is released"
                           % (head, ev.get("expires_at") or "?",
                              str(ev.get("state") or "active").upper()))
                elif evname == "lock_released_override":
                    msg = ("%s RELEASED BY OPERATOR (actor=%s) — "
                           "your lock was released by someone else"
                           % (head, ev.get("actor") or "?"))
                elif evname == "lock_suspended":
                    msg = ("%s SUSPENDED BY %s — enforcement is "
                           "paused; the claim stands"
                           % (head, ev.get("actor") or "?"))
                else:
                    msg = ("%s RESUMED BY %s — enforcement is "
                           "active again"
                           % (head, ev.get("actor") or "?"))
                groups.setdefault(chat, []).append(msg)
            continue
        job = jobs.get(ev.get("job_id") or "")
        if not job or not job.get("notify_chat"):
            continue
        head = "[%s] %s" % (job["program"], job["key"])
        msg = None
        if (ev.get("event") == "state_change"
                and ev.get("to_status") == "in_progress"):
            exp = job.get("expected_min")
            msg = "%s STARTED run %s%s" % (
                head, ev.get("run_id"),
                (" expected ~%g min" % exp) if exp else "")
        elif (ev.get("event") == "state_change"
                and ev.get("to_status") == "completed"):
            if job.get("state") == "held_at_checkpoint":
                # Spec 013 FR-008: a checkpoint hold is never
                # reported as a completion (or a failure).
                msg = ("%s HELD_AT_CHECKPOINT run %s conclusion=%s "
                       "— the job stopped itself cooperatively; "
                       "resubmit to continue"
                       % (head, ev.get("run_id"),
                          ev.get("conclusion") or "?"))
            else:
                marks = job.get("markers") or []
                hl = [m for m in marks
                      if ("RESULT" in m or "VERDICT" in m)][:3]
                msg = "%s COMPLETED run %s conclusion=%s markers=%d%s" % (
                    head, ev.get("run_id"), ev.get("conclusion") or "?",
                    len(marks),
                    (" | " + " ;; ".join(hl)) if hl
                    else (" | verdict pending harvest" if not marks
                          else ""))
        elif (ev.get("event") == "watermark_escalation"
                and ev.get("level") == "W2"):
            msg = "%s ESCALATED W2 %s run %s age=%s min" % (
                head, ev.get("classification"), ev.get("run_id"),
                ev.get("age_min"))
        elif ev.get("event") == "interrupted":
            # Spec 011 FR-004: the job's host went down mid-flight.
            msg = ("%s INTERRUPTED run %s — host %s going down at %s "
                   "(trigger=%s)" % (
                       head, ev.get("run_id"), ev.get("host"),
                       ev.get("ts"), ev.get("trigger")))
        if msg:
            groups.setdefault(job["notify_chat"], []).append(msg)
    if not groups:
        print("OUTBOX empty")
        return 0
    for chat in sorted(groups):
        print("NOTIFY %s" % chat)
        for msg in groups[chat]:
            print("  %s" % msg)
    return 0


def _record_bytes_valid(data):
    """True when data is a non-empty JSON record. Used by save_job's
    failure cleanup to tell a valid older record (leave standing)
    from a 0-byte/partial husk (remove)."""
    if not data:
        return False
    try:
        json.loads(data.decode("utf-8"))
        return True
    except (ValueError, UnicodeDecodeError):
        return False


def save_job(job):
    """Persist a job record under WRITE-VERIFY-RETRY + ATOMIC SWAP
    (spec 001 FR-012 + FR-013): the payload is written to a unique
    temp file and read back byte-for-byte; ONLY an atomic rename
    (os.replace) ever changes the record at its final path, and the
    renamed content is verified there. The final path is NEVER opened
    for writing: the pre-FR-013 fallback wrote it directly with
    O_TRUNC, so a wave-killed direct write destroyed the standing
    valid record before verification could fail (the losses of
    2026-10-28 01:53-02:23Z and 12:08Z). If no attempt lands, raise
    IOError with the prior record (if any) standing untouched - for
    an update, the old state remains the truth on disk and the next
    pass retries; for a new record, nothing stands at the final path
    at all, so no husk can exist."""
    payload = json.dumps(job, indent=2, sort_keys=True).encode("utf-8")
    final = job_path(job["id"])
    tmps = []
    try:
        for i in range(8):
            tmp = os.path.join(
                QUEUE_DIR, ".%s.%d.%d.%s.tmp"
                % (job["id"], os.getpid(), i, uuid.uuid4().hex[:6]))
            tmps.append(tmp)
            if _write_verified_bytes(tmp, payload):
                try:
                    os.replace(tmp, final)
                except OSError:
                    pass
                else:
                    try:
                        with open(final, "rb") as f:
                            landed = f.read()
                    except OSError:
                        landed = None
                    if landed == payload:
                        _fsync_dir(QUEUE_DIR)
                        return
            if i + 1 < 8:
                time.sleep(min(0.25 * (2 ** i), 8.0))
        raise IOError("save_job could not verify a write for job %s"
                      % job["id"])
    finally:
        for t in tmps:
            try:
                os.unlink(t)
            except OSError:
                pass


def _fsync_dir(path):
    """Best-effort fsync of a directory so a rename inside it is
    durable. Never raises."""
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def save_job_safe(job, context):
    """save_job for sweep-internal state updates (spec 001 FR-013):
    a single record's failed save must not kill the whole pass. On
    failure the event is ledgered and logged loudly and False is
    returned; the state on disk remains the older truth and the next
    sweep re-derives GitHub-side state and retries the update. The
    caller decides what a False means for its own step (the dispatch
    path treats a failed pre-dispatch save as skip-this-job: never
    dispatch what could not be recorded first)."""
    try:
        save_job(job)
        return True
    except (IOError, OSError) as exc:
        try:
            ledger("save_failed", job_id=job["id"], key=job.get("key"),
                   context=context, error=str(exc)[:200])
        except Exception:
            pass
        hlog("SAVE_FAILED job=%s context=%s error=%s"
             % (job["id"], context, exc))
        print("ERROR: save failed for job %s (%s); the sweep "
              "continues and the update retries next pass"
              % (job["id"], context), file=sys.stderr)
        return False


def load_jobs():
    jobs = []
    if not os.path.isdir(QUEUE_DIR):
        return jobs
    for name in sorted(os.listdir(QUEUE_DIR)):
        if name.endswith(".json"):
            try:
                with open(os.path.join(QUEUE_DIR, name),
                          encoding="utf-8") as f:
                    jobs.append(json.load(f))
            except (OSError, ValueError) as e:
                # A 0-byte or partial record (the silent-drop write
                # mode) must not crash every command or pose as a
                # job: skip it loudly (spec 001 FR-012).
                print("WARNING: skipping unreadable job file %s: %s"
                      % (name, e), file=sys.stderr)
    return jobs


def new_job_id(key):
    slug = re.sub(r"[^a-z0-9-]+", "-", key.lower()).strip("-")[:48] or "job"
    return "%s-%s-%s" % (now_dt().strftime("%Y%m%dT%H%M%SZ"), slug,
                         uuid.uuid4().hex[:6])


def run_cmd(args, timeout=120):
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as e:
        return 127, "", str(e)


def gh_json(args, timeout=120):
    rc, out, err = run_cmd(["gh"] + args, timeout=timeout)
    if rc != 0:
        return None, "gh %s failed rc=%d: %s" % (" ".join(args), rc,
                                                 err.strip()[:300])
    try:
        return json.loads(out), None
    except ValueError:
        return None, "gh %s returned non-JSON" % " ".join(args)


def rate_limit_remaining():
    """Free endpoint. Returns (remaining, error)."""
    data, err = gh_json(["api", "rate_limit"])
    if err:
        return None, err
    try:
        return int(data["resources"]["core"]["remaining"]), None
    except (KeyError, TypeError, ValueError):
        return None, "rate_limit response missing resources.core.remaining"


def list_runs(limit=50):
    """ONE batched list call covering all workflows."""
    return gh_json(["run", "list", "-R", REPO, "-L", str(limit), "--json",
                    "databaseId,status,conclusion,createdAt,updatedAt,"
                    "workflowName,url"])


def workflow_name(workflow_file):
    return workflow_file[:-4] if workflow_file.endswith(".yml") else workflow_file


# --- submit / adopt / cancel ----------------------------------------------

def find_active(jobs, key=None, run_id=None):
    for j in jobs:
        if j["state"] not in ACTIVE_STATES:
            continue
        if key is not None and j.get("key") == key:
            return j
        if run_id is not None and str(j.get("run_id")) == str(run_id):
            return j
    return None


def route_refusal(lane, workflow):
    """None if the (lane, workflow) pair routes; else a refusal line.
    Script jobs need an execute workflow on the lane's host; grab
    jobs need its grab workflow (FR-004: no silent venue fallback)."""
    want = "grab" if workflow == "grab.yml" else "execute"
    r = route_check(lane, workflow)
    if r is None or r[1] != want:
        host = LANE_HOST.get(lane, "?")
        return ("REFUSED routing: lane '%s' (host '%s', type %s) does not "
                "serve workflow '%s' as %s"
                % (lane, host, HOST_TYPE.get(host, "?"), workflow, want))
    return None


def cmd_submit(args):
    jobs = load_jobs()
    dup = find_active(jobs, key=args.key)
    if dup:
        print("REFUSED duplicate key '%s': active job %s (state=%s, run_id=%s)"
              % (args.key, dup["id"], dup["state"], dup.get("run_id")))
        return 3
    # Spec 016 (FR-003/FR-004): --lane auto (or a lane-less
    # submit under lanes.auto_default) records the job
    # lane-less; the sweep resolves its lane at dispatch time.
    # An explicit lane is never touched by auto routing.
    lane_arg = getattr(args, "lane", None)
    auto = lane_arg == "auto"
    if lane_arg is None:
        if LANES_AUTO_DEFAULT:
            auto = True
        else:
            print("ERROR: --lane is required (pass a lane name, or "
                  "--lane auto; or set lanes.auto_default: true to "
                  "make lane-less submits mean auto)")
            return 2
    needs = {}
    for attr, field in (("needs_cpu", "cpu_cores"),
                        ("needs_ram_mb", "ram_mb"),
                        ("needs_storage_mb", "storage_mb")):
        val = getattr(args, attr, None)
        if val is not None:
            if int(val) < 0:
                print("ERROR: --%s must be non-negative"
                      % attr.replace("_", "-"))
                return 2
            needs[field] = int(val)
    gpu_hint = getattr(args, "needs_gpu", None)
    if gpu_hint is not None:
        needs["gpu"] = True
        if gpu_hint is not True:
            if int(gpu_hint) < 0:
                print("ERROR: --needs-gpu VRAM must be non-negative")
                return 2
            needs["gpu_vram_mb"] = int(gpu_hint)
    venue = getattr(args, "venue", None) or "auto"
    if venue not in ("auto", "native", "wsl", "docker"):
        print("ERROR: --venue must be auto|native|wsl|docker")
        return 2
    if not auto:
        refusal = route_refusal(lane_arg, args.workflow)
        if refusal:
            print(refusal)
            return 3
    job = {
        "id": new_job_id(args.key),
        "key": args.key,
        "program": args.program,
        "workflow": args.workflow,
        "lane": None if auto else lane_arg,
        "cwd": args.cwd if args.cwd is not None else (
            None if auto else default_cwd(lane_arg, args.workflow)),
        "expected_min": args.expected_min,
        "state": "submitted",
        "created_at": now_iso(),
        "run_id": None,
        "run_status": None,
        "run_conclusion": None,
        "adopted": False,
        "verdict_source": None,
        "watermarks": {},
        "flags": [],
        "maintenance": bool(getattr(args, "maintenance", False)),
        "notify_chat": getattr(args, "notify_chat", None),
        # Spec 013 FR-003: an explicit --priority pins the job
        # as human-set; anything else is optimizer territory.
        "priority": (int(args.priority)
                     if getattr(args, "priority", None) is not None
                     else 0),
        "priority_source": ("human"
                            if getattr(args, "priority", None)
                            is not None else "auto"),
        # Spec 016 (FR-003): declared resource hints and the
        # venue preference ride on every record; on an auto
        # job they are the scorer's declared inputs, on an
        # explicit-lane job they are informational only.
        "venue": venue,
    }
    if auto:
        job["lane_request"] = "auto"
        if args.cwd is None:
            # No lane yet, so no per-host cwd can be resolved;
            # the auto-routing pass resolves it against the
            # lane it assigns (see cwd_auto in the sweep).
            job["cwd_auto"] = True
    if needs:
        job["needs"] = needs
    if args.workflow == "grab.yml":
        if not args.grab_path:
            print("ERROR: grab.yml jobs require --grab-path "
                  "(the desktop file path to return)")
            return 2
        job["grab_path"] = args.grab_path
        job["script_path"] = None
        job["script_sha256"] = None
    else:
        if not args.script or not os.path.isfile(args.script):
            print("ERROR: --script PATH must name an existing file for %s"
                  % args.workflow)
            return 2
        with open(args.script, "rb") as f:
            data = f.read()
        job["script_path"] = os.path.abspath(args.script)
        job["script_sha256"] = hashlib.sha256(data).hexdigest()
        image = getattr(args, "image", None) \
            or (None if auto else HOST_IMAGE.get(LANE_HOST.get(lane_arg)))
        if image:
            job["image"] = image
        if getattr(args, "mounts", None):
            job["mounts"] = args.mounts
    save_job(job)
    ledger("submitted", job_id=job["id"], key=job["key"],
           program=job["program"], workflow=job["workflow"],
           lane=job["lane"], script_sha256=job["script_sha256"])
    # FR-012: a submission is only reported queued after the loader
    # reads back the exact record that was written.
    landed = find_active(load_jobs(), key=job["key"])
    if (landed is None or landed["id"] != job["id"]
            or landed.get("script_sha256") != job["script_sha256"]):
        print("ERROR: submission %s did not verify on read-back; "
              "it is NOT queued" % job["id"])
        return 4
    print("SUBMITTED %s key=%s program=%s workflow=%s lane=%s"
          % (job["id"], job["key"], job["program"], job["workflow"],
             "auto" if auto else job["lane"]))
    return 0


def cmd_adopt(args):
    jobs = load_jobs()
    dup = find_active(jobs, key=args.key)
    if dup:
        print("REFUSED duplicate key '%s': active job %s" % (args.key,
                                                             dup["id"]))
        return 3
    dup = find_active(jobs, run_id=args.run_id)
    if dup:
        print("REFUSED: run %s already tracked by job %s" % (args.run_id,
                                                             dup["id"]))
        return 3
    refusal = route_refusal(args.lane, args.workflow)
    if refusal:
        print(refusal)
        return 3
    job = {
        "id": new_job_id(args.key),
        "key": args.key,
        "program": args.program,
        "workflow": args.workflow,
        "lane": args.lane,
        "cwd": None,
        "expected_min": args.expected_min,
        "state": "watching",
        "created_at": now_iso(),
        "adopted_at": now_iso(),
        "run_id": int(args.run_id),
        "run_status": None,
        "run_conclusion": None,
        "run_created_at": None,
        "adopted": True,
        "verdict_source": None,
        "watermarks": {},
        "flags": [],
        "notify_chat": getattr(args, "notify_chat", None),
        "priority": 0,
        "priority_source": "auto",
    }
    save_job(job)
    ledger("adopted", job_id=job["id"], key=job["key"],
           program=job["program"], workflow=job["workflow"],
           lane=job["lane"], run_id=job["run_id"])
    print("ADOPTED %s run_id=%s key=%s program=%s lane=%s"
          % (job["id"], job["run_id"], job["key"], job["program"],
             job["lane"]))
    return 0


def cmd_cancel(args):
    """Local only: retire a job that has NOT been dispatched. Never touches
    any GitHub run (Part 4: watchers do not cancel runs)."""
    jobs = load_jobs()
    target = None
    for j in jobs:
        if j["id"] == args.job or j.get("key") == args.job:
            target = j
            break
    if target is None:
        print("ERROR: no job with id/key '%s'" % args.job)
        return 2
    if target["state"] != "submitted":
        print("REFUSED: job %s is state=%s (already dispatched or terminal). "
              "gk cancel is local-only and never cancels a GitHub run."
              % (target["id"], target["state"]))
        return 3
    target["state"] = "cancelled"
    target["cancelled_at"] = now_iso()
    save_job(target)
    ledger("cancelled_local", job_id=target["id"], key=target["key"])
    print("CANCELLED (local only, nothing was dispatched) %s" % target["id"])
    return 0


def cmd_priority(args):
    """Live priority change (spec 013 FR-004): a submitted job
    only — ordering cannot apply to work already dispatched or
    done. Stamps the job human-set (FR-012's pin) and goes
    through the FR-013 verified write like every mutation."""
    job = _find_job(load_jobs(), args.job)
    if job is None:
        print("ERROR: no job with id/key '%s'" % args.job)
        return 2
    if job["state"] != "submitted":
        print("REFUSED: job %s is state=%s — priority orders the "
              "waiting set only, and this job has already left it. "
              "Nothing was changed."
              % (job["id"], job["state"]))
        return 3
    old = _prio(job)
    job["priority"] = int(args.value)
    job["priority_source"] = "human"
    save_job(job)
    ledger("priority_changed", job_id=job["id"], key=job.get("key"),
           old=old, new=int(args.value), source="human")
    print("PRIORITY %s key=%s %s -> %s (human-set; effective at "
          "the next sweep)" % (job["id"], job.get("key"), old,
                               int(args.value)))
    return 0


# --- checkpoint holds (spec 013 FR-007/FR-008/FR-009) ------------------------

def hold_signal_path(job):
    """The venue path of this job's hold-signal file, or None
    when its workflow runs on no signal-capable venue
    (grab.yml). The path is passed to the job at dispatch as
    the `hold_file` workflow input and reaches the job's
    environment as $SYNCYARD_HOLD_FILE (FR-007).

    A holds_dir entry is either one venue directory valid on
    every host, or a per-host mapping resolved through the
    job's lane host (LANE_HOST — the same derivation routing
    uses). A per-host mapping that names no directory for the
    job's host yields None: the job has no hold-signal venue
    on that host, never another host's directory (a laptop
    job handed the desktop's profile-tree path dies in the
    door wrapper before its payload runs)."""
    entry = CHECKPOINT_HOLDS_DIR.get(job.get("workflow"))
    if not entry:
        return None
    if isinstance(entry, dict):
        host = LANE_HOST.get(job.get("lane"))
        directory = entry.get(host) if host else None
        if not directory:
            return None
    else:
        directory = entry
    sep = "\\" if job.get("workflow") == "run.yml" else "/"
    return directory + sep + job["id"] + ".hold"


def checkpoint_marker_seen(job):
    """True when the job's harvested markers carry the
    checkpoint marker (FR-008): the marker alone, or followed
    by a space, colon or equals — the boundary rule the
    marker regex itself uses."""
    for ln in job.get("markers") or []:
        s = ln.strip()
        if s == CHECKPOINT_MARKER:
            return True
        if s.startswith(CHECKPOINT_MARKER) and s[
                len(CHECKPOINT_MARKER):
                len(CHECKPOINT_MARKER) + 1] in (" ", ":", "="):
            return True
    return False


def _signal_script_body(workflow, target_path, hold):
    """The signal writer's script: create (hold=True) or remove
    (hold=False) exactly the one venue file, printing a marker
    either way. One short script per venue dialect."""
    directory = target_path.rsplit("\\", 1)[0] \
        if "\\" in target_path else target_path.rsplit("/", 1)[0]
    if workflow == "run.yml":
        if hold:
            return "\n".join([
                "$ErrorActionPreference = 'Stop'",
                "New-Item -ItemType Directory -Force -Path '%s' "
                "| Out-Null" % directory,
                "Set-Content -NoNewline -Path '%s' -Value 'held'"
                % target_path,
                "'HOLD_SIGNAL_SET=%s'" % target_path,
            ]) + "\n"
        return "\n".join([
            "Remove-Item -Force -ErrorAction SilentlyContinue "
            "-Path '%s'" % target_path,
            "'HOLD_SIGNAL_CLEARED=%s'" % target_path,
        ]) + "\n"
    if hold:
        return "\n".join([
            "mkdir -p '%s'" % directory,
            "printf held > '%s'" % target_path,
            "echo \"HOLD_SIGNAL_SET=%s\"" % target_path,
        ]) + "\n"
    return "\n".join([
        "rm -f '%s'" % target_path,
        "echo \"HOLD_SIGNAL_CLEARED=%s\"" % target_path,
    ]) + "\n"


def _submit_signal_job(target, hold):
    """Deliver (or withdraw) a hold signal via an internal
    maintenance job on the target's own workflow (FR-009):
    maintenance, so the delivery flows under any pause, focus
    or host hold, exactly like the gatekeeper's reboot signal.
    Returns (signal_job_id, None) or (None, error)."""
    workflow = target.get("workflow")
    target_path = hold_signal_path(target)
    if not target_path:
        return None, ("workflow %s has no hold-signal venue"
                      % workflow)
    verb = "hold" if hold else "unhold"
    key = "gk-%s-%s" % (verb, target["id"])
    lane = target.get("lane") or "quick"
    refusal = route_refusal(lane, workflow)
    if refusal:
        return None, refusal
    jobs = load_jobs()
    if find_active(jobs, key=key):
        return None, ("a %s signal for %s is already in flight"
                      % (verb, target["id"]))
    os.makedirs(HOLDS_DIR, exist_ok=True)
    script_path = os.path.join(HOLDS_DIR, key + ".script")
    with open(script_path, "wb") as f:
        f.write(_signal_script_body(workflow, target_path,
                                    hold).encode("utf-8"))
    with open(script_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    job = {
        "id": new_job_id(key),
        "key": key,
        "program": "syncyard",
        "workflow": workflow,
        "lane": lane,
        "cwd": default_cwd(lane, workflow),
        "expected_min": 2,
        "state": "submitted",
        "created_at": now_iso(),
        "run_id": None,
        "run_status": None,
        "run_conclusion": None,
        "adopted": False,
        "verdict_source": None,
        "watermarks": {},
        "flags": [],
        "maintenance": True,
        "notify_chat": target.get("notify_chat"),
        "priority": 0,
        "priority_source": "auto",
        "script_path": script_path,
        "script_sha256": digest,
    }
    save_job(job)
    ledger("submitted", job_id=job["id"], key=job["key"],
           program=job["program"], workflow=job["workflow"],
           lane=job["lane"], script_sha256=job["script_sha256"])
    return job["id"], None


def _request_checkpoint_hold(job):
    """Record a checkpoint-hold request on a job and queue its
    venue signal (spec 013 FR-008). Returns (signal_job_id, err):
    err is None when the signal is queued, and both are None
    when the hold was already requested. Shared by `gk hold`
    and `gk drain` (spec 011 FR-007) so an operator hold and a
    drain raise the identical signal, and the harvest never
    asks which command raised it."""
    if job.get("hold_requested"):
        return None, None
    job["hold_requested"] = True
    job["hold_requested_at"] = now_iso()
    save_job(job)
    ledger("hold_requested", job_id=job["id"], key=job.get("key"),
           signal_path=hold_signal_path(job))
    return _submit_signal_job(job, hold=True)


def cmd_hold(args):
    """Request a cooperative checkpoint hold (spec 013 FR-009):
    record the request on the job, then deliver the signal
    file to its venue. Only a job that has not yet dispatched
    can act on the signal (it polls from its first step); the
    request is refused for grab jobs (no signal venue) and for
    terminal jobs."""
    job = _find_job(load_jobs(), args.job)
    if job is None:
        print("ERROR: no job with id/key '%s'" % args.job)
        return 2
    if job.get("workflow") == "grab.yml" \
            or job.get("workflow") not in CHECKPOINT_HOLDS_DIR:
        print("REFUSED: job %s runs %s, which has no hold-signal "
              "venue — grab jobs cannot be checkpoint-held."
              % (job["id"], job.get("workflow")))
        return 3
    if job["state"] in ("completed", "cancelled",
                        "held_at_checkpoint"):
        print("REFUSED: job %s is state=%s — there is no "
              "running or waiting work left to hold."
              % (job["id"], job["state"]))
        return 3
    if job.get("hold_requested"):
        print("hold already requested for %s (key=%s) at %s"
              % (job["id"], job.get("key"),
                 job.get("hold_requested_at")))
        return 0
    sid, err = _request_checkpoint_hold(job)
    if err:
        print("hold recorded on %s (key=%s), but the signal "
              "could not be queued: %s. The job will not see a "
              "signal until delivery succeeds — re-run 'gk hold' "
              "after fixing the cause."
              % (job["id"], job.get("key"), err))
        return 4
    print("HOLD REQUESTED for %s (key=%s): the signal file "
          "reaches the venue via %s; a job polling "
          "$SYNCYARD_HOLD_FILE stops at its next checkpoint. "
          "Note: a job already running cannot see a signal "
          "created after it started polling — see spec 013."
          % (job["id"], job.get("key"), sid))
    return 0


# --- sweep: dispatch --------------------------------------------------------

def recorded_run_ids(jobs):
    return {str(j["run_id"]) for j in jobs if j.get("run_id")}


def capture_run_id(workflow, before_iso, taken):
    """Find the run this dispatch created: same workflow, createdAt >= the
    moment just before dispatch, earliest first, skipping ids already
    recorded against other jobs. Never latches an id that does not resolve
    (the list call IS the resolution). Returns run id or None."""
    data, err = gh_json(["run", "list", "-R", REPO, "--workflow", workflow,
                         "-L", "10", "--json", "databaseId,createdAt,status"])
    if err or not data:
        return None
    cands = [r for r in data
             if r.get("createdAt", "") >= before_iso
             and str(r["databaseId"]) not in taken]
    if not cands:
        return None
    cands.sort(key=lambda r: r["createdAt"])
    return int(cands[0]["databaseId"])


def dispatch_one(job, jobs):
    """Returns True if the job now holds a run. Caller has already applied
    the lane/cap gates. Sets state=dispatching BEFORE the gh call so a
    killed sweep cannot double-dispatch."""
    job["state"] = "dispatching"
    job["dispatching_at"] = now_iso()
    if not save_job_safe(job, "pre_dispatch"):
        # Never dispatch what could not be recorded first (FR-013):
        # the record on disk still reads submitted, so the job is
        # simply skipped this pass and retried by the next sweep.
        job["state"] = "submitted"
        job.pop("dispatching_at", None)
        return False
    ledger("dispatching", job_id=job["id"], key=job["key"],
           workflow=job["workflow"], lane=job["lane"])
    before = now_iso()
    lane_label = LANE_LABEL[job["lane"]]
    if job["workflow"] == "grab.yml":
        gh_args = ["workflow", "run", "grab.yml", "-R", REPO,
                   "-f", "path=" + job["grab_path"],
                   "-f", "lane=" + lane_label]
    else:
        try:
            with open(job["script_path"], "rb") as f:
                payload = base64.b64encode(f.read()).decode("ascii")
        except OSError as e:
            # A missing or unreadable script file must fail THIS job
            # only, never the sweep. The classic cause is a script
            # staged in /tmp and wiped by a VM replacement between
            # submit and dispatch (field case 2026-11-04:
            # spec053-f0-build-repair-2026-11-04, whose FileNotFoundError
            # killed the sweep and left the record wedged in
            # dispatching). Return the job to submitted with the
            # error on the record: a restaged script revives it on a
            # later sweep with no operator surgery, and the pass
            # continues to the next waiting job in the same call.
            job["state"] = "submitted"
            job["last_dispatch_error"] = (
                "script unreadable: %s" % e)[:300]
            job.pop("dispatching_at", None)
            save_job_safe(job, "dispatch_failed_retryable")
            ledger("dispatch_failed_retryable", job_id=job["id"],
                   key=job["key"], error=job["last_dispatch_error"])
            return False
        gh_args = ["workflow", "run", job["workflow"], "-R", REPO,
                   "-f", "cmd_b64=" + payload,
                   "-f", "lane=" + lane_label]
        if job.get("cwd"):
            gh_args += ["-f", "cwd=" + job["cwd"]]
        if job.get("image"):
            gh_args += ["-f", "image=" + job["image"]]
        if job.get("mounts"):
            gh_args += ["-f", "mounts=" + job["mounts"]]
        hold_path = hold_signal_path(job)
        if hold_path:
            gh_args += ["-f", "hold_file=" + hold_path]
    rc, out, err = run_cmd(["gh"] + gh_args, timeout=60)
    run_id = None
    # Newer gh prints the created run URL on stdout - most robust capture.
    m = re.search(r"/actions/runs/(\d+)", out or "")
    if m:
        rid = m.group(1)
        if rid not in recorded_run_ids(jobs):
            run_id = int(rid)
    if run_id is None:
        run_id = capture_run_id(job["workflow"], before,
                                recorded_run_ids(jobs))
    if run_id is not None:
        job["state"] = "dispatched"
        job["run_id"] = run_id
        job["dispatched_at"] = now_iso()
        job.pop("dispatching_at", None)
        save_job_safe(job, "dispatched")
        ledger("dispatched", job_id=job["id"], key=job["key"],
               run_id=run_id, gh_rc=rc)
        return True
    if rc != 0:
        # gh itself failed and no run materialized: safe to retry next sweep.
        job["state"] = "submitted"
        job["last_dispatch_error"] = (err or "").strip()[:300]
        job.pop("dispatching_at", None)
        save_job_safe(job, "dispatch_failed_retryable")
        ledger("dispatch_failed_retryable", job_id=job["id"],
               key=job["key"], gh_rc=rc,
               error=job["last_dispatch_error"])
    else:
        # gh claims success but we cannot identify the run: do NOT guess
        # and do NOT auto-redispatch (that is the double-dispatch trap).
        job["state"] = "dispatching"
        if "dispatch_unconfirmed" not in job["flags"]:
            job["flags"].append("dispatch_unconfirmed")
        save_job_safe(job, "dispatch_unconfirmed")
        ledger("dispatch_unconfirmed", job_id=job["id"], key=job["key"])
    return False


def _signal_hold_exempt(job):
    """FR-015 route 2 exception: the gatekeeper's own signal
    jobs (maintenance jobs keyed gk-reboot-*, gk-hold-* or
    gk-unhold-*) are exempt from a host dispatch hold. The
    hold exists to stop NEW work starting on a host that is
    going down; a signal job is not new work, it IS the
    going-down / drain mechanism — if the hold blocked it,
    the maintenance route could never deliver its own
    signal, and a drain could never deliver the checkpoint
    holds it exists to raise. One predicate, shared by the
    dispatch pass and the fleet snapshot's held marks."""
    return bool(job.get("maintenance")) and str(
        job.get("key") or "").startswith(
            (REBOOT_KEY_PREFIX, "gk-hold-", "gk-unhold-"))


# --- Adaptive lanes: capability, history, auto routing (spec 016) -----------

def _run_duration_min(job):
    """A completed job's actual run duration in minutes:
    first-seen-in-progress to completion, falling back to
    dispatch-to-completion. None when the record cannot say."""
    end = parse_iso(job.get("completed_at"))
    start = parse_iso(job.get("first_seen_in_progress_at")
                      or job.get("dispatched_at"))
    if start is None or end is None:
        return None
    mins = minutes_between(start, end)
    return mins if mins is not None and mins >= 0 else None


def program_history(jobs, program):
    """The program's recent actuals (FR-006): its ten most
    recent completed jobs — median run duration, the dominant
    resource class of the lanes they ran on, and the last lane.
    Computed from the queue records themselves; nothing is
    cached, so the estimate can never drift from the record."""
    done = [j for j in jobs
            if j.get("program") == program and j.get("lane")
            and j.get("completed_at")]
    done.sort(key=lambda j: j.get("completed_at") or "", reverse=True)
    recent = done[:10]
    durs = [d for d in (_run_duration_min(j) for j in recent)
            if d is not None]
    classes = {}
    for j in recent:
        cls = LANE_CLASS.get(j["lane"], "any")
        classes[cls] = classes.get(cls, 0) + 1
    dominant = None
    if classes:
        dominant = max(sorted(classes), key=lambda c: classes[c])
    return {
        "samples": len(recent),
        "median_min": (round(statistics.median(durs), 1) if durs
                       else None),
        "dominant_class": dominant,
        "last_lane": recent[0]["lane"] if recent else None,
        "durations_min": [round(d, 1) for d in durs],
    }


def capability_view(host, hosts_state=None, now=None):
    """(snapshot, stale) for a host (FR-005). The snapshot is
    the record stored by `gk probe-host`; a missing snapshot
    reads as (None, True). Staleness is judged against
    lanes.capability_max_age_min; an unparseable probed_at is
    stale — unknown age is never treated as fresh."""
    if hosts_state is None:
        hosts_state = load_hosts_state()
    rec = (hosts_state.get(host) or {}).get("capability")
    if not isinstance(rec, dict):
        return None, True
    age = minutes_between(parse_iso(rec.get("probed_at")), now or now_dt())
    return rec, (age is None or age > CAPABILITY_MAX_AGE_MIN)


def _venue_match(job, lane_name):
    """Does the lane's venue match the job's venue preference?
    A scoring term only — eligibility is resolve_auto_lane's."""
    venue = job.get("venue") or "auto"
    if venue == "auto":
        return False
    host = LANE_HOST.get(lane_name)
    htype = HOST_TYPE.get(host)
    if venue == "docker":
        return htype == "docker"
    if venue == "wsl":
        return htype == "wsl2" or job.get("workflow") == "wsl.yml"
    if venue == "native":
        return htype not in ("wsl2", "docker")
    return False


def resolve_auto_lane(job, ctx):
    """The auto scorer (FR-006): a deterministic pure function
    of its recorded inputs. ctx carries the lane table
    (LANES order), per-lane load, the locks map, the held-host
    set, per-host capability views, the program history, and
    the evaluation instant. Returns (lane_name | None,
    snapshot) — the snapshot is the audit record: every input
    and every per-lane verdict, eligible or not."""
    needs = job.get("needs") or {}
    venue = job.get("venue") or "auto"
    history = ctx["history"]
    expected = job.get("expected_min")
    if expected is None:
        expected = history.get("median_min")
    want_kind = "grab" if job.get("workflow") == "grab.yml" else "execute"
    lanes_eval = {}
    cap_facts = {}
    degraded = False
    best = None
    for name in sorted(ctx["lanes"]):
        lane = ctx["lanes"][name]
        host = lane["host"]
        cap, stale = ctx["capabilities"].get(host, (None, True))
        usable = cap if (cap is not None and not stale) else None
        if usable is None:
            degraded = True
        elif host not in cap_facts:
            cap_facts[host] = {
                "probed_at": cap.get("probed_at"), "stale": stale,
                "cpu_cores": cap.get("cpu_cores"),
                "ram_total_mb": cap.get("ram_total_mb"),
                "ram_free_mb": cap.get("ram_free_mb"),
                "storage_free_gb": cap.get("storage_free_gb"),
                "gpus": cap.get("gpus"), "venues": cap.get("venues"),
            }
        load = ctx["load"].get(name, {"executing": 0, "waiting": 0})
        reasons = []
        route = route_check(name, job.get("workflow"))
        if route is None or route[1] != want_kind:
            reasons.append("lane does not serve workflow %s as %s"
                           % (job.get("workflow"), want_kind))
        if host in ctx["held_hosts"]:
            reasons.append("host %s held (going down)" % host)
        lock_rec = ctx["locks"].get(name)
        if (lock_rec and lock_rec.get("state") == "active"
                and lock_rec.get("program") != job.get("program")
                and not job.get("maintenance")):
            reasons.append("lane locked by %s (spec 015)"
                           % lock_rec.get("program"))
        # Hard resource hints (FR-006): a hard hint is never
        # guessed at — a GPU need requires an affirmative lane
        # class or affirmative fresh probe evidence.
        if needs.get("gpu"):
            cls = lane.get("resource_class", "any")
            if cls == "cpu":
                reasons.append("gpu need unmet: lane class is cpu")
            elif cls == "any" and not (usable and usable.get("gpus")):
                reasons.append(
                    "gpu need unmet: no GPU evidenced for this lane's "
                    "host (class 'any' needs a fresh probe listing one)")
            vram = needs.get("gpu_vram_mb")
            if vram is not None:
                biggest = max([g.get("vram_mb") or 0
                               for g in (usable or {}).get("gpus") or []],
                              default=0)
                if not usable or biggest < vram:
                    reasons.append(
                        "gpu vram need %d MB unmet (largest evidenced "
                        "GPU: %s)" % (vram, biggest if usable else "none"))
        if needs.get("ram_mb") is not None and usable is not None \
                and usable.get("ram_total_mb") is not None \
                and usable["ram_total_mb"] < needs["ram_mb"]:
            reasons.append("ram need %d MB exceeds host total %d MB"
                           % (needs["ram_mb"], usable["ram_total_mb"]))
        if needs.get("cpu_cores") is not None and usable is not None \
                and usable.get("cpu_cores") is not None \
                and usable["cpu_cores"] < needs["cpu_cores"]:
            reasons.append("cpu need %d cores exceeds host cores %d"
                           % (needs["cpu_cores"], usable["cpu_cores"]))
        if needs.get("storage_mb") is not None and usable is not None \
                and usable.get("storage_free_gb") is not None \
                and usable["storage_free_gb"] * 1024 < needs["storage_mb"]:
            reasons.append("storage need %d MB exceeds host free %d MB"
                           % (needs["storage_mb"],
                              int(usable["storage_free_gb"] * 1024)))
        # Venue eligibility (FR-006): eligibility plus the
        # _venue_match scoring bonus — never a translation.
        if venue == "docker":
            if not job.get("image"):
                reasons.append("docker venue requires --image")
            elif usable is not None \
                    and "docker" not in (usable.get("venues") or []):
                reasons.append("docker venue not probed on host %s" % host)
        elif venue == "wsl":
            serves_wsl = "wsl.yml" in HOST_WORKFLOWS.get(host, {})
            if HOST_TYPE.get(host) != "wsl2" and not serves_wsl:
                reasons.append("wsl venue: host %s serves no wsl.yml"
                               % host)
            elif usable is not None and HOST_TYPE.get(host) == "windows" \
                    and "wsl" not in (usable.get("venues") or []):
                reasons.append("wsl venue not probed on host %s" % host)
        elif venue == "native":
            if HOST_TYPE.get(host) in ("wsl2", "docker"):
                reasons.append("native venue excludes %s hosts"
                               % HOST_TYPE.get(host))
        if reasons:
            lanes_eval[name] = {"host": host, "eligible": False,
                                "reasons": reasons, "score": None,
                                "load": load}
            continue
        score = 100
        score -= 15 * load["executing"] + 5 * load["waiting"]
        if expected is not None:
            lo, hi = lane.get("duration_band") or (None, None)
            if lo is not None and expected < lo:
                score -= 40
            elif hi is not None and expected > hi:
                score -= 10
            else:
                score += 20
        if history.get("dominant_class") is not None \
                and history["dominant_class"] == lane.get(
                    "resource_class", "any"):
            score += 10
        if history.get("last_lane") == name:
            score += 5
        if _venue_match(job, name):
            score += 10
        if needs.get("ram_mb") is not None and usable is not None \
                and usable.get("ram_free_mb") is not None \
                and usable["ram_free_mb"] >= 2 * needs["ram_mb"]:
            score += 5
        lanes_eval[name] = {"host": host, "eligible": True,
                            "reasons": [], "score": score, "load": load}
        if best is None or score > lanes_eval[best]["score"]:
            best = name
    snapshot = {
        "evaluated_at": ctx["now_iso"],
        "program": job.get("program"),
        "workflow": job.get("workflow"),
        "needs": needs,
        "venue": venue,
        "expected_min_used": expected,
        "expected_source": ("declared" if job.get("expected_min")
                            is not None else
                            "history" if history.get("median_min")
                            is not None else None),
        "history": history,
        "degraded": degraded,
        "capability": cap_facts,
        "lanes": lanes_eval,
        "chosen": best,
        "score": lanes_eval[best]["score"] if best else None,
    }
    return best, snapshot


def _resolve_auto_jobs(jobs, locks, held_hosts):
    """The impure shell (FR-004/FR-006): evaluate every
    lane-less submitted job once, persist the outcome through
    the verified-write path, ledger it. Placement writes the
    lane — from then on the job is an ordinary explicit-lane
    job and is never re-resolved. Non-placement records the
    evaluation and ledgers auto_no_fit once per distinct
    unmet-reason set, so a waiting job does not spam the
    ledger every sweep."""
    pending = [j for j in jobs
               if j.get("state") == "submitted" and j.get("lane") is None]
    if not pending:
        return
    hosts_state = load_hosts_state()
    now = now_dt()
    load = {}
    for j in jobs:
        lane = j.get("lane")
        if not lane:
            continue
        entry = load.setdefault(lane, {"executing": 0, "waiting": 0})
        if j["state"] in RUN_HOLDING_STATES:
            entry["executing"] += 1
        elif j["state"] == "submitted":
            entry["waiting"] += 1
    lane_ctx = {n: {"host": LANE_HOST[n],
                    "resource_class": LANE_CLASS.get(n, "any"),
                    "duration_band": LANE_BAND.get(n, (None, None))}
                for n in LANES}
    capabilities = {}
    for host in {LANE_HOST[n] for n in LANES}:
        capabilities[host] = capability_view(host, hosts_state, now)
    for job in pending:
        ctx = {"lanes": lane_ctx, "load": load, "locks": locks,
               "held_hosts": held_hosts, "capabilities": capabilities,
               "history": program_history(jobs, job.get("program")),
               "now_iso": now_iso()}
        chosen, snapshot = resolve_auto_lane(job, ctx)
        if chosen is not None:
            job["lane"] = chosen
            if job.pop("cwd_auto", None):
                job["cwd"] = default_cwd(chosen, job["workflow"])
            job["auto_route"] = snapshot
            save_job_safe(job, "auto_routed")
            ledger("auto_routed", job_id=job["id"], key=job["key"],
                   program=job["program"], lane=chosen,
                   score=snapshot["score"], degraded=snapshot["degraded"])
            if snapshot["degraded"]:
                ledger("auto_degraded", job_id=job["id"],
                       key=job["key"], lane=chosen,
                       reason="capability snapshot missing or stale; "
                              "routed on bands, load and history")
        else:
            reasons = sorted({r for e in snapshot["lanes"].values()
                              for r in e["reasons"]})
            snapshot["unmet"] = reasons
            job["auto_wait"] = snapshot
            save_job_safe(job, "auto_wait")
            prev = (job.get("auto_no_fit_reasons") or [])
            if reasons != prev:
                job["auto_no_fit_reasons"] = reasons
                save_job_safe(job, "auto_no_fit")
                ledger("auto_no_fit", job_id=job["id"], key=job["key"],
                       program=job["program"], reasons=reasons)


def dispatch_pass(jobs, runs_by_id):
    """Returns number of jobs dispatched this sweep."""
    dispatched = 0
    # Lane locks (spec 015 FR-007): expiry is evaluated at
    # dispatch time, at the head of the pass — an expired lock
    # behaves exactly like a release from this pass on.
    locks = expire_locks(jobs=jobs)
    # Host dispatch holds (spec 011 FR-006 + FR-007: down holds
    # and drain holds alike) are computed here, ahead of their
    # use in the loop below, because auto routing must treat a
    # held host's lanes as ineligible too.
    held_hosts = {h for h, r in load_hosts_state().items()
                  if r.get("hold") or r.get("drain_hold")}
    # Auto routing (spec 016 FR-004/FR-006): lane-less
    # submitted jobs are resolved against this landscape —
    # locks and holds already applied — before ordering. A
    # resolved job then faces every gate below exactly as an
    # explicit submission does; an unresolved one is skipped
    # by the loop and re-evaluated next pass.
    _resolve_auto_jobs(jobs, locks, held_hosts)
    # Stale dispatching states: flag for a human, never auto-redispatch.
    for j in jobs:
        if j["state"] == "dispatching" and not j.get("run_id"):
            age = minutes_between(parse_iso(j.get("dispatching_at")), now_dt())
            if age is not None and age > STALE_DISPATCHING_MIN:
                if "stale_dispatching" not in j["flags"]:
                    j["flags"].append("stale_dispatching")
                    save_job_safe(j, "stale_dispatching")
                    ledger("stale_dispatching", job_id=j["id"], key=j["key"],
                           age_min=round(age, 1))
    holding = [j for j in jobs
               if j["state"] in RUN_HOLDING_STATES
               and j.get("run_status") != "completed"]
    active_count = len(holding)
    held_lane_counts = {}
    for j in holding:
        held_lane_counts[j["lane"]] = held_lane_counts.get(j["lane"], 0) + 1
    held_hashes = {(j["workflow"], j.get("script_sha256")) for j in holding
                   if j.get("script_sha256")}
    # Effective dispatch order (spec 013 FR-003/FR-012): the
    # optimizer's unified key, evaluated once against a single
    # instant so the whole pass orders consistently.
    now = now_dt()
    waiting = sorted([j for j in jobs if j["state"] == "submitted"],
                     key=lambda j: dispatch_sort_key(j, now))
    # Admission under a pause/focus record is one rule (spec 013
    # FR-002), evaluated before ordering: maintenance flows; a
    # job the focus record admits flows; the gatekeeper's own
    # reboot jobs flow via the maintenance exemption, as today.
    # A STOP record admits nothing beyond maintenance, exactly
    # as today's combined check did. Drain (FR-005) changes no
    # admission — it only adds the settle transition.
    prec = pause_record()
    gate_active = prec is not None or stop_state() is not None
    # Host dispatch holds (spec 011 FR-006 + FR-007): a host that
    # has gone down, or is under a deliberate drain hold, takes no
    # new dispatches on any of its lanes — maintenance included,
    # the host is going away — until the hold is cleared
    # (Phase 1: `gk host-resume`; the FR-010 recovery handshake
    # replaces it in Phase 5). Queued jobs wait; nothing reroutes.
    # (held_hosts itself is computed at the head of the pass —
    # spec 016: auto routing must exclude the same held set,
    # drain holds included.)
    # FR-015 route 2 exception: the gatekeeper's own signal
    # jobs are exempt from the host hold (_signal_hold_exempt).
    lanes_live = os.path.isfile(LANES_LIVE_FILE)
    for job in waiting:
        if job.get("lane") is None:
            continue  # auto job with no fit yet (spec 016): waits
        maint = bool(job.get("maintenance"))
        if gate_active and not maint:
            if prec is None or focus_match(job, prec) is None:
                continue  # held by the pause/focus/stop gate
        if LANE_HOST.get(job["lane"]) in held_hosts \
                and not _signal_hold_exempt(job):
            continue  # host going down: held (see status board)
        # Lane priority lock (spec 015 FR-002): while the lane
        # is ACTIVE-locked by another program, a non-maintenance
        # job waits — not refused, not reordered; its watermarks
        # escalate as the visibility mechanism. Priority is
        # never consulted here: it cannot trump a lock.
        # Maintenance bypasses (FR-008) and is ledgered when it
        # lands below.
        lock_rec = locks.get(job["lane"])
        lock_foreign_active = bool(
            lock_rec and lock_rec.get("state") == "active"
            and lock_rec.get("program") != job.get("program"))
        if lock_foreign_active and not maint:
            continue  # lane locked by another program (spec 015)
        if not lanes_live and active_count >= MAX_ACTIVE_DISPATCHED \
                and not maint:
            ledger("dispatch_deferred_cap", job_id=job["id"], key=job["key"],
                   active=active_count, cap=MAX_ACTIVE_DISPATCHED)
            continue
        if held_lane_counts.get(job["lane"], 0) \
                >= LANE_CONCURRENCY.get(job["lane"], 1) and not maint:
            continue  # lane serialization: another job of this class is live
        if (job["workflow"], job.get("script_sha256")) in held_hashes:
            ledger("dispatch_skipped_duplicate", job_id=job["id"],
                   key=job["key"], workflow=job["workflow"],
                   script_sha256=job.get("script_sha256"))
            continue
        if dispatch_one(job, jobs):
            dispatched += 1
            if lock_foreign_active and maint:
                # Spec 015 FR-008: the operator's class is
                # never locked out of his own door, and every
                # bypass of an ACTIVE lock is on the record.
                ledger("lock_bypassed", job_id=job["id"],
                       key=job["key"], lane=job["lane"],
                       holder=lock_rec.get("program"))
            active_count += 1
            held_lane_counts[job["lane"]] = \
                held_lane_counts.get(job["lane"], 0) + 1
            if job.get("script_sha256"):
                held_hashes.add((job["workflow"], job["script_sha256"]))
    return dispatched


# --- sweep: watch + watermarks ----------------------------------------------

def current_step(run_id):
    """One jobs call - used ONLY for a run that has breached executing W1,
    to compare step progression across breaches. Returns 'N:name' or None."""
    data, err = gh_json(["api", "repos/%s/actions/runs/%s/jobs"
                         % (REPO, run_id)])
    if err or not data or not data.get("jobs"):
        return None
    steps = data["jobs"][0].get("steps") or []
    for s in steps:
        if s.get("status") == "in_progress":
            return "%s:%s" % (s.get("number"), s.get("name"))
    return None


def extract_log_lines(raw):
    """gh run view --log lines are 'job<TAB>step<TAB>timestamp content'.
    Return content lines (prefix stripped when the shape matches)."""
    lines = []
    for line in raw.splitlines():
        parts = line.split("\t")
        content = parts[-1] if len(parts) >= 3 else line
        content = re.sub(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z ", "", content)
        lines.append(content)
    return lines


def fetch_log_archive(run_id):
    """Fallback log source: the REST logs archive (a zip of per-step text
    files). Some runs' logs cannot be read by `gh run view --log` at all
    (observed 2026-10-27, run 40512763692: --log failed on every attempt
    while the archive read fine). Returns combined text or None."""
    import io
    import zipfile
    try:
        p = subprocess.run(
            ["gh", "api", "repos/%s/actions/runs/%s/logs" % (REPO, run_id)],
            capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0 or not p.stdout:
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(p.stdout))
    except zipfile.BadZipFile:
        return None
    chunks = []
    for name in sorted(zf.namelist()):
        if name.endswith(".txt"):
            chunks.append(zf.read(name).decode("utf-8", "replace"))
    return "\n".join(chunks) if chunks else None


def fetch_log_once(job):
    rc, out, err = run_cmd(["gh", "run", "view", str(job["run_id"]), "-R",
                            REPO, "--log"], timeout=180)
    via = "gh-log"
    if rc != 0 or not out:
        out = fetch_log_archive(job["run_id"])
        via = "api-archive"
    if not out:
        job["log_pending"] = True
        ledger("log_fetch_failed", job_id=job["id"], key=job["key"],
               run_id=job["run_id"], rc=rc)
        return False
    lines = extract_log_lines(out)
    job["log_tail"] = lines[-200:]
    job["markers"] = [ln for ln in lines if MARKER_RE.match(ln.strip())]
    job["log_fetched_at"] = now_iso()
    job["log_pending"] = False
    job["log_source"] = via
    job["verdict_source"] = "markers"
    ledger("log_harvested", job_id=job["id"], key=job["key"],
           run_id=job["run_id"], markers=len(job["markers"]),
           log_lines=len(lines), via=via)
    return True


def exec_thresholds(job):
    exp = job.get("expected_min")
    lane = job["lane"]
    if lane in EXEC_WM_FIXED:
        return EXEC_WM_FIXED[lane]
    f1, f2 = EXEC_WM_FLOOR[lane]
    if exp:
        return (max(exp * 1.5, f1), max(exp * 2.0, f2))
    return (f1, f2)


def eval_watermarks(job, run):
    """Record Part 4 watermark breaches. Never cancels, never escalates
    beyond the ledger + job flags. Queued W1 is record-only during the
    pre-lane transition; queued W2 escalates (flag + ledger)."""
    events = 0
    wm = job.setdefault("watermarks", {})
    status = run.get("status")
    if status == "queued" or status in ("waiting", "requested", "pending"):
        start = parse_iso(job.get("run_created_at") or run.get("createdAt"))
        age = minutes_between(start, now_dt())
        if age is None:
            return 0
        w1, w2 = QUEUED_WM[job["lane"]]
        if age >= w1 and "queued_w1" not in wm:
            wm["queued_w1"] = now_iso()
            ledger("watermark_breach", job_id=job["id"], key=job["key"],
                   run_id=job["run_id"], lane=job["lane"], level="W1",
                   state="queued", age_min=round(age, 1), threshold_min=w1,
                   classification="starvation")
            events += 1
        if age >= w2 and "queued_w2" not in wm:
            wm["queued_w2"] = now_iso()
            if "watermark_queued_w2" not in job["flags"]:
                job["flags"].append("watermark_queued_w2")
            ledger("watermark_escalation", job_id=job["id"], key=job["key"],
                   run_id=job["run_id"], lane=job["lane"], level="W2",
                   state="queued", age_min=round(age, 1), threshold_min=w2,
                   classification="starvation")
            events += 1
    elif status == "in_progress":
        start = parse_iso(job.get("first_seen_in_progress_at"))
        age = minutes_between(start, now_dt())
        if age is None:
            return 0
        w1, w2 = exec_thresholds(job)
        if age >= w1 and "exec_w1" not in wm:
            wm["exec_w1"] = now_iso()
            step = current_step(job["run_id"])
            job["last_step_at_w1"] = step
            ledger("watermark_breach", job_id=job["id"], key=job["key"],
                   run_id=job["run_id"], lane=job["lane"], level="W1",
                   state="executing", age_min=round(age, 1),
                   threshold_min=w1, current_step=step)
            events += 1
        if age >= w2 and "exec_w2" not in wm:
            wm["exec_w2"] = now_iso()
            if "watermark_exec_w2" not in job["flags"]:
                job["flags"].append("watermark_exec_w2")
            step = current_step(job["run_id"])
            prev = job.get("last_step_at_w1")
            progression = ("step_advanced" if step != prev
                           else "step_frozen_or_unknown")
            job["last_step_at_w2"] = step
            ledger("watermark_escalation", job_id=job["id"], key=job["key"],
                   run_id=job["run_id"], lane=job["lane"], level="W2",
                   state="executing", age_min=round(age, 1),
                   threshold_min=w2, current_step=step,
                   step_progression=progression)
            events += 1
    return events


def watch_pass(jobs, runs_by_id):
    changes = 0
    breaches = 0
    for job in jobs:
        if job["state"] not in ("dispatched", "watching"):
            continue
        if not job.get("run_id"):
            continue
        run = runs_by_id.get(str(job["run_id"]))
        if run is None:
            if not job.get("not_in_list_noted"):
                job["not_in_list_noted"] = True
                save_job_safe(job, "run_not_in_list")
                ledger("run_not_in_list", job_id=job["id"], key=job["key"],
                       run_id=job["run_id"])
            continue
        job["not_in_list_noted"] = False
        if not job.get("run_created_at"):
            job["run_created_at"] = run.get("createdAt")
        old_status = job.get("run_status")
        new_status = run.get("status")
        if new_status == "in_progress" and not job.get(
                "first_seen_in_progress_at"):
            job["first_seen_in_progress_at"] = now_iso()
        if new_status != old_status:
            job["run_status"] = new_status
            job["run_conclusion"] = run.get("conclusion")
            changes += 1
            ledger("state_change", job_id=job["id"], key=job["key"],
                   run_id=job["run_id"], from_status=old_status,
                   to_status=new_status,
                   conclusion=run.get("conclusion"))
        if new_status == "completed":
            job["run_conclusion"] = run.get("conclusion")
            if not job.get("log_fetched_at"):
                fetch_log_once(job)
            if checkpoint_marker_seen(job):
                # Spec 013 FR-008: the job stopped itself at a
                # cooperative checkpoint. A distinct terminal
                # state — not a completion, not a failure —
                # whatever the run's conclusion says; the
                # conclusion is kept on the record verbatim.
                job["state"] = "held_at_checkpoint"
                job["held_at_checkpoint_at"] = now_iso()
                save_job_safe(job, "held_at_checkpoint")
                ledger("held_at_checkpoint", job_id=job["id"],
                       key=job["key"], run_id=job["run_id"],
                       run_conclusion=job["run_conclusion"])
            else:
                job["state"] = "completed"
                job["completed_at"] = (job.get("completed_at")
                                        or now_iso())
                # Spec 016 FR-005: a completed probe job's
                # harvested log becomes the host's capability
                # snapshot (its flags persist with this save).
                _maybe_ingest_probe(job)
                save_job_safe(job, "completed")
        else:
            breaches += eval_watermarks(job, run)
            save_job_safe(job, "watch_update")
    return changes, breaches


# --- sweep: main ------------------------------------------------------------

def acquire_lock():
    try:
        os.mkdir(LOCK_DIR)
        return True
    except FileExistsError:
        age = minutes_between(
            datetime.fromtimestamp(os.path.getmtime(LOCK_DIR),
                                   tz=timezone.utc), now_dt())
        if age is not None and age > LOCK_STALE_MIN:
            try:
                os.rmdir(LOCK_DIR)
                os.mkdir(LOCK_DIR)
                return True
            except OSError:
                return False
        return False


def maybe_settle_drain():
    """The drain settle (spec 013 FR-005): when a draining
    pause/focus record has nothing left executing outside its
    admitted set, the drain completes — the record stays (the
    pause itself stands) with `drain` cleared and the settle
    stamped and ledgered. Maintenance jobs never block a
    settle: they flow under every mode and are not what a
    drain waits for. Admitted jobs still executing DO block
    it under focus (the pipeline drains toward the focus set);
    under a plain pause nothing program-side is admitted, so
    any executing program job blocks."""
    prec = pause_record()
    if prec is None or not prec.get("drain"):
        return False
    for j in load_jobs():
        if j.get("maintenance"):
            continue
        if j["state"] not in RUN_HOLDING_STATES:
            continue
        if j.get("run_status") == "completed":
            continue
        if focus_match(j, prec) is not None:
            continue
        return False
    rec = pause_state()
    rec["drain"] = False
    rec["drain_settled_at"] = now_iso()
    _write_text_file(PAUSE_FILE,
                     json.dumps(rec, indent=2, sort_keys=True))
    ledger("drain_settled", mode=prec["mode"])
    hlog("DRAIN SETTLED at %s — the pause stands, fully "
         "quiescent." % rec["drain_settled_at"])
    return True


def cmd_sweep(_args):
    if not acquire_lock():
        print("SWEEP skipped: another sweep holds the lock")
        return 0
    try:
        stopped = stop_state()
        if stopped is not None:
            print("SWEEP %s STOPPED since %s (%s) — nothing spent, "
                  "nothing watched, nothing dispatched"
                  % (now_iso(), stopped.get("ts"),
                     stopped.get("reason") or "no reason recorded"))
            return 0
        remaining, err = rate_limit_remaining()
        if err:
            ledger("api_error", where="rate_limit", error=err)
            print("SWEEP budget=unknown (rate_limit error) dispatched=0 "
                  "changes=0 breaches=0")
            return 0
        if remaining < CRITICAL_REMAINING:
            ledger("budget_critical", remaining=remaining)
            print("SWEEP budget_remaining=%d CRITICAL: no dispatch, no "
                  "watch calls" % remaining)
            hlog("SWEEP budget_remaining=%d CRITICAL: no dispatch, no "
                 "watch calls" % remaining)
            return 0
        standdown = remaining < STANDDOWN_REMAINING
        if standdown:
            ledger("budget_standdown", remaining=remaining)
        runs, lerr = list_runs(50)
        runs_by_id = {}
        if lerr:
            ledger("api_error", where="run_list", error=lerr)
        else:
            runs_by_id = {str(r["databaseId"]): r for r in runs}
        jobs = load_jobs()
        # Host liveness + going-down ingest (spec 011 FR-001/003/006):
        # one backchannel contents read, before dispatch so a
        # just-ingested hold blocks this same pass. A liveness
        # failure is ledgered inside and never breaks the sweep.
        sweep_liveness(jobs)
        dispatched = 0
        if not standdown and not lerr:
            dispatched = dispatch_pass(jobs, runs_by_id)
            jobs = load_jobs()  # dispatch_one saved as it went; reload fresh
        changes = breaches = 0
        if not lerr:
            changes, breaches = watch_pass(jobs, runs_by_id)
            maybe_settle_drain()
        active = len([j for j in load_jobs()
                      if j["state"] in RUN_HOLDING_STATES
                      and j.get("run_status") != "completed"])
        waiting = len([j for j in load_jobs() if j["state"] == "submitted"])
        prec_now = pause_record()
        mode_tok = ""
        if prec_now is not None:
            mode_tok = (" FOCUSED" if prec_now["mode"] == "focused"
                        else " PAUSED")
            if prec_now.get("drain"):
                mode_tok += " DRAINING"
        summary = ("SWEEP %s budget_remaining=%d%s%s dispatched=%d "
                   "changes=%d breaches=%d active=%d waiting=%d"
                   % (now_iso(), remaining,
                      " STANDDOWN" if standdown else "",
                      mode_tok,
                      dispatched, changes, breaches, active, waiting))
        print(summary)
        hlog(summary)
        return 0
    finally:
        try:
            os.rmdir(LOCK_DIR)
        except OSError:
            pass


# --- status ------------------------------------------------------------------

def job_issues(j):
    """One job's current issue list, derived from its saved state only."""
    out = []
    for fl in j.get("flags", []):
        if fl.startswith("watermark"):
            out.append(fl.replace("_", " "))
    if j.get("log_pending"):
        out.append("log harvest failed/pending (verdict not yet readable)")
    if j["state"] == "dispatching":
        out.append("dispatch in progress (stale-dispatching is flagged "
                   "for a human, never auto-redispatched)")
    if j["state"] == "completed" and j.get("run_conclusion") not in (
            None, "success") and not j.get("markers"):
        out.append("completed with conclusion=%s and no markers"
                   % j.get("run_conclusion"))
    if j["state"] == "completed" and j.get("run_conclusion") == "success" \
            and j.get("verdict_source") != "markers":
        out.append("completed successfully but no marker verdict harvested")
    return out


def repo_health():
    """Project-repo health for `status --repo`: latest CI runs on main,
    open Dependabot alerts, and open Dependabot pull requests. Three API
    calls, made only when the flag is passed (never in the default
    local-only status). Failures degrade to an 'unavailable' line."""
    print("\nrepo health (%s):" % PROJECT_REPO)
    rc, out, _ = run_cmd(["gh", "run", "list", "-R", PROJECT_REPO, "-L", "3",
                          "--json", "workflowName,status,conclusion,"
                          "createdAt"], timeout=60)
    if rc == 0 and out:
        try:
            for r in json.loads(out):
                print("  CI %-10s %-12s %s (%s)"
                      % (r.get("workflowName"), r.get("status"),
                         r.get("conclusion") or "-", r.get("createdAt")))
        except ValueError:
            print("  CI unavailable (unparsable response)")
    else:
        print("  CI unavailable")
    rc, out, _ = run_cmd(["gh", "api", "repos/%s/dependabot/alerts"
                          "?state=open&per_page=30" % PROJECT_REPO],
                         timeout=60)
    if rc == 0 and out is not None:
        try:
            alerts = json.loads(out or "[]")
            if alerts:
                for a in alerts:
                    adv = a.get("security_advisory") or {}
                    print("  DEPENDABOT ALERT #%s %s (%s)"
                          % (a.get("number"), adv.get("summary", "?"),
                             adv.get("severity", "?")))
            else:
                print("  dependabot alerts: none open")
        except ValueError:
            print("  dependabot alerts: unavailable (unparsable response)")
    else:
        print("  dependabot alerts: unavailable")
    rc, out, _ = run_cmd(["gh", "pr", "list", "-R", PROJECT_REPO,
                          "--author", "app/dependabot", "--json",
                          "number,title"], timeout=60)
    if rc == 0 and out:
        try:
            prs = json.loads(out)
            for pr in prs:
                print("  DEPENDABOT PR #%s %s" % (pr.get("number"),
                                                  pr.get("title")))
            if not prs:
                print("  dependabot PRs: none open")
        except ValueError:
            print("  dependabot PRs: unavailable (unparsable response)")
    else:
        print("  dependabot PRs: unavailable")


def fetch_backchannel_heartbeat():
    """(heartbeat dict, error) — ONE contents-API read of the
    backchannel heartbeat, parsed exactly as backchannel_status_line
    always parsed it (ts / host / stopped / self_test ride the
    payload). Shared by the status line and the sweep's liveness
    pass (spec 011 FR-001) so there is one heartbeat format, read
    one way."""
    data, err = gh_json(["api", "repos/%s/contents/%s"
                         % (BC_REPO, BC_HEARTBEAT_PATH)])
    if err or not data:
        return None, err or "empty response"
    try:
        hb = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
        datetime.strptime(hb["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except Exception as e:
        return None, "unreadable heartbeat (%s)" % e
    return hb, None


def backchannel_status_line():
    """One status line for the failsafe backchannel (spec 010 T011):
    heartbeat age from the channel repo, with a stale alarm past
    BC_STALE_S. Fail-soft: any read error reports 'unknown', never
    breaks status. Costs one contents-API call per status run."""
    hb, err = fetch_backchannel_heartbeat()
    if hb is None:
        return "backchannel: heartbeat unknown (%s)" % err
    ts = datetime.strptime(hb["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)
    age = int((datetime.now(timezone.utc) - ts).total_seconds())
    st = hb.get("self_test") or {}
    st_txt = "ok" if st.get("ok") else ("FAILED" if st else "absent")
    line = ("backchannel: heartbeat %ds old host=%s stopped=%s self_test=%s"
            % (age, hb.get("host"), hb.get("stopped"), st_txt))
    if age > BC_STALE_S:
        line = ("!! BACKCHANNEL STALE: heartbeat %ds old (limit %ds) — %s"
                % (age, BC_STALE_S, line))
    return line


# --- Host liveness + going-down records (spec 011 FR-001/003/004/006) ------
#
# The gatekeeper keeps its own per-host liveness record in
# hosts.json beside the queue (verified writes, like every other
# gatekeeper state file):
#
#   { "<host>": {
#       "last_seen":         ISO ts of the last sweep that read a
#                            heartbeat for this host,
#       "last_heartbeat_ts": the heartbeat's own ts at that read,
#       "down":              last computed age verdict (bool),
#       "hold":              dispatch hold in effect (bool),
#       "hold_since":        going-down ts that set the hold,
#       "going_down":        the last ingested going-down record
#                            (with an added "ingested_at"), or null
#   } }
#
# A host is DOWN when its heartbeat age exceeds
# liveness.down_after_s (config), or while a dispatch hold stands.
# DOWN is a record/display state; dispatch is held only by the
# hold, which only a going-down ingest sets (FR-006) — a merely
# stale heartbeat never blocks dispatch in this phase.

GOING_DOWN_TRIGGERS = ("shutdown", "logoff", "update", "unknown",
                       "remote-reboot")


def load_hosts_state():
    try:
        with open(HOSTS_FILE, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_hosts_state(state):
    _write_text_file(HOSTS_FILE,
                     json.dumps(state, indent=2, sort_keys=True))


def _host_record(state, host):
    return state.setdefault(host, {
        "last_seen": None, "last_heartbeat_ts": None, "down": False,
        "hold": False, "hold_since": None, "going_down": None,
        "drain_hold": False, "drain_since": None,
        "guard": None, "guard_stale": False})


def heartbeat_hosts():
    """Config hosts whose liveness the backchannel heartbeat proves.
    The channel agent runs inside a WSL2 distro, so its heartbeat
    proves that distro's host and every host it rides `via`, in
    chain order. (The heartbeat's own `host` field is the machine
    hostname, not a config name, so attribution is by config
    shape, not by that field.)"""
    out = []
    for name, htype in HOST_TYPE.items():
        if htype != "wsl2":
            continue
        chain = [name]
        via = HOST_VIA.get(name)
        while via and via not in chain:
            chain.append(via)
            via = HOST_VIA.get(via)
        for h in chain:
            if h not in out:
                out.append(h)
    return out


def sweep_liveness(jobs):
    """The sweep's liveness pass (FR-001 + FR-003 detection): one
    backchannel contents read — the same read class the sweep
    already spends, one extra call per sweep — refreshing the
    per-host last-seen record from the heartbeat, then ingesting
    a going-down record if the heartbeat carries one (a
    `going_down` field the host adds to its heartbeat payload).
    Fail-soft: a read failure is ledgered and the pass returns;
    liveness never breaks a sweep."""
    state = load_hosts_state()
    hb, err = fetch_backchannel_heartbeat()
    if hb is None:
        ledger("api_error", where="backchannel_heartbeat", error=err)
        return state
    ts = parse_iso(hb["ts"])
    age = (now_dt() - ts).total_seconds() if ts else None
    for host in heartbeat_hosts():
        rec = _host_record(state, host)
        rec["last_seen"] = now_iso()
        rec["last_heartbeat_ts"] = hb["ts"]
        rec["down"] = bool(age is not None
                           and age > LIVENESS_DOWN_AFTER_S)
    try:
        save_hosts_state(state)
    except (IOError, OSError) as exc:
        hlog("HOSTS_SAVE_FAILED %s" % exc)
    gd = hb.get("going_down")
    if isinstance(gd, dict) and gd:
        ingest_going_down(gd, jobs=jobs)
    guard = hb.get("guard")
    if isinstance(guard, dict) and guard:
        _record_guard_beat(guard, hb.get("ts"))
    return state


def _record_guard_beat(guard, hb_ts):
    """Record the host guard's latest beat (spec 011 Phase 3,
    T007). The guard beats into a host file; the backchannel
    agent merges it into the heartbeat payload as `guard`
    ({host, ts, active, draining}) and this pass stores it on
    the host's liveness record. Staleness is judged against the
    heartbeat carrying the beat — both timestamps come from the
    host's own clock, so the comparison needs no trust in the
    gatekeeper's clock: a beat older than
    drain.guard_stale_after_s relative to its carrier is stale.
    Transitions ledger exactly once (guard_stale /
    guard_recovered), so a stopped guard is a standing,
    visible loss of protection — never a silent one."""
    host = guard.get("host")
    if not host:
        return
    state = load_hosts_state()
    rec = _host_record(state, host)
    rec["guard"] = {"ts": guard.get("ts"),
                    "active": guard.get("active"),
                    "draining": bool(guard.get("draining")),
                    "seen_at": now_iso()}
    gts = parse_iso(guard.get("ts"))
    hts = parse_iso(hb_ts)
    stale = bool(gts is not None and hts is not None
                 and (hts - gts).total_seconds() > DRAIN_GUARD_STALE_S)
    was = bool(rec.get("guard_stale"))
    rec["guard_stale"] = stale
    try:
        save_hosts_state(state)
    except (IOError, OSError) as exc:
        hlog("HOSTS_SAVE_FAILED %s" % exc)
        return
    if stale and not was:
        ledger("guard_stale", host=host, guard_ts=guard.get("ts"),
               heartbeat_ts=hb_ts)
        hlog("GUARD STALE host=%s guard_ts=%s heartbeat_ts=%s"
             % (host, guard.get("ts"), hb_ts))
    elif was and not stale:
        ledger("guard_recovered", host=host,
               guard_ts=guard.get("ts"))


def ingest_going_down(record, jobs=None):
    """Ingest one going-down record (spec 011 FR-003 gatekeeper
    side + FR-006). The host side writes the record at the START
    of a drain and pushes it through the backchannel; the sweep
    detects it as the heartbeat's `going_down` field and calls
    this. It is also directly callable (tests, operator use).

    Record format (JSON object):
      host:           config host name the record is about
      ts:             ISO-8601 UTC timestamp the host wrote the
                      record (the drain's start)
      trigger:        shutdown | logoff | update | unknown
      active_run_ids: [run ids active on the host at drain start]
      supervised:     [{ "id": ..., "checkpoint": <pointer|null> }]
                      supervised-work ids + checkpoint pointers
      uncheckpointed: [ids that did not checkpoint in the window]

    Effects, exactly once per (host, ts) — a re-sweep that sees
    the same record again is a no-op (dedupe is recorded in the
    liveness state file):
      - ledger `host_going_down` (host, ts, trigger, counts)
      - each affected job record gains interrupted_by=
        "host-shutdown" and interrupted_at=<record ts>, plus a
        ledger `interrupted` line — which is what the outbox
        turns into one push per affected notify registration
        (FR-004). Affected = an active job on one of the host's
        lanes whose run id the record names; when the record
        names no runs, every active job on the host's lanes.
      - a per-host dispatch hold: dispatch_pass skips every lane
        of the host until the hold clears. Phase 5's recovery
        handshake (FR-010) clears it then; in Phase 1 the
        stand-in is the operator command `gk host-resume <host>`.
    The gatekeeper never cancels and never re-dispatches: an
    annotated run may still complete in GitHub while the host
    dies, and the watch pass adopts that truth as usual.
    Returns a summary dict, or None when the record was already
    ingested or is malformed (no host / no ts)."""
    host = record.get("host")
    ts = record.get("ts")
    if not host or not ts:
        return None
    state = load_hosts_state()
    rec = _host_record(state, host)
    prior = rec.get("going_down") or {}
    if prior.get("ts") == ts:
        return None  # already ingested: no duplicate effects
    if jobs is None:
        jobs = load_jobs()
    trigger = record.get("trigger")
    if trigger not in GOING_DOWN_TRIGGERS:
        trigger = "unknown"
    run_ids = {str(r) for r in record.get("active_run_ids") or []}
    affected = []
    for j in jobs:
        if LANE_HOST.get(j.get("lane")) != host:
            continue
        if run_ids:
            if str(j.get("run_id")) not in run_ids:
                continue
        elif j["state"] not in ACTIVE_STATES:
            continue
        affected.append(j)
    stored = {
        "host": host, "ts": ts, "trigger": trigger,
        "active_run_ids": sorted(run_ids),
        "supervised": record.get("supervised") or [],
        "uncheckpointed": record.get("uncheckpointed") or [],
        "ingested_at": now_iso(),
    }
    rec["going_down"] = stored
    rec["hold"] = True
    rec["hold_since"] = ts
    rec["down"] = True
    save_hosts_state(state)
    ledger("host_going_down", host=host, ts=ts, trigger=trigger,
           active_runs=len(run_ids), affected_jobs=len(affected),
           supervised=len(stored["supervised"]),
           uncheckpointed=len(stored["uncheckpointed"]))
    for j in affected:
        j["interrupted_by"] = "host-shutdown"
        j["interrupted_at"] = ts
        j["interrupted_host"] = host
        save_job_safe(j, "host_going_down")
        ledger("interrupted", job_id=j["id"], key=j["key"],
               run_id=j.get("run_id"), host=host, ts=ts,
               trigger=trigger)
    hlog("HOST_GOING_DOWN host=%s ts=%s trigger=%s affected=%d"
         % (host, ts, trigger, len(affected)))
    return {"host": host, "ts": ts, "trigger": trigger,
            "affected": affected}


def host_status_lines(state=None):
    """Per-host liveness for `gk status` (FR-001): UP/DOWN plus
    heartbeat age, rendered from the persisted last-seen record —
    status spends no API on this; the sweep refreshes the record.
    A host never seen renders UNKNOWN, never DOWN: no heartbeat
    source has spoken for it yet."""
    if state is None:
        state = load_hosts_state()
    out = []
    for host in sorted(_CFG["hosts"]):
        rec = state.get(host) or {}
        hb_ts = parse_iso(rec.get("last_heartbeat_ts"))
        if hb_ts is None:
            out.append("host %s: UNKNOWN (no heartbeat seen)" % host)
            continue
        age = int((now_dt() - hb_ts).total_seconds())
        down = bool(rec.get("hold")) or age > LIVENESS_DOWN_AFTER_S
        line = "host %s: %s heartbeat %ds old (limit %ds)" % (
            host, "DOWN" if down else "UP", age, LIVENESS_DOWN_AFTER_S)
        if rec.get("hold"):
            line += (" — dispatch HELD since %s (going down; "
                     "`gk host-resume %s` clears)"
                     % (rec.get("hold_since"), host))
        gd = rec.get("going_down")
        if gd:
            line += " — going-down %s trigger=%s" % (
                gd.get("ts"), gd.get("trigger"))
        if rec.get("drain_hold"):
            line += (" — dispatch HELD (drain since %s; "
                     "`gk host-resume %s` clears)"
                     % (rec.get("drain_since"), host))
        guard = rec.get("guard")
        if guard:
            line += " — guard beat %s%s" % (
                guard.get("ts"),
                " STALE (protection not reporting)"
                if rec.get("guard_stale") else "")
        out.append(line)
    return out


def cmd_host_resume(args):
    """Clear a host's dispatch holds — the going-down hold
    (FR-006) and the drain hold (FR-007), either or both.
    Phase-1 stand-in for the FR-010 recovery handshake
    (Phase 5): an operator clears the holds by hand once the
    host is back and verified."""
    state = load_hosts_state()
    rec = state.get(args.host)
    if rec is None or not (rec.get("hold") or rec.get("drain_hold")):
        print("host %s: no dispatch hold in effect" % args.host)
        return 0
    rec["hold"] = False
    rec["hold_since"] = None
    rec["drain_hold"] = False
    rec["drain_since"] = None
    save_hosts_state(state)
    ledger("host_resumed", host=args.host)
    hlog("HOST_RESUMED %s at %s" % (args.host, now_iso()))
    print("host %s: dispatch hold cleared (Phase-1 stand-in for "
          "the FR-010 recovery handshake)" % args.host)
    return 0


# --- Host capability probes (spec 016 FR-005) -------------------------------
#
# `gk probe-host --host <host>` records a normalized capability
# snapshot for a host — the facts auto routing (FR-006) reads.
# The probe scripts only PRINT their platform's own tool output
# under section headers; all interpretation lives in the three
# parsers here, so a report produced anywhere parses the same
# way everywhere. Two recording routes, one parser:
#   dispatch: where this door has a transport for the host
#     (windows via run.yml, wsl2 via its via host's wsl.yml),
#     the probe rides as a maintenance job and the watch pass
#     files the completed job's harvested log;
#   ingest: `--ingest <file>` records a report produced by
#     running the shipped script on the host by any other
#     means — the route for macOS and Linux hosts.
# The script bodies below are the same bytes as the files in
# gatekeeper/probes/ (a battery check pins them equal).

PROBE_KEY_PREFIX = "gk-probe-"

PROBE_SCRIPT_WINDOWS = r"""Write-Output "SYNCYARD_PROBE 1 platform=windows"
Write-Output "## hostname"
hostname
Write-Output "## cpu"
Get-CimInstance Win32_Processor | Select-Object -First 1 | Format-List Name,NumberOfCores,NumberOfLogicalProcessors
Write-Output "## os"
Get-CimInstance Win32_OperatingSystem | Format-List TotalVisibleMemorySize,FreePhysicalMemory
Write-Output "## disk"
Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Format-List DeviceID,FreeSpace,Size
Write-Output "## nvidia"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { nvidia-smi --query-gpu=name,memory.total --format=csv,noheader } else { Write-Output "nvidia-smi: not found" }
Write-Output "## venues"
if (Get-Command wsl -ErrorAction SilentlyContinue) { Write-Output "venue wsl" }
if (Get-Command docker -ErrorAction SilentlyContinue) { Write-Output "venue docker" }
Write-Output "PROBE_END"
"""

PROBE_SCRIPT_LINUX = """#!/bin/sh
echo "SYNCYARD_PROBE 1 platform=linux"
echo "## hostname"
hostname
echo "## cpuinfo"
grep -m1 "model name" /proc/cpuinfo
grep -c "^processor" /proc/cpuinfo | sed 's/^/processors: /'
echo "## meminfo"
grep -E "^(MemTotal|MemAvailable):" /proc/meminfo
echo "## loadavg"
cat /proc/loadavg
echo "## df"
df -kP / | tail -1
echo "## nvidia"
if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi --query-gpu=name,memory.total --format=csv,noheader; else echo "nvidia-smi: not found"; fi
echo "## venues"
if command -v docker >/dev/null 2>&1; then echo "venue docker"; fi
echo "PROBE_END"
"""

PROBE_SCRIPT_DARWIN = """#!/bin/sh
echo "SYNCYARD_PROBE 1 platform=darwin"
echo "## hostname"
hostname
echo "## sysctl"
sysctl -n machdep.cpu.brand_string
echo "cores: $(sysctl -n hw.ncpu)"
echo "memsize: $(sysctl -n hw.memsize)"
echo "## vm_stat"
vm_stat | head -5
echo "## loadavg"
sysctl -n vm.loadavg
echo "## df"
df -k / | tail -1
echo "## gpu"
system_profiler SPDisplaysDataType | grep -E "Chipset Model|VRAM|Metal"
echo "## venues"
if command -v docker >/dev/null 2>&1; then echo "venue docker"; fi
echo "PROBE_END"
"""

PROBE_SCRIPTS = {"windows": ("probe-windows.ps1", PROBE_SCRIPT_WINDOWS),
                 "linux": ("probe-linux.sh", PROBE_SCRIPT_LINUX),
                 "darwin": ("probe-darwin.sh", PROBE_SCRIPT_DARWIN)}


def _probe_sections(text):
    """Split a probe report into (platform, {section: [lines]}).
    (None, {}) when the report header is absent."""
    platform = None
    sections = {}
    current = None
    for line in (text or "").splitlines():
        m = re.match(r"SYNCYARD_PROBE\s+1\s+platform=(\w+)", line.strip())
        if m:
            platform = m.group(1)
            continue
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return platform, sections


def _probe_hostname(sections):
    for line in sections.get("hostname") or []:
        if line.strip():
            return line.strip()
    return None


def _probe_nvidia(lines):
    """nvidia-smi CSV rows -> discrete GPU records."""
    gpus = []
    for line in lines or []:
        m = re.match(r"\s*(.+?),\s*(\d+)\s*MiB\s*$", line)
        if m:
            gpus.append({"name": m.group(1), "vram_mb": int(m.group(2)),
                         "class": "discrete"})
    return gpus


def _probe_venues(lines):
    venues = ["native"]
    for line in lines or []:
        m = re.match(r"\s*venue\s+(\w+)", line)
        if m and m.group(1) not in venues:
            venues.append(m.group(1))
    return venues


def _cim_pairs(lines):
    out = {}
    for line in lines or []:
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _to_int(s):
    try:
        return int(str(s).strip())
    except (TypeError, ValueError):
        return None


def parse_probe_windows(sections):
    if "cpu" not in sections and "os" not in sections:
        return None
    snap = {"platform": "windows", "hostname": _probe_hostname(sections),
            "cpu_cores": None, "cpu_model": None, "cpu_load_1m": None,
            "ram_total_mb": None, "ram_free_mb": None,
            "storage_free_gb": None, "gpus": [], "venues": ["native"]}
    cpu = _cim_pairs(sections.get("cpu"))
    snap["cpu_model"] = cpu.get("Name") or None
    snap["cpu_cores"] = _to_int(cpu.get("NumberOfCores"))
    osinfo = _cim_pairs(sections.get("os"))
    total_kb = _to_int(osinfo.get("TotalVisibleMemorySize"))
    free_kb = _to_int(osinfo.get("FreePhysicalMemory"))
    snap["ram_total_mb"] = total_kb // 1024 if total_kb else None
    snap["ram_free_mb"] = free_kb // 1024 if free_kb else None
    disks = sections.get("disk") or []
    blocks, cur = [], {}
    for line in disks + [""]:
        if ":" in line:
            k, v = line.split(":", 1)
            cur[k.strip()] = v.strip()
        elif cur:
            blocks.append(cur)
            cur = {}
    chosen = next((b for b in blocks if b.get("DeviceID") == "C:"),
                  blocks[0] if blocks else None)
    if chosen:
        free_b = _to_int(chosen.get("FreeSpace"))
        snap["storage_free_gb"] = (round(free_b / (1024 ** 3), 1)
                                   if free_b is not None else None)
        snap["storage_root"] = chosen.get("DeviceID")
    snap["gpus"] = _probe_nvidia(sections.get("nvidia"))
    snap["venues"] = _probe_venues(sections.get("venues"))
    return snap


def parse_probe_linux(sections):
    if "meminfo" not in sections and "cpuinfo" not in sections:
        return None
    snap = {"platform": "linux", "hostname": _probe_hostname(sections),
            "cpu_cores": None, "cpu_model": None, "cpu_load_1m": None,
            "ram_total_mb": None, "ram_free_mb": None,
            "storage_free_gb": None, "gpus": [], "venues": ["native"]}
    for line in sections.get("cpuinfo") or []:
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip() == "model name":
                snap["cpu_model"] = v.strip()
            elif k.strip() == "processors":
                snap["cpu_cores"] = _to_int(v)
    for line in sections.get("meminfo") or []:
        m = re.match(r"(MemTotal|MemAvailable):\s+(\d+)\s*kB", line)
        if m:
            key = "ram_total_mb" if m.group(1) == "MemTotal" \
                else "ram_free_mb"
            snap[key] = int(m.group(2)) // 1024
    for line in sections.get("loadavg") or []:
        parts = line.split()
        if parts:
            try:
                snap["cpu_load_1m"] = float(parts[0])
            except ValueError:
                pass
        break
    for line in sections.get("df") or []:
        parts = line.split()
        if len(parts) >= 6:
            avail_kb = _to_int(parts[3])
            snap["storage_free_gb"] = (round(avail_kb / 1048576, 1)
                                       if avail_kb is not None else None)
            snap["storage_root"] = parts[5]
    snap["gpus"] = _probe_nvidia(sections.get("nvidia"))
    snap["venues"] = _probe_venues(sections.get("venues"))
    return snap


def parse_probe_darwin(sections):
    if "sysctl" not in sections:
        return None
    snap = {"platform": "darwin", "hostname": _probe_hostname(sections),
            "cpu_cores": None, "cpu_model": None, "cpu_load_1m": None,
            "ram_total_mb": None, "ram_free_mb": None,
            "storage_free_gb": None, "gpus": [], "venues": ["native"]}
    sysctl = [l.strip() for l in (sections.get("sysctl") or []) if l.strip()]
    if sysctl:
        snap["cpu_model"] = sysctl[0]
    for line in sysctl:
        if line.startswith("cores:"):
            snap["cpu_cores"] = _to_int(line.split(":", 1)[1])
        elif line.startswith("memsize:"):
            size = _to_int(line.split(":", 1)[1])
            snap["ram_total_mb"] = size // 1048576 if size else None
    page_size, free_pages = None, None
    for line in sections.get("vm_stat") or []:
        m = re.search(r"page size of (\d+) bytes", line)
        if m:
            page_size = int(m.group(1))
        m = re.match(r"\s*Pages free:\s+(\d+)\.", line)
        if m:
            free_pages = int(m.group(1))
    if page_size and free_pages is not None:
        snap["ram_free_mb"] = free_pages * page_size // 1048576
    for line in sections.get("loadavg") or []:
        m = re.search(r"\{\s*([0-9.]+)", line)
        if m:
            snap["cpu_load_1m"] = float(m.group(1))
        break
    for line in sections.get("df") or []:
        parts = line.split()
        if len(parts) >= 6:
            avail_kb = _to_int(parts[3])
            snap["storage_free_gb"] = (round(avail_kb / 1048576, 1)
                                       if avail_kb is not None else None)
            snap["storage_root"] = parts[5]
    # GPUs: a `VRAM (Total)` line after a chipset makes it
    # discrete; an Apple chipset is unified memory (vram = the
    # machine's RAM pool, flagged shared — never a discrete
    # budget); anything else is recorded honestly as none.
    pending = None
    for line in sections.get("gpu") or []:
        m = re.search(r"Chipset Model:\s*(.+)$", line)
        if m:
            if pending:
                snap["gpus"].append(pending)
            name = m.group(1).strip()
            if "Apple" in name:
                pending = {"name": name,
                           "vram_mb": snap["ram_total_mb"],
                           "class": "unified", "vram_shared": True}
            else:
                pending = {"name": name, "vram_mb": None, "class": "none"}
            continue
        m = re.search(r"VRAM \(Total\):\s*(\d+)\s*GB", line)
        if m and pending is not None:
            pending["vram_mb"] = int(m.group(1)) * 1024
            pending["class"] = "discrete"
            pending.pop("vram_shared", None)
    if pending:
        snap["gpus"].append(pending)
    snap["venues"] = _probe_venues(sections.get("venues"))
    if "wsl" in snap["venues"]:
        snap["venues"].remove("wsl")  # WSL does not exist on macOS
    return snap


_PROBE_PARSERS = {"windows": parse_probe_windows,
                  "linux": parse_probe_linux,
                  "darwin": parse_probe_darwin}


def parse_probe_report(text):
    """One interface for all three platforms (FR-005): a probe
    report (any route) -> the normalized snapshot, or None
    when the report is not a probe report we can read."""
    platform, sections = _probe_sections(text)
    parser = _PROBE_PARSERS.get(platform or "")
    if parser is None:
        return None
    return parser(sections)


def store_capability(host, snapshot):
    """File a snapshot under the host's record in hosts.json,
    through the same verified-write discipline as liveness."""
    snap = dict(snapshot)
    snap["host"] = host
    snap["probed_at"] = now_iso()
    state = load_hosts_state()
    rec = _host_record(state, host)
    rec["capability"] = snap
    save_hosts_state(state)
    return snap


def cmd_probe_host(args):
    host = args.host
    if host not in HOST_TYPE:
        print("ERROR: unknown host '%s' (configured: %s)"
              % (host, ", ".join(sorted(HOST_TYPE))))
        return 2
    if getattr(args, "ingest", None):
        try:
            with open(args.ingest, "r", encoding="utf-8",
                      errors="replace") as f:
                text = f.read()
        except OSError as e:
            print("ERROR: cannot read probe report %s: %s"
                  % (args.ingest, e))
            return 2
        snap = parse_probe_report(text)
        if snap is None:
            print("ERROR: %s is not a readable SYNCYARD_PROBE report; "
                  "nothing stored" % args.ingest)
            return 3
        stored = store_capability(host, snap)
        ledger("capability_probed", host=host, route="ingest",
               platform=stored.get("platform"))
        hlog("PROBED %s (%s via ingest) at %s"
             % (host, stored.get("platform"), stored["probed_at"]))
        print("PROBED %s platform=%s cpus=%s ram_mb=%s gpus=%d "
              "venues=%s probed_at=%s"
              % (host, stored.get("platform"), stored.get("cpu_cores"),
                 stored.get("ram_total_mb"), len(stored.get("gpus") or []),
                 ",".join(stored.get("venues") or []),
                 stored["probed_at"]))
        return 0
    htype = HOST_TYPE[host]
    plat = {"windows": "windows", "wsl2": "linux"}.get(htype)
    if plat is None:
        fname = {"linux": "probe-linux.sh",
                 "macos": "probe-darwin.sh"}.get(htype, "probe script")
        print("REFUSED: this door has no transport workflow for "
              "%s hosts, so the probe cannot be dispatched. Run "
              "gatekeeper/probes/%s on the host and record it "
              "with: gk probe-host --host %s --ingest <report-file>"
              % (htype, fname, host))
        return 3
    fname, body = PROBE_SCRIPTS[plat]
    probes_dir = os.path.join(BASE, "probes")
    os.makedirs(probes_dir, exist_ok=True)
    script_path = os.path.join(probes_dir, "%s-%s" % (host, fname))
    _write_text_file(script_path, body)
    if htype == "windows":
        workflow = "run.yml"
        lane = next((n for n in sorted(LANES)
                     if LANE_HOST.get(n) == host
                     and route_check(n, workflow)), None)
    else:  # wsl2: the probe rides the via host's wsl.yml lane
        workflow = "wsl.yml"
        via = HOST_VIA.get(host)
        lane = next((n for n in sorted(LANES)
                     if LANE_HOST.get(n) == via
                     and route_check(n, workflow)), None)
    if lane is None:
        print("REFUSED: no lane serves %s for host '%s'; the probe "
              "cannot ride" % (workflow, host))
        return 3
    key = "%s%s-%s" % (PROBE_KEY_PREFIX, host,
                       now_dt().strftime("%Y%m%d%H%M%S"))
    ns = types.SimpleNamespace(
        program="syncyard", lane=lane, workflow=workflow,
        script=script_path, cwd=None, expected_min=5, key=key,
        grab_path=None, maintenance=True, image=None, mounts=None,
        notify_chat=None, priority=None)
    rc = cmd_submit(ns)
    if rc != 0:
        return rc
    job = find_active(load_jobs(), key=key)
    if job is not None:
        job["probe_host"] = host
        save_job(job)
    ledger("capability_probe_dispatched", host=host, key=key,
           job_id=job["id"] if job else None, lane=lane,
           workflow=workflow)
    print("PROBE QUEUED %s key=%s lane=%s workflow=%s — the snapshot "
          "lands when the probe job completes"
          % (host, key, lane, workflow))
    return 0


def _maybe_ingest_probe(job):
    """Watch-pass hook (FR-005): a completed probe job's
    harvested log is parsed and filed under its probed host.
    Mutates the job's ingest flags only; the caller's save
    persists them."""
    if not str(job.get("key") or "").startswith(PROBE_KEY_PREFIX):
        return
    if job.get("capability_ingested") or job.get("capability_ingest_failed"):
        return
    host = job.get("probe_host") or LANE_HOST.get(job.get("lane"))
    snap = parse_probe_report("\n".join(job.get("log_tail") or []))
    if snap is None or host is None:
        job["capability_ingest_failed"] = True
        ledger("capability_probe_unparseable", job_id=job["id"],
               key=job["key"], host=host)
        return
    stored = store_capability(host, snap)
    job["capability_ingested"] = True
    ledger("capability_probed", host=host, route="probe-job",
           job_id=job["id"], platform=stored.get("platform"))


# --- Controlled reboot (spec 011 FR-015) ------------------------------------
#
# `gk reboot --host <host>` requests a controlled reboot of a fleet
# host under the drain discipline. Route determination is reads-only
# and happens FIRST; with neither route live the host is reported
# unreachable and nothing is written but the ledger event. With
# active jobs on the host's lanes and no --force, the command
# refuses and names them (the polite path is `gk drain` or
# waiting). On acceptance the going-down record goes through the
# EXISTING ingest_going_down — annotations, dispatch hold, and
# outbox pushes are Phase 1 semantics exactly — then the request is
# ledgered, and only then is the host signaled:
#   route backchannel: the backchannel agent runs the host OS's
#     shutdown with a grace delay (the agent sits in the host's
#     WSL2 distro, so the Windows shutdown.exe is invoked through
#     its /mnt/c path);
#   route maintenance: a maintenance job carrying the same delayed
#     shutdown is submitted and the next sweep dispatches it (it
#     is exempt from the going-down hold it rides under — see
#     dispatch_pass).
# Exit codes: 0 signaled (or queued to signal), 3 refused (active
# jobs, no --force), 4 host unreachable, 1 the chosen route was
# live but the signal itself failed, 2 usage error.

REBOOT_KEY_PREFIX = "gk-reboot-"
REBOOT_SIGNAL_TEXT = "Syncyard gatekeeper reboot"
# A job in one of these states on one of the host's lanes makes
# the host busy for reboot purposes.
REBOOT_ACTIVE_STATES = ("submitted", "dispatching", "dispatched")


def _host_lanes(host):
    return [ln for ln in LANES if LANE_HOST.get(ln) == host]


def _host_active_jobs(host, jobs):
    lanes = set(_host_lanes(host))
    return [j for j in jobs if j.get("lane") in lanes
            and j["state"] in REBOOT_ACTIVE_STATES]


def _reboot_route(host):
    """Reads-only route determination. Returns (route, detail):
    ("backchannel", heartbeat) when the backchannel heartbeat is
    fresh (age <= BC_STALE_S) and actually proves this host;
    ("maintenance", online_runners) when at least one runner of
    the door repo is online; (None, None) when neither is live.

    Runner names are not part of config, so the mapping is honest
    about its limit: any online runner proves the lane-owning
    host (the host every lane names) is up; the maintenance route
    is viable only for that host."""
    hb, _err = fetch_backchannel_heartbeat()
    if hb is not None and host in heartbeat_hosts() \
            and not hb.get("stopped"):
        ts = parse_iso(hb.get("ts"))
        age = (now_dt() - ts).total_seconds() if ts else None
        if age is not None and age <= BC_STALE_S:
            return "backchannel", hb
    data, err = gh_json(["api", "repos/%s/actions/runners" % REPO])
    if not err and data:
        online = [r for r in data.get("runners") or []
                  if r.get("status") == "online"]
        lane_hosts = {LANE_HOST.get(ln) for ln in LANES}
        if online and host in lane_hosts:
            return "maintenance", online
    return None, None


def _bc_client_path():
    """Locate the backchannel client (bc.py): $SYNCYARD_BC first,
    then the repo layout (../backchannel beside gatekeeper/),
    then the canonical project checkout."""
    cands = []
    if os.environ.get("SYNCYARD_BC"):
        cands.append(os.environ["SYNCYARD_BC"])
    cands.append(os.path.join(BASE, "..", "backchannel", "bc.py"))
    cands.append(os.path.expanduser(
        "~/workspace/syncyard-build/backchannel/bc.py"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def cmd_reboot(args):
    host = args.host
    delay = REBOOT_GRACE_S if args.delay is None else args.delay
    if delay < 0:
        print("ERROR: --delay must be >= 0 seconds")
        return 2
    # 1. Route determination FIRST (reads only).
    route, _detail = _reboot_route(host)
    if route is None:
        print("REBOOT UNREACHABLE host=%s" % host)
        ledger("host_reboot_unreachable", host=host,
               requested_by=args.requested_by)
        return 4
    # 2. Refusal: active jobs and no --force. No signal, no
    # going-down record — the drain (FR-007) or waiting is the
    # polite path.
    jobs = load_jobs()
    active = _host_active_jobs(host, jobs)
    if active and not args.force:
        print("REFUSED reboot: host %s has active jobs: %s"
              % (host, ", ".join(j["id"] for j in active)))
        ledger("host_reboot_refused", host=host,
               requested_by=args.requested_by,
               active_jobs=[j["id"] for j in active])
        return 3
    # 3. Acceptance, in pinned order: the going-down record
    # through the existing ingest (annotations + hold + outbox),
    # then the request ledger line, then the signal.
    ts = now_iso()
    record = {
        "host": host,
        "ts": ts,
        "trigger": "remote-reboot",
        "active_run_ids": [j["run_id"] for j in active
                           if j.get("run_id")],
        "supervised": [],
        "uncheckpointed": [j["id"] for j in active],
    }
    ingest_going_down(record, jobs=jobs)
    ledger("host_reboot_requested", host=host,
           requested_by=args.requested_by, route=route, delay_s=delay)
    # 4. Signal.
    if route == "backchannel":
        body = ("/mnt/c/Windows/System32/shutdown.exe /r /t %d "
                "/c \"%s\"" % (delay, REBOOT_SIGNAL_TEXT))
        bc = _bc_client_path()
        if bc is None:
            print("REBOOT SIGNAL FAILED host=%s route=backchannel: "
                  "backchannel client (bc.py) not found" % host)
            ledger("host_reboot_signal_failed", host=host, route=route,
                   error="bc.py not found")
            return 1
        rc, out, err = run_cmd([sys.executable, bc, "send", "--cmd",
                                body], timeout=120)
        if rc != 0:
            print("REBOOT SIGNAL FAILED host=%s route=backchannel "
                  "rc=%d: %s" % (host, rc, (err or out or "").strip()[:300]))
            ledger("host_reboot_signal_failed", host=host, route=route,
                   error=("bc send rc=%d" % rc))
            return 1
        hlog("HOST_REBOOT host=%s route=backchannel delay_s=%s ts=%s"
             % (host, delay, ts))
        print("REBOOT SIGNALED host=%s route=backchannel delay_s=%s "
              "— the going-down record is written and dispatch to "
              "the host is held until it returns (`gk host-resume %s`)"
              % (host, delay, host))
        return 0
    # Maintenance route: the signal is a normal maintenance job
    # through the existing submit machinery; the next sweep
    # dispatches it (hold-exempt, see dispatch_pass).
    lanes = _host_lanes(host)
    if not lanes:
        print("REBOOT SIGNAL FAILED host=%s route=maintenance: "
              "no lane is configured for this host" % host)
        ledger("host_reboot_signal_failed", host=host, route=route,
               error="no lane configured for host")
        return 1
    script_path = os.path.join(BASE, "reboot-signal-%s.ps1" % host)
    script = ("shutdown.exe /r /t %d /c \"%s\"; exit 0\n"
              % (delay, REBOOT_SIGNAL_TEXT))
    try:
        _write_text_file(script_path, script)
    except (IOError, OSError) as exc:
        print("REBOOT SIGNAL FAILED host=%s route=maintenance: "
              "could not write the signal script (%s)" % (host, exc))
        ledger("host_reboot_signal_failed", host=host, route=route,
               error=str(exc)[:200])
        return 1
    import types as _types
    sub_args = _types.SimpleNamespace(
        key="%s%s-%s" % (REBOOT_KEY_PREFIX, host,
                         now_dt().strftime("%Y%m%dT%H%M%SZ")),
        program="syncyard", workflow="run.yml", lane=lanes[0],
        script=script_path, grab_path=None, cwd=None, image=None,
        mounts=None, expected_min=1.0, maintenance=True,
        notify_chat=None)
    rc = cmd_submit(sub_args)
    if rc != 0:
        print("REBOOT SIGNAL FAILED host=%s route=maintenance: "
              "the signal job was not accepted (submit rc=%d)"
              % (host, rc))
        ledger("host_reboot_signal_failed", host=host, route=route,
               error="submit rc=%d" % rc)
        return 1
    hlog("HOST_REBOOT host=%s route=maintenance delay_s=%s ts=%s"
         % (host, delay, ts))
    print("REBOOT SIGNAL QUEUED host=%s route=maintenance delay_s=%s "
          "— the signal job rides the next sweep; the going-down "
          "record is written and dispatch to the host is held "
          "until it returns (`gk host-resume %s`)"
          % (host, delay, host))
    return 0


# --- Host drain (spec 011 FR-007) -------------------------------------------
#
# `gk drain --host <host>` is the deliberate, operator-invoked
# drain of ONE host: hold the host's dispatch, signal its
# running checkpoint-capable jobs through the spec 013 hold
# contract, wait out the checkpoint window (drain.window_s,
# per-lane overrides, FR-005), then print the verdict in plain
# language. The command never shuts anything down itself.
#
# Reconciliation with spec 013's operator drain (`gk pause
# --drain` / `gk focus --drain`), stated once, here: the two
# are different scopes of one idea and neither replaces the
# other. The 013 drain is pipeline-scoped and passive — it
# changes admission on the global pause/focus record and
# settles itself in the sweep when the non-admitted set stops
# executing; it signals nothing and holds no host. The 011
# drain is host-scoped and active — it sets a per-host drain
# hold in the liveness record (dispatch_pass treats it exactly
# like the FR-006 going-down hold), raises checkpoint holds
# on the host's running jobs, waits, and reports. They
# compose: admission is the conjunction of both (a job must
# pass the global gate AND its host's holds), and each clears
# only its own state — `gk resume` clears the global record,
# `gk host-resume <host>` clears the host's holds. The
# gatekeeper's own signal jobs (gk-hold-*, gk-unhold-*,
# gk-reboot-*) are exempt from host holds in dispatch_pass,
# or a drain could never deliver the signals it exists to
# raise.
#
# Exit codes: 0 = CLEAN verdict, 1 = INTERRUPTING verdict,
# 2 = usage error (unknown host).

DRAIN_TERMINAL_STATES = ("held_at_checkpoint", "completed",
                         "cancelled")


def drain_window_for_lane(lane):
    """The checkpoint window for one lane (FR-005): the
    per-lane override when configured, else the host window."""
    return DRAIN_PER_LANE_WINDOW_S.get(lane, DRAIN_WINDOW_S)


def _drain_tracked_jobs(host, jobs):
    """The jobs a host drain accounts for: non-maintenance jobs
    on the host's lanes that hold a live run. Maintenance jobs
    are operator mechanisms (the drain's own signal jobs among
    them) and are never signaled or counted; queued jobs are
    not interrupted — the drain hold simply keeps them waiting,
    and the verdict says how many are waiting."""
    lanes = set(_host_lanes(host))
    return [j for j in jobs if j.get("lane") in lanes
            and not j.get("maintenance")
            and j["state"] in RUN_HOLDING_STATES
            and j.get("run_status") != "completed"]


def _drain_probe(tracked):
    """One drain wait-loop poll: ask GitHub for each tracked
    run's status (one run-view call per still-running run) and
    feed the results through the SAME watch_pass the sweep
    uses, so a checkpoint harvest during a drain lands in the
    identical state, with the identical markers and ledger
    lines, as a sweep harvest (spec 013 FR-008)."""
    runs = {}
    for j in tracked:
        if j["state"] not in ("dispatched", "watching") \
                or not j.get("run_id"):
            continue
        data, err = gh_json(["run", "view", str(j["run_id"]),
                             "-R", REPO, "--json",
                             "status,conclusion,createdAt"])
        if err or not data:
            continue
        runs[str(j["run_id"])] = {
            "status": data.get("status"),
            "conclusion": data.get("conclusion"),
            "createdAt": data.get("createdAt")
            or j.get("run_created_at") or now_iso()}
    if runs:
        watch_pass(tracked, runs)


def cmd_drain(args):
    host = args.host
    if host not in _CFG["hosts"]:
        print("ERROR: unknown host '%s' (config hosts: %s)"
              % (host, ", ".join(sorted(_CFG["hosts"]))))
        return 2
    jobs = load_jobs()
    tracked = _drain_tracked_jobs(host, jobs)
    lanes = set(_host_lanes(host))
    queued = [j for j in jobs if j.get("lane") in lanes
              and j["state"] == "submitted"
              and not j.get("maintenance")]
    started = now_iso()
    if args.window is not None:
        windows = {j["id"]: float(args.window) for j in tracked}
    else:
        windows = {j["id"]: float(drain_window_for_lane(j["lane"]))
                   for j in tracked}
    ledger("host_drain_started", host=host,
           requested_by=args.requested_by, tracked=len(tracked),
           queued_waiting=len(queued))
    hlog("HOST_DRAIN host=%s tracked=%d at %s"
         % (host, len(tracked), started))
    # 1. Signal first: raise the checkpoint hold on every
    # tracked job whose workflow has a hold venue, through the
    # shared spec 013 helper, so the signal is byte-identical
    # to an operator `gk hold`. Jobs with no venue (grab) or a
    # signal that could not be queued are accounted by name in
    # the verdict — they cannot checkpoint under this drain.
    signal_failed = []
    for j in tracked:
        if j.get("workflow") not in CHECKPOINT_HOLDS_DIR:
            continue
        _sid, err = _request_checkpoint_hold(j)
        if err:
            signal_failed.append(j["id"])
            hlog("HOST_DRAIN signal failed host=%s job=%s: %s"
                 % (host, j["id"], err))
    # 2. The drain hold: dispatch to this host stops (the
    # gatekeeper's own signal jobs stay exempt, above). The
    # hold stands after the verdict until `gk host-resume`.
    state = load_hosts_state()
    rec = _host_record(state, host)
    rec["drain_hold"] = True
    rec["drain_since"] = started
    rec["drain_by"] = args.requested_by
    save_hosts_state(state)
    # 3. Wait out the windows. Each tracked job is classified
    # at its own lane's deadline (FR-005): checkpointed
    # (held_at_checkpoint), finished (completed), ended
    # (cancelled), or — still running at its deadline —
    # uncheckpointed. Terminal truth comes from the records,
    # refreshed by the same watch machinery the sweep runs.
    outcomes = {}
    pending = list(tracked)
    start_mono = time.monotonic()
    max_window = max(windows.values(), default=0.0)
    while pending:
        _drain_probe(pending)
        fresh = {j["id"]: j for j in load_jobs()}
        elapsed = time.monotonic() - start_mono
        still = []
        for j in pending:
            cur = fresh.get(j["id"], j)
            if cur["state"] in DRAIN_TERMINAL_STATES:
                outcomes[j["id"]] = cur["state"]
            elif elapsed >= windows[j["id"]]:
                outcomes[j["id"]] = "uncheckpointed"
            else:
                still.append(cur)
        pending = still
        if not pending:
            break
        if elapsed >= max_window:
            for j in pending:
                outcomes[j["id"]] = "uncheckpointed"
            break
        time.sleep(DRAIN_POLL_S if DRAIN_POLL_S > 0 else 0.05)
    # 4. The verdict, in plain language (FR-007).
    by_id = {j["id"]: j for j in tracked}
    checkpointed = [i for i, o in outcomes.items()
                    if o == "held_at_checkpoint"]
    finished = [i for i, o in outcomes.items() if o == "completed"]
    ended = [i for i, o in outcomes.items() if o == "cancelled"]
    uncp = [i for i, o in outcomes.items()
            if o == "uncheckpointed"]
    clean = not uncp
    ledger("host_drain_verdict", host=host,
           verdict="clean" if clean else "interrupting",
           checkpointed=checkpointed, finished=finished,
           ended=ended, uncheckpointed=uncp,
           signal_failed=signal_failed)
    print("DRAIN host=%s — tracked %d running job(s), %d queued "
          "job(s) waiting under the drain hold"
          % (host, len(tracked), len(queued)))
    for i in checkpointed:
        print("  CHECKPOINTED %s key=%s — stopped at its "
              "checkpoint; the owning program resubmits to "
              "continue" % (i, by_id[i].get("key")))
    for i in finished:
        print("  FINISHED %s key=%s — completed during the "
              "window; not interrupted" % (i, by_id[i].get("key")))
    for i in ended:
        print("  ENDED %s key=%s — cancelled during the window"
              % (i, by_id[i].get("key")))
    for i in uncp:
        j = by_id[i]
        if j.get("workflow") not in CHECKPOINT_HOLDS_DIR:
            why = ("it runs %s, which has no checkpoint hold "
                   "venue" % j.get("workflow"))
        elif i in signal_failed:
            why = "its hold signal could not be queued"
        else:
            why = ("it did not honor the checkpoint hold "
                   "inside its window (%gs)"
                   % windows[i])
        print("  UNCHECKPOINTED %s key=%s — %s; if the host "
              "goes down now this job is interrupted and its "
              "in-flight work is lost (resubmission is the "
              "owning program's call)" % (i, j.get("key"), why))
    print("  supervised work: none registered — the FR-008 "
          "vehicle is Phase 4, so this verdict covers "
          "gatekeeper jobs only")
    if clean:
        print("DRAIN CLEAN host=%s — nothing will be "
              "interrupted. Dispatch to the host stays held "
              "until `gk host-resume %s`." % (host, host))
        return 0
    print("DRAIN INTERRUPTING host=%s — %d job(s) named above "
          "had not checkpointed when their window expired. "
          "Dispatch to the host stays held until "
          "`gk host-resume %s`." % (host, len(uncp), host))
    return 1

def cmd_profile(args):
    """Spec 016 FR-001: render the profile table. Read-only —
    switching the active profile is an operator config edit
    (lanes.active_profile) that takes effect at the next
    sweep; there is deliberately no runtime verb for it."""
    if getattr(args, "suggest", False) \
            or getattr(args, "name", None) == "suggest":
        return cmd_profile_suggest(args)
    profiles = _CFG.get("lane_profiles") or {}
    if getattr(args, "list", False):
        for name in sorted(profiles):
            prof = profiles[name] or {}
            enabled = sum(1 for l in prof.values()
                          if isinstance(l, dict) and l.get("enabled", True))
            print("%s%s — %d lanes (%d enabled)"
                  % (name, " (active)" if name == ACTIVE_PROFILE else "",
                     len(prof), enabled))
        return 0
    name = getattr(args, "name", None) or ACTIVE_PROFILE
    prof = profiles.get(name)
    if prof is None:
        print("ERROR: unknown profile '%s' (configured: %s)"
              % (name, ", ".join(sorted(profiles))))
        return 2
    print("profile %s%s — %d lanes"
          % (name, " (active)" if name == ACTIVE_PROFILE else "",
             len(prof)))
    print("  (switching profiles is a config edit: "
          "lanes.active_profile; it takes effect at the next sweep, "
          "running runs untouched)")
    for lname in sorted(prof):
        lane = prof[lname] or {}
        band = lane.get("duration_band") or [None, None]
        print("  %-12s host=%-16s class=%-4s conc=%d band=[%s, %s] "
              "enabled=%s queued=%s%s"
              % (lname, lane.get("host"),
                 lane.get("resource_class", "any"),
                 lane.get("concurrency", 1), band[0], band[1],
                 lane.get("enabled", True), lane.get("queued"),
                 (" pin=" + str(lane["workflow"]))
                 if lane.get("workflow") else ""))
    return 0


# --- Profile suggest (spec 016 follow-on) ---------------------------
# `gk profile suggest` drafts a lane profile for one host from
# its recorded capability snapshot (FR-005) and prints the
# reasoning beside every choice. It is a drafting aid only:
# it never edits the config, never writes hosts.json, and
# never switches the active profile. The one file it may write
# is an explicit --out path, refused when the path already
# exists or is the live config file. FR-001's rule stands:
# a profile takes effect only when an operator merges it into
# the config and the next process loads it.

# The suggestion thresholds. Deterministic, stated policy:
# a normal lane is suggested only when the snapshot measures
# at least this many cores and at least this much RAM.
SUGGEST_NORMAL_MIN_CORES = 8
SUGGEST_NORMAL_MIN_RAM_MB = 16 * 1024

# Lane templates when the configured fleet-default profile
# does not carry a same-named lane: these mirror the
# fleet-default lanes in config.py's DEFAULTS verbatim
# (host and concurrency are set by the suggestion itself).
_SUGGEST_FALLBACK = {
    "quick": {"runner_label": "lane-quick", "resource_class": "cpu",
              "duration_band": [0, 10], "queued": [10, 30],
              "executing": {"mode": "fixed", "values": [5, 15]}},
    "normal": {"runner_label": "lane-normal", "resource_class": "cpu",
               "duration_band": [0, 45], "queued": [20, 45],
               "executing": {"mode": "fixed", "values": [45, 90]}},
    "long": {"runner_label": "lane-long", "resource_class": "cpu",
             "duration_band": [30, None], "queued": [45, 120],
             "executing": {"mode": "expectation", "floor": [90, 180]}},
    "gpu": {"runner_label": "lane-gpu", "resource_class": "gpu",
            "duration_band": [0, None], "queued": [30, 90],
            "executing": {"mode": "expectation", "floor": [120, 240]}},
}


def _suggest_template(lane_name):
    """The fleet-default profile's lane of this name, or the
    mirrored fallback when the profile does not carry it."""
    prof = (_CFG.get("lane_profiles") or {}).get("fleet-default") or {}
    lane = prof.get(lane_name)
    if isinstance(lane, dict):
        return lane
    return _SUGGEST_FALLBACK[lane_name]


def _suggested_lane(lane_name, host):
    """One suggested lane: the fleet-default template's class,
    band, and watermarks, bound to the suggested host, with
    concurrency always 1 (raising it is a human edit)."""
    t = _suggest_template(lane_name)
    ex = t.get("executing") or {}
    return {
        "host": host,
        "runner_label": t.get("runner_label") or ("lane-" + lane_name),
        "concurrency": 1,
        "resource_class": t.get("resource_class", "any"),
        "duration_band": list(t.get("duration_band") or [None, None]),
        "queued": list(t.get("queued") or [20, 45]),
        "executing": dict(ex) if ex else {"mode": "fixed",
                                          "values": [45, 90]},
    }


def _fmt_measured(value, suffix=""):
    """A measured value rendered for reasoning, or 'unknown'.
    Unknown is a stated fact in this system, never a zero."""
    return "unknown" if value is None else "%s%s" % (value, suffix)


def _suggest_yaml_value(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _suggest_lane_yaml(lane_name, lane, indent):
    pad = " " * indent
    ex = lane["executing"]
    lines = [
        "%s%s:" % (pad, lane_name),
        "%s  host: %s" % (pad, lane["host"]),
        "%s  runner_label: %s" % (pad, lane["runner_label"]),
        "%s  concurrency: %d" % (pad, lane["concurrency"]),
        "%s  resource_class: %s" % (pad, lane["resource_class"]),
        "%s  duration_band: [%s, %s]"
        % (pad, _suggest_yaml_value(lane["duration_band"][0]),
           _suggest_yaml_value(lane["duration_band"][1])),
        "%s  queued: [%s, %s]"
        % (pad, lane["queued"][0], lane["queued"][1]),
        "%s  executing:" % pad,
        "%s    mode: %s" % (pad, ex.get("mode")),
    ]
    if ex.get("mode") == "expectation":
        lines.append("%s    floor: [%s, %s]"
                     % (pad, ex["floor"][0], ex["floor"][1]))
    else:
        lines.append("%s    values: [%s, %s]"
                     % (pad, ex["values"][0], ex["values"][1]))
    return lines


def build_profile_suggestion(host, snap):
    """(lanes, reasoning) for one host's fresh snapshot.

    lanes is an ordered dict of suggested lane name to lane
    mapping; reasoning is a list of plain sentences, one per
    decision, each naming the measured values it rests on.
    Pure function of the snapshot: the same snapshot always
    drafts the same profile. Unknown fields justify nothing:
    a lane whose bar cannot be proven from measured values is
    not suggested, and the reasoning says so."""
    cores = snap.get("cpu_cores")
    ram_mb = snap.get("ram_total_mb")
    gpus = snap.get("gpus")
    measured_gpus = [g for g in (gpus or []) if isinstance(g, dict)
                     and (g.get("name")
                          or g.get("vram_mb") is not None)]
    lanes = {}
    reasoning = []

    lanes["quick"] = _suggested_lane("quick", host)
    reasoning.append(
        "quick: suggested. Every host gets a quick lane for "
        "short work; this one does not depend on measured values.")

    if cores is not None and ram_mb is not None \
            and cores >= SUGGEST_NORMAL_MIN_CORES \
            and ram_mb >= SUGGEST_NORMAL_MIN_RAM_MB:
        lanes["normal"] = _suggested_lane("normal", host)
        reasoning.append(
            "normal: suggested. The snapshot measures cpu_cores=%d "
            "(>= %d) and ram_total_mb=%d (%d GiB, >= %d GiB), so "
            "the host can carry a mid-size lane beside quick."
            % (cores, SUGGEST_NORMAL_MIN_CORES, ram_mb,
               ram_mb // 1024, SUGGEST_NORMAL_MIN_RAM_MB // 1024))
    else:
        why = []
        if cores is None:
            why.append("cpu_cores is unknown, and unknown never "
                       "counts as meeting the bar")
        elif cores < SUGGEST_NORMAL_MIN_CORES:
            why.append("cpu_cores=%d is below %d"
                       % (cores, SUGGEST_NORMAL_MIN_CORES))
        if ram_mb is None:
            why.append("ram_total_mb is unknown, and unknown never "
                       "counts as meeting the bar")
        elif ram_mb < SUGGEST_NORMAL_MIN_RAM_MB:
            why.append("ram_total_mb=%d (%d GiB) is below %d GiB"
                       % (ram_mb, ram_mb // 1024,
                          SUGGEST_NORMAL_MIN_RAM_MB // 1024))
        reasoning.append("normal: not suggested. " + "; ".join(why)
                         + ".")

    lanes["long"] = _suggested_lane("long", host)
    reasoning.append(
        "long: suggested. Every host gets a long lane with "
        "expectation-mode watermarks for work that outgrows the "
        "other bands.")

    if measured_gpus:
        lanes["gpu"] = _suggested_lane("gpu", host)
        big = max(measured_gpus,
                  key=lambda g: g.get("vram_mb") or 0)
        vram = ("vram_mb=%d" % big["vram_mb"]
                if big.get("vram_mb") is not None
                else "VRAM unknown")
        shared = (" The GPU is unified memory, so that VRAM is "
                  "the shared memory pool, not a discrete budget."
                  if big.get("class") == "unified" else "")
        reasoning.append(
            "gpu: suggested. The snapshot measures %d GPU(s); the "
            "largest is %s with %s (class=%s).%s"
            % (len(measured_gpus), big.get("name") or "an unnamed "
               "GPU", vram, big.get("class") or "unknown", shared))
    elif gpus is None:
        reasoning.append(
            "gpu: not suggested. The snapshot records no GPU "
            "information at all, so GPU presence is unknown; "
            "unknown is never treated as a GPU (and never as "
            "zero either). Probe again with a GPU-aware tool if "
            "this host has one.")
    elif len(gpus) == 0:
        reasoning.append(
            "gpu: not suggested. The probe measured no GPU on "
            "this host.")
    else:
        reasoning.append(
            "gpu: not suggested. The snapshot's GPU entries carry "
            "no measured name or VRAM, so GPU presence is "
            "unknown; unknown is never treated as a GPU.")

    reasoning.append(
        "concurrency: 1 on every suggested lane. A suggestion is "
        "a starting draft; raising concurrency is a human edit.")
    return lanes, reasoning


def render_profile_suggestion(host, name, snap, age_min):
    """The draft as one text: the reasoning as YAML comments
    over a lanes.profiles fragment an operator can merge into
    syncyard.yaml by hand. Also returns nothing else; the
    caller decides where the text goes."""
    lanes, reasoning = build_profile_suggestion(host, snap)
    gpus = snap.get("gpus") or []
    gpu_desc = "; ".join(
        "%s vram_mb=%s class=%s"
        % (g.get("name") or "unnamed",
           _fmt_measured(g.get("vram_mb")),
           g.get("class") or "unknown")
        for g in gpus if isinstance(g, dict)) or "none measured"
    header = [
        "# gk profile suggest: a DRAFT lane profile for host "
        "'%s'." % host,
        "# Nothing was applied. This command never edits the "
        "config, never",
        "# writes hosts.json, and never switches the active "
        "profile. To use",
        "# the draft, merge the fragment below into syncyard.yaml "
        "under",
        "# lanes.profiles and set lanes.active_profile to '%s'."
        % name,
        "#",
        "# Source: the capability snapshot for host '%s' recorded "
        "in" % host,
        "# hosts.json, probed at %s (age %s min; the freshness "
        "bound is"
        % (snap.get("probed_at"),
           ("%d" % age_min) if age_min is not None else "unknown"),
        "# lanes.capability_max_age_min = %d min, and this snapshot "
        "is" % CAPABILITY_MAX_AGE_MIN,
        "# inside it).",
        "# Measured: platform=%s hostname=%s"
        % (snap.get("platform"), snap.get("hostname")),
        "#   cpu_cores=%s cpu_model=%s cpu_load_1m=%s"
        % (_fmt_measured(snap.get("cpu_cores")),
           snap.get("cpu_model") or "unknown",
           _fmt_measured(snap.get("cpu_load_1m"))),
        "#   ram_total_mb=%s ram_free_mb=%s"
        % (_fmt_measured(snap.get("ram_total_mb")),
           _fmt_measured(snap.get("ram_free_mb"))),
        "#   storage_free_gb=%s storage_root=%s"
        % (_fmt_measured(snap.get("storage_free_gb")),
           snap.get("storage_root") or "unknown"),
        "#   gpus: %s" % gpu_desc,
        "#   venues: %s"
        % (", ".join(snap.get("venues") or []) or "unknown"),
        "#",
        "# Reasoning (deterministic rules; the same snapshot "
        "always drafts",
        "# the same profile):",
    ]
    for line in reasoning:
        header.append("#   " + line)
    if name in (_CFG.get("lane_profiles") or {}):
        header.append("#")
        header.append("# Note: a profile named '%s' already exists "
                      "in the" % name)
        header.append("# config; rename this draft before merging "
                      "if both are kept.")
    header.append("# Watermarks and duration bands are copied from "
                  "the")
    header.append("# fleet-default profile's same-named lanes.")
    body = ["lanes:", "  profiles:", "    %s:" % name]
    for lane_name in ("quick", "normal", "long", "gpu"):
        if lane_name in lanes:
            body.extend(_suggest_lane_yaml(lane_name,
                                           lanes[lane_name], 6))
    return "\n".join(header + body) + "\n"


def _live_config_path():
    """The config file this process loaded (or would load):
    $SYNCYARD_CONFIG, else $SYNCARD_CONFIG, else the file
    beside this script. --out must never overwrite it."""
    for var in ("SYNCYARD_CONFIG", "SYNCARD_CONFIG"):
        path = os.environ.get(var)
        if path:
            return os.path.abspath(path)
    return os.path.join(BASE, _cfg_mod.CONFIG_FILENAME)


def cmd_profile_suggest(args):
    """Spec 016 follow-on: draft a lane profile for one host
    from its capability snapshot, with the reasoning printed
    beside every choice. Read-only against the config and
    hosts.json; the only write anywhere is an explicit --out
    file, refused when it already exists or is the live
    config. Refusals are exit 2, fail closed like the config
    loader: a suggestion is never drafted from a snapshot the
    routing rules would already call unusable."""
    host = getattr(args, "host", None)
    if host is None:
        hosts = sorted({lane["host"] for lane in _CFG["lanes"].values()})
        if len(hosts) == 1:
            host = hosts[0]
        else:
            print("ERROR: the active profile '%s' spans hosts %s; "
                  "name one with --host"
                  % (ACTIVE_PROFILE, ", ".join(hosts)))
            return 2
    if host not in HOST_TYPE:
        print("ERROR: unknown host '%s' (configured: %s)"
              % (host, ", ".join(sorted(HOST_TYPE))))
        return 2
    name = getattr(args, "profile_name", None)
    if name is None:
        candidate = "%s-suggested" % host
        name = candidate if re.fullmatch(r"[a-z][a-z0-9-]*",
                                         candidate) else "suggested"
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name or ""):
        print("ERROR: profile name '%s' must match [a-z][a-z0-9-]* "
              "(the config loader's rule)" % name)
        return 2
    state = load_hosts_state()
    snap, stale = capability_view(host, state)
    if snap is None:
        print("ERROR: no capability snapshot recorded for host "
              "'%s'; run `gk probe-host --host %s` first. A "
              "suggestion is never drafted from no evidence."
              % (host, host))
        return 2
    if stale:
        age = minutes_between(parse_iso(snap.get("probed_at")),
                              now_dt())
        print("ERROR: the capability snapshot for host '%s' is "
              "stale (probed at %s, age %s min, bound %d min); "
              "refresh it with `gk probe-host --host %s` and "
              "suggest again. A suggestion is never drafted from "
              "a snapshot routing would call degraded."
              % (host, snap.get("probed_at"),
                 ("%d" % age) if age is not None else "unknown",
                 CAPABILITY_MAX_AGE_MIN, host))
        return 2
    age = minutes_between(parse_iso(snap.get("probed_at")), now_dt())
    text = render_profile_suggestion(host, name, snap, age)
    out = getattr(args, "out", None)
    if out:
        target = os.path.abspath(out)
        if target == _live_config_path():
            print("ERROR: --out %s is the live config file; a "
                  "suggestion never overwrites the config. Pick "
                  "another path." % out)
            return 2
        if os.path.exists(target):
            print("ERROR: --out %s already exists; a suggestion "
                  "never overwrites a file. Pick another path or "
                  "remove it first." % out)
            return 2
        try:
            with open(target, "x", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            print("ERROR: cannot write --out %s: %s" % (out, e))
            return 2
        print("WROTE suggested profile '%s' for host '%s' to %s "
              "(a draft; nothing was applied)" % (name, host, out))
        return 0
    sys.stdout.write(text)
    return 0


def capability_status_lines(hosts_state=None):
    """Spec 016 FR-008: one line per configured host — the
    recorded capability snapshot and its freshness, or the
    honest unknown."""
    if hosts_state is None:
        hosts_state = load_hosts_state()
    lines = []
    for host in sorted(HOST_TYPE):
        snap, stale = capability_view(host, hosts_state)
        if snap is None:
            lines.append("  %s: unknown (no probe recorded — "
                         "`gk probe-host --host %s`)" % (host, host))
        else:
            lines.append(
                "  %s: platform=%s cpus=%s ram_total_mb=%s gpus=%d "
                "venues=%s probed_at=%s (%s)"
                % (host, snap.get("platform"), snap.get("cpu_cores"),
                   snap.get("ram_total_mb"),
                   len(snap.get("gpus") or []),
                   ",".join(snap.get("venues") or []),
                   snap.get("probed_at"), "STALE" if stale else "fresh"))
    return lines


def cmd_status(args):
    jobs = load_jobs()
    by_state = {}
    for j in jobs:
        by_state[j["state"]] = by_state.get(j["state"], 0) + 1
    print("GATEKEEPER STATUS %s" % now_iso())
    st = stop_state()
    if st is not None:
        print("!! STOPPED since %s — %s (sweeps no-op until `gk start`)"
              % (st.get("ts"), st.get("reason") or "no reason recorded"))
    prec = pause_record()
    if prec is not None:
        if prec["mode"] == "focused":
            print("!! FOCUSED since %s — %s (only jobs matching "
                  "[%s] dispatch; maintenance jobs still flow)"
                  % (prec.get("ts"),
                     prec.get("reason") or "no reason recorded",
                     selectors_text(prec)))
        else:
            print("!! PAUSED since %s — %s (program dispatch held; "
                  "maintenance jobs still flow)"
                  % (prec.get("ts"),
                     prec.get("reason") or "no reason recorded"))
        if prec.get("drain"):
            print("!! DRAINING since %s — executing jobs finish, "
                  "nothing new dispatches, then the pause settles"
                  % (prec.get("drain_since") or prec.get("ts")))
    print(backchannel_status_line())
    for line in host_status_lines():
        print(line)
    # Lane locks (spec 015 FR-009): rendered in all modes,
    # from the same file the dispatch pass enforces.
    locks_map = load_locks()
    for line in locks_section_lines(locks_map):
        print(line)
    # Adaptive lanes (spec 016 FR-008): the active profile and
    # every host's capability standing.
    print("profile: %s (%d lanes resolved%s)"
          % (ACTIVE_PROFILE, len(LANES),
             "; lane-less submits mean auto"
             if LANES_AUTO_DEFAULT else "; --lane auto opts in"))
    print("capability:")
    for line in capability_status_lines():
        print(line)
    print("jobs by state: %s" % (", ".join(
        "%s=%d" % (k, by_state[k]) for k in sorted(by_state)) or "none"))
    queued = [j for j in jobs if j.get("run_status") == "queued"
              or j["state"] in ("submitted", "dispatched")]
    running = [j for j in jobs if j.get("run_status") == "in_progress"]
    print("queued: %d   running: %d" % (len(queued), len(running)))
    print("")
    print("%-46s %-16s %-7s %-11s %-12s %s" % ("KEY", "PROGRAM", "LANE",
                                               "STATE", "RUN_STATUS",
                                               "RUN_ID / FLAGS"))
    for j in sorted(jobs, key=lambda x: x["created_at"]):
        if j["state"] in ("completed", "cancelled") and not j.get("run_id"):
            continue
        print("%-46s %-16s %-7s %-11s %-12s %s%s"
              % (j["key"][:46], j["program"][:16],
                 j.get("lane") or "auto", j["state"],
                 str(j.get("run_status")), str(j.get("run_id") or "-"),
                 ("  flags=" + ",".join(j["flags"])) if j["flags"] else ""))
    done = [j for j in jobs if j["state"] == "completed"]
    if done:
        print("\ncompleted:")
        for j in done:
            print("  %s run=%s conclusion=%s verdict_source=%s markers=%d"
                  % (j["key"], j.get("run_id"), j.get("run_conclusion"),
                     j.get("verdict_source"),
                     len(j.get("markers", []))))
    # Spec 013 FR-003/FR-006/FR-012: the waiting set in
    # effective dispatch order (the same key the sweep uses,
    # evaluated now), with priorities, sources, and each job's
    # standing under the current pause/focus record.
    now = now_dt()
    waiting = sorted([j for j in jobs if j["state"] == "submitted"],
                     key=lambda j: dispatch_sort_key(j, now))
    if waiting:
        print("\nwaiting (dispatch order):")
        for j in waiting:
            value, source = effective_priority(j, now)
            mark = ""
            if j.get("maintenance"):
                mark = " [maintenance: admitted]"
            elif prec is not None:
                sel = focus_match(j, prec)
                mark = (" [admitted via %s]" % sel) if sel \
                    else " [held]"
            lock_rec = locks_map.get(j["lane"])
            if lock_rec and lock_rec.get("state") == "active" \
                    and not j.get("maintenance") \
                    and j.get("program") != lock_rec.get("program"):
                mark += " [lock-held by %s]" % lock_rec.get("program")
            # Spec 016 FR-008: an auto job's standing — still
            # unplaced (with its last unmet reasons) or resolved
            # (with the recorded score and any degradation).
            if j.get("lane") is None:
                unmet = (j.get("auto_wait") or {}).get("unmet") or []
                mark += " [auto: no fit yet%s]" % (
                    (" — " + "; ".join(unmet[:2])) if unmet else "")
            elif j.get("auto_route"):
                snap = j["auto_route"]
                mark += " [auto→%s score=%s%s]" % (
                    j["lane"], snap.get("score"),
                    " degraded" if snap.get("degraded") else "")
            print("  pri=%-4s (%s) lane=%-7s key=%s%s"
                  % (value, source, j.get("lane") or "auto",
                     j["key"], mark))
    held_cp = [j for j in jobs if j["state"] == "held_at_checkpoint"]
    if held_cp:
        print("\nheld at checkpoint:")
        for j in held_cp:
            print("  %s run=%s conclusion=%s held_at=%s — resubmit "
                  "to continue" % (j["key"], j.get("run_id"),
                                    j.get("run_conclusion"),
                                    j.get("held_at_checkpoint_at")))
    issues = [(j, job_issues(j)) for j in jobs]
    issues = [(j, i) for (j, i) in issues if i]
    print("\nISSUES: %d" % len(issues))
    for j, iss in issues:
        for s in iss:
            print("  [%s] %s — %s" % (j["program"], j["key"], s))
    if os.path.isfile(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        print("\nlast ledger events:")
        for ln in lines[-10:]:
            try:
                e = json.loads(ln)
                print("  %s %-24s %s" % (e.get("ts"), e.get("event"),
                                         e.get("key") or e.get("run_id")
                                         or ""))
            except ValueError:
                print("  " + ln)
    if getattr(args, "repo", False):
        repo_health()
    return 0


# --- fleet snapshot (spec 014 FR-008, gatekeeper-emitted) -------------------

def snapshot_held(job, prec, stopped, held_hosts):
    """Whether a waiting job is held, by the sweep's own
    admission rules. Under a stop the sweep itself no-ops, so
    nothing dispatches and every waiting job is held,
    maintenance included. Under a pause/focus (spec 013
    FR-002) a job is held when no selector admits it, except
    maintenance jobs, which a pause never holds. A job whose
    lane's host carries a dispatch hold (spec 011 FR-006)
    waits too, except the gatekeeper's own signal jobs
    (FR-015, _signal_hold_exempt). Lane locks, the active
    cap, lane serialization and duplicate-script skips are
    pass state, not record state, and are not marks here —
    `gk status` likewise renders lock-held as a separate
    mark from held."""
    if stopped:
        return True
    held = False
    if prec is not None and not job.get("maintenance"):
        held = focus_match(job, prec) is None
    if LANE_HOST.get(job.get("lane")) in held_hosts \
            and not _signal_hold_exempt(job):
        held = True
    return held


def build_snapshot():
    """The spec 014 FR-008 fleet snapshot, derived from this
    gatekeeper's own state by its own code paths: the
    FR-001-normalized pause record (pause_record), the stop
    record (stop_state), the host-state file
    (load_hosts_state), the FR-012 dispatch order
    (dispatch_sort_key, evaluated once against a single
    instant, exactly as the dispatch pass orders), and the
    sweep's admission rule (snapshot_held). One derivation,
    two surfaces: `gk status` for the operator at the
    terminal, this JSON for the tray feed, transported by
    fleet_status.py (publisher v2). Pure reads; writes
    nothing."""
    stop = stop_state()
    prec = pause_record()
    held_hosts = {h for h, r in load_hosts_state().items()
                  if r.get("hold") or r.get("drain_hold")}
    if stop is not None:
        mode = "stopped"
        reason = stop.get("reason") or ""
    elif prec is not None:
        mode = prec["mode"]
        reason = prec.get("reason") or ""
    else:
        mode = "running"
        reason = ""
    focus = {"keys": [], "programs": [], "chats": []}
    draining = bool(held_hosts)
    if prec is not None:
        focus = {"keys": list(prec["except_keys"]),
                 "programs": list(prec["except_programs"]),
                 "chats": list(prec["except_chats"])}
        draining = draining or bool(prec.get("drain"))
    now = now_dt()
    waiting_jobs = sorted(
        [j for j in load_jobs() if j["state"] == "submitted"],
        key=lambda j: dispatch_sort_key(j, now))
    waiting = []
    for job in waiting_jobs:
        entry = {
            "key": job.get("key") or "",
            "program": job.get("program") or "",
            "lane": job.get("lane") or "",
            "held": snapshot_held(job, prec, stop is not None,
                                  held_hosts),
        }
        for opt in ("priority", "priority_source"):
            if job.get(opt) is not None:
                entry[opt] = job[opt]
        waiting.append(entry)
    return {
        "generated_at": now_iso(),
        "mode": mode,
        "draining": draining,
        "reason": reason,
        "focus": focus,
        "waiting": waiting,
    }


def cmd_snapshot(_args):
    """Print the FR-008 fleet snapshot as a single JSON
    object on stdout — the fleet-status publisher's primary
    source (publisher v2). Nothing else prints to stdout;
    unreadable job records warn on stderr as everywhere."""
    print(json.dumps(build_snapshot(), indent=2, sort_keys=True))
    return 0


# --- CLI ----------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="gk", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("submit")
    p.add_argument("--program", required=True)
    p.add_argument("--workflow", required=True, choices=WORKFLOWS)
    p.add_argument("--lane", choices=tuple(LANES) + ("auto",),
                   default=None,
                   help="lane to run on, or 'auto' (spec 016 FR-004): "
                        "the sweep picks the lane from the job's "
                        "resource hints, program history, queue load "
                        "and host capability. Omit only when "
                        "lanes.auto_default is true (then it means "
                        "auto)")
    p.add_argument("--script")
    p.add_argument("--grab-path")
    p.add_argument("--cwd")
    p.add_argument("--image",
                   help="container image for a docker-transport job "
                        "(defaults to the lane host's configured image)")
    p.add_argument("--mounts",
                   help="comma-separated host:container mount pairs "
                        "for a docker-transport job")
    p.add_argument("--needs-cpu", type=int, default=None,
                   help="resource hint (spec 016 FR-003): minimum "
                        "CPU cores the job needs")
    p.add_argument("--needs-ram-mb", type=int, default=None,
                   help="resource hint (spec 016 FR-003): minimum "
                        "RAM in MB the job needs")
    p.add_argument("--needs-storage-mb", type=int, default=None,
                   help="resource hint (spec 016 FR-003): minimum "
                        "free storage in MB the job needs")
    p.add_argument("--needs-gpu", nargs="?", const=True, default=None,
                   type=int, metavar="VRAM_MB",
                   help="resource hint (spec 016 FR-003): the job "
                        "needs a GPU; optionally with a minimum "
                        "VRAM in MB")
    p.add_argument("--venue", choices=("auto", "native", "wsl", "docker"),
                   default="auto",
                   help="venue preference (spec 016 FR-003): "
                        "eligibility for auto routing; docker "
                        "requires --image, wsl requires a Windows "
                        "host serving wsl.yml")
    p.add_argument("--expected-min", type=float)
    p.add_argument("--key", required=True)
    p.add_argument("--maintenance", action="store_true",
                   help="operator maintenance job: dispatches even while "
                        "paused, bypassing the active cap and lane "
                        "serialization (never the duplicate check)")
    p.add_argument("--notify-chat", default=None,
                   help="chat id to push started/completed/escalated "
                        "updates for this job to (see `gk outbox`)")
    p.add_argument("--priority", type=int, default=None,
                   help="human-set dispatch priority (spec 013 "
                        "FR-003): higher dispatches first; setting "
                        "it pins the job against the optimizer")
    p.set_defaults(fn=cmd_submit)

    p = sub.add_parser("pause")
    p.add_argument("--reason", default="")
    p.add_argument("--drain", action="store_true",
                   help="drain first (spec 013 FR-005): executing "
                        "jobs finish, nothing new dispatches, then "
                        "the pause settles")
    p.set_defaults(fn=cmd_pause)

    p = sub.add_parser("focus",
                       help="pause all but the selected jobs "
                            "(spec 013 FR-001): only jobs matching "
                            "a selector dispatch")
    p.add_argument("--except-key", action="append", default=None,
                   help="job key or key prefix to keep flowing "
                        "(repeatable)")
    p.add_argument("--except-program", action="append", default=None,
                   help="program to keep flowing (repeatable)")
    p.add_argument("--except-chat", action="append", default=None,
                   help="notify-chat id whose jobs keep flowing "
                        "(repeatable)")
    p.add_argument("--reason", default="")
    p.add_argument("--drain", action="store_true",
                   help="drain toward the focus set (spec 013 "
                        "FR-005)")
    p.set_defaults(fn=cmd_focus)

    p = sub.add_parser("resume")
    p.add_argument("--job", default=None,
                   help="release one job's checkpoint hold "
                        "(spec 013 FR-009) instead of clearing "
                        "the pipeline pause")
    p.set_defaults(fn=cmd_resume)

    p = sub.add_parser("priority",
                       help="change a waiting job's priority "
                            "(spec 013 FR-004)")
    p.add_argument("job", help="job id or key (submitted jobs only)")
    p.add_argument("value", type=int,
                   help="new priority (higher dispatches first)")
    p.set_defaults(fn=cmd_priority)

    p = sub.add_parser("hold",
                       help="request a cooperative checkpoint hold "
                            "on a job (spec 013 FR-009)")
    p.add_argument("job", help="job id or key")
    p.set_defaults(fn=cmd_hold)

    p = sub.add_parser("lock",
                       help="acquire, renew, suspend or resume a "
                            "lane priority lock (spec 015), or "
                            "list the locks with --list")
    p.add_argument("--lane", choices=LANES, default=None,
                   help="lane to lock (required unless --list)")
    p.add_argument("--program", default=None,
                   help="the acting program: the holder when "
                        "acquiring; the actor when suspending, "
                        "resuming or (on unlock) releasing")
    p.add_argument("--ttl-min", type=float, default=None,
                   dest="ttl_min",
                   help="lock lifetime in minutes (default: "
                        "lock.default_ttl_min, 240; must be "
                        "within [1, lock.max_ttl_min])")
    p.add_argument("--reason", default="",
                   help="reason recorded on the lock")
    p.add_argument("--suspend", action="store_true",
                   help="suspend enforcement (spec 015 FR-005): "
                        "the lane flows, the claim stands, new "
                        "acquires by others stay refused")
    p.add_argument("--resume", action="store_true",
                   help="resume enforcement of a suspended lock "
                        "without re-competing for the lane")
    p.add_argument("--force", action="store_true",
                   help="operator override: suspend or resume "
                        "another program's lock (ledgered, and "
                        "the holder is notified)")
    p.add_argument("--list", action="store_true",
                   help="print the locks section and exit")
    p.set_defaults(fn=cmd_lock)

    p = sub.add_parser("unlock",
                       help="release a lane priority lock "
                            "(spec 015 FR-003): the holder "
                            "releases, or the operator overrides "
                            "with --force")
    p.add_argument("--lane", required=True, choices=LANES,
                   help="lane to release")
    p.add_argument("--program", default=None,
                   help="the acting program (must name the "
                        "holder, unless --force)")
    p.add_argument("--force", action="store_true",
                   help="operator override: release another "
                        "program's lock (ledgered distinctly; "
                        "the holder is notified)")
    p.set_defaults(fn=cmd_unlock)

    p = sub.add_parser("start")
    p.set_defaults(fn=cmd_start)

    p = sub.add_parser("stop")
    p.add_argument("--reason", default="")
    p.set_defaults(fn=cmd_stop)

    p = sub.add_parser("purge")
    p.add_argument("--older-than-days", type=float, default=None,
                   help="only purge terminal records created more than "
                        "N days ago (default: all terminal records)")
    p.add_argument("--ledger", action="store_true",
                   help="also archive ledger.jsonl and start a fresh one")
    p.add_argument("--yes", action="store_true",
                   help="actually delete (default is a dry run)")
    p.set_defaults(fn=cmd_purge)

    p = sub.add_parser("outbox")
    p.add_argument("--ack", action="store_true",
                   help="advance the outbox cursor to the current "
                        "ledger end (after the pushes are delivered)")
    p.set_defaults(fn=cmd_outbox)

    p = sub.add_parser("adopt")
    p.add_argument("--run-id", required=True)
    p.add_argument("--program", required=True)
    p.add_argument("--workflow", required=True, choices=WORKFLOWS)
    p.add_argument("--lane", required=True, choices=LANES)
    p.add_argument("--expected-min", type=float)
    p.add_argument("--key", required=True)
    p.add_argument("--notify-chat", default=None,
                   help="chat id to push started/completed/escalated "
                        "updates for this job to (see `gk outbox`)")
    p.set_defaults(fn=cmd_adopt)

    p = sub.add_parser("cancel")
    p.add_argument("job", help="job id or key (local only, pre-dispatch)")
    p.set_defaults(fn=cmd_cancel)

    p = sub.add_parser("sweep")
    p.set_defaults(fn=cmd_sweep)

    p = sub.add_parser("host-resume",
                       help="clear a host's dispatch holds — the "
                            "going-down hold (spec 011 FR-006) and "
                            "the drain hold (FR-007); Phase-1 "
                            "stand-in for the FR-010 recovery "
                            "handshake)")
    p.add_argument("host", help="config host name, e.g. desktop")
    p.set_defaults(fn=cmd_host_resume)

    p = sub.add_parser("reboot",
                       help="request a controlled reboot of a host "
                            "(spec 011 FR-015): refuses while jobs "
                            "are active unless --force; writes the "
                            "going-down record before signaling; "
                            "exit 3 = refused, 4 = host unreachable")
    p.add_argument("--host", required=True,
                   help="config host name, e.g. desktop")
    p.add_argument("--force", action="store_true",
                   help="reboot even with active jobs on the host "
                        "(they are annotated interrupted; nothing "
                        "is cancelled or re-dispatched)")
    p.add_argument("--delay", type=int, default=None,
                   help="grace delay in seconds before the host "
                        "goes down (default: reboot.grace_s, 60)")
    p.add_argument("--requested-by", default=None,
                   help="chat id requesting the reboot (recorded "
                        "in the ledger)")
    p.set_defaults(fn=cmd_reboot)

    p = sub.add_parser("drain",
                       help="drain one host (spec 011 FR-007): "
                            "hold its dispatch, signal its running "
                            "jobs to checkpoint, wait out the "
                            "window (drain.window_s / per-lane "
                            "overrides), and print the CLEAN / "
                            "INTERRUPTING verdict; exit 0 = CLEAN, "
                            "1 = INTERRUPTING, 2 = usage error. "
                            "Host-scoped: distinct from the "
                            "pipeline drain of `gk pause --drain` "
                            "(spec 013), which it composes with")
    p.add_argument("--host", required=True,
                   help="config host name, e.g. desktop")
    p.add_argument("--window", type=float, default=None,
                   help="checkpoint window in seconds for every "
                        "lane (default: config drain section)")
    p.add_argument("--requested-by", default=None,
                   help="chat id requesting the drain (recorded "
                        "in the ledger)")
    p.set_defaults(fn=cmd_drain)

    p = sub.add_parser("status")
    p.add_argument("--repo", action="store_true",
                   help="also show project-repo CI + Dependabot health")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("snapshot",
                       help="print the spec 014 FR-008 fleet "
                            "snapshot as one JSON object on "
                            "stdout, derived by the gatekeeper's "
                            "own ordering and admission code "
                            "(the fleet-status publisher's "
                            "primary source)")
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("profile",
                       help="render the lane profiles (spec 016 "
                            "FR-001); switching the active profile "
                            "is a config edit, not a runtime verb. "
                            "'gk profile suggest' drafts a new "
                            "profile from a host capability "
                            "snapshot (spec 016 follow-on) and "
                            "applies nothing")
    p.add_argument("--list", action="store_true",
                   help="list every profile with its lane count")
    p.add_argument("--show", action="store_true",
                   help="show one profile's lanes in detail")
    p.add_argument("--suggest", action="store_true",
                   help="draft a lane profile for one host from "
                        "its capability snapshot, with the "
                        "reasoning printed as comments; same as "
                        "'gk profile suggest'. Never applied")
    p.add_argument("--host", default=None,
                   help="suggest: the host whose capability "
                        "snapshot to draft from (default: the "
                        "active profile's host, when it has "
                        "exactly one)")
    p.add_argument("--name", "--profile-name", dest="profile_name",
                   default=None,
                   help="suggest: name for the drafted profile "
                        "(default: <host>-suggested)")
    p.add_argument("--out", default=None,
                   help="suggest: write the draft to this path "
                        "instead of stdout; refused when the "
                        "path exists or is the live config file")
    p.add_argument("name", nargs="?", default=None,
                   help="profile to show (default: the active "
                        "one); the word 'suggest' drafts a new "
                        "profile instead (see --suggest)")
    p.set_defaults(fn=cmd_profile)

    p = sub.add_parser("probe-host",
                       help="record a host capability snapshot "
                            "(spec 016 FR-005) for auto routing: "
                            "dispatch the probe through the door "
                            "where a transport exists, or --ingest "
                            "a probe report produced on the host")
    p.add_argument("--host", required=True)
    p.add_argument("--ingest", default=None,
                   help="path to a SYNCYARD_PROBE report file to "
                        "parse and store")
    p.set_defaults(fn=cmd_probe_host)

    args = ap.parse_args(argv)
    os.makedirs(QUEUE_DIR, exist_ok=True)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

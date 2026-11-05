#!/usr/bin/env python3
"""Fleet-status snapshot publisher (spec 014 FR-009, task T010).

Builds the snapshot the Syncyard tray's fleet section reads
(spec 014 FR-008 contract) from the gatekeeper's own state
files and publishes it into the backchannel repo, whose desktop
agent pulls it every cycle; the tray reads it from the agent's
clone through its `fleetStatusPath` setting. Owner gate opened
2026-11-03; transport = backchannel, as FR-009 anticipated.

Snapshot shape (mirrors spec 013's status vocabulary; the tray
shows an unrecognized mode verbatim, never blank):

    {
      "generated_at": ISO-8601 UTC,
      "mode": "running" | "paused" | "focused" | "stopped",
      "draining": bool,
      "reason": pause reason or "",
      "focus": {"keys": [...], "programs": [...], "chats": [...]},
      "waiting": [{"key", "program", "lane", "held",
                   optional "priority", "priority_source"}],
      "source": "gatekeeper" | "fallback"
    }

Publisher v2 (the spec 013 fleet deploy): the primary source
is the gatekeeper's own emission — `gk snapshot`, which
derives this shape from the gatekeeper's own normalized
pause record, its FR-012 dispatch ordering, its admission
marks, and the host-state file. One derivation, two surfaces
(terminal status and tray panel), permanently. This module
is then a thin transporter: it runs the snapshot command,
validates the JSON against the contract, labels it
"source": "gatekeeper", and publishes it verbatim. On ANY
failure of that path (no gatekeeper, nonzero exit, timeout,
invalid shape) it falls back to the state-file derivation
below, labeled "source": "fallback" — a dead gatekeeper is
exactly when the panel matters, and the label keeps the two
semantics from ever being silently mixed. The snapshot
command comes from $SYNCYARD_GK_SNAPSHOT_CMD when set, else
the gk.py beside this file; the fleet unit points the
variable at the fleet gatekeeper.

Mode, focus, and draining mirror the gatekeeper's own
derivations — spec 013 FR-001's pause-record normalization
(selector lists with the legacy singular `except_key` merged
in; mode is "focused" when any selector is present) and the
two drain records the system actually keeps:

- the pipeline drain flag, `drain: true` on the pause record
  (spec 013 FR-005, `gk pause --drain`), and
- a host dispatch hold in the host-state file (spec 011
  FR-006: a host that is going down takes no dispatches
  until its hold clears).

`held` is computed per waiting job with spec 013's admission
rule: under a stop every waiting job is held; under a
pause/focus a job is held when no selector admits it (key
equality-or-prefix, program, chat) — except maintenance
jobs, which a pause never holds. A job waiting only because
its lane's host is held is NOT marked held here: the
lane-to-host map lives in the gatekeeper config, this
publisher stays dependency-free, and draining=true carries
that signal. (In the fleet build a host hold is set in
exactly one place — the going-down ingest — and cleared in
exactly one place, host-resume, so `hold` alone is the
operative drain state.)

Fallback-path divergences from the gatekeeper's own
derivation, kept knowingly: waiting order is an approximation
of the sweep's effective dispatch order (jobs whose lane has
no active run first, then jobs behind a busy lane, submission
order inside each group — not the FR-012 effective-priority
order); `held` does not mark lane-host-held jobs (the
lane-to-host map lives in the gatekeeper config, and this
fallback stays dependency-free; draining=true carries that
signal); passed-through `priority` values are the records'
nominal values, as on the primary path. The "source" label
is what keeps a reader from mistaking any of this for the
gatekeeper's own marks.

Single-shot: run on a cadence (the tray's default staleness
bound is 300 s; a 2-minute publish cadence plus the agent's
~60 s pull keeps the displayed age comfortably inside it).
Publishing goes through the GitHub contents API, the same
channel bc.py uses: one GET for the current sha plus one PUT
per cycle.

Multi-channel (spec 024): --path is repeatable; one snapshot
is built once and published byte-identically to every
configured path, one per machine channel
(channels/<channel>/fleet-status.json), so every host's tray
can read its fleet panel from its own channel copy. With no
--path the historical single default is published. The API
base can be overridden with the FLEET_STATUS_API_BASE
environment variable (a GitHub Enterprise base, or a local
stand-in for install tests); the default is unchanged.
"""

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_REPO = "tbitcs/syncyard-backchannel"
DEFAULT_PATH = "channels/syncyard-dev/fleet-status.json"
API_BASE = "https://api.github.com"


def _api_base():
    return os.environ.get("FLEET_STATUS_API_BASE") or API_BASE


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _normalize_pause(raw):
    """Spec 013 FR-001's normalization of a stored pause
    record: selector lists as strings, the legacy singular
    `except_key` merged into the key list, drain as a bool.
    `raw` is the parsed record dict ({} when the file exists
    but does not parse — the gatekeeper treats that as paused
    with no fields, and so does this view)."""
    keys = [str(k) for k in (raw.get("except_keys") or [])]
    legacy = raw.get("except_key")
    if legacy and str(legacy) not in keys:
        keys.append(str(legacy))
    return {
        "keys": keys,
        "programs": [str(p) for p in (raw.get("except_programs") or [])],
        "chats": [str(c) for c in (raw.get("except_chats") or [])],
        "drain": bool(raw.get("drain")),
        "reason": raw.get("reason") or "",
    }


def _focus_admits(job, focus):
    """Spec 013 FR-002's selector match: key on equality or
    prefix, program and chat (the job's notify_chat) on
    equality."""
    key = str(job.get("key") or "")
    for sel in focus["keys"]:
        if key == sel or key.startswith(sel):
            return True
    program = job.get("program")
    if program and str(program) in focus["programs"]:
        return True
    chat = job.get("notify_chat")
    if chat and str(chat) in focus["chats"]:
        return True
    return False


def _host_draining(gk_dir):
    """True when any host in the host-state file carries a
    dispatch hold (spec 011 FR-006). The file's `going_down`
    field is the last ingested record and can outlive its
    hold as history, so the live signal is `hold` alone."""
    hosts = _read_json(os.path.join(gk_dir, "hosts.json")) or {}
    return any(isinstance(h, dict) and h.get("hold")
               for h in hosts.values())


def build_snapshot(gk_dir):
    """The FR-008 snapshot dict from the gatekeeper state in
    gk_dir (PAUSED.json / STOPPED.json / hosts.json /
    queue/*.json). This is publisher v2's FALLBACK derivation,
    used only when the gatekeeper's own emission is
    unavailable; obtain_snapshot labels its output
    accordingly."""
    stop_path = os.path.join(gk_dir, "STOPPED.json")
    stopped = os.path.isfile(stop_path)
    pause_path = os.path.join(gk_dir, "PAUSED.json")
    paused = os.path.isfile(pause_path)
    pause = _normalize_pause(_read_json(pause_path) or {}) \
        if paused else None
    if stopped:
        mode = "stopped"
    elif pause is not None:
        focused = bool(pause["keys"] or pause["programs"]
                       or pause["chats"])
        mode = "focused" if focused else "paused"
    else:
        mode = "running"
    focus = {"keys": [], "programs": [], "chats": []}
    draining = _host_draining(gk_dir)
    reason = ""
    if stopped:
        # The displayed reason belongs to the state in force:
        # a stopped fleet shows the STOP record's reason,
        # never a stale pause's.
        stop_rec = _read_json(stop_path) or {}
        reason = stop_rec.get("reason") or ""
    if pause is not None:
        focus = {"keys": pause["keys"], "programs": pause["programs"],
                 "chats": pause["chats"]}
        draining = draining or pause["drain"]
        if not stopped:
            reason = pause["reason"]
    waiting = []
    queue_dir = os.path.join(gk_dir, "queue")
    jobs = []
    busy_lanes = set()
    if os.path.isdir(queue_dir):
        for name in os.listdir(queue_dir):
            if not name.endswith(".json"):
                continue
            job = _read_json(os.path.join(queue_dir, name))
            if not job:
                continue
            if job.get("state") == "submitted":
                jobs.append(job)
            elif job.get("state") in ("dispatching", "dispatched",
                                      "watching"):
                busy_lanes.add(job.get("lane") or "")
    # Free lanes first (they can dispatch on the next sweep),
    # submission order inside each group — see the docstring
    # for what this approximation does not replicate.
    jobs.sort(key=lambda j: (j.get("lane") in busy_lanes,
                             j.get("created_at") or "",
                             j.get("key") or ""))
    for job in jobs:
        if stopped:
            held = True
        elif pause is not None:
            held = not job.get("maintenance") \
                and not _focus_admits(job, focus)
        else:
            held = False
        entry = {
            "key": job.get("key") or "",
            "program": job.get("program") or "",
            "lane": job.get("lane") or "",
            "held": held,
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


SNAPSHOT_CMD_ENV = "SYNCYARD_GK_SNAPSHOT_CMD"
SNAPSHOT_TIMEOUT_S = 30


def snapshot_command():
    """Argv for the gatekeeper-emitted snapshot (publisher
    v2's primary source): $SYNCYARD_GK_SNAPSHOT_CMD when set
    and parseable, else the gk.py beside this file. An
    unparseable value resolves to the default rather than
    breaking the cycle — the run's failure handling would
    only fall back anyway, and the default is the honest
    first try in the co-located layout."""
    raw = os.environ.get(SNAPSHOT_CMD_ENV)
    if raw:
        try:
            argv = shlex.split(raw)
        except ValueError:
            argv = []
        if argv:
            return argv
    return [sys.executable,
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "gk.py"),
            "snapshot"]


def _valid_snapshot(obj):
    """Structural check of a gatekeeper-emitted snapshot
    against the FR-008 contract: the required keys with the
    required types. Anything less is not transported — the
    fallback derivation serves instead, labeled."""
    if not isinstance(obj, dict):
        return False
    if not isinstance(obj.get("generated_at"), str):
        return False
    if not isinstance(obj.get("mode"), str):
        return False
    if not isinstance(obj.get("draining"), bool):
        return False
    if not isinstance(obj.get("reason"), str):
        return False
    focus = obj.get("focus")
    if not isinstance(focus, dict):
        return False
    for name in ("keys", "programs", "chats"):
        val = focus.get(name)
        if not isinstance(val, list) \
                or not all(isinstance(x, str) for x in val):
            return False
    waiting = obj.get("waiting")
    if not isinstance(waiting, list):
        return False
    for entry in waiting:
        if not isinstance(entry, dict):
            return False
        for name in ("key", "program", "lane"):
            if not isinstance(entry.get(name), str):
                return False
        if not isinstance(entry.get("held"), bool):
            return False
    return True


def obtain_snapshot(gk_dir):
    """The snapshot to publish, with its derivation labeled
    in the top-level `source` field: the gatekeeper's own
    emission ("gatekeeper") when the snapshot command exits
    0 inside the timeout and its stdout validates against
    the contract; this module's state-file derivation
    ("fallback") on any failure — missing gatekeeper,
    nonzero exit, timeout, unparseable or invalid shape.
    The gatekeeper's JSON is transported verbatim apart
    from the added label."""
    snap = None
    try:
        out = subprocess.run(snapshot_command(), capture_output=True,
                             text=True, timeout=SNAPSHOT_TIMEOUT_S)
        if out.returncode == 0:
            parsed = json.loads(out.stdout)
            if _valid_snapshot(parsed):
                snap = parsed
    except Exception:
        snap = None
    if snap is None:
        snap = build_snapshot(gk_dir)
        snap["source"] = "fallback"
    else:
        snap["source"] = "gatekeeper"
    return snap


class ContentsAPI:
    """Minimal GitHub contents-API client (same shape as
    backchannel/bc.py's; kept standalone so this publisher has
    no cross-tree import)."""

    def __init__(self, repo=DEFAULT_REPO, token=None):
        self.repo = repo
        self.token = token if token is not None else self._find_token()

    @staticmethod
    def _find_token():
        for var in ("GH_TOKEN", "GITHUB_TOKEN"):
            val = os.environ.get(var)
            if val:
                return val
        try:
            out = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True,
                timeout=15)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
        return None

    def _request(self, method, path, payload=None):
        url = "%s/repos/%s/contents/%s" % (
            _api_base(), self.repo, urllib.parse.quote(path))
        data = None
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                return None
            raise

    def get_sha(self, path):
        res = self._request("GET", path)
        return res.get("sha") if res else None

    def core_remaining(self):
        """Core REST budget remaining, from the rate_limit
        endpoint (which does not itself consume budget).
        Returns None when the check itself fails — a failed
        preflight must not block publishing."""
        url = _api_base() + "/rate_limit"
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                data = json.loads(res.read().decode("utf-8"))
            return int(data["resources"]["core"]["remaining"])
        except Exception:
            return None

    def put_file(self, path, content_bytes, message, sha=None):
        payload = {
            "message": message,
            "content": base64.b64encode(content_bytes).decode("ascii"),
        }
        if sha:
            payload["sha"] = sha
        return self._request("PUT", path, payload)


BUDGET_FLOOR = 1000  # the sweep's stand-down line, mirrored


class BudgetSkip(Exception):
    """The shared GitHub budget is below the fleet floor;
    publishing waits for a later cycle (the rate_limit check
    itself is free, so a skip costs no budget)."""

    def __init__(self, remaining):
        super().__init__("core remaining %s" % remaining)
        self.remaining = remaining


def publish(gk_dir, repo=DEFAULT_REPO, path=DEFAULT_PATH, api=None,
            paths=None):
    """Build the snapshot once and put it at every target path
    in repo (a single path, or one per machine channel when
    paths is given — the bodies are byte-identical, same
    generated_at). Returns the snapshot dict. Each path
    retries once on a sha conflict: the blob sha is that
    file's alone, so a 409/422 means another publisher run
    (an operator's manual run, an install's gate publish)
    landed between our GET and PUT. Any other failure
    propagates — a publish that did not land is never
    reported as one that did."""
    api = api or ContentsAPI(repo=repo)
    remaining = api.core_remaining()
    if remaining is not None and remaining < BUDGET_FLOOR:
        raise BudgetSkip(remaining)
    snap = obtain_snapshot(gk_dir)
    body = (json.dumps(snap, indent=2, sort_keys=True) + "\n").encode("utf-8")
    message = ("fleet-status: %s mode=%s waiting=%d"
               % (snap["generated_at"], snap["mode"],
                  len(snap["waiting"])))
    for target in (list(paths) if paths else [path]):
        for attempt in (1, 2):
            sha = api.get_sha(target)
            try:
                api.put_file(target, body, message, sha=sha)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (409, 422) and attempt == 1:
                    continue
                raise
        else:
            raise RuntimeError(
                "publish retry loop exited without a result")
    return snap


STATE_NAME = ".fleet-status-publisher.json"
LOCK_NAME = ".fleet-status-publisher.lock"
LOCK_STALE_S = 600


def _load_pub_state(gk_dir):
    return _read_json(os.path.join(gk_dir, STATE_NAME)) or {}


def _save_pub_state(gk_dir, state):
    """Best-effort: the publisher's own health record (failure
    count for the daily health check and the recovery note on
    the next success). A failed state write never blocks or
    fails a publish."""
    try:
        with open(os.path.join(gk_dir, STATE_NAME), "w",
                  encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
    except OSError:
        pass


def _acquire_lock(gk_dir):
    """Single-publisher lock (the timer prevents its own
    overlap; this stops an operator's manual run racing it).
    Returns the lock path, or None when a fresh lock stands.
    A lock older than LOCK_STALE_S is a dead run's and is
    taken over."""
    path = os.path.join(gk_dir, LOCK_NAME)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, ("%d %s\n" % (os.getpid(), now_iso())).encode())
            os.close(fd)
            return path
        except FileExistsError:
            try:
                age = datetime.now(timezone.utc).timestamp() \
                    - os.path.getmtime(path)
            except OSError:
                age = LOCK_STALE_S + 1
            if age <= LOCK_STALE_S:
                return None
            try:
                os.unlink(path)
            except OSError:
                return None
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gk-dir", default=os.path.dirname(
        os.path.abspath(__file__)),
        help="gatekeeper state directory (default: beside this file)")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--path", action="append", default=None,
                    help="contents path to publish to; repeatable, "
                         "one per machine channel (default: %s)"
                         % DEFAULT_PATH)
    ap.add_argument("--print-only", action="store_true",
                    help="print the snapshot; publish nothing")
    args = ap.parse_args(argv)
    paths = args.path or [DEFAULT_PATH]
    if args.print_only:
        print(json.dumps(obtain_snapshot(args.gk_dir), indent=2,
                         sort_keys=True))
        return 0
    lock = _acquire_lock(args.gk_dir)
    if lock is None:
        print("SKIPPED_LOCKED another publisher run holds the lock")
        return 0
    try:
        try:
            snap = publish(args.gk_dir, repo=args.repo, paths=paths)
        except BudgetSkip as skip:
            print("SKIPPED_BUDGET core_remaining=%s (floor %d)"
                  % (skip.remaining, BUDGET_FLOOR))
            return 0
        except Exception as exc:
            state = _load_pub_state(args.gk_dir)
            state["consecutive_failures"] = \
                int(state.get("consecutive_failures") or 0) + 1
            state["last_error"] = "%s: %s" % (type(exc).__name__, exc)
            state["last_failure_at"] = now_iso()
            _save_pub_state(args.gk_dir, state)
            raise
        state = _load_pub_state(args.gk_dir)
        recovered = int(state.get("consecutive_failures") or 0)
        state["consecutive_failures"] = 0
        state["last_success"] = snap["generated_at"]
        _save_pub_state(args.gk_dir, state)
        line = ("PUBLISHED %s mode=%s waiting=%d source=%s"
                % (snap["generated_at"], snap["mode"],
                   len(snap["waiting"]), snap.get("source") or "?"))
        if len(paths) > 1:
            line += " paths=%d" % len(paths)
        if recovered:
            line += " recovered_after=%d" % recovered
        print(line)
        return 0
    finally:
        try:
            os.unlink(lock)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())

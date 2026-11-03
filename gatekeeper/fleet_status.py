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
      "mode": "running" | "paused" | "stopped",
      "draining": false,
      "reason": pause reason or "",
      "focus": {"keys": [...], "programs": [...], "chats": [...]},
      "waiting": [{"key", "program", "lane",
                   optional "priority", "priority_source", "held"}]
    }

`draining` is reported false by this publisher: drain state
(spec 011 phases 2-3) lives on the host side in the current
fleet build and the gatekeeper holds no drain record of its
own; reporting a guess would violate the contract's honesty
rule. When a gatekeeper-side drain record exists, this is
where it gets read.

Single-shot: run on a cadence (the tray's default staleness
bound is 300 s; a 2-minute publish cadence plus the agent's
~60 s pull keeps the displayed age comfortably inside it).
Publishing goes through the GitHub contents API, the same
channel bc.py uses: one GET for the current sha plus one PUT
per cycle.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_REPO = "tbitcs/syncyard-backchannel"
DEFAULT_PATH = "channels/syncyard-dev/fleet-status.json"
API_BASE = "https://api.github.com"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def build_snapshot(gk_dir):
    """The FR-008 snapshot dict from the gatekeeper state in
    gk_dir (PAUSED.json / STOPPED.json / queue/*.json)."""
    stopped = os.path.isfile(os.path.join(gk_dir, "STOPPED.json"))
    pause = _read_json(os.path.join(gk_dir, "PAUSED.json"))
    paused = pause is not None or os.path.isfile(
        os.path.join(gk_dir, "PAUSED.json"))
    if stopped:
        mode = "stopped"
    elif paused:
        mode = "paused"
    else:
        mode = "running"
    reason = (pause or {}).get("reason") or ""
    focus = {"keys": [], "programs": [], "chats": []}
    except_key = (pause or {}).get("except_key")
    if except_key:
        focus["keys"] = [except_key]
    waiting = []
    queue_dir = os.path.join(gk_dir, "queue")
    jobs = []
    if os.path.isdir(queue_dir):
        for name in os.listdir(queue_dir):
            if not name.endswith(".json"):
                continue
            job = _read_json(os.path.join(queue_dir, name))
            if job and job.get("state") == "submitted":
                jobs.append(job)
    jobs.sort(key=lambda j: (j.get("created_at") or "", j.get("key") or ""))
    for job in jobs:
        entry = {
            "key": job.get("key") or "",
            "program": job.get("program") or "",
            "lane": job.get("lane") or "",
        }
        for opt in ("priority", "priority_source", "held"):
            if job.get(opt) is not None:
                entry[opt] = job[opt]
        waiting.append(entry)
    return {
        "generated_at": now_iso(),
        "mode": mode,
        "draining": False,
        "reason": reason,
        "focus": focus,
        "waiting": waiting,
    }


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
            API_BASE, self.repo, urllib.parse.quote(path))
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

    def put_file(self, path, content_bytes, message, sha=None):
        payload = {
            "message": message,
            "content": base64.b64encode(content_bytes).decode("ascii"),
        }
        if sha:
            payload["sha"] = sha
        return self._request("PUT", path, payload)


def publish(gk_dir, repo=DEFAULT_REPO, path=DEFAULT_PATH, api=None):
    """Build the snapshot and put it at path in repo. Returns
    the snapshot dict. Retries once on a sha conflict (the
    desktop agent pushes heartbeat commits to the same repo;
    a 409 here means its push landed between our GET and PUT)."""
    snap = build_snapshot(gk_dir)
    body = (json.dumps(snap, indent=2, sort_keys=True) + "\n").encode("utf-8")
    api = api or ContentsAPI(repo=repo)
    for attempt in (1, 2):
        sha = api.get_sha(path)
        try:
            api.put_file(path, body, "fleet-status: %s mode=%s waiting=%d"
                         % (snap["generated_at"], snap["mode"],
                            len(snap["waiting"])), sha=sha)
            return snap
        except urllib.error.HTTPError as exc:
            if exc.code in (409, 422) and attempt == 1:
                continue
            raise
    return snap


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gk-dir", default=os.path.dirname(
        os.path.abspath(__file__)),
        help="gatekeeper state directory (default: beside this file)")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--path", default=DEFAULT_PATH)
    ap.add_argument("--print-only", action="store_true",
                    help="print the snapshot; publish nothing")
    args = ap.parse_args(argv)
    if args.print_only:
        print(json.dumps(build_snapshot(args.gk_dir), indent=2,
                         sort_keys=True))
        return 0
    snap = publish(args.gk_dir, repo=args.repo, path=args.path)
    print("PUBLISHED %s mode=%s waiting=%d"
          % (snap["generated_at"], snap["mode"], len(snap["waiting"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

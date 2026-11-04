#!/usr/bin/env python3
"""bc.py - VM-side client for the Syncyard failsafe backchannel.

Spec: specs/009-failsafe-backchannel/spec.md (FR-003).

Talks to the DEDICATED channel `syncyard-dev` in the PRIVATE repo
tbitcs/syncyard-backchannel, and only to that channel. The channel
is owned by the Desktop Access chat; this client is its tool.

Design points (from the spec):
  - `send` goes through the GitHub contents API, NOT local git, so
    it keeps working during VM write waves that deny exec-born
    processes their local writes.
  - Every command carries a unique id and the sha256 of its body.
  - `send` refuses to overwrite an unacknowledged pending command
    (a cmd.json whose id has no matching out.json) unless --force.
  - `send` verifies by reading cmd.json back and comparing the id
    and sha256; a mismatch is a loud failure, never a quiet "sent".
  - The desktop agent polls the repo over git and burns no REST
    quota; this client is the only REST user, and it runs rarely
    (operator-initiated sends and status checks).

Python 3 standard library only.

Token: $GH_TOKEN or $GITHUB_TOKEN if set, else `gh auth token`.
"""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

REPO = "tbitcs/syncyard-backchannel"
CHANNEL = "channels/syncyard-dev"
CMD_PATH = CHANNEL + "/cmd.json"
OUT_PATH = CHANNEL + "/out.json"
HEARTBEAT_PATH = CHANNEL + "/heartbeat.json"
STOP_PATH = CHANNEL + "/STOP"

API_BASE = "https://api.github.com"
STALE_HEARTBEAT_S = 300  # FR-005: >5 min is stale
SKEW_TOLERANCE_S = 30  # a heartbeat ts this far in the FUTURE is
# clock skew, not freshness: the host's WSL clock and the VM clock
# are different clocks, and WSL clocks are known to drift across
# host sleep. Judging a future-dated heartbeat "fresh" would let
# a dead agent hide behind a skewed clock for skew + the whole
# stale window, so past this tolerance the age is reported as
# unjudgeable instead.


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested offline in test_bc.py)
# ---------------------------------------------------------------------------

def now_iso(dt=None):
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_command_id(dt=None, suffix=None):
    """Unique, sortable command id: UTC stamp + 6 hex chars."""
    dt = dt or datetime.now(timezone.utc)
    suffix = suffix or uuid.uuid4().hex[:6]
    return dt.strftime("%Y%m%dT%H%M%SZ") + "-bc-" + suffix


def body_sha256(body):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def pack_command(body, cmd_id=None, timeout_s=600, dt=None):
    """Pack a command body into the cmd.json dict. Pure."""
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    dt = dt or datetime.now(timezone.utc)
    return {
        "id": cmd_id or make_command_id(dt),
        "sha256": body_sha256(body),
        "body": body,
        "timeout_s": int(timeout_s),
        "created_at": now_iso(dt),
        "state": "pending",
        "channel": "syncyard-dev",
    }


def is_acknowledged(cmd, out):
    """A pending command is acknowledged once out.json carries its id."""
    if not cmd or not cmd.get("id"):
        return True  # nothing pending
    return bool(out) and out.get("id") == cmd.get("id")


def should_refuse_overwrite(existing_cmd, existing_out, force):
    """FR-003 refusal rule. Pure."""
    if force:
        return False
    if not existing_cmd:
        return False
    return not is_acknowledged(existing_cmd, existing_out)


def parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def heartbeat_age_s(heartbeat, dt=None):
    """Seconds since the heartbeat's ts, or None if unreadable."""
    if not heartbeat:
        return None
    ts = parse_iso(heartbeat.get("ts"))
    if ts is None:
        return None
    return int(((dt or datetime.now(timezone.utc)) - ts).total_seconds())


# ---------------------------------------------------------------------------
# GitHub contents API layer (mocked in tests)
# ---------------------------------------------------------------------------

class ContentsAPI:
    """Minimal GitHub contents-API client for one repo."""

    def __init__(self, repo=REPO, token=None):
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
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "syncyard-bc",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and method == "GET":
                return None
            raise RuntimeError(
                "GitHub API %s %s failed: HTTP %s %s" % (
                    method, path, exc.code,
                    exc.read().decode("utf-8", "replace")[:400]))

    def get_file(self, path):
        """Return (parsed_json_or_text, sha) or (None, None) if absent.

        Files whose content parses as JSON come back as dicts;
        anything else comes back as text (e.g. the STOP file).
        """
        res = self._request("GET", path)
        if res is None:
            return None, None
        raw = base64.b64decode(res["content"]).decode("utf-8")
        try:
            return json.loads(raw), res.get("sha")
        except ValueError:
            return raw, res.get("sha")

    def put_file(self, path, content_bytes, message, sha=None):
        payload = {
            "message": message,
            "content": base64.b64encode(content_bytes).decode("ascii"),
        }
        if sha:
            payload["sha"] = sha
        res = self._request("PUT", path, payload)
        return (res or {}).get("content", {}).get("sha")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_heartbeat(api, args):
    hb, _ = api.get_file(HEARTBEAT_PATH)
    if hb is None:
        print("BC_HEARTBEAT ABSENT (no heartbeat.json on the channel)")
        return 2
    if not isinstance(hb, dict):
        # get_file returns raw text when the content does not
        # parse as JSON: a corrupt or half-written heartbeat is
        # unreadable, not a traceback.
        print("BC_HEARTBEAT UNREADABLE (heartbeat.json did not parse "
              "as a JSON object)")
        return 2
    age = heartbeat_age_s(hb)
    print("BC_HEARTBEAT ts=%s host=%s user=%s last_executed=%s stopped=%s" % (
        hb.get("ts"), hb.get("host"), hb.get("user"),
        hb.get("last_executed_id"), hb.get("stopped")))
    st = hb.get("self_test")
    if isinstance(st, dict):
        checks = st.get("checks") or {}
        print("BC_SELFTEST ok=%s ts=%s checks=%s" % (
            st.get("ok"), st.get("ts"),
            ",".join("%s=%d" % (k, 1 if v else 0)
                     for k, v in sorted(checks.items()))))
    else:
        print("BC_SELFTEST ABSENT (agent predates the self-test, spec 010 T010)")
    if age is None:
        print("BC_HEARTBEAT_AGE UNKNOWN (unparseable ts)")
        return 2
    print("BC_HEARTBEAT_AGE_S %d" % age)
    if age < -SKEW_TOLERANCE_S:
        print("BC_HEARTBEAT_SKEW age_s=%d - the host clock is ahead "
              "of this clock; freshness cannot be judged from this "
              "heartbeat (spec 009 FR-005)" % age)
        return 2
    if age > STALE_HEARTBEAT_S:
        print("BC_HEARTBEAT_STALE age_s=%d limit_s=%d - report to the "
              "Desktop Access chat (spec 009 FR-005)" % (
                  age, STALE_HEARTBEAT_S))
        return 3
    print("BC_HEARTBEAT_FRESH 1")
    return 0


def cmd_status(api, args):
    rc = cmd_heartbeat(api, args)
    cmd, _ = api.get_file(CMD_PATH)
    out, _ = api.get_file(OUT_PATH)
    stop, _ = api.get_file(STOP_PATH)
    if cmd is not None and not isinstance(cmd, dict):
        print("BC_LAST_CMD unreadable (cmd.json did not parse as a "
              "JSON object)")
    elif cmd:
        state = "acknowledged" if is_acknowledged(
            cmd, out if isinstance(out, dict) else None) else "pending"
        print("BC_LAST_CMD id=%s state=%s created_at=%s" % (
            cmd.get("id"), state, cmd.get("created_at")))
    else:
        print("BC_LAST_CMD none")
    if out is not None and not isinstance(out, dict):
        print("BC_LAST_OUT unreadable (out.json did not parse as a "
              "JSON object)")
    elif out:
        print("BC_LAST_OUT id=%s exit_code=%s finished_at=%s" % (
            out.get("id"), out.get("exit_code"), out.get("finished_at")))
    else:
        print("BC_LAST_OUT none")
    print("BC_STOP %s" % ("present (agent halted)" if stop is not None
                          else "absent"))
    return rc


def do_send(api, cmd_dict, force=False):
    """Send one packed command. Returns 0 on a verified send.

    Raises RuntimeError on the refusal rule or a read-back mismatch;
    callers turn that into a loud non-zero exit.
    """
    existing_cmd, existing_sha = api.get_file(CMD_PATH)
    existing_out, _ = api.get_file(OUT_PATH)
    if not isinstance(existing_cmd, dict):
        # A cmd.json that does not parse as a JSON object names
        # no pending command the agent could execute (the agent
        # skips it too); treat it as no command. A corrupt
        # out.json proves no acknowledgement, so it stays None
        # and the refusal rule remains conservative.
        existing_cmd = None
    if not isinstance(existing_out, dict):
        existing_out = None
    if should_refuse_overwrite(existing_cmd, existing_out, force):
        raise RuntimeError(
            "refusing to overwrite unacknowledged pending command %s "
            "(no matching out.json); wait for it or re-run with --force"
            % (existing_cmd or {}).get("id"))
    blob = json.dumps(cmd_dict, indent=1, sort_keys=True).encode("utf-8")
    api.put_file(CMD_PATH, blob,
                 "backchannel: command %s" % cmd_dict["id"],
                 sha=existing_sha)
    back, _ = api.get_file(CMD_PATH)
    if (not isinstance(back, dict) or back.get("id") != cmd_dict["id"]
            or back.get("sha256") != cmd_dict["sha256"]):
        raise RuntimeError(
            "read-back verification FAILED for command %s - treat as "
            "NOT sent" % cmd_dict["id"])
    return 0


def cmd_send(api, args):
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            body = fh.read()
    elif args.cmd:
        body = args.cmd
    else:
        body = sys.stdin.read()
    cmd_dict = pack_command(body, timeout_s=args.timeout_s)
    try:
        do_send(api, cmd_dict, force=args.force)
    except RuntimeError as exc:
        print("BC_SEND_REFUSED %s" % exc)
        return 3
    print("BC_SENT id=%s sha256=%s" % (cmd_dict["id"], cmd_dict["sha256"]))
    return 0


def cmd_wait(api, args):
    deadline = time.time() + args.timeout_s
    while True:
        out, _ = api.get_file(OUT_PATH)
        if isinstance(out, dict) and out.get("id") == args.id:
            print("BC_DONE id=%s exit_code=%s" % (
                out.get("id"), out.get("exit_code")))
            if out.get("stdout_tail"):
                print("--- stdout tail ---")
                print(out["stdout_tail"])
            if out.get("stderr_tail"):
                print("--- stderr tail ---")
                print(out["stderr_tail"])
            return 0 if out.get("exit_code") == 0 else 4
        if time.time() >= deadline:
            print("BC_WAIT_TIMEOUT id=%s after %ss" % (
                args.id, args.timeout_s))
            return 5
        time.sleep(args.poll_s)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="bc.py",
        description="Syncyard failsafe backchannel client "
                    "(syncyard-dev channel, Desktop Access chat only)")
    parser.add_argument("--repo", default=REPO,
                        help="backchannel repo (default %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("heartbeat", help="print heartbeat age / freshness")
    sub.add_parser("status", help="heartbeat + last command/out + STOP")

    p_send = sub.add_parser("send", help="send a command (contents API)")
    p_send.add_argument("--file", help="file whose contents are the command")
    p_send.add_argument("--cmd", help="command string (else stdin)")
    p_send.add_argument("--timeout-s", type=int, default=600,
                        help="agent-side execution timeout (default 600)")
    p_send.add_argument("--force", action="store_true",
                        help="overwrite an unacknowledged pending command")

    p_wait = sub.add_parser("wait", help="wait for out.json for a command id")
    p_wait.add_argument("id")
    p_wait.add_argument("--timeout-s", type=int, default=900)
    p_wait.add_argument("--poll-s", type=int, default=20)

    args = parser.parse_args(argv)
    api = ContentsAPI(repo=args.repo)
    if args.command == "heartbeat":
        return cmd_heartbeat(api, args)
    if args.command == "status":
        return cmd_status(api, args)
    if args.command == "send":
        return cmd_send(api, args)
    if args.command == "wait":
        return cmd_wait(api, args)
    return 2


if __name__ == "__main__":
    sys.exit(main())

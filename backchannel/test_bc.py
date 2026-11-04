#!/usr/bin/env python3
"""Offline unit tests for bc.py (spec 009 FR-003).

No network: the GitHub contents API layer is replaced by an
in-memory mock implementing the same get_file / put_file shape.

Run: python3 -m unittest test_bc -v   (from backchannel/)
or:  python3 test_bc.py
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bc  # noqa: E402


class MockAPI:
    """In-memory ContentsAPI stand-in: path -> (content_bytes, sha)."""

    def __init__(self):
        self.files = {}
        self.put_count = 0

    def get_file(self, path):
        entry = self.files.get(path)
        if entry is None:
            return None, None
        raw, sha = entry
        text = raw.decode("utf-8")
        try:
            return json.loads(text), sha
        except ValueError:
            return text, sha

    def put_file(self, path, content_bytes, message, sha=None):
        self.put_count += 1
        new_sha = "sha%d-%s" % (self.put_count, path)
        self.files[path] = (content_bytes, new_sha)
        return new_sha

    def seed_json(self, path, obj):
        blob = json.dumps(obj).encode("utf-8")
        self.files[path] = (blob, "seed-" + path)


class PackingTests(unittest.TestCase):
    def test_pack_carries_id_sha_and_body(self):
        dt = datetime(2026, 10, 29, 23, 0, 0, tzinfo=timezone.utc)
        cmd = bc.pack_command("echo hi\n", dt=dt)
        self.assertEqual(cmd["body"], "echo hi\n")
        self.assertEqual(
            cmd["sha256"], hashlib.sha256(b"echo hi\n").hexdigest())
        self.assertEqual(cmd["created_at"], "2026-10-29T23:00:00Z")
        self.assertTrue(cmd["id"].startswith("20261029T230000Z-bc-"))
        self.assertEqual(cmd["state"], "pending")
        self.assertEqual(cmd["channel"], "syncyard-dev")

    def test_ids_are_unique(self):
        ids = {bc.make_command_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)

    def test_explicit_id_is_kept(self):
        cmd = bc.pack_command("true", cmd_id="fixed-id-1")
        self.assertEqual(cmd["id"], "fixed-id-1")

    def test_pack_bytes_body_decodes_utf8(self):
        raw = "echo ✓\n".encode("utf-8")
        cmd = bc.pack_command(raw)
        self.assertEqual(cmd["body"], "echo ✓\n")
        self.assertEqual(cmd["sha256"],
                         hashlib.sha256(raw).hexdigest())


class RefusalTests(unittest.TestCase):
    def test_no_existing_command_no_refusal(self):
        self.assertFalse(bc.should_refuse_overwrite(None, None, False))

    def test_acknowledged_command_no_refusal(self):
        cmd = {"id": "a1"}
        out = {"id": "a1", "exit_code": 0}
        self.assertFalse(bc.should_refuse_overwrite(cmd, out, False))

    def test_unacknowledged_command_refused(self):
        cmd = {"id": "a1"}
        self.assertTrue(bc.should_refuse_overwrite(cmd, None, False))
        self.assertTrue(
            bc.should_refuse_overwrite(cmd, {"id": "other"}, False))

    def test_force_overrides_refusal(self):
        cmd = {"id": "a1"}
        self.assertFalse(bc.should_refuse_overwrite(cmd, None, True))


class SendTests(unittest.TestCase):
    def test_send_writes_and_verifies(self):
        api = MockAPI()
        cmd = bc.pack_command("echo ok", cmd_id="cmd-1")
        self.assertEqual(bc.do_send(api, cmd), 0)
        stored, _ = api.get_file(bc.CMD_PATH)
        self.assertEqual(stored["id"], "cmd-1")
        self.assertEqual(stored["sha256"], cmd["sha256"])

    def test_send_refuses_over_pending(self):
        api = MockAPI()
        api.seed_json(bc.CMD_PATH, {"id": "pending-1", "sha256": "x"})
        with self.assertRaises(RuntimeError):
            bc.do_send(api, bc.pack_command("echo 2", cmd_id="cmd-2"))
        stored, _ = api.get_file(bc.CMD_PATH)
        self.assertEqual(stored["id"], "pending-1")

    def test_send_force_replaces_pending(self):
        api = MockAPI()
        api.seed_json(bc.CMD_PATH, {"id": "pending-1", "sha256": "x"})
        cmd = bc.pack_command("echo 2", cmd_id="cmd-2")
        self.assertEqual(bc.do_send(api, cmd, force=True), 0)
        stored, _ = api.get_file(bc.CMD_PATH)
        self.assertEqual(stored["id"], "cmd-2")

    def test_send_after_acknowledgement(self):
        api = MockAPI()
        api.seed_json(bc.CMD_PATH, {"id": "old-1", "sha256": "x"})
        api.seed_json(bc.OUT_PATH, {"id": "old-1", "exit_code": 0})
        cmd = bc.pack_command("echo 3", cmd_id="cmd-3")
        self.assertEqual(bc.do_send(api, cmd), 0)

    def test_readback_mismatch_is_loud(self):
        class CorruptAPI(MockAPI):
            def put_file(self, path, content_bytes, message, sha=None):
                mangled = json.loads(content_bytes.decode("utf-8"))
                mangled["sha256"] = "0" * 64
                return super().put_file(
                    path, json.dumps(mangled).encode("utf-8"), message, sha)

        api = CorruptAPI()
        with self.assertRaises(RuntimeError):
            bc.do_send(api, bc.pack_command("echo x", cmd_id="cmd-9"))


class HeartbeatTests(unittest.TestCase):
    def test_age(self):
        dt = datetime(2026, 10, 29, 23, 5, 0, tzinfo=timezone.utc)
        hb = {"ts": "2026-10-29T23:00:00Z"}
        self.assertEqual(bc.heartbeat_age_s(hb, dt=dt), 300)

    def test_age_unparseable(self):
        self.assertIsNone(bc.heartbeat_age_s({"ts": "junk"}))
        self.assertIsNone(bc.heartbeat_age_s(None))
        self.assertIsNone(bc.heartbeat_age_s({}))

    def test_age_of_a_future_ts_is_negative(self):
        # The raw math is signed on purpose: a heartbeat stamped
        # an hour ahead of this clock yields -3600, and the
        # freshness judgment (below) is what must refuse to call
        # that "fresh".
        dt = datetime(2026, 10, 29, 23, 0, 0, tzinfo=timezone.utc)
        hb = {"ts": "2026-10-30T00:00:00Z"}
        self.assertEqual(bc.heartbeat_age_s(hb, dt=dt), -3600)


def run_heartbeat(api):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = bc.cmd_heartbeat(api, None)
    return rc, buf.getvalue()


def seed_heartbeat(api, age_s):
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    api.seed_json(bc.HEARTBEAT_PATH, {
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": "TESTHOST", "user": "tpierson",
        "last_executed_id": None, "stopped": False})


class HeartbeatJudgmentTests(unittest.TestCase):
    """cmd_heartbeat's verdicts, driven through the mock API.

    Guards the FR-005 freshness judgment against the clock-skew
    failure: the host's WSL clock and the VM clock are different
    clocks, so a heartbeat can arrive stamped in the future.
    """

    def test_fresh_heartbeat_reports_fresh(self):
        api = MockAPI()
        seed_heartbeat(api, age_s=5)
        rc, out = run_heartbeat(api)
        self.assertEqual(rc, 0)
        self.assertIn("BC_HEARTBEAT_FRESH 1", out)

    def test_stale_heartbeat_reports_stale(self):
        api = MockAPI()
        seed_heartbeat(api, age_s=bc.STALE_HEARTBEAT_S + 120)
        rc, out = run_heartbeat(api)
        self.assertEqual(rc, 3)
        self.assertIn("BC_HEARTBEAT_STALE", out)

    def test_future_heartbeat_is_skew_never_fresh(self):
        # An agent that died while its host clock ran ahead
        # leaves a future-dated heartbeat; a naive age check
        # calls it fresh for the whole skew + stale window.
        api = MockAPI()
        seed_heartbeat(api, age_s=-7200)
        rc, out = run_heartbeat(api)
        self.assertEqual(rc, 2)
        self.assertIn("BC_HEARTBEAT_SKEW", out)
        self.assertNotIn("BC_HEARTBEAT_FRESH", out)

    def test_small_future_ts_within_tolerance_is_fresh(self):
        # Ordinary clock jitter between two healthy machines
        # must not cry skew.
        api = MockAPI()
        seed_heartbeat(api, age_s=-5)
        rc, out = run_heartbeat(api)
        self.assertEqual(rc, 0)
        self.assertIn("BC_HEARTBEAT_FRESH 1", out)


class CorruptFileTests(unittest.TestCase):
    """Channel files that do not parse as JSON objects.

    get_file returns raw text for unparseable content; every
    command path must treat that as unreadable channel state,
    never as a traceback (the status check is the tool operators
    reach for exactly when the channel looks broken).
    """

    def seed_raw(self, api, path, raw):
        api.files[path] = (raw, "raw-" + path)

    def test_corrupt_heartbeat_is_unreadable(self):
        api = MockAPI()
        self.seed_raw(api, bc.HEARTBEAT_PATH, b"this is not json{{{\n")
        rc, out = run_heartbeat(api)
        self.assertEqual(rc, 2)
        self.assertIn("BC_HEARTBEAT UNREADABLE", out)

    def test_status_survives_corrupt_cmd_and_out(self):
        api = MockAPI()
        seed_heartbeat(api, age_s=5)
        self.seed_raw(api, bc.CMD_PATH, b"\x00\x01 not json")
        self.seed_raw(api, bc.OUT_PATH, b"[1, 2")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bc.cmd_status(api, None)
        text = buf.getvalue()
        self.assertEqual(rc, 0)  # the heartbeat's verdict
        self.assertIn("BC_LAST_CMD unreadable", text)
        self.assertIn("BC_LAST_OUT unreadable", text)

    def test_status_valid_cmd_with_corrupt_out_is_pending(self):
        api = MockAPI()
        seed_heartbeat(api, age_s=5)
        api.seed_json(bc.CMD_PATH, {"id": "c1",
                                    "created_at": "2026-10-29T23:00:00Z"})
        self.seed_raw(api, bc.OUT_PATH, b"garbage")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bc.cmd_status(api, None)
        text = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("BC_LAST_CMD id=c1 state=pending", text)
        self.assertIn("BC_LAST_OUT unreadable", text)

    def test_send_overwrites_a_corrupt_cmd(self):
        # A cmd.json that does not parse names no command the
        # agent could run (it skips it too), so it must not
        # brick the channel behind the refusal rule.
        api = MockAPI()
        self.seed_raw(api, bc.CMD_PATH, b"not json at all")
        cmd = bc.pack_command("echo ok", cmd_id="cmd-new")
        self.assertEqual(bc.do_send(api, cmd), 0)
        stored, _ = api.get_file(bc.CMD_PATH)
        self.assertEqual(stored["id"], "cmd-new")

    def test_corrupt_out_proves_no_acknowledgement(self):
        # The conservative direction: a pending command plus an
        # unreadable out.json is still a refusal, never a guess
        # that the command completed.
        api = MockAPI()
        api.seed_json(bc.CMD_PATH, {"id": "pending-1", "sha256": "x"})
        self.seed_raw(api, bc.OUT_PATH, b"garbage")
        with self.assertRaises(RuntimeError):
            bc.do_send(api, bc.pack_command("echo 2", cmd_id="cmd-2"))

    def test_readback_returning_junk_is_loud_not_a_crash(self):
        class JunkReadbackAPI(MockAPI):
            def get_file(self, path):
                if path == bc.CMD_PATH and self.put_count:
                    return "junk-not-json", "sha-x"
                return super().get_file(path)

        api = JunkReadbackAPI()
        with self.assertRaises(RuntimeError):
            bc.do_send(api, bc.pack_command("echo x", cmd_id="cmd-9"))

    def test_wait_ignores_corrupt_out_and_times_out(self):
        api = MockAPI()
        self.seed_raw(api, bc.OUT_PATH, b"garbage")
        args = types.SimpleNamespace(id="w1", timeout_s=1, poll_s=0.05)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bc.cmd_wait(api, args)
        self.assertEqual(rc, 5)
        self.assertIn("BC_WAIT_TIMEOUT", buf.getvalue())

    def test_wait_reports_the_recorded_exit_code(self):
        api = MockAPI()
        api.seed_json(bc.OUT_PATH, {"id": "w1", "exit_code": 3})
        args = types.SimpleNamespace(id="w1", timeout_s=5, poll_s=0.05)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bc.cmd_wait(api, args)
        self.assertEqual(rc, 4)
        self.assertIn("BC_DONE id=w1 exit_code=3", buf.getvalue())

        api2 = MockAPI()
        api2.seed_json(bc.OUT_PATH, {"id": "w1", "exit_code": 0})
        with contextlib.redirect_stdout(io.StringIO()):
            rc = bc.cmd_wait(api2, args)
        self.assertEqual(rc, 0)


class SendRaceTests(unittest.TestCase):
    """The lost-command race, client side: a second writer wins
    between do_send's read of cmd.json and its PUT. The contents
    API answers the stale-sha PUT with an HTTP error; the send
    must surface as a loud refusal (exit 3), never as a crash or
    a quiet success."""

    def test_conflicting_put_is_a_loud_refusal(self):
        class ConflictingAPI(MockAPI):
            def put_file(self, path, content_bytes, message, sha=None):
                raise RuntimeError(
                    "GitHub API PUT %s failed: HTTP 409 conflict"
                    % path)

        api = ConflictingAPI()
        with self.assertRaises(RuntimeError):
            bc.do_send(api, bc.pack_command("echo x", cmd_id="cmd-r"))

        args = types.SimpleNamespace(file=None, cmd="echo raced",
                                     timeout_s=600, force=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = bc.cmd_send(api, args)
        self.assertEqual(rc, 3)
        self.assertIn("BC_SEND_REFUSED", buf.getvalue())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Offline unit tests for bc.py (spec 009 FR-003).

No network: the GitHub contents API layer is replaced by an
in-memory mock implementing the same get_file / put_file shape.

Run: python3 -m unittest test_bc -v   (from backchannel/)
or:  python3 test_bc.py
"""

import base64
import hashlib
import json
import os
import sys
import unittest
from datetime import datetime, timezone

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


class ChannelTests(unittest.TestCase):
    def tearDown(self):
        bc.set_channel("syncyard-dev")

    def test_default_channel_is_syncyard_dev(self):
        self.assertEqual(bc.CHANNEL_NAME, "syncyard-dev")
        self.assertEqual(bc.CMD_PATH, "channels/syncyard-dev/cmd.json")

    def test_set_channel_repoints_paths_and_packing(self):
        bc.set_channel("syncyard-laptop")
        self.assertEqual(bc.CHANNEL_NAME, "syncyard-laptop")
        self.assertEqual(bc.CMD_PATH, "channels/syncyard-laptop/cmd.json")
        self.assertEqual(bc.OUT_PATH, "channels/syncyard-laptop/out.json")
        self.assertEqual(bc.HEARTBEAT_PATH,
                         "channels/syncyard-laptop/heartbeat.json")
        self.assertEqual(bc.STOP_PATH, "channels/syncyard-laptop/STOP")
        cmd = bc.pack_command("echo hi")
        self.assertEqual(cmd["channel"], "syncyard-laptop")


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


if __name__ == "__main__":
    unittest.main()

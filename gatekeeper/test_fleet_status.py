#!/usr/bin/env python3
"""Tests for the fleet-status publisher (spec 014 FR-009/T010).

Pins the FR-008 feed contract the tray parses in
runner/tray/SyncyardTray.cs (FleetStatus.Load): the top-level
key set, the waiting-entry key set, and the unavailability the
tray shows when no snapshot exists (publisher absent = the
pre-FR-009 state, exercised by simply not running this).
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fleet_status  # noqa: E402

TOP_KEYS = {"generated_at", "mode", "draining", "reason",
            "focus", "waiting"}
FOCUS_KEYS = {"keys", "programs", "chats"}
WAITING_KEYS = {"key", "program", "lane",
                "priority", "priority_source", "held"}


def write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


class SnapshotShape(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.dir, "queue"))

    def job(self, name, **kw):
        base = {"key": name, "program": "prog", "lane": "quick",
                "state": "submitted", "created_at": "2026-11-03T00:00:00Z"}
        base.update(kw)
        write(os.path.join(self.dir, "queue", name + ".json"), base)

    def test_running_empty(self):
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(set(snap), TOP_KEYS)
        self.assertEqual(snap["mode"], "running")
        self.assertEqual(snap["draining"], False)
        self.assertEqual(snap["reason"], "")
        self.assertEqual(set(snap["focus"]), FOCUS_KEYS)
        self.assertEqual(snap["waiting"], [])
        # generated_at parses as ISO-8601 (the tray's staleness
        # math depends on it).
        datetime.strptime(snap["generated_at"], "%Y-%m-%dT%H:%M:%SZ")

    def test_waiting_order_and_shape(self):
        self.job("b-job", created_at="2026-11-03T01:00:00Z")
        self.job("a-job", created_at="2026-11-03T00:30:00Z")
        self.job("done-job", state="completed")
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual([w["key"] for w in snap["waiting"]],
                         ["a-job", "b-job"])
        for w in snap["waiting"]:
            self.assertTrue(set(w) <= WAITING_KEYS)
            self.assertTrue({"key", "program", "lane"} <= set(w))

    def test_optional_fields_pass_through(self):
        self.job("p-job", priority=5, priority_source="operator",
                 held=True)
        snap = fleet_status.build_snapshot(self.dir)
        w = snap["waiting"][0]
        self.assertEqual(w["priority"], 5)
        self.assertEqual(w["priority_source"], "operator")
        self.assertEqual(w["held"], True)

    def test_paused_with_reason_and_focus(self):
        write(os.path.join(self.dir, "PAUSED.json"),
              {"ts": "2026-11-03T00:00:00Z", "reason": "owner hold",
               "except_key": "hot-fix"})
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["mode"], "paused")
        self.assertEqual(snap["reason"], "owner hold")
        self.assertEqual(snap["focus"]["keys"], ["hot-fix"])

    def test_paused_corrupt_record_still_paused(self):
        with open(os.path.join(self.dir, "PAUSED.json"), "w") as f:
            f.write("not json{{{")
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["mode"], "paused")

    def test_stopped_wins_over_paused(self):
        write(os.path.join(self.dir, "PAUSED.json"), {"reason": "x"})
        write(os.path.join(self.dir, "STOPPED.json"), {"reason": "y"})
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["mode"], "stopped")


class FakeAPI:
    def __init__(self):
        self.puts = []
        self.sha = "abc"

    def get_sha(self, path):
        return self.sha

    def put_file(self, path, body, message, sha=None):
        self.puts.append((path, body, message, sha))
        return {}


class Publish(unittest.TestCase):
    def test_publish_puts_snapshot(self):
        d = tempfile.mkdtemp()
        os.mkdir(os.path.join(d, "queue"))
        api = FakeAPI()
        snap = fleet_status.publish(d, api=api)
        self.assertEqual(len(api.puts), 1)
        path, body, _msg, sha = api.puts[0]
        self.assertEqual(path, fleet_status.DEFAULT_PATH)
        self.assertEqual(sha, "abc")
        self.assertEqual(json.loads(body.decode("utf-8")), snap)


if __name__ == "__main__":
    unittest.main()

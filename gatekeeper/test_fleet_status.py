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
        self.job("p-job", priority=5, priority_source="human")
        snap = fleet_status.build_snapshot(self.dir)
        w = snap["waiting"][0]
        self.assertEqual(w["priority"], 5)
        self.assertEqual(w["priority_source"], "human")
        # No pause in force: nothing is held, and the flag is
        # always present (the tray keys on its presence).
        self.assertEqual(w["held"], False)

    def test_focused_full_selectors_and_legacy_merge(self):
        write(os.path.join(self.dir, "PAUSED.json"),
              {"reason": "focus run",
               "except_keys": ["leg5", "hot-fix"],
               "except_key": "leg5",
               "except_programs": ["chronoc"],
               "except_chats": ["chat-1"]})
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["mode"], "focused")
        # The legacy singular merges without duplicating.
        self.assertEqual(snap["focus"]["keys"], ["leg5", "hot-fix"])
        self.assertEqual(snap["focus"]["programs"], ["chronoc"])
        self.assertEqual(snap["focus"]["chats"], ["chat-1"])

    def test_drain_from_pause_record(self):
        write(os.path.join(self.dir, "PAUSED.json"),
              {"reason": "winding down", "drain": True})
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["mode"], "paused")
        self.assertEqual(snap["draining"], True)

    def test_drain_from_host_hold(self):
        write(os.path.join(self.dir, "hosts.json"),
              {"desktop": {"hold": True, "going_down": None},
               "desktop-wsl": {"hold": False, "going_down": None}})
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["mode"], "running")
        self.assertEqual(snap["draining"], True)

    def test_going_down_history_alone_does_not_drain(self):
        write(os.path.join(self.dir, "hosts.json"),
              {"desktop": {"hold": False,
                           "going_down": {"ts": "2026-11-03T00:00:00Z"}}})
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["draining"], False)

    def test_held_matrix(self):
        write(os.path.join(self.dir, "PAUSED.json"),
              {"except_keys": ["leg5"],
               "except_programs": ["chronoc"],
               "except_chats": ["chat-9"]})
        self.job("leg5j2", program="other")
        self.job("leg6x", program="other")
        self.job("anykey", program="chronoc")
        self.job("anykey2", program="other", notify_chat="chat-9")
        self.job("maint", program="other", maintenance=True)
        snap = fleet_status.build_snapshot(self.dir)
        held = {w["key"]: w["held"] for w in snap["waiting"]}
        self.assertEqual(held, {"leg5j2": False, "leg6x": True,
                                "anykey": False, "anykey2": False,
                                "maint": False})

    def test_stopped_holds_everything(self):
        write(os.path.join(self.dir, "STOPPED.json"), {})
        self.job("j1", maintenance=True)
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["waiting"][0]["held"], True)

    def test_stopped_shows_stop_reason_not_pause_reason(self):
        write(os.path.join(self.dir, "PAUSED.json"),
              {"reason": "old pause reason"})
        write(os.path.join(self.dir, "STOPPED.json"),
              {"ts": "2026-11-03T00:00:00Z",
               "reason": "owner stop order"})
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["mode"], "stopped")
        self.assertEqual(snap["reason"], "owner stop order")

    def test_stopped_corrupt_record_still_stopped(self):
        with open(os.path.join(self.dir, "STOPPED.json"), "w") as f:
            f.write("not json{{{")
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["mode"], "stopped")
        self.assertEqual(snap["reason"], "")

    def test_free_lane_jobs_sort_before_busy_lane_jobs(self):
        # A normal-lane job submitted first still sorts behind
        # a quick-lane job when normal is busy and quick is
        # free: the quick job is the one that can go next.
        self.job("busy-job", lane="normal",
                 created_at="2026-11-03T02:00:00Z", state="watching")
        self.job("old-waiting", lane="normal",
                 created_at="2026-11-03T00:00:00Z")
        self.job("new-waiting", lane="quick",
                 created_at="2026-11-03T01:00:00Z")
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual([w["key"] for w in snap["waiting"]],
                         ["new-waiting", "old-waiting"])

    def test_paused_with_reason_and_focus(self):
        # A pause record carrying an exception selector is a
        # FOCUS under spec 013 FR-001, not a plain pause; the
        # legacy singular except_key merges into the key list.
        write(os.path.join(self.dir, "PAUSED.json"),
              {"ts": "2026-11-03T00:00:00Z", "reason": "owner hold",
               "except_key": "hot-fix"})
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["mode"], "focused")
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
    def __init__(self, fail_first_put_with=None, remaining=5000,
                 fail_all_puts_with=None):
        self.puts = []
        self.sha = "abc"
        self.fail_first_put_with = fail_first_put_with
        self.fail_all_puts_with = fail_all_puts_with
        self.remaining = remaining

    def core_remaining(self):
        return self.remaining

    def get_sha(self, path):
        return self.sha

    def put_file(self, path, body, message, sha=None):
        self.puts.append((path, body, message, sha))
        if self.fail_all_puts_with is not None:
            raise self.fail_all_puts_with
        if self.fail_first_put_with is not None \
                and len(self.puts) == 1:
            raise self.fail_first_put_with
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

    def test_publish_retries_once_on_sha_conflict(self):
        import urllib.error
        d = tempfile.mkdtemp()
        os.mkdir(os.path.join(d, "queue"))
        conflict = urllib.error.HTTPError(
            "https://api.github.com", 409, "conflict", None, None)
        api = FakeAPI(fail_first_put_with=conflict)
        snap = fleet_status.publish(d, api=api)
        self.assertEqual(len(api.puts), 2)
        self.assertEqual(snap["mode"], "running")

    def test_publish_double_conflict_raises_never_reports(self):
        # A publish that never landed must surface as a
        # failure, never as a printed PUBLISHED line: two
        # consecutive conflicts exhaust the retry and raise.
        import urllib.error
        d = tempfile.mkdtemp()
        os.mkdir(os.path.join(d, "queue"))
        conflict = urllib.error.HTTPError(
            "https://api.github.com", 409, "conflict", None, None)
        api = FakeAPI(fail_all_puts_with=conflict)
        with self.assertRaises(urllib.error.HTTPError):
            fleet_status.publish(d, api=api)
        self.assertEqual(len(api.puts), 2)

    def test_publish_skips_under_budget_floor(self):
        d = tempfile.mkdtemp()
        os.mkdir(os.path.join(d, "queue"))
        api = FakeAPI(remaining=fleet_status.BUDGET_FLOOR - 1)
        with self.assertRaises(fleet_status.BudgetSkip):
            fleet_status.publish(d, api=api)
        self.assertEqual(api.puts, [])


class MultiPathPublish(unittest.TestCase):
    """Spec 024 FR-007: one snapshot, byte-identical bodies at
    every configured channel path."""

    def test_publish_multiple_paths_identical_bodies(self):
        d = tempfile.mkdtemp()
        os.mkdir(os.path.join(d, "queue"))
        api = FakeAPI()
        paths = ["channels/syncyard-dev/fleet-status.json",
                 "channels/syncyard-laptop/fleet-status.json"]
        snap = fleet_status.publish(d, api=api, paths=paths)
        self.assertEqual([p[0] for p in api.puts], paths)
        bodies = [p[1] for p in api.puts]
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(json.loads(bodies[0].decode("utf-8")), snap)

    def test_publish_paths_still_retries_per_path(self):
        import urllib.error
        d = tempfile.mkdtemp()
        os.mkdir(os.path.join(d, "queue"))
        conflict = urllib.error.HTTPError(
            "https://api.github.com", 422, "conflict", None, None)
        api = FakeAPI(fail_first_put_with=conflict)
        fleet_status.publish(d, api=api, paths=["a/f.json", "b/f.json"])
        # First path: conflict + retry = 2 puts; second path: 1.
        self.assertEqual(len(api.puts), 3)


class ApiBase(unittest.TestCase):
    """Spec 024: FLEET_STATUS_API_BASE overrides the API base;
    unset it is exactly the historical default."""

    def test_api_base_default(self):
        import unittest.mock
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(fleet_status._api_base(),
                             "https://api.github.com")

    def test_api_base_override(self):
        import unittest.mock
        env = {"FLEET_STATUS_API_BASE": "http://127.0.0.1:9/stub"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(fleet_status._api_base(),
                             "http://127.0.0.1:9/stub")


if __name__ == "__main__":
    unittest.main()

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
# The additive queue-depth fields (tray 0.4.3.0), emitted
# by BOTH derivations: the fallback below and the
# gatekeeper's own build_snapshot (see GatekeeperSnapshot),
# so the tray reads the same queue model from either
# source. The Transporter's canned GK_EMITTED fixture
# predates them and is still transported verbatim.
FALLBACK_KEYS = TOP_KEYS | {"active", "lanes", "queue_state",
                            "running_total", "waiting_total"}
ACTIVE_KEYS = {"key", "program", "lane", "run_id"}
LANE_KEYS = {"cap", "running", "waiting", "state"}
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
        self.assertEqual(set(snap), FALLBACK_KEYS)
        self.assertEqual(snap["mode"], "running")
        self.assertEqual(snap["draining"], False)
        self.assertEqual(snap["reason"], "")
        self.assertEqual(set(snap["focus"]), FOCUS_KEYS)
        self.assertEqual(snap["waiting"], [])
        self.assertEqual(snap["active"], [])
        self.assertEqual(snap["running_total"], 0)
        self.assertEqual(snap["waiting_total"], 0)
        self.assertEqual(snap["queue_state"], "idle")
        # generated_at parses as ISO-8601 (the tray's staleness
        # math depends on it).
        datetime.strptime(snap["generated_at"], "%Y-%m-%dT%H:%M:%SZ")

    def test_active_entries_and_run_id(self):
        self.job("run-job", state="dispatched", run_id=4242,
                 created_at="2026-11-03T01:00:00Z")
        self.job("watch-job", lane="long", state="watching",
                 created_at="2026-11-03T00:30:00Z")
        self.job("wait-job", lane="gpu")
        snap = fleet_status.build_snapshot(self.dir)
        # Submission order; run_id only when the record has one.
        self.assertEqual([a["key"] for a in snap["active"]],
                         ["watch-job", "run-job"])
        for a in snap["active"]:
            self.assertTrue(set(a) <= ACTIVE_KEYS)
            self.assertTrue({"key", "program", "lane"} <= set(a))
        self.assertEqual(snap["active"][1]["run_id"], 4242)
        self.assertNotIn("run_id", snap["active"][0])
        self.assertEqual(snap["running_total"], 2)
        self.assertEqual(snap["waiting_total"], 1)
        # The queued gpu lane is "waiting", not "full", so
        # the fleet as a whole is running, not saturated.
        self.assertEqual(snap["lanes"]["gpu"]["state"],
                         "waiting")
        self.assertEqual(snap["queue_state"], "running")

    def test_queue_state_waiting_when_nothing_running(self):
        self.job("wait-job")
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["queue_state"], "waiting")
        self.assertEqual(snap["lanes"]["quick"]["state"],
                         "waiting")
        self.assertEqual(snap["lanes"]["quick"]["waiting"], 1)
        self.assertEqual(snap["lanes"]["quick"]["running"], 0)

    def test_lane_states_and_full_queue_state(self):
        from unittest import mock
        caps = {"quick": 1, "normal": 2}
        with mock.patch.object(fleet_status, "_lane_caps",
                               return_value=caps):
            # quick: at cap with a queue behind it -> full.
            self.job("q-run", state="dispatched")
            self.job("q-wait")
            # normal: one of two slots busy, queue behind
            # it -> running (capacity remains), so the fleet
            # is not full while any queued lane has room.
            self.job("n-run", lane="normal", state="watching")
            self.job("n-wait", lane="normal")
            snap = fleet_status.build_snapshot(self.dir)
            self.assertEqual(snap["queue_state"], "running")
            self.assertEqual(snap["lanes"]["quick"],
                             {"cap": 1, "running": 1, "waiting": 1,
                              "state": "full"})
            self.assertEqual(snap["lanes"]["normal"],
                             {"cap": 2, "running": 1, "waiting": 1,
                              "state": "running"})
            for lane in snap["lanes"].values():
                self.assertEqual(set(lane), LANE_KEYS)
            # Fill normal's second slot: now every lane with
            # a queue is at cap and the fleet reads full.
            self.job("n-run2", lane="normal", state="dispatched")
            snap = fleet_status.build_snapshot(self.dir)
            self.assertEqual(snap["lanes"]["normal"]["state"],
                             "full")
            self.assertEqual(snap["queue_state"], "full")

    def test_unconfigured_lane_defaults_to_cap_1(self):
        self.job("x-run", lane="exotic", state="dispatched")
        self.job("x-wait", lane="exotic")
        snap = fleet_status.build_snapshot(self.dir)
        self.assertEqual(snap["lanes"]["exotic"],
                         {"cap": 1, "running": 1, "waiting": 1,
                          "state": "full"})

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


HERE = os.path.dirname(os.path.abspath(__file__))

GK_EMITTED = {
    "generated_at": "2026-11-03T12:00:00Z",
    "mode": "focused",
    "draining": False,
    "reason": "from the gatekeeper",
    "focus": {"keys": ["leg5"], "programs": [], "chats": []},
    "waiting": [
        {"key": "z-first", "program": "prog", "lane": "gpu",
         "held": False},
        {"key": "a-second", "program": "prog", "lane": "quick",
         "held": True, "priority": 90, "priority_source": "human"},
    ],
}


class Transporter(unittest.TestCase):
    """Publisher v2: the gatekeeper's emission is transported
    verbatim (plus the source label); any failure of that
    path serves the fallback derivation, labeled."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.dir, "queue"))
        # Fixture state the FALLBACK would derive from: a
        # plain pause and one local job — deliberately unlike
        # GK_EMITTED, so the two sources are distinguishable.
        write(os.path.join(self.dir, "PAUSED.json"),
              {"reason": "local pause"})
        write(os.path.join(self.dir, "queue", "local-job.json"),
              {"key": "local-job", "program": "prog", "lane": "quick",
               "state": "submitted",
               "created_at": "2026-11-03T00:00:00Z"})

    def stub(self, body, name="stub.py"):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return '"%s" "%s"' % (sys.executable, path)

    def env(self, cmd):
        from unittest import mock
        return mock.patch.dict(
            os.environ, {fleet_status.SNAPSHOT_CMD_ENV: cmd})

    def test_gatekeeper_emission_published_verbatim(self):
        cmd = self.stub("import json, sys\n"
                        "sys.stdout.write(json.dumps(%r))\n"
                        % (GK_EMITTED,))
        with self.env(cmd):
            api = FakeAPI()
            snap = fleet_status.publish(self.dir, api=api)
        self.assertEqual(snap["source"], "gatekeeper")
        self.assertEqual(set(snap), TOP_KEYS | {"source"})
        expected = dict(GK_EMITTED)
        expected["source"] = "gatekeeper"
        self.assertEqual(snap, expected)
        body = json.loads(api.puts[0][1].decode("utf-8"))
        self.assertEqual(body, expected)

    def test_failing_command_falls_back(self):
        cmd = self.stub("import sys\nsys.exit(3)\n")
        with self.env(cmd):
            snap = fleet_status.publish(self.dir, api=FakeAPI())
        self.assertEqual(snap["source"], "fallback")
        self.assertEqual(snap["mode"], "paused")
        self.assertEqual([w["key"] for w in snap["waiting"]],
                         ["local-job"])
        self.assertEqual(snap["waiting"][0]["held"], True)

    def test_garbage_output_falls_back(self):
        cmd = self.stub("print('not json at all')\n")
        with self.env(cmd):
            snap = fleet_status.obtain_snapshot(self.dir)
        self.assertEqual(snap["source"], "fallback")
        self.assertEqual(snap["mode"], "paused")

    def test_invalid_shape_falls_back(self):
        cmd = self.stub("import json, sys\n"
                        "sys.stdout.write(json.dumps("
                        "{'mode': 5, 'waiting': 'no'}))\n")
        with self.env(cmd):
            snap = fleet_status.obtain_snapshot(self.dir)
        self.assertEqual(snap["source"], "fallback")

    def test_absent_command_falls_back(self):
        with self.env("/nonexistent/gk-snapshot-stub"):
            snap = fleet_status.obtain_snapshot(self.dir)
        self.assertEqual(snap["source"], "fallback")
        self.assertEqual(snap["mode"], "paused")

    def test_timeout_falls_back(self):
        from unittest import mock
        cmd = self.stub("import time\ntime.sleep(10)\n")
        with self.env(cmd), mock.patch.object(
                fleet_status, "SNAPSHOT_TIMEOUT_S", 1):
            snap = fleet_status.obtain_snapshot(self.dir)
        self.assertEqual(snap["source"], "fallback")

    def test_snapshot_command_resolution(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(fleet_status.SNAPSHOT_CMD_ENV, None)
            argv = fleet_status.snapshot_command()
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("gk.py"))
        self.assertEqual(argv[2], "snapshot")
        with self.env('"/bin/echo" hi there'):
            self.assertEqual(fleet_status.snapshot_command(),
                             ["/bin/echo", "hi", "there"])


def load_gk_module(dirpath):
    """The spec013 battery's pattern: gk.py + config.py copied
    into a temp state dir and loaded as a module from there,
    so every state path the module reads is the fixture's."""
    import importlib.util
    for name in ("gk.py", "config.py"):
        with open(os.path.join(HERE, name), encoding="utf-8") as f:
            src = f.read()
        with open(os.path.join(dirpath, name), "w",
                  encoding="utf-8") as f:
            f.write(src)
    spec = importlib.util.spec_from_file_location(
        "gk_under_test", os.path.join(dirpath, "gk.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import datetime as _datetime_module  # noqa: E402

GK_NOW = datetime(2026, 11, 3, 12, 0, 0,
                  tzinfo=_datetime_module.timezone.utc)


class GatekeeperSnapshot(unittest.TestCase):
    """`gk snapshot`: the FR-008 shape derived by the
    gatekeeper's own code paths — FR-001 normalization,
    FR-012 ordering (asserted against dispatch_sort_key
    itself), and the sweep's admission marks."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.gk = load_gk_module(self.dir)
        self.gk.now_dt = lambda: GK_NOW
        os.makedirs(self.gk.QUEUE_DIR, exist_ok=True)

    def job(self, jid, **kw):
        base = {"id": jid, "key": jid, "program": "prog",
                "lane": "quick", "state": "submitted",
                "created_at": "2026-11-03T11:00:00Z", "flags": []}
        base.update(kw)
        write(os.path.join(self.gk.QUEUE_DIR, jid + ".json"), base)
        return base

    def put(self, name, obj):
        write(os.path.join(self.dir, name), obj)

    def ordering_jobs(self):
        # Effective priorities at GK_NOW: human-high 90
        # (human); auto-aged 69 (base 40 + fit 5 + aging 24);
        # human-mid 50 (human); human-45 45 (human) before
        # auto-fresh 45 (base 40 + fit 5 + aging 0) on the
        # human-before-auto tie-break.
        self.job("auto-fresh", expected_min=5,
                 created_at="2026-11-03T11:59:00Z")
        self.job("human-high", priority=90,
                 priority_source="human",
                 created_at="2026-11-03T11:00:00Z")
        self.job("auto-aged", expected_min=5,
                 created_at="2026-11-03T08:00:00Z")
        self.job("human-45", priority=45, priority_source="human",
                 created_at="2026-11-03T09:00:00Z")
        self.job("human-mid", priority=50,
                 priority_source="human",
                 created_at="2026-11-03T10:00:00Z")
        self.job("done-job", state="completed")

    def test_waiting_order_is_the_sweeps_dispatch_order(self):
        self.ordering_jobs()
        snap = self.gk.build_snapshot()
        self.assertEqual(snap["mode"], "running")
        self.assertEqual(snap["draining"], False)
        keys = [w["key"] for w in snap["waiting"]]
        self.assertEqual(keys, ["human-high", "auto-aged",
                                "human-mid", "human-45",
                                "auto-fresh"])
        jobs = [j for j in self.gk.load_jobs()
                if j["state"] == "submitted"]
        sweep_order = [j["key"] for j in sorted(
            jobs, key=lambda j: self.gk.dispatch_sort_key(j, GK_NOW))]
        self.assertEqual(keys, sweep_order)
        by_key = {w["key"]: w for w in snap["waiting"]}
        self.assertEqual(by_key["human-high"]["priority"], 90)
        self.assertEqual(by_key["human-high"]["priority_source"],
                         "human")
        self.assertNotIn("priority", by_key["auto-aged"])
        self.assertTrue(all(w["held"] is False
                            for w in snap["waiting"]))
        self.assertEqual(set(snap), FALLBACK_KEYS)

    def test_paused_mode_reason_and_maintenance_exemption(self):
        self.put("PAUSED.json", {"reason": "owner hold"})
        self.job("plain")
        self.job("maint", maintenance=True)
        snap = self.gk.build_snapshot()
        self.assertEqual(snap["mode"], "paused")
        self.assertEqual(snap["reason"], "owner hold")
        held = {w["key"]: w["held"] for w in snap["waiting"]}
        self.assertEqual(held, {"plain": True, "maint": False})

    def test_focused_mode_legacy_merge_and_held_matrix(self):
        self.put("PAUSED.json",
                 {"reason": "focus run", "except_key": "leg5",
                  "except_programs": ["chronoc"],
                  "except_chats": ["chat-9"]})
        self.job("leg5j2", program="other")
        self.job("leg6x", program="other")
        self.job("anykey", program="chronoc")
        self.job("anykey2", program="other", notify_chat="chat-9")
        self.job("maint", program="other", maintenance=True)
        snap = self.gk.build_snapshot()
        self.assertEqual(snap["mode"], "focused")
        self.assertEqual(snap["focus"]["keys"], ["leg5"])
        held = {w["key"]: w["held"] for w in snap["waiting"]}
        self.assertEqual(held, {"leg5j2": False, "leg6x": True,
                                "anykey": False, "anykey2": False,
                                "maint": False})

    def test_stopped_holds_everything_with_stop_reason(self):
        self.put("PAUSED.json", {"reason": "old pause reason"})
        self.put("STOPPED.json", {"reason": "owner stop order"})
        self.job("j1")
        self.job("maint", maintenance=True)
        snap = self.gk.build_snapshot()
        self.assertEqual(snap["mode"], "stopped")
        self.assertEqual(snap["reason"], "owner stop order")
        self.assertTrue(all(w["held"] for w in snap["waiting"]))

    def test_draining_from_pause_flag_keeps_paused_mode(self):
        self.put("PAUSED.json", {"reason": "winding down",
                                 "drain": True})
        snap = self.gk.build_snapshot()
        self.assertEqual(snap["mode"], "paused")
        self.assertEqual(snap["draining"], True)

    def test_draining_from_host_hold_marks_lane_jobs_held(self):
        self.put("hosts.json", {"desktop": {"hold": True}})
        self.job("plain")
        self.job("maint-plain", maintenance=True)
        self.job("gk-reboot-desktop-1", maintenance=True)
        snap = self.gk.build_snapshot()
        self.assertEqual(snap["mode"], "running")
        self.assertEqual(snap["draining"], True)
        held = {w["key"]: w["held"] for w in snap["waiting"]}
        # Every default lane routes to host "desktop": its
        # hold holds them all — except the gatekeeper's own
        # signal jobs (FR-015), which are the drain mechanism.
        self.assertEqual(held, {"plain": True, "maint-plain": True,
                                "gk-reboot-desktop-1": False})

    def test_queue_fields_idle_when_empty(self):
        from unittest import mock
        caps = {"quick": 1, "normal": 3}
        with mock.patch.object(self.gk, "LANE_CONCURRENCY",
                               caps):
            snap = self.gk.build_snapshot()
        self.assertEqual(snap["active"], [])
        self.assertEqual(snap["running_total"], 0)
        self.assertEqual(snap["waiting_total"], 0)
        self.assertEqual(snap["queue_state"], "idle")
        self.assertEqual(snap["lanes"], {
            "normal": {"cap": 3, "running": 0, "waiting": 0,
                       "state": "idle"},
            "quick": {"cap": 1, "running": 0, "waiting": 0,
                      "state": "idle"}})

    def test_queue_fields_active_entries_and_run_id(self):
        self.job("run-job", state="dispatched", run_id=4242,
                 created_at="2026-11-03T11:30:00Z")
        self.job("watch-job", lane="long", state="watching",
                 created_at="2026-11-03T11:15:00Z")
        self.job("wait-job", lane="gpu")
        snap = self.gk.build_snapshot()
        # Submission order; run_id only when the record has
        # one — the same shape the fallback emits.
        self.assertEqual([a["key"] for a in snap["active"]],
                         ["watch-job", "run-job"])
        for a in snap["active"]:
            self.assertTrue(set(a) <= ACTIVE_KEYS)
            self.assertTrue({"key", "program", "lane"}
                            <= set(a))
        self.assertEqual(snap["active"][1]["run_id"], 4242)
        self.assertNotIn("run_id", snap["active"][0])
        self.assertEqual(snap["running_total"], 2)
        self.assertEqual(snap["waiting_total"], 1)
        # The queued gpu lane is "waiting", not "full", so
        # the fleet as a whole is running, not saturated.
        self.assertEqual(snap["lanes"]["gpu"]["state"],
                         "waiting")
        self.assertEqual(snap["queue_state"], "running")

    def test_queue_state_waiting_when_nothing_running(self):
        self.job("wait-job")
        snap = self.gk.build_snapshot()
        self.assertEqual(snap["queue_state"], "waiting")
        self.assertEqual(snap["lanes"]["quick"]["state"],
                         "waiting")
        self.assertEqual(snap["lanes"]["quick"]["waiting"], 1)
        self.assertEqual(snap["lanes"]["quick"]["running"], 0)

    def test_queue_fields_lane_states_and_full(self):
        from unittest import mock
        caps = {"quick": 1, "normal": 2}
        with mock.patch.object(self.gk, "LANE_CONCURRENCY",
                               caps):
            # quick: at cap with a queue behind it -> full.
            self.job("q-run", state="dispatched")
            self.job("q-wait")
            # normal: one of two slots busy, queue behind
            # it -> running (capacity remains), so the
            # fleet is not full while any queued lane has
            # room.
            self.job("n-run", lane="normal", state="watching")
            self.job("n-wait", lane="normal")
            snap = self.gk.build_snapshot()
            self.assertEqual(snap["queue_state"], "running")
            self.assertEqual(snap["lanes"]["quick"],
                             {"cap": 1, "running": 1,
                              "waiting": 1, "state": "full"})
            self.assertEqual(snap["lanes"]["normal"],
                             {"cap": 2, "running": 1,
                              "waiting": 1, "state": "running"})
            for lane in snap["lanes"].values():
                self.assertEqual(set(lane), LANE_KEYS)
            # Fill normal's second slot: now every lane
            # with a queue is at cap, fleet reads full.
            self.job("n-run2", lane="normal",
                     state="dispatched")
            snap = self.gk.build_snapshot()
            self.assertEqual(snap["lanes"]["normal"]["state"],
                             "full")
            self.assertEqual(snap["queue_state"], "full")

    def test_queue_fields_unconfigured_lane_cap_1(self):
        from unittest import mock
        with mock.patch.object(self.gk, "LANE_CONCURRENCY",
                               {"quick": 1}):
            self.job("x-run", lane="exotic",
                     state="dispatched")
            self.job("x-wait", lane="exotic")
            snap = self.gk.build_snapshot()
        self.assertEqual(snap["lanes"]["exotic"],
                         {"cap": 1, "running": 1, "waiting": 1,
                          "state": "full"})

    def test_cli_snapshot_prints_one_json_object(self):
        import subprocess
        self.put("hosts.json", {"desktop": {"hold": True}})
        self.job("plain")
        out = subprocess.run(
            [sys.executable, os.path.join(self.dir, "gk.py"),
             "snapshot"], capture_output=True, text=True,
            timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        snap = json.loads(out.stdout)
        self.assertEqual(set(snap), FALLBACK_KEYS)
        self.assertEqual(snap["draining"], True)
        self.assertEqual(snap["waiting"][0]["key"], "plain")
        self.assertEqual(snap["waiting"][0]["held"], True)
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


class _FakeResponse:
    """A urlopen result whose read() truncates (IncompleteRead)
    a set number of times before returning the body."""

    def __init__(self, body, truncations):
        self.body = body
        self.truncations = truncations

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        import http.client
        if self.truncations > 0:
            self.truncations -= 1
            raise http.client.IncompleteRead(b"partial", 5)
        return self.body


class ContentsAPIReadRetry(unittest.TestCase):
    """The publisher's contents-API GET reads retry truncated
    responses (2026-11-06: nine consecutive publish cycles lost
    to IncompleteRead through the egress proxy). PUTs stay
    single-attempt and HTTP errors are never retried."""

    def _api(self):
        return fleet_status.ContentsAPI(token="test-token")

    def test_get_succeeds_after_one_truncation(self):
        import unittest.mock
        res = _FakeResponse(b'{"sha": "abc"}', truncations=1)
        with unittest.mock.patch("urllib.request.urlopen",
                                 return_value=res) as mock_open, \
                unittest.mock.patch("fleet_status.time.sleep"):
            self.assertEqual(self._api().get_sha("p"), "abc")
            self.assertEqual(mock_open.call_count, 2)

    def test_get_gives_up_after_three_attempts(self):
        import http.client
        import unittest.mock
        res = _FakeResponse(b"{}", truncations=99)
        with unittest.mock.patch("urllib.request.urlopen",
                                 return_value=res) as mock_open, \
                unittest.mock.patch("fleet_status.time.sleep"):
            with self.assertRaises(http.client.IncompleteRead):
                self._api().get_sha("p")
            self.assertEqual(mock_open.call_count, 3)

    def test_put_is_not_retried(self):
        import http.client
        import unittest.mock
        res = _FakeResponse(b"{}", truncations=99)
        with unittest.mock.patch("urllib.request.urlopen",
                                 return_value=res) as mock_open, \
                unittest.mock.patch("fleet_status.time.sleep"):
            with self.assertRaises(http.client.IncompleteRead):
                self._api()._request("PUT", "p", payload={"x": 1})
            self.assertEqual(mock_open.call_count, 1)

    def test_get_404_returns_none_without_retry(self):
        import unittest.mock
        import urllib.error
        err = urllib.error.HTTPError("u", 404, "Not Found", None, None)
        with unittest.mock.patch("urllib.request.urlopen",
                                 side_effect=err) as mock_open, \
                unittest.mock.patch("fleet_status.time.sleep"):
            self.assertIsNone(self._api()._request("GET", "p"))
            self.assertEqual(mock_open.call_count, 1)


if __name__ == "__main__":
    unittest.main()

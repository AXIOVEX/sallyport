#!/usr/bin/env python3
"""Chaos battery for the backchannel agent (spec 010 T012).

Runs the REAL backchannel/agent.sh against a local file://
channel repo and subjects it to the failure cases:

  1. start: heartbeat appears carrying a passing self-test
  2. a command executes and its output lands (id-matched)
  3. duplicate id with a different body is NOT re-executed
  4. corrupt cmd.json: agent survives, heartbeat keeps moving
  5. STOP file: execution halts, heartbeat reports stopped
  6. STOP removed: execution resumes
  7. kill -9 + restart: heartbeat resumes, the last executed
     id is NOT replayed (the replay guard survives), and a
     fresh id executes
  8. a multi-line body ending in a newline (the `bc.py send
     --file` shape) executes byte-exactly (issue #36)
  9. output larger than the stored tail: the record carries
     exactly the last TAIL_BYTES, honestly a tail
 10. exit-code honesty: a body's own exit status, death by
     signal, cooperative timeout, and a TERM-ignoring body
     the wrapper must still bound (issue #36)
 11. a genuinely mismatched sha256 is still refused (exit
     65, nothing executed) and the loop carries on
 12. git stall: with the remote blackholed (every git network
     call hangs until its timeout), the heartbeat keeps a
     bounded cadence - it is written at the top of the cycle,
     before any git operation - and the self-test refreshes
     periodically instead of only at start (2026-11-03 field
     lesson: stacked 120s timeouts stretched the beat to 904s
     and the tray honestly reported DISCONNECTED)
 13. sync recovery (0.2.2): with the clone diverged (an
     unpushed local commit plus a moved remote - the standing
     state once the fleet-status publisher commits every 2
     minutes), the sync step recovers by rebase in the same
     cycle, the remote file lands in the working tree, the
     heartbeat reports sync ok, no rebase is left in progress,
     and an oversized envelope timeout is clamped agent-side
 14. push rejected (pre-receive hook), pull healthy: the
     agent keeps beating locally, the REMOTE heartbeat freezes
     at the last delivered beat (the honest staleness signal
     consumers judge by), the rejection is logged, and when
     the rejection clears the backlog delivers
 15. envelope timeout edges: 0 (GNU timeout treats a 0
     duration as no timeout - an unbounded body would stall
     the loop), negative, and a 29-digit value (bash -gt
     errors on it and reads as not-clamped) all end up
     bounded agent-side
 16. SSH transport failure (deploy-key route, dead port):
     the agent stays alive and beats locally, the heartbeat
     reports sync not-ok and the self-test reports the repo
     unreachable - a dead transport is visible, not silent
 17. replay-guard loss: with .last_executed_id deleted and
     the same cmd.json re-pushed, the guard is re-seeded
     from out.json and the command is NOT replayed
     (FR-004(b) survives the guard file's loss)
 18. kill during execution: the interrupted command is
     re-run from the top after the restart (at-least-once
     semantics, pinned deliberately), and its result lands
 19. STOP arriving mid-execution: the running command is
     NOT interrupted (the spec gates new commands); the
     next command is halted and the heartbeat reports
     stopped
 20. channel isolation (SYNCBC_CHANNEL, PR #66): an agent
     pointed at a second channel (syncyard-laptop) executes
     there, heartbeats there, and leaves the default
     channel's state untouched

Everything lives in a temp dir; the agent is pointed at it via
SYNCBC_BASE / SYNCBC_REPO_URL / SYNCBC_POLL_S=1. Stdlib only.

Run: python3 backchannel/test_chaos.py   (a few minutes)
"""
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(HERE, "agent.sh")
CHANNEL = "channels/syncyard-dev"
fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f" — {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        fails.append(name)


def run(cmd, cwd=None, env=None, timeout=60):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)


def git_env(home):
    env = dict(os.environ)
    env["HOME"] = home
    return env


def wait_for(pred, timeout_s, what):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        val = pred()
        if val:
            return val
        time.sleep(0.5)
    print(f"  (timeout waiting for {what})", flush=True)
    return None


class Rig:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="bc-chaos-")
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home)
        with open(os.path.join(self.home, ".gitconfig"), "w") as f:
            f.write("[user]\n\tname = Chaos Test\n\temail = chaos@test\n"
                    "[init]\n\tdefaultBranch = main\n")
        self.env = git_env(self.home)
        self.remote = os.path.join(self.root, "remote.git")
        run(["git", "init", "--bare", "-b", "main", self.remote], env=self.env)
        self.driver = os.path.join(self.root, "driver")
        run(["git", "clone", "-q", self.remote, self.driver], env=self.env)
        chan = os.path.join(self.driver, CHANNEL)
        os.makedirs(chan)
        with open(os.path.join(chan, "README.md"), "w") as f:
            f.write("chaos channel\n")
        run(["git", "-C", self.driver, "add", "-A"], env=self.env)
        run(["git", "-C", self.driver, "commit", "-q", "-m", "seed"],
            env=self.env)
        run(["git", "-C", self.driver, "push", "-q", "origin", "main"],
            env=self.env)
        self.base = os.path.join(self.root, "agent-base")
        self.agent_env = dict(self.env)
        self.agent_env.update({
            "SYNCBC_BASE": self.base,
            "SYNCBC_REPO_URL": "file://" + self.remote,
            "SYNCBC_POLL_S": "1",
            "SYNCBC_TIMEOUT_S": "30",
        })
        self.proc = None
        self.agent_log = os.path.join(self.root, "agent-stdout.log")

    # -- channel helpers -------------------------------------------------
    def chan_file(self, name):
        return os.path.join(self.base, "repo", CHANNEL, name)

    def read_chan_json(self, name):
        try:
            with open(self.chan_file(name)) as f:
                return json.load(f)
        except Exception:
            return None

    def push(self, files):
        """files: {relpath: content-or-None(delete)} committed+pushed.
        The agent pushes heartbeat/out commits too, so the driver
        rebases onto the remote head before (and if needed during)
        each push."""
        run(["git", "-C", self.driver, "pull", "-q", "--rebase"], env=self.env)
        for rel, content in files.items():
            p = os.path.join(self.driver, rel)
            if content is None:
                run(["git", "-C", self.driver, "rm", "-q", "--ignore-unmatch",
                     rel], env=self.env)
                if os.path.exists(p):
                    os.unlink(p)
            else:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as f:
                    f.write(content)
                run(["git", "-C", self.driver, "add", rel], env=self.env)
        run(["git", "-C", self.driver, "commit", "-q", "-m", "chaos push",
             "--allow-empty"], env=self.env)
        r = run(["git", "-C", self.driver, "push", "-q", "origin", "main"],
                env=self.env)
        if r.returncode != 0:  # the agent pushed in between; rebase + retry
            run(["git", "-C", self.driver, "pull", "-q", "--rebase"],
                env=self.env)
            r = run(["git", "-C", self.driver, "push", "-q", "origin", "main"],
                    env=self.env)
        assert r.returncode == 0, "driver push failed: " + r.stderr

    def push_cmd(self, cid, body, timeout_s=None):
        doc = {"id": cid, "body": body,
               "sha256": hashlib.sha256(body.encode()).hexdigest(),
               "created_at": "2026-10-31T00:00:00Z"}
        if timeout_s is not None:
            doc["timeout_s"] = timeout_s
        self.push({CHANNEL + "/cmd.json": json.dumps(doc, indent=1)})

    # -- agent process ---------------------------------------------------
    def start_agent(self):
        out = open(self.agent_log, "ab")
        self.proc = subprocess.Popen(
            ["bash", AGENT], env=self.agent_env, stdout=out,
            stderr=subprocess.STDOUT, start_new_session=True)

    def kill_agent(self, sig=signal.SIGKILL):
        if self.proc and self.proc.poll() is None:
            os.killpg(os.getpgid(self.proc.pid), sig)
            self.proc.wait(timeout=15)

    def agent_log_text(self):
        try:
            with open(os.path.join(self.base, "agent.log")) as f:
                return f.read()
        except Exception:
            return ""

    def cleanup(self):
        self.kill_agent()
        if os.environ.get("CHAOS_KEEP"):
            print(f"rig preserved at {self.root}")
        else:
            shutil.rmtree(self.root, ignore_errors=True)


def main():
    rig = Rig()
    try:
        # 1: start + self-test heartbeat
        rig.start_agent()
        hb = wait_for(lambda: rig.read_chan_json("heartbeat.json"),
                      60, "first heartbeat")
        check("start: heartbeat written", hb is not None)
        # The startup beat is written BEFORE the startup
        # self-test runs (0.2.2: the beat never waits on a git
        # operation - the self-test's own ls-remote is one), so
        # the first heartbeat on disk legitimately carries no
        # self_test yet. Assert on a beat that carries a passing
        # self-test, not on first appearance (the scenario-7
        # lesson; a first-appearance read here flaked once in
        # three full runs, 2026-11-04).
        def passing_selftest_hb():
            h = rig.read_chan_json("heartbeat.json") or {}
            return h if (h.get("self_test") or {}).get("ok") is True \
                else None

        hb = wait_for(passing_selftest_hb, 60,
                      "passing self-test heartbeat") or hb
        st = (hb or {}).get("self_test") or {}
        check("start: self-test present and ok", st.get("ok") is True,
              f"self_test={st}")
        check("start: self-test checks all true",
              all((st.get("checks") or {}).values()) and len(st.get("checks") or {}) == 6,
              f"checks={st.get('checks')}")

        # 2: a command executes
        rig.push_cmd("chaos-1", "echo CHAOS_ONE")
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-1"
                       and rig.read_chan_json("out.json"), 45, "chaos-1 out")
        check("execute: out.json id-matched", out is not None)
        check("execute: stdout carries the marker",
              out is not None and "CHAOS_ONE" in (out.get("stdout_tail") or ""))

        # 3: duplicate id, different body: NOT re-executed
        rig.push_cmd("chaos-1", "echo CHAOS_TWO")
        time.sleep(5)
        out = rig.read_chan_json("out.json") or {}
        check("duplicate: out still the first execution",
              "CHAOS_ONE" in (out.get("stdout_tail") or "")
              and "CHAOS_TWO" not in (out.get("stdout_tail") or ""))
        check("duplicate: executed exactly once in agent log",
              rig.agent_log_text().count("executing id=chaos-1") == 1)

        # 4: corrupt cmd.json
        hb_ts_before = (rig.read_chan_json("heartbeat.json") or {}).get("ts")
        rig.push({CHANNEL + "/cmd.json": "this is not json{{{\n"})
        check("corrupt: agent process alive", rig.proc.poll() is None)
        moved = wait_for(lambda: (rig.read_chan_json("heartbeat.json") or {}).get("ts")
                         not in (None, hb_ts_before), 25, "heartbeat advance")
        check("corrupt: heartbeat keeps moving", bool(moved),
              f"ts stuck at {hb_ts_before}")
        out = rig.read_chan_json("out.json") or {}
        check("corrupt: nothing new executed", out.get("id") == "chaos-1")

        # 5: STOP halts execution, heartbeat reports stopped
        rig.push({CHANNEL + "/STOP": "chaos test\n"})
        rig.push_cmd("chaos-2", "echo CHAOS_STOPPED")
        stopped = wait_for(lambda: (rig.read_chan_json("heartbeat.json") or {}).get("stopped") is True,
                           25, "stopped heartbeat")
        check("STOP: heartbeat reports stopped=true", bool(stopped))
        time.sleep(3)
        out = rig.read_chan_json("out.json") or {}
        check("STOP: queued command not executed", out.get("id") != "chaos-2")

        # 6: STOP removed: execution resumes
        rig.push({CHANNEL + "/STOP": None})
        rig.push_cmd("chaos-3", "echo CHAOS_THREE")
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-3"
                       and rig.read_chan_json("out.json"), 45, "chaos-3 out")
        check("resume: command executes after STOP removed",
              out is not None and "CHAOS_THREE" in (out.get("stdout_tail") or ""))

        # 7: kill -9 + restart: no replay, fresh ids execute
        hb_ts_pre = (rig.read_chan_json("heartbeat.json") or {}).get("ts")
        rig.kill_agent(signal.SIGKILL)
        time.sleep(1)
        rig.start_agent()
        # The pre-kill heartbeat file persists in the clone, so a bare
        # ts read proves nothing (it returns the stale value instantly
        # and races the restarted agent's first cycle - the PR #26 CI
        # flake). Require the ts to ADVANCE past the pre-kill value,
        # twice (two full post-restart cycles), before the replay probe.
        def advanced_past(prev):
            ts = (rig.read_chan_json("heartbeat.json") or {}).get("ts")
            return ts if ts and prev and ts > prev else None
        hb_first = wait_for(lambda: advanced_past(hb_ts_pre), 60,
                            "post-restart heartbeat advance")
        check("restart: heartbeat resumes", hb_first is not None)
        hb_second = wait_for(lambda: advanced_past(hb_first), 60,
                             "second post-restart heartbeat") if hb_first else None
        check("restart: heartbeat advances twice", hb_second is not None)
        rig.push_cmd("chaos-3", "echo CHAOS_REPLAY")
        # Poll the whole window: any appearance of the replay body in
        # out.json fails fast; a full quiet window is the pass.
        replayed = None
        deadline = time.time() + 12
        while time.time() < deadline:
            out = rig.read_chan_json("out.json") or {}
            if "CHAOS_REPLAY" in (out.get("stdout_tail") or ""):
                replayed = out
                break
            time.sleep(0.5)
        out = rig.read_chan_json("out.json") or {}
        check("restart: last executed id not replayed",
              replayed is None
              and "CHAOS_THREE" in (out.get("stdout_tail") or ""))
        check("restart: chaos-3 executed exactly once across restart",
              rig.agent_log_text().count("executing id=chaos-3") == 1)
        rig.push_cmd("chaos-4", "echo CHAOS_FOUR")
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-4"
                       and rig.read_chan_json("out.json"), 45, "chaos-4 out")
        check("restart: fresh id executes",
              out is not None and "CHAOS_FOUR" in (out.get("stdout_tail") or ""))

        # 8: multi-line body ending in a newline — the shape of
        # every `bc.py send --file` (issue #36). The body reports
        # the byte count of the script file it runs as, so the
        # check pins byte-exact delivery, not just execution.
        ml_body = ("echo CHAOS_ML_ONE\n"
                   "echo \"CHAOS_BODY_BYTES $(wc -c < \"$0\")\"\n"
                   "echo CHAOS_ML_TWO\n")
        rig.push_cmd("chaos-5", ml_body)
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-5"
                       and rig.read_chan_json("out.json"), 45, "chaos-5 out")
        ml_out = (out or {}).get("stdout_tail") or ""
        check("multiline: trailing-newline body executes (not sha-refused)",
              out is not None and out.get("exit_code") == 0
              and "CHAOS_ML_ONE" in ml_out and "CHAOS_ML_TWO" in ml_out,
              f"out={out}")
        check("multiline: executed bytes are the body's exact bytes",
              f"CHAOS_BODY_BYTES {len(ml_body.encode())}" in ml_out,
              f"stdout={ml_out!r}")

        # 9: output larger than the stored tail. The record must
        # carry exactly the LAST TAIL_BYTES of the full output —
        # a tail, honestly: the end marker present, the start
        # marker gone, byte-for-byte the known suffix. The body
        # is a single line without a trailing newline so this
        # case pins write_out's tail semantics alone, separate
        # from the case-8 delivery fix.
        tail_bytes = 8000  # agent default SYNCBC_TAIL_BYTES
        big_lines = "".join(f"LINE {i} " + "A" * 44 + "\n"
                            for i in range(1, 401))
        big_full = "CHAOS_BIG_START\n" + big_lines + "CHAOS_BIG_END\n"
        big_body = ("printf 'CHAOS_BIG_START\\n'; i=1; "
                    "while [ $i -le 400 ]; do "
                    "printf 'LINE %d " + "A" * 44 + "\\n' $i; "
                    "i=$((i+1)); done; "
                    "printf 'CHAOS_BIG_END\\n'")
        assert len(big_full.encode()) > tail_bytes
        rig.push_cmd("chaos-6", big_body)
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-6"
                       and rig.read_chan_json("out.json"), 60, "chaos-6 out")
        stored = (out or {}).get("stdout_tail") or ""
        expect_tail = big_full.encode()[-tail_bytes:].decode()
        check("bigtail: exit code honest (0)",
              out is not None and out.get("exit_code") == 0)
        check("bigtail: stored stdout is exactly the last TAIL_BYTES",
              stored == expect_tail,
              f"stored {len(stored.encode())}B vs expected "
              f"{len(expect_tail.encode())}B")
        check("bigtail: tail semantics visible (end kept, start gone)",
              stored.endswith("CHAOS_BIG_END\n")
              and "CHAOS_BIG_START" not in stored)

        # 10: exit-code honesty (issue #36). Single-line bodies
        # without a trailing newline, so these pin the execution
        # wrapper only, independent of the case-8 fix.
        rig.push_cmd("chaos-7", "echo CHAOS_EXIT_THREE; exit 3")
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-7"
                       and rig.read_chan_json("out.json"), 45, "chaos-7 out")
        check("exitcode: body's own status recorded (3)",
              out is not None and out.get("exit_code") == 3
              and "CHAOS_EXIT_THREE" in (out.get("stdout_tail") or ""),
              f"out={out}")

        rig.push_cmd("chaos-8", "kill -9 $$")
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-8"
                       and rig.read_chan_json("out.json"), 45, "chaos-8 out")
        check("exitcode: death by SIGKILL recorded (137, never 0)",
              out is not None and out.get("exit_code") == 137,
              f"out={out}")

        rig.push_cmd("chaos-9", "sleep 60", timeout_s=3)
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-9"
                       and rig.read_chan_json("out.json"), 60, "chaos-9 out")
        check("exitcode: cooperative timeout recorded (124, never 0)",
              out is not None and out.get("exit_code") == 124,
              f"out={out}")

        # A body that ignores SIGTERM must still be bounded by
        # the wrapper (escalation), not run out its natural life
        # while the single-threaded agent — and its heartbeat —
        # stalls behind it.
        rig.push_cmd("chaos-10",
                     "trap '' TERM; echo CHAOS_STUBBORN_START; "
                     "sleep 60; echo CHAOS_STUBBORN_END",
                     timeout_s=3)
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-10"
                       and rig.read_chan_json("out.json"), 120, "chaos-10 out")
        span = None
        if out and out.get("started_at") and out.get("finished_at"):
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            span = (datetime.strptime(out["finished_at"], fmt)
                    - datetime.strptime(out["started_at"], fmt)).total_seconds()
        stub_out = (out or {}).get("stdout_tail") or ""
        check("exitcode: TERM-ignoring body bounded by the wrapper",
              out is not None and out.get("exit_code") not in (0, None)
              and "CHAOS_STUBBORN_START" in stub_out
              and "CHAOS_STUBBORN_END" not in stub_out
              and span is not None and span <= 25,
              f"out={out} span={span}")

        # 11: the sha refusal is a security property and must
        # survive the byte-exact fix: a body whose claimed
        # sha256 belongs to a DIFFERENT body is refused with
        # exit 65 and never executes; the next honest command
        # still runs.
        doc = {"id": "chaos-11", "body": "echo CHAOS_TAMPERED",
               "sha256": hashlib.sha256(b"some other body").hexdigest(),
               "created_at": "2026-10-31T00:00:00Z"}
        rig.push({CHANNEL + "/cmd.json": json.dumps(doc, indent=1)})
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-11"
                       and rig.read_chan_json("out.json"), 45, "chaos-11 out")
        check("refusal: genuine sha mismatch refused (exit 65)",
              out is not None and out.get("exit_code") == 65
              and "CHAOS_TAMPERED" not in (out.get("stdout_tail") or "")
              and "SHA MISMATCH id=chaos-11" in rig.agent_log_text(),
              f"out={out}")
        rig.push_cmd("chaos-12", "echo CHAOS_AFTER_REFUSAL")
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-12"
                       and rig.read_chan_json("out.json"), 45, "chaos-12 out")
        check("refusal: loop unharmed, next command executes",
              out is not None and out.get("exit_code") == 0
              and "CHAOS_AFTER_REFUSAL" in (out.get("stdout_tail") or ""))

        # 12: git stall (2026-11-03 field lesson). Restart the
        # agent with a 6s git timeout and a 3-cycle self-test
        # refresh. First, against the healthy remote: the
        # self-test must refresh on cadence, with no restart.
        # Then blackhole the agent clone's remote: a listener
        # that accepts TCP and never answers, so every git
        # network operation hangs until its timeout kills it.
        # The heartbeat is written at the top of the cycle, before
        # any git operation, and again after execution - so even
        # with pull + the whole push stack timing out, beats land
        # at most (push stack + sleep) apart (~19s here). An agent
        # that beats only at the end of the cycle gaps ~25s.
        with open(AGENT) as fh:
            agent_src = fh.read()
        check("gitstall: shipped git timeout default is 45s",
              "SYNCBC_GIT_TIMEOUT_S:-45" in agent_src)
        rig.kill_agent(sig=signal.SIGTERM)
        time.sleep(1)
        for var in ("http_proxy", "https_proxy", "HTTP_PROXY",
                    "HTTPS_PROXY"):
            rig.agent_env.pop(var, None)
        rig.agent_env["no_proxy"] = "127.0.0.1"
        rig.agent_env["SYNCBC_GIT_TIMEOUT_S"] = "6"
        rig.agent_env["SYNCBC_SELFTEST_EVERY"] = "3"
        selftests_before = rig.agent_log_text().count("self-test written")
        rig.start_agent()
        refreshed = wait_for(
            lambda: rig.agent_log_text().count("self-test written")
            >= selftests_before + 3,  # start-up + two refreshes
            60, "self-test refreshes")
        check("gitstall: self-test refreshes without a restart",
              refreshed is not None,
              f"before={selftests_before} "
              f"after={rig.agent_log_text().count('self-test written')}")
        blackhole = socket.socket()
        blackhole.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blackhole.bind(("127.0.0.1", 0))
        blackhole.listen(8)
        bh_port = blackhole.getsockname()[1]
        bh_socks = []

        def bh_accept():
            while True:
                try:
                    conn, _ = blackhole.accept()
                    bh_socks.append(conn)
                except OSError:
                    return
        bh_thread = threading.Thread(target=bh_accept, daemon=True)
        bh_thread.start()
        clone = os.path.join(rig.base, "repo")
        run(["git", "-C", clone, "remote", "set-url", "origin",
             f"http://127.0.0.1:{bh_port}/chan.git"], env=rig.env)
        beats = []
        last_ts = None
        deadline = time.time() + 75
        while time.time() < deadline and len(beats) < 5:
            ts = (rig.read_chan_json("heartbeat.json") or {}).get("ts")
            if ts and ts != last_ts:
                beats.append((time.time(), ts))
                last_ts = ts
            time.sleep(0.4)
        gaps = [round(beats[i + 1][0] - beats[i][0], 1)
                for i in range(len(beats) - 1)]
        check("gitstall: heartbeat keeps beating against a dead remote",
              len(beats) >= 4, f"beats={len(beats)} gaps={gaps}")
        check("gitstall: no beat gap reaches the stacked-timeout span",
              bool(gaps) and max(gaps) <= 22,
              f"gaps={gaps}")
        blackhole.close()
        for conn in bh_socks:
            try:
                conn.close()
            except OSError:
                pass

        # 13: sync recovery (0.2.2). Restore the real remote,
        # stop the agent, and diverge the clone by hand: one
        # unpushed local commit in the agent clone, one new
        # remote commit (a publisher-style file) from the
        # driver. An ff-only pull can never land the remote
        # file from this state; the sync step's rebase
        # fallback must, in the same cycle, and the heartbeat
        # must say sync is ok.
        run(["git", "-C", clone, "remote", "set-url", "origin",
             "file://" + rig.remote], env=rig.env)
        rig.kill_agent(sig=signal.SIGTERM)
        time.sleep(1)
        with open(os.path.join(clone, CHANNEL, "local-only.txt"),
                  "w") as fh:
            fh.write("unpushed local commit\n")
        run(["git", "-C", clone, "add", "-A"], env=rig.env)
        run(["git", "-C", clone, "commit", "-q", "-m",
             "local divergence"], env=rig.env)
        rig.push({CHANNEL + "/fleet-status.json":
                  '{\n "marker": "CHAOS13_REMOTE"\n}\n'})
        rig.start_agent()

        def landed():
            try:
                with open(os.path.join(clone, CHANNEL,
                                       "fleet-status.json")) as fh:
                    return "CHAOS13_REMOTE" in fh.read()
            except OSError:
                return False
        got = wait_for(landed, 60, "fleet-status landing")
        check("sync: diverged clone recovers, remote file lands",
              got is True)
        hb = wait_for(
            lambda: (rig.read_chan_json("heartbeat.json") or {})
            .get("sync", {}).get("ok") is True
            and rig.read_chan_json("heartbeat.json"),
            30, "sync ok heartbeat")
        check("sync: heartbeat reports sync ok",
              hb is not None, f"hb={hb}")
        check("sync: recovery logged as a rebase",
              "pull recovered via rebase" in rig.agent_log_text())
        check("sync: no rebase left in progress",
              not os.path.exists(os.path.join(clone, ".git",
                                              "rebase-merge"))
              and not os.path.exists(os.path.join(clone, ".git",
                                                  "rebase-apply")))
        with open(AGENT) as fh:
            agent_src = fh.read()
        check("clamp: shipped max-timeout default is 600",
              "SYNCBC_MAX_TIMEOUT_S:-600" in agent_src)
        rig.push_cmd("chaos-clamp", "echo CHAOS_CLAMP_OK",
                     timeout_s=9999)
        out = wait_for(
            lambda: (rig.read_chan_json("out.json") or {}).get("id")
            == "chaos-clamp" and rig.read_chan_json("out.json"),
            45, "chaos-clamp out")
        check("clamp: oversized envelope timeout clamped, "
              "command still runs",
              out is not None and out.get("exit_code") == 0
              and "clamped to SYNCBC_MAX_TIMEOUT_S=600"
              in rig.agent_log_text(),
              f"out={out}")

        # 14: push rejected, pull healthy. A pre-receive hook
        # on the bare remote refuses every push while pulls
        # keep working - the GitHub-side shape of a revoked
        # credential or a protected branch. The agent must
        # keep cycling and beating locally; the remote
        # heartbeat freezes at the last delivered beat, which
        # is exactly the staleness consumers judge by; and
        # once the rejection clears, the stacked local commits
        # deliver in order.
        hook = os.path.join(rig.remote, "hooks", "pre-receive")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\nexit 1\n")
        os.chmod(hook, 0o755)

        def remote_hb_ts():
            run(["git", "-C", rig.driver, "fetch", "-q", "origin"],
                env=rig.env)
            r = run(["git", "-C", rig.driver, "show",
                     "origin/main:" + CHANNEL + "/heartbeat.json"],
                    env=rig.env)
            if r.returncode != 0:
                return None
            try:
                return json.loads(r.stdout).get("ts")
            except ValueError:
                return None

        frozen_ts = remote_hb_ts()
        local_before = (rig.read_chan_json("heartbeat.json")
                        or {}).get("ts")

        def local_beat_after(prev):
            ts = (rig.read_chan_json("heartbeat.json") or {}).get("ts")
            return ts if ts and prev and ts > prev else None

        adv1 = wait_for(lambda: local_beat_after(local_before), 45,
                        "local beat under push rejection")
        adv2 = wait_for(lambda: local_beat_after(adv1), 45,
                        "second local beat under push rejection") \
            if adv1 else None
        check("pushfail: agent keeps beating locally while every "
              "push is rejected", adv2 is not None)
        check("pushfail: remote heartbeat frozen at the last "
              "delivered beat (honest staleness for consumers)",
              frozen_ts is not None and remote_hb_ts() == frozen_ts,
              f"frozen={frozen_ts} now={remote_hb_ts()}")
        check("pushfail: the rejection is logged",
              "push failed" in rig.agent_log_text())
        check("pushfail: agent process alive",
              rig.proc.poll() is None)
        os.unlink(hook)

        def remote_advanced():
            ts = remote_hb_ts()
            return ts if ts and frozen_ts and ts > frozen_ts \
                else None

        caught = wait_for(remote_advanced, 60,
                          "remote catch-up after hook removal")
        check("pushfail: delivery resumes when the rejection "
              "clears", caught is not None,
              f"frozen={frozen_ts} now={remote_hb_ts()}")

        # 15: envelope timeout edges. Restart with a 4s default
        # so a fallen-back execution is quick to observe.
        rig.kill_agent(sig=signal.SIGTERM)
        time.sleep(1)
        rig.agent_env["SYNCBC_TIMEOUT_S"] = "4"
        rig.start_agent()
        rig.push_cmd("chaos-t0", "sleep 30; echo ZERO_LATE",
                     timeout_s=0)
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-t0"
                       and rig.read_chan_json("out.json"), 45,
                       "chaos-t0 out")
        check("timeout0: a 0 envelope cannot disable the bound "
              "(falls back to the default, killed at 124)",
              out is not None and out.get("exit_code") == 124
              and "ZERO_LATE" not in (out.get("stdout_tail") or "")
              and "timeout_s=0 is not a bound"
              in rig.agent_log_text(),
              f"out={out}")
        rig.push_cmd("chaos-tneg", "sleep 30; echo NEG_LATE",
                     timeout_s=-5)
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-tneg"
                       and rig.read_chan_json("out.json"), 45,
                       "chaos-tneg out")
        check("timeoutneg: a negative envelope falls back to "
              "the default (killed at 124)",
              out is not None and out.get("exit_code") == 124
              and "NEG_LATE" not in (out.get("stdout_tail") or ""),
              f"out={out}")
        rig.push_cmd("chaos-thuge", "echo HUGE_OK",
                     timeout_s=99999999999999999999999999999)
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-thuge"
                       and rig.read_chan_json("out.json"), 45,
                       "chaos-thuge out")
        # The clamp assertion names this command's own value:
        # the generic clamp line already sits in the log from
        # scenario 13's 9999 case, so only the value-specific
        # line proves THIS envelope was clamped.
        check("timeoutbig: a 29-digit envelope is clamped on "
              "length and the command still runs",
              out is not None and out.get("exit_code") == 0
              and "HUGE_OK" in (out.get("stdout_tail") or "")
              and "timeout_s=99999999999999999999999999999 "
              "clamped to SYNCBC_MAX_TIMEOUT_S=600"
              in rig.agent_log_text(),
              f"out={out}")

        # 16: SSH transport failure - the deploy-key route a
        # second machine uses (backchannel/INSTALL.md), pointed
        # at a dead port. Connection refused is instant, so the
        # cycles stay fast; what matters is the shape: the
        # agent stays up, keeps beating locally, and both the
        # sync state and the self-test SAY the transport is
        # down instead of the failure going silent.
        rig.kill_agent(sig=signal.SIGTERM)
        time.sleep(1)
        clone = os.path.join(rig.base, "repo")
        run(["git", "-C", clone, "remote", "set-url", "origin",
             "ssh://git@127.0.0.1:1/chan.git"], env=rig.env)
        rig.agent_env["SYNCBC_TIMEOUT_S"] = "30"
        rig.agent_env["SYNCBC_SELFTEST_EVERY"] = "1"
        rig.start_agent()

        def ssh_dead_visible():
            hb = rig.read_chan_json("heartbeat.json") or {}
            st = hb.get("self_test") or {}
            checks = st.get("checks") or {}
            if (hb.get("sync") or {}).get("ok") is False \
                    and checks.get("repo_reachable") is False:
                return hb
            return None

        hb16 = wait_for(ssh_dead_visible, 60,
                        "sync/self-test failure visibility")
        check("sshfail: heartbeat reports sync not-ok and the "
              "self-test reports the repo unreachable",
              hb16 is not None, f"hb={hb16}")
        check("sshfail: agent process alive on a dead transport",
              rig.proc.poll() is None)
        ts16a = (rig.read_chan_json("heartbeat.json") or {}).get("ts")
        ts16b = wait_for(lambda: local_beat_after(ts16a), 45,
                         "local beat on a dead transport")
        check("sshfail: local heartbeat keeps advancing",
              ts16b is not None)
        rig.kill_agent(sig=signal.SIGTERM)
        time.sleep(1)
        run(["git", "-C", clone, "remote", "set-url", "origin",
             "file://" + rig.remote], env=rig.env)
        rig.agent_env["SYNCBC_SELFTEST_EVERY"] = "15"

        # 17: replay-guard loss (FR-004(b)). The guard file is
        # local state outside the repo; lose it (a wiped base,
        # a reinstall that kept the clone) and re-push the very
        # cmd.json that already executed. The guard must be
        # re-seeded from out.json and the command must not run
        # a second time.
        rig.start_agent()
        rig.push_cmd("chaos-g1", "echo GUARD_ONE")
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-g1"
                       and rig.read_chan_json("out.json"), 45,
                       "chaos-g1 out")
        check("guardloss: setup command executed",
              out is not None and out.get("exit_code") == 0)
        rig.kill_agent(sig=signal.SIGTERM)
        time.sleep(1)
        os.unlink(os.path.join(rig.base, ".last_executed_id"))
        rig.start_agent()
        rig.push_cmd("chaos-g1", "echo GUARD_ONE")
        restored = wait_for(
            lambda: "replay guard restored from out.json"
            in rig.agent_log_text() or None, 30, "guard restore")
        check("guardloss: guard re-seeded from out.json",
              bool(restored))
        time.sleep(8)
        check("guardloss: the executed command is not replayed",
              rig.agent_log_text().count("executing id=chaos-g1")
              == 1)
        rig.push_cmd("chaos-g2", "echo GUARD_TWO")
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-g2"
                       and rig.read_chan_json("out.json"), 45,
                       "chaos-g2 out")
        check("guardloss: fresh commands still execute",
              out is not None
              and "GUARD_TWO" in (out.get("stdout_tail") or ""))

        # 18: kill during execution - at-least-once, pinned on
        # purpose. The replay guard is written only after an
        # execution completes, so a command interrupted by the
        # agent's death re-runs from the top after the restart;
        # senders must write idempotent bodies. The counter
        # file proves the double run; out.json proves the
        # second run's result is the one that lands.
        counter = os.path.join(rig.root, "chaos18-counter.txt")

        def counter_runs():
            try:
                with open(counter) as fh:
                    return fh.read().count("RUN")
            except OSError:
                return 0

        rig.push_cmd("chaos-18",
                     "echo RUN >> %s; sleep 8; echo EIGHTEEN_DONE"
                     % counter, timeout_s=30)
        began = wait_for(lambda: counter_runs() >= 1 or None, 45,
                         "chaos-18 first run")
        check("atleastonce: first run began", began is not None)
        rig.kill_agent(signal.SIGKILL)
        time.sleep(1)
        rig.start_agent()
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-18"
                       and rig.read_chan_json("out.json"), 90,
                       "chaos-18 out")
        check("atleastonce: the interrupted command re-runs "
              "from the top and its result lands",
              out is not None and out.get("exit_code") == 0
              and "EIGHTEEN_DONE" in (out.get("stdout_tail") or "")
              and counter_runs() == 2,
              f"out={out} runs={counter_runs()}")

        # 19: STOP arriving mid-execution. The spec's kill
        # switch gates NEW commands (FR-004(c) acceptance);
        # the running body is not interrupted - it finishes
        # and reports honestly - and the next command is the
        # one that halts.
        rig.push_cmd("chaos-19", "sleep 10; echo STOP_SURVIVOR",
                     timeout_s=30)
        began = wait_for(
            lambda: "executing id=chaos-19" in rig.agent_log_text()
            or None, 45, "chaos-19 executing")
        check("stopmid: command began", began is not None)
        rig.push({CHANNEL + "/STOP": "chaos test\n"})
        out = wait_for(lambda: (rig.read_chan_json("out.json") or {}).get("id") == "chaos-19"
                       and rig.read_chan_json("out.json"), 60,
                       "chaos-19 out")
        check("stopmid: the running command is not interrupted",
              out is not None and out.get("exit_code") == 0
              and "STOP_SURVIVOR" in (out.get("stdout_tail") or ""),
              f"out={out}")
        rig.push_cmd("chaos-19b", "echo SHOULD_NOT_RUN")
        stopped = wait_for(lambda: (rig.read_chan_json("heartbeat.json") or {}).get("stopped") is True,
                           30, "stopped heartbeat (mid-execution STOP)")
        time.sleep(3)
        out = rig.read_chan_json("out.json") or {}
        check("stopmid: the next command is halted while STOP "
              "is present", bool(stopped)
              and out.get("id") != "chaos-19b")
        rig.push({CHANNEL + "/STOP": None})

        # 20: channel isolation (PR #66). Restart the agent
        # pointed at a second channel in the same repo - the
        # TRISTEN-LAPTOP shape. It must execute and heartbeat
        # on ITS channel only, and the default channel's
        # recorded state must not move. (The replay guard is
        # per-BASE in the rig; each real machine has its own
        # base, and the probe id differs from the guard value,
        # so the switch is exercised cleanly.)
        rig.kill_agent(sig=signal.SIGTERM)
        time.sleep(1)
        rig.agent_env["SYNCBC_CHANNEL"] = "syncyard-laptop"
        laptop_rel = "channels/syncyard-laptop"
        dev_out_before = (rig.read_chan_json("out.json")
                          or {}).get("id")
        rig.start_agent()
        doc = {"id": "chaos-laptop-1",
               "body": "echo LAPTOP_CHANNEL_OK",
               "sha256": hashlib.sha256(
                   b"echo LAPTOP_CHANNEL_OK").hexdigest(),
               "created_at": "2026-11-04T00:00:00Z"}
        rig.push({laptop_rel + "/cmd.json":
                  json.dumps(doc, indent=1)})

        def laptop_json(name):
            try:
                with open(os.path.join(rig.base, "repo",
                                       laptop_rel, name)) as fh:
                    return json.load(fh)
            except Exception:
                return None

        out = wait_for(
            lambda: (laptop_json("out.json") or {}).get("id")
            == "chaos-laptop-1" and laptop_json("out.json"),
            45, "laptop channel out")
        check("channel: a SYNCBC_CHANNEL agent executes on its "
              "own channel",
              out is not None
              and "LAPTOP_CHANNEL_OK"
              in (out.get("stdout_tail") or ""),
              f"out={out}")
        hb20 = laptop_json("heartbeat.json")
        check("channel: the second channel carries its own "
              "heartbeat", hb20 is not None
              and bool(hb20.get("ts")))
        out_dev = rig.read_chan_json("out.json") or {}
        check("channel: the default channel is untouched by "
              "the second agent",
              out_dev.get("id") == dev_out_before,
              f"dev out={out_dev.get('id')} before={dev_out_before}")
        rig.agent_env.pop("SYNCBC_CHANNEL", None)
    finally:
        rig.cleanup()

    print()
    if fails:
        print(f"TEST_CHAOS: FAIL ({len(fails)} failing)")
        sys.exit(1)
    print("TEST_CHAOS: ALL PASS")


if __name__ == "__main__":
    main()

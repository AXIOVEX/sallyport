#!/usr/bin/env python3
"""Static battery for the host shutdown guard.

This VM cannot compile Windows targets, so — like the tray
battery — these are source-shape checks on
SyncyardGuard.cs. They pin the guard's four speed duties
from the owner's 2026-11-02 directive and the design's
safety shape:

  (a) the OS shutdown block is registered with a reason
      string, immediately and while work runs;
  (b) checkpoint hold files are raised locally at the
      registry's paths — no gatekeeper round-trip exists
      anywhere in the component;
  (c) the going-down record is written FIRST (before the
      veto), in the Phase 1 ingest shape, through a
      verified write;
  (d) the block is released on settle (all jobs ended, or
      the window expired) and the pending action is
      re-issued — delay, never prevent.

Run: python3 runner/guard/test_guard_source.py
"""
import pathlib
import re
import sys

SRC = pathlib.Path(__file__).with_name(
    "SyncyardGuard.cs").read_text(encoding="utf-8")
INSTALL = pathlib.Path(__file__).with_name(
    "install-guard.ps1").read_text(encoding="utf-8")


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    return ok


def main():
    q = SRC.index("WM_QUERYENDSESSION")
    results = [
        check("(a) shutdown block P/Invokes are declared",
              "ShutdownBlockReasonCreate" in SRC
              and "ShutdownBlockReasonDestroy" in SRC),
        check("(a) the block reason names the running count",
              '"Syncyard: " + count +' in SRC
              and "checkpointing before shutdown" in SRC),
        check("(a) the block is pre-registered while work "
              "runs (steady-state tick), not only at "
              "session end",
              "EnsureBlock(jobs.Count);" in SRC),
        check("(b) holds are raised by local file creation "
              "at each entry's hold path",
              "File.WriteAllText(j.HoldFileWin" in SRC
              and "RaiseHolds(jobs);" in SRC),
        check("(b) no gatekeeper round-trip exists in the "
              "component (no network clients at all)",
              "HttpClient" not in SRC
              and "WebRequest" not in SRC
              and "Process.Start(\"gh" not in SRC),
        check("(c) the drain writes the record before it "
              "raises holds and before any veto return",
              SRC.index("WriteGoingDown(trigger, jobs);")
              < SRC.index("RaiseHolds(jobs);")
              and "BeginDrain(logoff, jobs, trigger);" in SRC),
        check("(c) the record carries the Phase 1 ingest "
              "fields, in shape",
              all(k in SRC for k in (
                  '\\"host\\"', '\\"ts\\"', '\\"trigger\\"',
                  '\\"active_run_ids\\"', '\\"supervised\\"',
                  '\\"uncheckpointed\\"'))),
        check("(c) writes are verified (temp + read-back "
              "compare + replace)",
              "WriteVerified" in SRC and "BytesEqual" in SRC
              and "File.Replace(tmp," in SRC),
        check("(c) a stale going-down record from a "
              "previous boot is cleared at startup",
              "ClearStaleGoingDown" in SRC),
        check("(d) settle releases the block before "
              "re-issuing the pending action",
              SRC.index("ReleaseBlock();", SRC.index(
                  "private void SettleDrain"))
              < SRC.index("Process.Start(psi);",
                  SRC.index("private void SettleDrain"))),
        check("(d) settle fires on empty registry or on "
              "window expiry — both paths exist",
              "SettleDrain(false)" in SRC
              and "SettleDrain(true)" in SRC
              and "_drainDeadlineUtc" in SRC),
        check("(d) the re-issued non-logoff action is a "
              "restart (named design decision)",
              '"/r /t 5' in SRC),
        check("(d) the guard yields once to its own "
              "re-issued action (no veto loop)",
              "_yieldUntilUtc" in SRC),
        check("(d) logoff is re-issued via ExitWindowsEx "
              "with the shutdown privilege enabled",
              "ExitWindowsEx(EWX_LOGOFF" in SRC
              and "SeShutdownPrivilege" in SRC),
        check("session end: a veto is returned while a "
              "drain runs or begins",
              "m.Result = IntPtr.Zero" in SRC[SRC.index(
                  "if (m.Msg == WM_QUERYENDSESSION)"):]),
        check("session end: WM_ENDSESSION is the "
              "last-chance record write",
              "if (m.Msg == WM_ENDSESSION)" in SRC
              and "_recordWritten" in SRC),
        check("windowless: the one form is never shown",
              "base.SetVisibleCore(false);" in SRC
              and "ShowInTaskbar = false;" in SRC),
        check("the window handle is force-created at "
              "startup (session-end messages need a live "
              "handle even with no jobs running)",
              "IntPtr forced = Handle;" in SRC),
        check("harder to exit than the tray: no NotifyIcon, "
              "no Exit menu, user close refused",
              "NotifyIcon" not in SRC
              and "e.Cancel = true;" in SRC),
        check("single instance, no duplicate-instance "
              "dialog (no MessageBox anywhere)",
              "SyncyardGuardSingleton" in SRC
              and "MessageBox" not in SRC),
        check("guard beat: written on a ~10 s cadence with "
              "the contract fields",
              "WriteBeat" in SRC and "guard-heartbeat.json"
              in SRC and '\\"draining\\"' in SRC),
        check("registry: entries age out beyond the job "
              "timeout (orphans from a host kill)",
              "StaleEntryMinutes = 390" in SRC),
        check("no console use (the owner's no-window rule)",
              "Console." not in SRC),
        check("installer: in-box csc, winexe, Run key, "
              "settings written only when absent",
              "csc.exe" in INSTALL
              and "/target:winexe" in INSTALL
              and '"SyncyardGuard"' in INSTALL
              and "left untouched" in INSTALL),
    ]
    print("GUARD_SOURCE %s" % ("OK" if all(results)
                               else "FAILURES"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

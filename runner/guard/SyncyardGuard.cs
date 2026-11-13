// SyncyardGuard.cs - host shutdown guard for Syncyard.
//
// Covers shutdown protection and
// recovery, Phase 3 - the guard component and the going-down
// WRITER. The gatekeeper side of this contract (ingest,
// exactly-once per (host, ts), the dispatch hold)
// shipped in Phase 1; this component is the host end of it.
//
// Claim: when the host is told to shut down, restart, or log
// off while checkpoint-capable gatekeeper jobs are running,
// the guard (a) registers the OS shutdown block with a
// reason string immediately, (b) raises the running jobs'
// checkpoint hold files LOCALLY - at the paths, in
// seconds, with no gatekeeper round-trip - (c) writes the
// going-down record FIRST (verified write, the exact Phase 1
// record shape) where the backchannel agent merges it into
// the heartbeat payload the sweep already reads, and
// (d) releases the block when every running job has
// checkpointed or ended, or when the configured checkpoint
// window expires (default 120 s), then re-issues the
// pending action. It delays a shutdown; it never prevents
// one.
//
// Form: a sibling of the tray, not part of it (
// sanctions "the tray or a sibling"). The guard has NO
// user interface at all: no tray icon, no menu, no exit
// command - deliberately harder to exit than the tray (whose
// Exit item is a feature), so the protection is not
// dismissed by a stray click. The one Form exists only as a
// message target for WM_QUERYENDSESSION / WM_ENDSESSION and
// is never shown. Stopping the guard means killing the
// process, a deliberate act - and even that is visible: the
// guard beats into guard-heartbeat.json every ~10 s, the
// agent merges the beat into the fleet heartbeat, and the
// gatekeeper ledgers `guard_stale` when the beat goes old
// (Phase 3: a stopped guard is a host-health
// entry, never silent).
//
// Compile (no SDK install needed - csc ships with Windows):
//   csc /target:winexe /out:SyncyardGuard.exe
//       /reference:System.Windows.Forms.dll SyncyardGuard.cs
//
// C# 5 compatible on purpose (Framework 4.x csc), mirroring
// the tray's discipline. The VM cannot compile Windows
// targets: CI evidence for this file is the source battery
// (runner/guard/test_guard_source.py); the first real build
// is the owner's install run, like the tray's.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace SyncyardGuard
{
    internal sealed class GuardSettings
    {
        // `host` MUST equal the gatekeeper config's host name
        // for this machine ("desktop" in the fleet config):
        // the ingest matches a going-down record to a host's
        // lanes by this string.
        public string Host = "desktop";
        // Checkpoint window. Ships equal to the
        // gatekeeper's drain.window_s default (120 s); the
        // two are set independently because the guard must
        // act even when the gatekeeper is unreachable.
        public int WindowS = 120;
        public string Distro = "Ubuntu-24.04";
        public string SyncyardDir =
            @"C:\Users\you\Development\Syncyard";
        public string HoldsDirWin =
            @"C:\Users\you\Development\Syncyard\holds";
        public string HoldsDirWsl =
            "/home/you/.syncyard/holds";

        public string GoingDownPath
        {
            get { return Path.Combine(SyncyardDir, "going-down.json"); }
        }

        public string GuardHeartbeatPath
        {
            get { return Path.Combine(SyncyardDir,
                "guard-heartbeat.json"); }
        }

        public string OutcomePath
        {
            get
            {
                return Path.Combine(SyncyardDir,
                    "guard-drain-outcome.json");
            }
        }

        public string RunningDirWin
        {
            get
            {
                return Path.Combine(
                    Directory.GetParent(HoldsDirWin).FullName,
                    "running");
            }
        }

        public string RunningDirWsl
        {
            get
            {
                string parent = HoldsDirWsl.TrimEnd('/');
                int cut = parent.LastIndexOf('/');
                if (cut > 0) parent = parent.Substring(0, cut);
                return @"\\wsl.localhost\" + Distro +
                    (parent + "/running").Replace('/', '\\');
            }
        }

        // Minimal flat-JSON reader: guard-settings.json is a
        // flat object of strings/numbers written by the
        // installer (and editable by the owner); anything
        // unparseable falls back to the fleet defaults above
        // and is logged - a settings typo must never leave
        // the host unguarded.
        public static GuardSettings Load(string path,
            Action<string> log)
        {
            var s = new GuardSettings();
            string text;
            try { text = File.ReadAllText(path); }
            catch { return s; }
            s.Host = ExtractString(text, "host") ?? s.Host;
            s.Distro = ExtractString(text, "distro") ?? s.Distro;
            s.SyncyardDir = ExtractString(text, "syncyard_dir")
                ?? s.SyncyardDir;
            s.HoldsDirWin = ExtractString(text,
                "holds_dir_win") ?? s.HoldsDirWin;
            s.HoldsDirWsl = ExtractString(text,
                "holds_dir_wsl") ?? s.HoldsDirWsl;
            int w = ExtractInt(text, "window_s");
            if (w > 0) s.WindowS = w;
            log("settings: host=" + s.Host + " window_s=" +
                s.WindowS);
            return s;
        }

        private static string ExtractString(string json,
            string key)
        {
            int i = json.IndexOf("\"" + key + "\"",
                StringComparison.Ordinal);
            if (i < 0) return null;
            int colon = json.IndexOf(':', i);
            if (colon < 0) return null;
            int q1 = json.IndexOf('"', colon);
            if (q1 < 0) return null;
            var sb = new StringBuilder();
            for (int p = q1 + 1; p < json.Length; p++)
            {
                char c = json[p];
                if (c == '\\' && p + 1 < json.Length)
                {
                    p++;
                    char e = json[p];
                    sb.Append(e == 'n' ? '\n' : e);
                    continue;
                }
                if (c == '"') return sb.ToString();
                sb.Append(c);
            }
            return null;
        }

        private static int ExtractInt(string json, string key)
        {
            int i = json.IndexOf("\"" + key + "\"",
                StringComparison.Ordinal);
            if (i < 0) return 0;
            int colon = json.IndexOf(':', i);
            if (colon < 0) return 0;
            int p = colon + 1;
            while (p < json.Length &&
                (json[p] == ' ' || json[p] == '\t')) p++;
            int start = p;
            while (p < json.Length &&
                (char.IsDigit(json[p]) || json[p] == '-')) p++;
            int v;
            if (p > start &&
                int.TryParse(json.Substring(start, p - start),
                    out v)) return v;
            return 0;
        }
    }

    internal sealed class JobEntry
    {
        public string JobId;
        public string RunId;        // github.run_id, as string
        public string Workflow;
        public string Lane;
        public string HoldFileWin;
        public DateTime StartedUtc;
    }

    internal static class Program
    {
        internal static readonly string ExeDir =
            AppDomain.CurrentDomain.BaseDirectory;
        internal static readonly string LogPath =
            Path.Combine(ExeDir, "guard.log");

        internal static void Log(string msg)
        {
            try
            {
                File.AppendAllText(LogPath,
                    DateTime.UtcNow.ToString(
                        "yyyy-MM-ddTHH:mm:ssZ") + " " + msg +
                    "\r\n");
            }
            catch { /* logging must never kill the guard */ }
        }

        [STAThread]
        private static void Main()
        {
            bool created;
            using (var mutex = new Mutex(true,
                "SyncyardGuardSingleton", out created))
            {
                if (!created) return; // already guarding; no UI
                Application.SetUnhandledExceptionMode(
                    UnhandledExceptionMode.CatchException);
                Application.ThreadException +=
                    delegate(object sender,
                        ThreadExceptionEventArgs e)
                    {
                        Log("unhandled UI exception (caught): " +
                            e.Exception);
                    };
                Application.Run(new GuardForm());
            }
        }
    }

    internal sealed class GuardForm : Form
    {
        private const int WM_QUERYENDSESSION = 0x0011;
        private const int WM_ENDSESSION = 0x0016;
        private const int ENDSESSION_LOGOFF = unchecked(
            (int)0x80000000);
        private const uint EWX_LOGOFF = 0x00000000;
        private const uint EWX_FORCEIFHUNG = 0x00000010;

        // A registry entry older than this is an orphan (the
        // workflow's unregister step died with the host):
        // the GitHub job timeout is 360 minutes; the margin
        // keeps any live job covered.
        private const int StaleEntryMinutes = 390;

        [DllImport("user32.dll", SetLastError = true,
            CharSet = CharSet.Unicode)]
        private static extern bool ShutdownBlockReasonCreate(
            IntPtr hWnd, string pwszReason);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool ShutdownBlockReasonDestroy(
            IntPtr hWnd);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool ExitWindowsEx(uint uFlags,
            uint dwReason);

        [DllImport("kernel32.dll")]
        private static extern IntPtr GetCurrentProcess();

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool OpenProcessToken(
            IntPtr processHandle, uint desiredAccess,
            out IntPtr tokenHandle);

        [DllImport("advapi32.dll", SetLastError = true,
            CharSet = CharSet.Unicode)]
        private static extern bool LookupPrivilegeValue(
            string lpSystemName, string lpName,
            out long lpLuid);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool AdjustTokenPrivileges(
            IntPtr tokenHandle, bool disableAllPrivileges,
            ref TokenPrivileges newState, int bufferLength,
            IntPtr previousState, IntPtr returnLength);

        [StructLayout(LayoutKind.Sequential)]
        private struct TokenPrivileges
        {
            public int PrivilegeCount;
            public long Luid;
            public int Attributes;
        }

        private readonly GuardSettings _s;
        private readonly Timer _timer;
        private bool _blockHeld;
        private int _blockCount = -1;
        private bool _draining;
        private bool _pendingLogoff;
        private string _drainTrigger = "";
        private DateTime _drainDeadlineUtc;
        private DateTime _yieldUntilUtc = DateTime.MinValue;
        private List<JobEntry> _drainJobs =
            new List<JobEntry>();
        private bool _recordWritten;
        private DateTime _lastBeatUtc = DateTime.MinValue;

        public GuardForm()
        {
            // The form is a message target only: it is never
            // shown (SetVisibleCore below), has no icon, no
            // menu, no controls - there is nothing to click
            // and no Exit path.
            Text = "Syncyard Guard";
            ShowInTaskbar = false;
            // Force the window handle into existence NOW:
            // session-end messages are delivered to live
            // top-level window handles, and this form is
            // never shown - without a handle created here, a
            // shutdown arriving before the first job (and the
            // first block registration) would never reach
            // WndProc at all.
            IntPtr forced = Handle;
            _s = GuardSettings.Load(Path.Combine(Program.ExeDir,
                "guard-settings.json"), Program.Log);
            ClearStaleGoingDown();
            _timer = new Timer();
            _timer.Interval = 2000;
            _timer.Tick += delegate { TickGuarded(); };
            _timer.Start();
            Program.Log("guard started (host=" + _s.Host + ")");
            WriteBeat(ScanJobs().Count, false);
        }

        protected override void SetVisibleCore(bool value)
        {
            // Never visible, in any session state: windowless
            // is this program's whole posture (the owner's
            // standing rule: no window from any agent
            // operation, ever).
            base.SetVisibleCore(false);
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            // Defense in depth: a user close is refused and
            // logged. (Nothing shows the form, so this should
            // be unreachable; if it ever fires, the log says
            // someone tried.)
            if (e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                Program.Log("user close refused and logged");
                return;
            }
            base.OnFormClosing(e);
        }

        // ---- the registry --------------------------------

        private List<JobEntry> ScanJobs()
        {
            var found = new List<JobEntry>();
            ScanDir(_s.RunningDirWin, found);
            ScanDir(_s.RunningDirWsl, found);
            return found;
        }

        private void ScanDir(string dir, List<JobEntry> found)
        {
            string[] files;
            try { files = Directory.GetFiles(dir, "*.entry"); }
            catch { return; } // absent dir = no jobs on venue
            foreach (string f in files)
            {
                try
                {
                    var e = ParseEntry(f);
                    if (e == null) continue;
                    double age = (DateTime.UtcNow -
                        e.StartedUtc).TotalMinutes;
                    if (age > StaleEntryMinutes)
                    {
                        Program.Log("stale registry entry aged " +
                            "out: " + e.JobId);
                        try { File.Delete(f); } catch { }
                        continue;
                    }
                    found.Add(e);
                }
                catch { /* one bad entry never blinds the rest */ }
            }
        }

        private static JobEntry ParseEntry(string path)
        {
            var kv = new Dictionary<string, string>();
            foreach (string line in File.ReadAllLines(path))
            {
                int eq = line.IndexOf('=');
                if (eq > 0)
                    kv[line.Substring(0, eq).Trim()] =
                        line.Substring(eq + 1).Trim();
            }
            var e = new JobEntry();
            if (!kv.TryGetValue("job_id", out e.JobId) ||
                string.IsNullOrEmpty(e.JobId)) return null;
            kv.TryGetValue("run_id", out e.RunId);
            kv.TryGetValue("workflow", out e.Workflow);
            kv.TryGetValue("lane", out e.Lane);
            kv.TryGetValue("hold_file_win", out e.HoldFileWin);
            string started;
            DateTime st;
            if (kv.TryGetValue("started", out started) &&
                DateTime.TryParse(started, null,
                    System.Globalization.DateTimeStyles
                        .AdjustToUniversal |
                    System.Globalization.DateTimeStyles
                        .AssumeUniversal, out st))
                e.StartedUtc = st;
            else
                e.StartedUtc = File.GetLastWriteTimeUtc(path);
            return e;
        }

        // ---- the shutdown block --------------------------

        private void EnsureBlock(int count)
        {
            if (_blockHeld && _blockCount == count) return;
            string reason = "Syncyard: " + count +
                " job(s) running - checkpointing before shutdown";
            if (ShutdownBlockReasonCreate(Handle, reason))
            {
                _blockHeld = true;
                _blockCount = count;
                Program.Log("shutdown block registered: " +
                    reason);
            }
            else
            {
                Program.Log("ShutdownBlockReasonCreate FAILED " +
                    "(err " + Marshal.GetLastWin32Error() + ")");
            }
        }

        private void ReleaseBlock()
        {
            if (!_blockHeld) return;
            ShutdownBlockReasonDestroy(Handle);
            _blockHeld = false;
            _blockCount = -1;
            Program.Log("shutdown block released");
        }

        // ---- the going-down record (the WRITER) ----------
        //
        // The record below is the Phase 1 ingest contract,
        // byte-shape for byte-shape: the gatekeeper dedupes
        // per (host, ts), coerces unknown triggers to
        // "unknown", matches `active_run_ids` against live
        // queue records (and, when the list is empty,
        // annotates every active job on the host's lanes),
        // stores `supervised` verbatim, and counts
        // `uncheckpointed`. The guard writes it BEFORE it
        // vetoes the shutdown - record first, always - and
        // `uncheckpointed` at write time is the at-risk set
        // (every running job; none has checkpointed yet).
        // The final checkpoint accounting is the
        // gatekeeper's marker harvest, never a record
        // rewrite: a rewrite under the same (host, ts) would
        // be deduped away by design.

        private void WriteGoingDown(string trigger,
            List<JobEntry> jobs)
        {
            var sb = new StringBuilder();
            sb.Append("{\n");
            sb.Append("  \"host\": ").Append(JsonStr(_s.Host))
                .Append(",\n");
            sb.Append("  \"ts\": ").Append(JsonStr(UtcNow()))
                .Append(",\n");
            sb.Append("  \"trigger\": ").Append(
                JsonStr(trigger)).Append(",\n");
            sb.Append("  \"active_run_ids\": [");
            bool first = true;
            foreach (JobEntry j in jobs)
            {
                long rid;
                if (j.RunId != null &&
                    long.TryParse(j.RunId, out rid))
                {
                    if (!first) sb.Append(", ");
                    sb.Append(rid);
                    first = false;
                }
            }
            sb.Append("],\n");
            sb.Append("  \"supervised\": [],\n");
            sb.Append("  \"uncheckpointed\": [");
            first = true;
            foreach (JobEntry j in jobs)
            {
                if (!first) sb.Append(", ");
                sb.Append(JsonStr(j.JobId));
                first = false;
            }
            sb.Append("]\n}\n");
            if (WriteVerified(_s.GoingDownPath, sb.ToString()))
            {
                _recordWritten = true;
                Program.Log("going-down record written (" +
                    trigger + ", " + jobs.Count + " job(s))");
            }
            else
            {
                Program.Log("going-down record write FAILED");
            }
        }

        private void ClearStaleGoingDown()
        {
            // A going-down file left by a previous boot must
            // not re-ingest as a fresh event if the
            // gatekeeper's dedupe state was ever reset. A
            // record older than 15 minutes at guard start is
            // stale by construction (a live drain's record is
            // fresh, and a drain itself is bounded by the
            // checkpoint window); clear it to {} - which the
            // agent's merge skips as an empty object.
            try
            {
                string p = _s.GoingDownPath;
                if (!File.Exists(p)) return;
                string text = File.ReadAllText(p);
                int i = text.IndexOf("\"ts\"", StringComparison
                    .Ordinal);
                if (i < 0) return;
                int q1 = text.IndexOf('"', text.IndexOf(':', i));
                int q2 = text.IndexOf('"', q1 + 1);
                DateTime ts;
                if (q1 >= 0 && q2 > q1 && DateTime.TryParse(
                    text.Substring(q1 + 1, q2 - q1 - 1), null,
                    System.Globalization.DateTimeStyles
                        .AdjustToUniversal |
                    System.Globalization.DateTimeStyles
                        .AssumeUniversal, out ts) &&
                    (DateTime.UtcNow - ts).TotalMinutes > 15)
                {
                    WriteVerified(p, "{}\n");
                    Program.Log("cleared stale going-down " +
                        "record from a previous boot");
                }
            }
            catch { /* best effort; dedupe is the backstop */ }
        }

        // Verified write, the discipline in miniature:
        // write a temp file in the same directory, read it
        // back and byte-compare, then move it over the
        // target. The reader (the backchannel agent) therefore
        // only ever sees the old record or the new one, whole.
        private static bool WriteVerified(string path,
            string content)
        {
            byte[] want = new UTF8Encoding(false)
                .GetBytes(content);
            for (int attempt = 0; attempt < 3; attempt++)
            {
                try
                {
                    string dir = Path.GetDirectoryName(path);
                    if (!string.IsNullOrEmpty(dir))
                        Directory.CreateDirectory(dir);
                    string tmp = path + ".tmp-" +
                        Process.GetCurrentProcess().Id;
                    File.WriteAllBytes(tmp, want);
                    byte[] got = File.ReadAllBytes(tmp);
                    if (!BytesEqual(got, want))
                    {
                        try { File.Delete(tmp); } catch { }
                        continue;
                    }
                    if (File.Exists(path)) File.Replace(tmp,
                        path, null);
                    else File.Move(tmp, path);
                    return true;
                }
                catch { /* retried below */ }
            }
            return false;
        }

        private static bool BytesEqual(byte[] a, byte[] b)
        {
            if (a.Length != b.Length) return false;
            for (int i = 0; i < a.Length; i++)
                if (a[i] != b[i]) return false;
            return true;
        }

        private void WriteBeat(int active, bool draining)
        {
            string json = "{\"host\": " + JsonStr(_s.Host) +
                ", \"ts\": " + JsonStr(UtcNow()) +
                ", \"active\": " + active +
                ", \"draining\": " +
                (draining ? "true" : "false") + "}\n";
            if (WriteVerified(_s.GuardHeartbeatPath, json))
                _lastBeatUtc = DateTime.UtcNow;
        }

        private static string UtcNow()
        {
            return DateTime.UtcNow.ToString(
                "yyyy-MM-ddTHH:mm:ssZ");
        }

        private static string JsonStr(string v)
        {
            if (v == null) return "\"\"";
            return "\"" + v.Replace("\\", "\\\\")
                .Replace("\"", "\\\"") + "\"";
        }

        // ---- hold raising (LOCAL, no round-trip) ---------

        private void RaiseHolds(List<JobEntry> jobs)
        {
            foreach (JobEntry j in jobs)
            {
                if (string.IsNullOrEmpty(j.HoldFileWin))
                {
                    Program.Log("no hold venue for job " +
                        j.JobId + " - cannot signal it");
                    continue;
                }
                try
                {
                    string dir = Path.GetDirectoryName(
                        j.HoldFileWin);
                    if (!string.IsNullOrEmpty(dir))
                        Directory.CreateDirectory(dir);
                    // The file's EXISTENCE is the signal
                    //; the content names
                    // who raised it and when.
                    File.WriteAllText(j.HoldFileWin,
                        "held by SyncyardGuard " + UtcNow() +
                        "\r\n");
                    Program.Log("hold raised for " + j.JobId +
                        " at " + j.HoldFileWin);
                }
                catch (Exception ex)
                {
                    Program.Log("hold raise FAILED for " +
                        j.JobId + ": " + ex.Message);
                }
            }
        }

        // ---- the drain -----------------------------------

        private void BeginDrain(bool logoff,
            List<JobEntry> jobs, string trigger)
        {
            // (a) the block first - it is one API call and it
            // is what buys every later step its time.
            EnsureBlock(jobs.Count);
            // (c) the record, through the fastest channel the
            // ingest contract supports: the backchannel
            // heartbeat payload, whose file merge happens on
            // the agent's next cycle and whose read the sweep
            // already spends every pass. (Host-local latency:
            // this write. End-to-end latency is bounded by
            // the agent cycle (~60 s) plus the sweep cadence
            // - stated in the spec delta, not hidden.)
            WriteGoingDown(trigger, jobs);
            // (b) the checkpoint signals, raised locally.
            RaiseHolds(jobs);
            // (d) the bounded wait starts now.
            _draining = true;
            _pendingLogoff = logoff;
            _drainTrigger = trigger;
            _drainJobs = jobs;
            _drainDeadlineUtc = DateTime.UtcNow.AddSeconds(
                _s.WindowS);
            Program.Log("drain begun: " + trigger + ", " +
                jobs.Count + " job(s), window " + _s.WindowS +
                " s");
        }

        private void SettleDrain(bool expired)
        {
            List<JobEntry> remaining = ScanJobs();
            ReleaseBlock();
            var sb = new StringBuilder();
            sb.Append("{\n  \"host\": ").Append(
                JsonStr(_s.Host)).Append(",\n");
            sb.Append("  \"ts\": ").Append(JsonStr(UtcNow()))
                .Append(",\n");
            sb.Append("  \"trigger\": ").Append(
                JsonStr(_drainTrigger)).Append(",\n");
            sb.Append("  \"expired\": ").Append(
                expired ? "true" : "false").Append(",\n");
            sb.Append("  \"remaining\": [");
            bool first = true;
            foreach (JobEntry j in remaining)
            {
                if (!first) sb.Append(", ");
                sb.Append(JsonStr(j.JobId));
                first = false;
            }
            sb.Append("]\n}\n");
            WriteVerified(_s.OutcomePath, sb.ToString());
            Program.Log("drain settled: expired=" + expired +
                " remaining=" + remaining.Count +
                " - re-issuing the pending action");
            _draining = false;
            // Yield once to our own re-issued action: the
            // next WM_QUERYENDSESSION inside this window is
            // the shutdown we ourselves resumed, and vetoing
            // it would loop forever. A genuinely NEW shutdown
            // after the yield re-arms the guard normally.
            _yieldUntilUtc = DateTime.UtcNow.AddMinutes(10);
            if (_pendingLogoff)
            {
                EnableShutdownPrivilege();
                if (!ExitWindowsEx(EWX_LOGOFF |
                    EWX_FORCEIFHUNG, 0))
                    Program.Log("ExitWindowsEx logoff FAILED " +
                        "(err " + Marshal.GetLastWin32Error() +
                        ")");
            }
            else
            {
                // Restart, deliberately: WM_QUERYENDSESSION
                // cannot distinguish the vetoed action
                // (restart vs poweroff), and a restart returns
                // the host to service under Part C,
                // where logon brings the fleet back; a
                // poweroff leaves it dark until someone is at
                // the machine. Named for the owner's review
                // in the spec delta (Amendment 2).
                var psi = new ProcessStartInfo("shutdown.exe",
                    "/r /t 5 /c \"Syncyard drain complete - " +
                    "resuming the restart held for " +
                    "checkpoints\"");
                psi.CreateNoWindow = true;
                psi.UseShellExecute = false;
                try { Process.Start(psi); }
                catch (Exception ex)
                {
                    Program.Log("re-issue restart FAILED: " +
                        ex.Message);
                }
            }
        }

        private static void EnableShutdownPrivilege()
        {
            IntPtr token;
            if (!OpenProcessToken(GetCurrentProcess(),
                0x0028 /* TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY */,
                out token)) return;
            long luid;
            if (!LookupPrivilegeValue(null,
                "SeShutdownPrivilege", out luid)) return;
            var tp = new TokenPrivileges();
            tp.PrivilegeCount = 1;
            tp.Luid = luid;
            tp.Attributes = 0x00000002; // SE_PRIVILEGE_ENABLED
            AdjustTokenPrivileges(token, false, ref tp, 0,
                IntPtr.Zero, IntPtr.Zero);
        }

        // ---- the steady-state tick ------------------------

        private void TickGuarded()
        {
            try { Tick(); }
            catch (Exception ex)
            {
                Program.Log("tick fault (caught): " +
                    ex.Message);
            }
        }

        private void Tick()
        {
            List<JobEntry> jobs = ScanJobs();
            if (_draining)
            {
                if (jobs.Count == 0) SettleDrain(false);
                else if (DateTime.UtcNow >= _drainDeadlineUtc)
                    SettleDrain(true);
            }
            else if (jobs.Count > 0)
            {
                // Pre-registered while work runs, so the
                // session-end veto below is backed by a
                // standing block, not a race to register one
                // inside the shutdown's own countdown.
                EnsureBlock(jobs.Count);
            }
            else ReleaseBlock();
            if ((DateTime.UtcNow - _lastBeatUtc).TotalSeconds
                >= 10)
                WriteBeat(jobs.Count, _draining);
        }

        // ---- session-end messages -------------------------

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WM_QUERYENDSESSION)
            {
                int lp = m.LParam.ToInt32();
                bool logoff = (lp & ENDSESSION_LOGOFF) != 0;
                bool critical = (lp & 0x40000000) != 0;
                string trigger = logoff ? "logoff" : "shutdown";
                if (DateTime.UtcNow < _yieldUntilUtc)
                {
                    Program.Log("queryendsession allowed (own " +
                        "re-issued action)");
                    m.Result = new IntPtr(1);
                    return;
                }
                if (_draining)
                {
                    Program.Log("queryendsession during drain " +
                        "- still holding");
                    m.Result = IntPtr.Zero;
                    return;
                }
                List<JobEntry> jobs = ScanJobs();
                if (jobs.Count == 0)
                {
                    // Nothing to protect: allow. No record
                    // yet - a query another app cancels is not
                    // a going-down, and a record here would set
                    // a spurious gatekeeper hold for a shutdown
                    // that never happened. The WM_ENDSESSION
                    // handler below writes the record when the
                    // session ACTUALLY ends. (With jobs running
                    // the record IS written at query time,
                    // before the veto: that shutdown is real
                    // because the guard re-issues it itself.)
                    Program.Log("queryendsession (" + trigger +
                        ") with no running jobs - allowed");
                    m.Result = new IntPtr(1);
                    return;
                }
                if (critical)
                {
                    // A forced session end cannot be vetoed
                    // reliably; the record and the holds are
                    // still raised first - the checkpoint
                    // window may not be honored by the OS,
                    // and the outcome says so.
                    Program.Log("CRITICAL queryendsession with " +
                        jobs.Count + " job(s) - recording and " +
                        "signaling without a veto");
                    EnsureBlock(jobs.Count);
                    WriteGoingDown(trigger, jobs);
                    RaiseHolds(jobs);
                    m.Result = new IntPtr(1);
                    return;
                }
                BeginDrain(logoff, jobs, trigger);
                m.Result = IntPtr.Zero; // veto: retry later
                return;
            }
            if (m.Msg == WM_ENDSESSION)
            {
                if (m.WParam != IntPtr.Zero && !_recordWritten)
                {
                    // Last chance: the session is ending past
                    // us (forced, or a second attempt we did
                    // not see). One fast local write; nothing
                    // else is possible at this point.
                    try
                    {
                        WriteGoingDown("shutdown", ScanJobs());
                    }
                    catch { }
                }
                base.WndProc(ref m);
                return;
            }
            base.WndProc(ref m);
        }
    }
}

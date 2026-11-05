// SyncyardTray.cs - windowless supervisor for the Syncyard runners.
//
// Spec: AXIOVEX/syncyard spec 007 (windowless supervision),
// spec 014 (tray settings UI and dynamic fleet display), and
// spec 018 (tray connection indicator).
// Claim: the desktop runners run with NO console window created and
// none left open in the owner's interactive session, while staying in
// that session so jobs keep access to the owner's WSL distro, audio
// devices, GPU, and bench hardware (a Windows service in session 0
// cannot reach those). This tray app is the supervisor: it starts
// each runner hidden, restarts it if it dies (capped, then it asks
// for the owner), and hides any stray runner console window.
//
// Spec 014: the tray's own behavior (supervision, restart cap,
// refresh cadence, autostart, per-lane supervision) is managed from
// a settings dialog and persisted to tray-settings.json beside the
// exe; fleet state (the gatekeeper's mode and queue, spec 013) is
// displayed read-only from a published snapshot file when one
// exists, and shown as unavailable, with the reason, when none
// does. The tray never edits fleet state and never creates a
// console; its windows (settings, the status window, and the
// read-only consoles viewer) open only when the owner asks
// from the menu.
//
// Spec 021: the tray carries a build stamp and can update itself
// to the latest published build. An owner-clicked menu check
// reads a small JSON manifest from the project's GitHub
// releases; a newer build is downloaded, verified against the
// manifest's SHA-256, and swapped in by a UI-free helper mode
// of this same exe, which relaunches the tray. The spec 018
// connection indicator stays signal-file only; an update is
// installed only on the owner's explicit click - 0.4.3.0 adds
// an automatic check a few seconds after startup, and a check
// downloads nothing and installs nothing.
//
// 0.4.3.0: the menu also carries the fleet queue line (the
// snapshot's four-state queue summary: idle, waiting, running,
// full), a Status window rendering the snapshot's queue depth
// (active jobs, per-lane caps and states) beside this
// machine's own state and the update state, and a read-only
// tabbed consoles viewer over the runners' own log files.
// The runners run with no console window, so there is no
// console to show; their logs are the output, and the viewer
// tails them. It replaces the old Show-runner-consoles
// item, which hunted session windows by title and could
// never show a healthy runner.
//
// Compile (no SDK install needed - csc ships with Windows):
//   csc /target:winexe /out:SyncyardTray.exe /win32icon:logo.ico
//       /resource:logo.png,SyncyardLogo.png
//       /reference:System.Windows.Forms.dll
//       /reference:System.Drawing.dll SyncyardTray.cs
// The mark is embedded as the SyncyardLogo.png resource so the tray
// icon never depends on a file being in the right folder.
//
// C# 5 compatible on purpose (Framework 4.x csc).

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;


// Assembly identity (2026-11-03, tray antivirus root-cause fix 1): the
// compiled tray must carry its real name, maker, and version. Builds
// before this shipped with version 0.0.0.0 and every identity field
// empty, which is one of the four factors in the root-cause record
// (TRAY-AV-ROOT-CAUSE-2026-11-03). Keep the versions in step with the
// repo VERSION file on every release.
[assembly: System.Reflection.AssemblyTitle("Syncyard Tray")]
[assembly: System.Reflection.AssemblyDescription("Syncyard fleet tray: runner supervision, settings, and connection status")]
[assembly: System.Reflection.AssemblyCompany("Axiovex Systems, LLC")]
[assembly: System.Reflection.AssemblyProduct("Syncyard Tray")]
[assembly: System.Reflection.AssemblyCopyright("Copyright (c) Axiovex Systems, LLC")]
[assembly: System.Reflection.AssemblyVersion("0.4.3.0")]
[assembly: System.Reflection.AssemblyFileVersion("0.4.3.0")]

namespace SyncyardTray
{
    internal sealed class RunnerDef
    {
        public string Lane;
        public string Dir;
        public Process StartedByUs;          // cmd.exe hosting run.cmd
        public List<DateTime> RestartTimes = new List<DateTime>();
        public bool Faulted;                  // restart cap hit
        public bool Running;                  // listener seen at last tick

        public string DisplayName
        {
            get { return "lane-" + Lane + "  (" + Dir + ")"; }
        }
    }

    // Minimal, tolerant JSON reading for the two flat shapes this
    // program consumes (its own settings file, spec 014 FR-001, and
    // the fleet snapshot, FR-008), plus the escaping its one writer
    // needs. Hand-rolled on purpose: the tray compiles with the
    // in-box csc and only the WinForms + Drawing references, and
    // that route does not change. Tolerance rules: an unknown key
    // is ignored, a missing key yields the caller's default, a
    // malformed value yields the caller's default. Nothing here
    // throws on bad content.
    internal static class MiniJson
    {
        public static bool HasKey(string text, string key)
        {
            return FindValueStart(text, key) >= 0;
        }

        public static string GetString(string text, string key)
        {
            if (text == null) return null;
            int i = FindValueStart(text, key);
            if (i < 0 || i >= text.Length || text[i] != '"') return null;
            return ParseString(text, i);
        }

        public static bool GetBool(string text, string key, bool def)
        {
            if (text == null) return def;
            int i = FindValueStart(text, key);
            if (i < 0) return def;
            if (MatchAt(text, i, "true")) return true;
            if (MatchAt(text, i, "false")) return false;
            return def;
        }

        public static int GetInt(string text, string key, int def)
        {
            if (text == null) return def;
            int i = FindValueStart(text, key);
            if (i < 0) return def;
            int j = i;
            if (j < text.Length && (text[j] == '-' || text[j] == '+')) j++;
            int start = j;
            while (j < text.Length && text[j] >= '0' && text[j] <= '9') j++;
            if (j == start) return def;
            int v;
            return int.TryParse(text.Substring(i, j - i),
                NumberStyles.Integer, CultureInfo.InvariantCulture,
                out v) ? v : def;
        }

        public static string GetObject(string text, string key)
        {
            if (text == null) return null;
            int i = FindValueStart(text, key);
            if (i < 0 || i >= text.Length || text[i] != '{') return null;
            return ExtractBalanced(text, i);
        }

        public static List<string> GetObjects(string text, string key)
        {
            var list = new List<string>();
            if (text == null) return list;
            int i = FindValueStart(text, key);
            if (i < 0 || i >= text.Length || text[i] != '[') return list;
            int j = i + 1;
            while (j < text.Length)
            {
                while (j < text.Length &&
                       (char.IsWhiteSpace(text[j]) || text[j] == ',')) j++;
                if (j >= text.Length || text[j] == ']') break;
                if (text[j] == '{')
                {
                    string obj = ExtractBalanced(text, j);
                    if (obj == null) break;
                    list.Add(obj);
                    j += obj.Length;
                }
                else
                {
                    // A scalar array element: skip to the separator.
                    while (j < text.Length && text[j] != ',' &&
                           text[j] != ']') j++;
                }
            }
            return list;
        }

        public static List<string> GetStringArray(string text, string key)
        {
            var list = new List<string>();
            if (text == null) return list;
            int i = FindValueStart(text, key);
            if (i < 0 || i >= text.Length || text[i] != '[') return list;
            int j = i + 1;
            while (j < text.Length)
            {
                while (j < text.Length &&
                       (char.IsWhiteSpace(text[j]) || text[j] == ',')) j++;
                if (j >= text.Length || text[j] == ']') break;
                if (text[j] == '"')
                {
                    string s = ParseString(text, j);
                    if (s == null) break;
                    list.Add(s);
                    j = SkipString(text, j);
                }
                else
                {
                    while (j < text.Length && text[j] != ',' &&
                           text[j] != ']') j++;
                }
            }
            return list;
        }

        // The top-level keys of a JSON object string, in
        // order. MiniJson otherwise reads values by key, but
        // the snapshot's "lanes" object is keyed BY lane
        // name, so its keys are data. String-aware and
        // depth-aware; tolerant like everything here - a
        // malformed object yields the keys read so far.
        public static List<string> GetObjectKeys(string obj)
        {
            var keys = new List<string>();
            if (obj == null) return keys;
            int i = 0;
            while (i < obj.Length && obj[i] != '{') i++;
            if (i >= obj.Length) return keys;
            i++;
            int depth = 1;
            while (i < obj.Length && depth >= 1)
            {
                char c = obj[i];
                if (c == '"')
                {
                    if (depth == 1)
                    {
                        // A depth-1 string followed by a
                        // colon is a key; any other depth-1
                        // string is a value.
                        string s = ParseString(obj, i);
                        if (s == null) break;
                        int j = SkipString(obj, i);
                        int k = j;
                        while (k < obj.Length &&
                               char.IsWhiteSpace(obj[k])) k++;
                        if (k < obj.Length && obj[k] == ':')
                            keys.Add(s);
                        i = j;
                        continue;
                    }
                    i = SkipString(obj, i);
                    continue;
                }
                if (c == '{' || c == '[') depth++;
                else if (c == '}' || c == ']') depth--;
                i++;
            }
            return keys;
        }

        public static string Escape(string s)
        {
            if (s == null) return "";
            var sb = new StringBuilder();
            foreach (char c in s)
            {
                if (c == '"') sb.Append("\\\"");
                else if (c == '\\') sb.Append("\\\\");
                else if (c < ' ') sb.Append(' ');
                else sb.Append(c);
            }
            return sb.ToString();
        }

        // Index of the first character of the value for "key", or
        // -1. Finds the key as a quoted token followed by a colon.
        private static int FindValueStart(string text, string key)
        {
            if (text == null) return -1;
            string needle = "\"" + key + "\"";
            int from = 0;
            while (from < text.Length)
            {
                int i = text.IndexOf(needle, from,
                    StringComparison.Ordinal);
                if (i < 0) return -1;
                int j = i + needle.Length;
                while (j < text.Length && char.IsWhiteSpace(text[j])) j++;
                if (j < text.Length && text[j] == ':')
                {
                    j++;
                    while (j < text.Length &&
                           char.IsWhiteSpace(text[j])) j++;
                    return j;
                }
                from = i + 1;
            }
            return -1;
        }

        private static bool MatchAt(string text, int i, string word)
        {
            if (i < 0 || i + word.Length > text.Length) return false;
            return string.Compare(text, i, word, 0, word.Length,
                StringComparison.Ordinal) == 0;
        }

        // text[i] must be the opening quote. Returns the unescaped
        // string content, or null if the string never terminates.
        private static string ParseString(string text, int i)
        {
            var sb = new StringBuilder();
            int j = i + 1;
            while (j < text.Length)
            {
                char c = text[j];
                if (c == '"') return sb.ToString();
                if (c == '\\' && j + 1 < text.Length)
                {
                    char e = text[j + 1];
                    if (e == 'n') sb.Append('\n');
                    else if (e == 't') sb.Append('\t');
                    else if (e == 'r') sb.Append('\r');
                    else sb.Append(e);
                    j += 2;
                    continue;
                }
                sb.Append(c);
                j++;
            }
            return null;
        }

        // Index just past the string that opens at text[i].
        private static int SkipString(string text, int i)
        {
            int j = i + 1;
            while (j < text.Length)
            {
                if (text[j] == '\\') { j += 2; continue; }
                if (text[j] == '"') return j + 1;
                j++;
            }
            return j;
        }

        // The balanced {...} or [...] substring opening at text[i],
        // string-aware; null if it never closes.
        private static string ExtractBalanced(string text, int i)
        {
            char open = text[i];
            char close = open == '{' ? '}' : ']';
            int depth = 0;
            bool inStr = false;
            for (int j = i; j < text.Length; j++)
            {
                char c = text[j];
                if (inStr)
                {
                    if (c == '\\') j++;
                    else if (c == '"') inStr = false;
                    continue;
                }
                if (c == '"') inStr = true;
                else if (c == open) depth++;
                else if (c == close)
                {
                    depth--;
                    if (depth == 0) return text.Substring(i, j - i + 1);
                }
            }
            return null;
        }
    }

    // The tray's own settings (spec 014 Part A): flat JSON in
    // tray-settings.json beside the tray exe, loaded at startup,
    // saved only from the settings dialog. The writer emits exactly
    // the keys the reader consumes - the source battery proves the
    // parity. Defaults are the pre-spec-014 behavior, so a tray
    // with no settings file behaves exactly as the old tray did.
    internal sealed class TraySettings
    {
        public bool Autostart = true;
        public bool SupervisionEnabled = true;
        public int MaxRestartsPerHour = 3;
        public int TickSeconds = 5;
        public bool HideStrayConsoles = true;
        public string FleetStatusPath =
            @"C:\Users\trist\Development\Syncyard\status\fleet-status.json";
        public int FleetStaleSeconds = 300;
        // Spec 021 FR-007: the runner root is per-machine. The
        // default is the original desktop layout; a machine
        // whose lanes live elsewhere (the laptop) sets
        // runnerRoot in tray-settings.json and the lane dirs
        // are built from it. Nothing else about a lane changes.
        public string RunnerRoot =
            @"C:\Users\trist\Development\Syncyard\runners";
        // Spec 018: the backchannel agent's heartbeat file on this
        // host. The deployed agent (backchannel/agent.sh) writes
        // it at ~/syncyard-backchannel/repo/channels/syncyard-dev/
        // heartbeat.json inside the owner's WSL distro; the tray
        // reads the same file through the distro's UNC share.
        public string ConnectionHeartbeatPath =
            @"\\wsl.localhost\Ubuntu-24.04\home\tpierson\syncyard-backchannel\repo\channels\syncyard-dev\heartbeat.json";
        public int ConnectionStaleSeconds = 180;
        public int ConnectionDownSeconds = 900;

        private readonly Dictionary<string, bool> _laneEnabled =
            new Dictionary<string, bool>();

        public static readonly string[] Lanes =
            { "long", "quick", "normal", "gpu" };

        // Set by Load/Save when the file could not be read or
        // written; the tray logs it and carries on with defaults.
        public static string LastNote;

        public bool LaneEnabled(string lane)
        {
            bool v;
            return _laneEnabled.TryGetValue(lane, out v) ? v : true;
        }

        public void SetLaneEnabled(string lane, bool enabled)
        {
            _laneEnabled[lane] = enabled;
        }

        public static string FilePath
        {
            get
            {
                return Path.Combine(
                    AppDomain.CurrentDomain.BaseDirectory,
                    "tray-settings.json");
            }
        }

        public static TraySettings Defaults()
        {
            var s = new TraySettings();
            foreach (var lane in Lanes) s._laneEnabled[lane] = true;
            s.Autostart = AutostartIsRegistered();
            return s;
        }

        public static TraySettings Load()
        {
            var s = Defaults();
            try
            {
                if (!File.Exists(FilePath))
                {
                    LastNote = "no settings file; defaults in use";
                    return s;
                }
                string text = File.ReadAllText(FilePath);
                s.Autostart = MiniJson.GetBool(text, "autostart",
                    s.Autostart);
                s.SupervisionEnabled = MiniJson.GetBool(text,
                    "supervisionEnabled", s.SupervisionEnabled);
                s.MaxRestartsPerHour = Clamp(MiniJson.GetInt(text,
                    "maxRestartsPerHour", s.MaxRestartsPerHour), 1, 20);
                s.TickSeconds = Clamp(MiniJson.GetInt(text,
                    "tickSeconds", s.TickSeconds), 2, 60);
                s.HideStrayConsoles = MiniJson.GetBool(text,
                    "hideStrayConsoles", s.HideStrayConsoles);
                string fp = MiniJson.GetString(text, "fleetStatusPath");
                if (!string.IsNullOrEmpty(fp)) s.FleetStatusPath = fp;
                string rr = MiniJson.GetString(text, "runnerRoot");
                if (!string.IsNullOrEmpty(rr)) s.RunnerRoot = rr;
                s.FleetStaleSeconds = Clamp(MiniJson.GetInt(text,
                    "fleetStaleSeconds", s.FleetStaleSeconds), 30, 3600);
                string hb = MiniJson.GetString(text,
                    "connectionHeartbeatPath");
                if (!string.IsNullOrEmpty(hb))
                    s.ConnectionHeartbeatPath = hb;
                s.ConnectionStaleSeconds = Clamp(MiniJson.GetInt(text,
                    "connectionStaleSeconds", s.ConnectionStaleSeconds),
                    10, 3600);
                s.ConnectionDownSeconds = Clamp(MiniJson.GetInt(text,
                    "connectionDownSeconds", s.ConnectionDownSeconds),
                    30, 86400);
                if (s.ConnectionStaleSeconds >= s.ConnectionDownSeconds)
                {
                    // An inverted pair cannot be evaluated; fall
                    // back to the FR-001 defaults rather than
                    // guess which bound the owner meant.
                    s.ConnectionStaleSeconds = 180;
                    s.ConnectionDownSeconds = 900;
                }
                foreach (var lane in Lanes)
                    s.SetLaneEnabled(lane, MiniJson.GetBool(text,
                        "laneEnabled_" + lane, true));
            }
            catch (Exception ex)
            {
                LastNote = "settings file unreadable (" + ex.Message +
                    "); defaults in use";
                return Defaults();
            }
            return s;
        }

        public void Save()
        {
            LastNote = null;
            try { File.WriteAllText(FilePath, Serialize()); }
            catch (Exception ex)
            {
                LastNote = "settings save failed: " + ex.Message;
            }
        }

        public string Serialize()
        {
            var sb = new StringBuilder();
            sb.Append("{\n");
            Line(sb, "version", "1", true);
            Line(sb, "autostart", Bool(Autostart), true);
            Line(sb, "supervisionEnabled", Bool(SupervisionEnabled), true);
            Line(sb, "maxRestartsPerHour",
                MaxRestartsPerHour.ToString(CultureInfo.InvariantCulture),
                true);
            Line(sb, "tickSeconds",
                TickSeconds.ToString(CultureInfo.InvariantCulture), true);
            Line(sb, "hideStrayConsoles", Bool(HideStrayConsoles), true);
            Line(sb, "fleetStatusPath",
                "\"" + MiniJson.Escape(FleetStatusPath) + "\"", true);
            Line(sb, "runnerRoot",
                "\"" + MiniJson.Escape(RunnerRoot) + "\"", true);
            Line(sb, "fleetStaleSeconds",
                FleetStaleSeconds.ToString(CultureInfo.InvariantCulture),
                true);
            Line(sb, "connectionHeartbeatPath",
                "\"" + MiniJson.Escape(ConnectionHeartbeatPath) + "\"",
                true);
            Line(sb, "connectionStaleSeconds",
                ConnectionStaleSeconds.ToString(
                    CultureInfo.InvariantCulture),
                true);
            Line(sb, "connectionDownSeconds",
                ConnectionDownSeconds.ToString(
                    CultureInfo.InvariantCulture),
                true);
            for (int i = 0; i < Lanes.Length; i++)
                Line(sb, "laneEnabled_" + Lanes[i],
                    Bool(LaneEnabled(Lanes[i])), i < Lanes.Length - 1);
            sb.Append("}\n");
            return sb.ToString();
        }

        private static void Line(StringBuilder sb, string key,
            string value, bool comma)
        {
            sb.Append("  \"").Append(key).Append("\": ").Append(value);
            sb.Append(comma ? ",\n" : "\n");
        }

        private static string Bool(bool v) { return v ? "true" : "false"; }

        private static int Clamp(int v, int lo, int hi)
        {
            return v < lo ? lo : v > hi ? hi : v;
        }

        // --- autostart (HKCU Run) -----------------------------------
        // The Run value is the machine's actual autostart state:
        // installers set it, the spec 011 conversion retires it,
        // and this class is the only tray code that touches it -
        // read to tell the truth in the dialog and in the load
        // default, written only by ApplyAutostart from the
        // dialog's save path (spec 014 FR-005). The tray never
        // registers itself at startup.

        private const string RunKeyPath =
            @"Software\Microsoft\Windows\CurrentVersion\Run";
        private const string RunValueName = "SyncyardTray";

        public static bool AutostartIsRegistered()
        {
            try
            {
                using (var key = Microsoft.Win32.Registry.CurrentUser
                    .OpenSubKey(RunKeyPath, false))
                {
                    return key != null &&
                        key.GetValue(RunValueName) != null;
                }
            }
            catch { return false; }
        }

        public void ApplyAutostart()
        {
            try
            {
                using (var key = Microsoft.Win32.Registry.CurrentUser
                    .OpenSubKey(RunKeyPath, true))
                {
                    if (key == null) return;
                    if (Autostart)
                        key.SetValue(RunValueName, "\"" +
                            Application.ExecutablePath + "\"");
                    else
                        key.DeleteValue(RunValueName, false);
                }
            }
            catch (Exception ex)
            {
                LastNote = "autostart change failed: " + ex.Message;
            }
        }
    }

    internal sealed class FleetJob
    {
        public string Key = "";
        public string Program = "";
        public string Lane = "";
        public int Priority;
        public bool HasPriority;
        public string PrioritySource;   // null when not published
        public bool Held;
        public bool HasHeld;
        public string RunId;            // active entries only,
                                        // null when not published
    }

    // One lane's queue depth from the snapshot's "lanes"
    // object (0.4.3.0): how many runs the lane may carry at
    // once, how many it carries, how many wait, and the
    // lane's state in the four-state model (idle, waiting,
    // running, full), carried verbatim like the mode.
    internal sealed class FleetLane
    {
        public string Lane = "";
        public int Cap;
        public int Running;
        public int Waiting;
        public string State = "";
    }

    // A read-only view of the gatekeeper's published status
    // (spec 014 Part C, contract in FR-008). The tray reads the
    // snapshot file at most once per tick and never writes it.
    // The mode string is carried verbatim - a mode this build
    // does not know is displayed as read, never dropped.
    internal sealed class FleetStatus
    {
        public bool HasData;
        public bool Stale;
        public string Problem;          // "missing" | "unparseable"
        public string Mode = "";
        public bool Draining;
        public string Reason = "";
        public string GeneratedAt = "";
        public double AgeSeconds = -1;
        public readonly List<string> FocusKeys = new List<string>();
        public readonly List<string> FocusPrograms = new List<string>();
        public readonly List<string> FocusChats = new List<string>();
        public readonly List<FleetJob> Waiting = new List<FleetJob>();
        // Queue depth (0.4.3.0, additive): a snapshot whose
        // publisher does not emit these fields leaves
        // HasQueueData false and the totals at -1, and the
        // displays say running is unknown rather than
        // guessing it from the waiting list.
        public readonly List<FleetJob> Active = new List<FleetJob>();
        public readonly List<FleetLane> Lanes = new List<FleetLane>();
        public bool HasQueueData;
        public string QueueState;       // null when not published
        public int RunningTotal = -1;
        public int WaitingTotal = -1;

        public string ModeLine()
        {
            return Draining ? Mode + " - DRAINING" : Mode;
        }

        public static string FormatAge(double seconds)
        {
            if (seconds < 0) return "age unknown";
            if (seconds < 90) return ((int)seconds) + "s old";
            if (seconds < 5400) return ((int)(seconds / 60)) + "m old";
            return ((int)(seconds / 3600)) + "h old";
        }

        public static FleetStatus Load(string path, int staleSeconds)
        {
            var st = new FleetStatus();
            try
            {
                if (string.IsNullOrEmpty(path) || !File.Exists(path))
                {
                    st.Problem = "missing";
                    return st;
                }
                string text = File.ReadAllText(path);
                string mode = MiniJson.GetString(text, "mode");
                if (mode == null)
                {
                    st.Problem = "unparseable";
                    return st;
                }
                st.HasData = true;
                st.Mode = mode;
                st.Draining = MiniJson.GetBool(text, "draining", false);
                st.Reason = MiniJson.GetString(text, "reason") ?? "";
                st.GeneratedAt =
                    MiniJson.GetString(text, "generated_at") ?? "";
                string focus = MiniJson.GetObject(text, "focus");
                if (focus != null)
                {
                    st.FocusKeys.AddRange(
                        MiniJson.GetStringArray(focus, "keys"));
                    st.FocusPrograms.AddRange(
                        MiniJson.GetStringArray(focus, "programs"));
                    st.FocusChats.AddRange(
                        MiniJson.GetStringArray(focus, "chats"));
                }
                foreach (var obj in MiniJson.GetObjects(text, "waiting"))
                {
                    var job = new FleetJob();
                    job.Key = MiniJson.GetString(obj, "key") ?? "";
                    job.Program = MiniJson.GetString(obj, "program") ?? "";
                    job.Lane = MiniJson.GetString(obj, "lane") ?? "";
                    job.HasPriority = MiniJson.HasKey(obj, "priority");
                    job.Priority = MiniJson.GetInt(obj, "priority", 0);
                    job.PrioritySource =
                        MiniJson.GetString(obj, "priority_source");
                    job.HasHeld = MiniJson.HasKey(obj, "held");
                    job.Held = MiniJson.GetBool(obj, "held", false);
                    st.Waiting.Add(job);
                }
                // Queue depth, parsed under its own guard: a
                // malformed depth section degrades to "not
                // published" and must never poison the mode
                // and waiting reads above.
                try
                {
                    foreach (var obj in MiniJson.GetObjects(
                        text, "active"))
                    {
                        var job = new FleetJob();
                        job.Key = MiniJson.GetString(obj, "key") ?? "";
                        job.Program =
                            MiniJson.GetString(obj, "program") ?? "";
                        job.Lane = MiniJson.GetString(obj, "lane") ?? "";
                        job.RunId = MiniJson.GetString(obj, "run_id");
                        if (job.RunId == null &&
                            MiniJson.HasKey(obj, "run_id"))
                        {
                            // Run ids are numbers in the
                            // records; carry the digits.
                            int rid = MiniJson.GetInt(obj, "run_id",
                                -1);
                            if (rid >= 0)
                                job.RunId = rid.ToString(
                                    CultureInfo.InvariantCulture);
                        }
                        st.Active.Add(job);
                    }
                    string lanesObj = MiniJson.GetObject(text,
                        "lanes");
                    if (lanesObj != null)
                    {
                        foreach (var name in MiniJson.GetObjectKeys(
                            lanesObj))
                        {
                            string sub = MiniJson.GetObject(
                                lanesObj, name);
                            if (sub == null) continue;
                            var lane = new FleetLane();
                            lane.Lane = name;
                            lane.Cap = MiniJson.GetInt(sub, "cap",
                                0);
                            lane.Running = MiniJson.GetInt(sub,
                                "running", 0);
                            lane.Waiting = MiniJson.GetInt(sub,
                                "waiting", 0);
                            lane.State = MiniJson.GetString(sub,
                                "state") ?? "";
                            st.Lanes.Add(lane);
                        }
                    }
                    if (MiniJson.HasKey(text, "queue_state"))
                    {
                        st.QueueState = MiniJson.GetString(text,
                            "queue_state");
                        st.HasQueueData = st.QueueState != null;
                    }
                    if (MiniJson.HasKey(text, "running_total"))
                        st.RunningTotal = MiniJson.GetInt(text,
                            "running_total", -1);
                    if (MiniJson.HasKey(text, "waiting_total"))
                        st.WaitingTotal = MiniJson.GetInt(text,
                            "waiting_total", -1);
                }
                catch
                {
                    st.Active.Clear();
                    st.Lanes.Clear();
                    st.HasQueueData = false;
                    st.QueueState = null;
                    st.RunningTotal = -1;
                    st.WaitingTotal = -1;
                }
                DateTime gen;
                if (DateTime.TryParse(st.GeneratedAt,
                        CultureInfo.InvariantCulture,
                        DateTimeStyles.AdjustToUniversal |
                        DateTimeStyles.AssumeUniversal, out gen))
                {
                    st.AgeSeconds =
                        (DateTime.UtcNow - gen).TotalSeconds;
                    if (st.AgeSeconds < 0) st.AgeSeconds = 0;
                    st.Stale = st.AgeSeconds > staleSeconds;
                }
            }
            catch
            {
                st.HasData = false;
                st.Problem = "unparseable";
            }
            return st;
        }
    }

    // The tray's connection state (spec 018): whether this host
    // is in contact with the fleet, judged ONLY from signals
    // already present on the host - the backchannel agent's
    // heartbeat file (primary) and, when a publisher exists, the
    // fleet-status snapshot (secondary). The tray makes no
    // network calls of its own and never becomes a second
    // watcher; the indicator is display only (FR-007): this
    // section reads the signal files and nothing else - it
    // starts nothing, stops nothing, and writes nothing.
    internal sealed class ConnectionStatus
    {
        public const int Connected = 0;
        public const int Stale = 1;
        public const int Disconnected = 2;

        public int State = Disconnected;
        public bool HasSignal;
        public double AgeSeconds = -1;
        public string SignalName = "";   // "heartbeat" | "fleet snapshot"
        public bool HeartbeatSeen;
        public bool SnapshotSeen;

        // The pure state function (FR-001): no signal ever seen
        // is DISCONNECTED - a fresh install must not claim a
        // connection it has never observed.
        public static int StateForAge(bool hasSignal, double ageSeconds,
            int staleSeconds, int downSeconds)
        {
            if (!hasSignal) return Disconnected;
            if (ageSeconds <= staleSeconds) return Connected;
            if (ageSeconds <= downSeconds) return Stale;
            return Disconnected;
        }

        public static string StateName(int state)
        {
            return state == Connected ? "CONNECTED"
                 : state == Stale ? "STALE" : "DISCONNECTED";
        }

        public static string FormatAgeShort(double seconds)
        {
            if (seconds < 90) return ((int)seconds) + " s";
            return ((int)(seconds / 60)) + " min";
        }

        // The menu/tooltip wording (FR-003). The no-signal case
        // reads "unknown" in words even though the icon state
        // is DISCONNECTED: the words report what the tray knows,
        // the icon reports the state it must assume.
        public string Line()
        {
            if (!HasSignal)
                return "Connection: unknown (no signal seen yet)";
            if (State == Connected)
                return "Connection: connected (" + SignalName + " " +
                    FormatAgeShort(AgeSeconds) + " ago)";
            if (State == Stale)
                return "Connection: stale (" + SignalName + " " +
                    FormatAgeShort(AgeSeconds) + " ago)";
            return "Connection: disconnected (no " + SignalName +
                " for " + FormatAgeShort(AgeSeconds) + ")";
        }

        // Signal honesty (FR-004): name exactly the signals the
        // judgement could rest on - never imply a fleet-side
        // confirmation the tray does not have.
        public string SignalDetail()
        {
            if (HeartbeatSeen && SnapshotSeen)
                return "signals: backchannel heartbeat + fleet snapshot";
            if (SnapshotSeen) return "signal: fleet snapshot only";
            return "signal: backchannel heartbeat only";
        }

        // Evaluate the newest available signal. The snapshot's
        // age is the one FleetStatus already computed from its
        // embedded generated_at; a snapshot with no readable
        // stamp is not a usable signal.
        public static ConnectionStatus Evaluate(string heartbeatPath,
            FleetStatus fleet, int staleSeconds, int downSeconds)
        {
            var cs = new ConnectionStatus();
            double hbAge;
            cs.HeartbeatSeen = TryHeartbeatAge(heartbeatPath, out hbAge);
            double snapAge = -1;
            if (fleet != null && fleet.HasData && fleet.AgeSeconds >= 0)
            {
                cs.SnapshotSeen = true;
                snapAge = fleet.AgeSeconds;
            }
            if (cs.HeartbeatSeen &&
                (!cs.SnapshotSeen || hbAge <= snapAge))
            {
                cs.HasSignal = true;
                cs.AgeSeconds = hbAge;
                cs.SignalName = "heartbeat";
            }
            else if (cs.SnapshotSeen)
            {
                cs.HasSignal = true;
                cs.AgeSeconds = snapAge;
                cs.SignalName = "fleet snapshot";
            }
            cs.State = StateForAge(cs.HasSignal, cs.AgeSeconds,
                staleSeconds, downSeconds);
            return cs;
        }

        // The heartbeat's age from its own embedded "ts" (the
        // agent stamps every beat, backchannel/agent.sh), with
        // the file's write time as the fallback when the stamp
        // is unreadable. Read-only and vanishing-tolerant: the
        // agent replaces the file atomically, but a share can
        // still drop mid-read - retry once, then treat the file
        // as missing for this tick.
        private static bool TryHeartbeatAge(string path,
            out double ageSeconds)
        {
            ageSeconds = -1;
            for (int attempt = 0; attempt < 2; attempt++)
            {
                try
                {
                    if (string.IsNullOrEmpty(path) ||
                        !File.Exists(path))
                        return false;
                    string text = File.ReadAllText(path);
                    string ts = MiniJson.GetString(text, "ts");
                    DateTime stamp;
                    if (ts != null && DateTime.TryParse(ts,
                            CultureInfo.InvariantCulture,
                            DateTimeStyles.AdjustToUniversal |
                            DateTimeStyles.AssumeUniversal, out stamp))
                    {
                        ageSeconds =
                            (DateTime.UtcNow - stamp).TotalSeconds;
                    }
                    else
                    {
                        ageSeconds = (DateTime.UtcNow -
                            File.GetLastWriteTimeUtc(path)).TotalSeconds;
                    }
                    if (ageSeconds < 0) ageSeconds = 0;
                    return true;
                }
                catch { /* vanished mid-read: one retry, above */ }
            }
            return false;
        }

        // The heartbeat signal's own age, for the status
        // window (0.4.3.0): the same read Evaluate makes,
        // exposed so the window can show the backchannel
        // agent's last beat beside the combined judgement.
        public static bool HeartbeatAgeSeconds(string path,
            out double ageSeconds)
        {
            return TryHeartbeatAge(path, out ageSeconds);
        }
    }

    internal static class Program
    {
        // The apply half of self-update (spec 021): the running
        // tray starts a detached copy of this same exe with the
        // verified download's path as its argument, then exits.
        // The helper waits for that old process to be gone,
        // renames the installed exe to .bak, renames the
        // download into its place, and starts the new build.
        // Every step is logged to the tray log. There is no UI
        // here at all, by design: no icon, no window, no prompt,
        // and no singleton claim (the relaunched tray takes it).
        private static int ApplyUpdateHelper(string newPath)
        {
            ProgramLog("update helper: applying update from " +
                newPath);
            string exePath;
            try
            {
                exePath = Process.GetCurrentProcess()
                    .MainModule.FileName;
            }
            catch (Exception ex)
            {
                ProgramLog("update helper: cannot name own " +
                    "exe: " + ex.Message);
                return 1;
            }
            if (!File.Exists(newPath))
            {
                ProgramLog("update helper: staged file " +
                    "missing; aborting, nothing changed");
                return 1;
            }
            int waitedMs = 0;
            while (OtherInstanceRunning(exePath) &&
                   waitedMs < 60000)
            {
                Thread.Sleep(500);
                waitedMs += 500;
            }
            if (OtherInstanceRunning(exePath))
            {
                ProgramLog("update helper: another tray " +
                    "instance is still running after 60 s; " +
                    "aborting, nothing changed");
                return 1;
            }
            string bakPath = exePath + ".bak";
            try
            {
                if (File.Exists(bakPath)) File.Delete(bakPath);
                File.Move(exePath, bakPath);
                ProgramLog("update helper: previous build " +
                    "kept as " + bakPath);
                File.Move(newPath, exePath);
                ProgramLog("update helper: new build in place");
            }
            catch (Exception ex)
            {
                ProgramLog("update helper: swap failed: " +
                    ex.Message);
                try
                {
                    if (!File.Exists(exePath) &&
                        File.Exists(bakPath))
                    {
                        File.Move(bakPath, exePath);
                        ProgramLog("update helper: rolled " +
                            "back to the previous build");
                    }
                }
                catch (Exception ex2)
                {
                    ProgramLog("update helper: rollback " +
                        "failed: " + ex2.Message);
                }
            }
            try
            {
                if (File.Exists(exePath))
                {
                    Process.Start(exePath);
                    ProgramLog("update helper: tray relaunched");
                }
                else
                {
                    ProgramLog("update helper: no tray exe " +
                        "in place; nothing relaunched");
                }
            }
            catch (Exception ex)
            {
                ProgramLog("update helper: relaunch failed: " +
                    ex.Message);
            }
            return 0;
        }

        // True while any other process is running this exe
        // path (the tray that started the helper, until it
        // exits; or an unrelated later instance, which the
        // helper must not disturb).
        private static bool OtherInstanceRunning(string exePath)
        {
            int me = Process.GetCurrentProcess().Id;
            Process[] all;
            try { all = Process.GetProcesses(); }
            catch { return false; }
            bool found = false;
            foreach (var p in all)
            {
                try
                {
                    if (p.Id == me) continue;
                    string path = null;
                    try { path = p.MainModule.FileName; }
                    catch { }
                    if (path != null &&
                        string.Equals(path, exePath,
                            StringComparison.OrdinalIgnoreCase))
                    {
                        found = true;
                        break;
                    }
                }
                catch { }
                finally { p.Dispose(); }
            }
            return found;
        }

        [STAThread]
        private static void Main(string[] args)
        {
            // Self-update helper mode (spec 021) runs before
            // the singleton mutex and before any UI exists:
            // the helper must be able to replace the exe while
            // no tray instance holds it, and it shows nothing.
            if (args.Length >= 2 &&
                args[0] == "--apply-update")
            {
                Environment.Exit(ApplyUpdateHelper(args[1]));
                return;
            }
            bool created;
            using (var mutex = new Mutex(true, "SyncyardTraySingleton", out created))
            {
                if (!created)
                {
                    MessageBox.Show("Syncyard Tray is already running.",
                        "Syncyard Tray", MessageBoxButtons.OK,
                        MessageBoxIcon.Information);
                    return;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                // The tray is a supervisor: a fault on its own UI
                // thread must be logged, never surfaced as the modal
                // .NET exception dialog. (2026-11-01 field crash: the
                // default dialog's own construction faulted and the
                // tray died with it.) CatchException + this handler
                // keeps the process alive and the incident in the log.
                Application.SetUnhandledExceptionMode(
                    UnhandledExceptionMode.CatchException);
                Application.ThreadException +=
                    delegate(object sender, ThreadExceptionEventArgs e)
                    {
                        ProgramLog("unhandled UI exception (caught): " +
                            e.Exception);
                    };
                // Exit forensics (2026-11-04, after three silent
                // tray deaths with no log line and no Windows
                // crash event): a fatal fault on any non-UI
                // thread is logged before the process dies, and
                // every orderly end of the message loop is logged
                // as it happens. A death that leaves NEITHER line
                // behind was an external termination, and the
                // per-minute "tray alive" line bounds when it
                // happened. Nothing here can log a TerminateProcess
                // kill; the watchdog's relaunch record covers that
                // case from the other side.
                AppDomain.CurrentDomain.UnhandledException +=
                    delegate(object sender,
                        UnhandledExceptionEventArgs e)
                    {
                        ProgramLog("unhandled appdomain exception " +
                            "(fatal): " + e.ExceptionObject);
                    };
                Application.ApplicationExit +=
                    delegate(object sender, EventArgs e)
                    {
                        ProgramLog("tray process exiting " +
                            "(ApplicationExit)");
                    };
                Application.Run(new TrayApp());
            }
        }

        private static void ProgramLog(string msg)
        {
            try
            {
                string dir = Path.Combine(
                    Environment.GetFolderPath(
                        Environment.SpecialFolder.LocalApplicationData),
                    "SyncyardTray");
                Directory.CreateDirectory(dir);
                File.AppendAllText(Path.Combine(dir, "tray.log"),
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") +
                    "  " + msg + Environment.NewLine);
            }
            catch { }
        }
    }

    internal sealed class TrayApp : ApplicationContext
    {
        private const int SW_HIDE = 0;

        [DllImport("user32.dll")]
        private static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool DestroyIcon(IntPtr hIcon);

        private readonly NotifyIcon _icon;
        private readonly System.Windows.Forms.Timer _timer;
        private readonly List<RunnerDef> _runners = new List<RunnerDef>();
        private readonly string _logPath;
        private readonly Image _logoImage;
        private Icon _currentIcon;
        private int _iconState = -1;
        private int _iconConnState = -1;
        // The one menu strip for the tray's whole lifetime:
        // created once in the constructor, bound to the icon
        // once, and never replaced or disposed while the tray
        // lives; RebuildMenu rebuilds its items in place (see
        // the note there for the disposed-strip crash this
        // invariant exists to prevent).
        private ContextMenuStrip _menu;
        private bool _menuOpen;
        private bool _menuDirty;
        private int _tick;
        private TraySettings _settings;
        private FleetStatus _fleet;
        private ConnectionStatus _conn;
        private int _lastConnState = -1;
        private SettingsForm _settingsForm;
        private StatusForm _statusForm;
        private ConsolesForm _consolesForm;
        private System.Windows.Forms.Timer _launchTimer;

        // Self-update state (spec 021). The build stamp is the
        // value the menu header and the startup log line carry;
        // keep it in step with the assembly version attributes
        // at the top of this file and the repo VERSION file
        // (the update compare itself reads the live assembly
        // version, so a stamp drift could mislabel, never
        // misjudge). All other fields are UI-thread state,
        // written by the posted completions of the two worker
        // threads below; the workers never touch the menu.
        private const string BuildId = "0.4.3.0";
        private const string UpdateManifestUrl =
            "https://github.com/AXIOVEX/syncyard/releases/latest/download/tray-update.json";
        private string _updateVersion;     // newer build, if any
        private string _updateSha256;
        private string _updateUrl;
        private bool _updateChecked;       // a check has finished
        private bool _updateBusy;          // check/download runs
        private bool _updateDownloading;   // the run is the exe
        private string _pendingUpdatePath; // verified .new file
        private bool _updateLaunched;      // helper started
        private SynchronizationContext _uiContext;
        // Check bookkeeping (0.4.3.0): when the last check
        // finished and what it found, for the status window;
        // and the launch check's one balloon - the launch
        // check arms it, whichever completion path runs
        // first shows it, and it fires at most once.
        private DateTime _lastCheckAt = DateTime.MinValue;
        private string _lastCheckResult;   // null = never checked
        private bool _checkFromLaunch;
        private bool _launchBalloonPending;
        private bool _updateBalloonShown;

        // The queue line's waiting-state styling: one shared
        // bold font and one amber, held for the process's
        // life (the icon discipline: rare and tight - a font
        // per rebuild would leak a handle per tick).
        private static readonly Font QueueAlertFont =
            new Font(Control.DefaultFont, FontStyle.Bold);
        private static readonly Color QueueAlertColor =
            Color.FromArgb(192, 120, 0);

        public TrayApp()
        {
            string baseDir = Path.Combine(
                Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData),
                "SyncyardTray");
            Directory.CreateDirectory(baseDir);
            _logPath = Path.Combine(baseDir, "tray.log");

            // The project mark is embedded in this program as the
            // SyncyardLogo.png resource (spec 007 FR-007); a logo.png
            // next to the exe is only a fallback. The tray icon IS
            // the mark, with a slash overlay when runners are down.
            try
            {
                var asm = System.Reflection.Assembly
                    .GetExecutingAssembly();
                using (var stream = asm.GetManifestResourceStream(
                    "SyncyardLogo.png"))
                {
                    if (stream != null)
                        _logoImage = new Bitmap(Image.FromStream(stream));
                }
            }
            catch { _logoImage = null; }
            if (_logoImage == null)
            {
                try
                {
                    string logoPath = Path.Combine(
                        AppDomain.CurrentDomain.BaseDirectory, "logo.png");
                    if (File.Exists(logoPath))
                        _logoImage = Image.FromFile(logoPath);
                }
                catch { _logoImage = null; }
            }

            // Settings load before the runner table exists:
            // the lane dirs are built from the per-machine
            // runner root the settings carry (spec 021 FR-007).
            _settings = TraySettings.Load();

            _runners.Add(new RunnerDef { Lane = "long",   Dir = Path.Combine(_settings.RunnerRoot, "long") });
            _runners.Add(new RunnerDef { Lane = "quick",  Dir = Path.Combine(_settings.RunnerRoot, "quick") });
            _runners.Add(new RunnerDef { Lane = "normal", Dir = Path.Combine(_settings.RunnerRoot, "normal") });
            _runners.Add(new RunnerDef { Lane = "gpu",    Dir = Path.Combine(_settings.RunnerRoot, "gpu") });

            _icon = new NotifyIcon();
            _icon.Text = "Syncyard runners";
            _icon.Visible = true;
            _icon.DoubleClick += delegate { RebuildMenu(); };

            // The menu strip is created and bound ONCE, here;
            // RebuildMenu only ever rebuilds its items.
            _menu = new ContextMenuStrip();
            _menu.Opened += delegate { _menuOpen = true; };
            _menu.Closed += delegate
            {
                _menuOpen = false;
                if (_menuDirty) { _menuDirty = false; RebuildMenu(); }
            };
            _icon.ContextMenuStrip = _menu;

            if (TraySettings.LastNote != null)
                Log("settings: " + TraySettings.LastNote);

            Log("tray started");
            Log("tray build " + BuildId);
            RefreshStates();
            StartMissing(false);
            if (_settings.HideStrayConsoles) HideStrayConsoles();
            _fleet = FleetStatus.Load(_settings.FleetStatusPath,
                _settings.FleetStaleSeconds);
            // The connection baseline (spec 018): evaluated
            // once here so the first tick's compare-and-log has
            // a prior state; the startup state itself is not a
            // transition and is not logged as one.
            _conn = ConnectionStatus.Evaluate(
                _settings.ConnectionHeartbeatPath, _fleet,
                _settings.ConnectionStaleSeconds,
                _settings.ConnectionDownSeconds);
            _lastConnState = _conn.State;
            RebuildMenu();

            _timer = new System.Windows.Forms.Timer();
            _timer.Interval = _settings.TickSeconds * 1000;
            _timer.Tick += OnTick;
            _timer.Start();

            // One update check a few seconds after startup
            // (0.4.3.0): the same check the menu item runs,
            // on its background thread. A check downloads
            // nothing and installs nothing - an update it
            // finds is announced once (balloon + the menu
            // item's text) and installed only on the
            // owner's click, exactly like a manual check's.
            _launchTimer = new System.Windows.Forms.Timer();
            _launchTimer.Interval = 4000;
            _launchTimer.Tick += delegate
            {
                _launchTimer.Stop();
                _launchTimer.Dispose();
                _launchTimer = null;
                if (!_updateBusy)
                {
                    Log("update check at launch");
                    _checkFromLaunch = true;
                    StartUpdateCheck();
                }
            };
            _launchTimer.Start();
        }

        // --- supervision ------------------------------------------------

        private void OnTick(object sender, EventArgs e)
        {
            // A supervision hiccup must never surface as the modal
            // .NET exception dialog or kill the tray: log and ride on.
            try { TickCore(); }
            catch (Exception ex) { Log("tick error (caught): " + ex); }
        }

        private void TickCore()
        {
            _tick++;
            // Self-update plumbing on the UI thread (spec 021):
            // capture the context the worker completions post
            // to, and act as the backstop launcher if a verified
            // download completed with no context captured.
            if (_uiContext == null)
                _uiContext = SynchronizationContext.Current;
            if (_pendingUpdatePath != null) LaunchHelperAndExit();
            bool wasFaulted = AnyFaulted();
            RefreshStates();
            StartMissing(false);
            if (_tick % 12 == 0)
                Log("tray alive");     // every ~60 s: bounds any
                                       // silent death to a minute
            if (_tick % 6 == 0 && _settings.HideStrayConsoles)
                HideStrayConsoles();   // every ~30 s
            if (!wasFaulted && AnyFaulted())
            {
                _icon.ShowBalloonTip(10000, "Syncyard runners",
                    "A runner kept dying and hit the restart cap. " +
                    "It will not be restarted again - open the tray menu.",
                    ToolTipIcon.Error);
            }
            // One fleet read per tick, after supervision, so the
            // menu, tooltip, and any open dialog all render the
            // same snapshot (spec 014 FR-008/FR-010).
            ReloadSignals();
            MaybeShowUpdateBalloon();
            RebuildMenu();
            RefreshSettingsDialog();
            RefreshStatusDialog();
        }

        // The one fleet read plus the connection judgement
        // (spec 018 FR-001): shared by the tick and by the
        // status window's own refresh, so both surfaces
        // render the same snapshot and a transition is
        // logged exactly once, by whichever surface reads
        // first. A transition is logged with the signal
        // age that caused it, and only a transition
        // (FR-006) - steady-state reads write nothing.
        internal void ReloadSignals()
        {
            _fleet = FleetStatus.Load(_settings.FleetStatusPath,
                _settings.FleetStaleSeconds);
            _conn = ConnectionStatus.Evaluate(
                _settings.ConnectionHeartbeatPath, _fleet,
                _settings.ConnectionStaleSeconds,
                _settings.ConnectionDownSeconds);
            if (_conn.State != _lastConnState)
            {
                Log("connection " +
                    ConnectionStatus.StateName(_lastConnState) +
                    " -> " + ConnectionStatus.StateName(_conn.State) +
                    " (" + (_conn.HasSignal
                        ? _conn.SignalName + " age " +
                            ((int)_conn.AgeSeconds) + " s"
                        : "no signal seen") + ")");
                _lastConnState = _conn.State;
            }
        }

        private bool AnyFaulted()
        {
            foreach (var r in _runners) if (r.Faulted) return true;
            return false;
        }

        private void RefreshStates()
        {
            var listeners = new List<Process>();
            try { listeners.AddRange(Process.GetProcessesByName("Runner.Listener")); }
            catch { /* process census is best-effort */ }

            foreach (var r in _runners)
            {
                r.Running = false;
                foreach (var p in listeners)
                {
                    string path = SafePath(p);
                    if (path != null && path.StartsWith(r.Dir,
                        StringComparison.OrdinalIgnoreCase))
                    {
                        r.Running = true;
                        break;
                    }
                }
            }
        }

        private static string SafePath(Process p)
        {
            try { return p.MainModule.FileName; }
            catch { return null; }
        }

        // The supervision pass. manual=true is an explicit owner
        // act (Start all, dialog buttons): it bypasses the master
        // supervision toggle but never a lane's own toggle -
        // an unsupervised lane is started only from its own
        // per-lane Start control (spec 014 FR-004/FR-006).
        private void StartMissing(bool manual)
        {
            if (!manual && !_settings.SupervisionEnabled) return;
            foreach (var r in _runners)
            {
                if (!_settings.LaneEnabled(r.Lane)) continue;
                if (r.Running || r.Faulted) continue;
                if (!Directory.Exists(r.Dir)) continue;
                if (!File.Exists(Path.Combine(r.Dir, "run.cmd"))) continue;

                var now = DateTime.UtcNow;
                r.RestartTimes.RemoveAll(t => (now - t).TotalHours > 1.0);
                if (r.RestartTimes.Count >= _settings.MaxRestartsPerHour)
                {
                    r.Faulted = true;
                    Log("OWNER NEEDED: " + r.Lane + " hit the restart cap");
                    continue;
                }
                StartRunner(r);
            }
        }

        private void StartRunner(RunnerDef r)
        {
            try
            {
                var psi = new ProcessStartInfo();
                psi.FileName = Path.Combine(
                    Environment.GetFolderPath(
                        Environment.SpecialFolder.System), "cmd.exe");
                psi.Arguments = "/c run.cmd";
                psi.WorkingDirectory = r.Dir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;
                r.StartedByUs = Process.Start(psi);
                r.RestartTimes.Add(DateTime.UtcNow);
                Log("started " + r.Lane + " hidden (dir " + r.Dir + ")");
            }
            catch (Exception ex)
            {
                Log("start failed for " + r.Lane + ": " + ex.Message);
            }
        }

        private void StopRunner(RunnerDef r)
        {
            // Kill the listener for this dir, and the run.cmd host if we
            // started it. A listener killed mid-job kills that job - the
            // menu asks for confirmation first.
            try
            {
                foreach (var p in Process.GetProcessesByName("Runner.Listener"))
                {
                    string path = SafePath(p);
                    if (path != null && path.StartsWith(r.Dir,
                        StringComparison.OrdinalIgnoreCase))
                    {
                        p.Kill();
                        Log("stopped listener for " + r.Lane);
                    }
                }
            }
            catch (Exception ex) { Log("stop failed " + r.Lane + ": " + ex.Message); }
            try
            {
                if (r.StartedByUs != null && !r.StartedByUs.HasExited)
                    r.StartedByUs.Kill();
            }
            catch { }
            r.Running = false;
        }

        private void StartAll()
        {
            foreach (var r in _runners)
            {
                r.Faulted = false;
                r.RestartTimes.Clear();
            }
            RefreshStates();
            StartMissing(true);
            RefreshStates();
            RebuildMenu();
        }

        private void RestartRunner(RunnerDef r)
        {
            // One act composed of the existing acts, each of which
            // logs itself: stop, fault-clear, start (spec 014 FR-006).
            StopRunner(r);
            r.Faulted = false;
            r.RestartTimes.Clear();
            RefreshStates();
            if (!r.Running) StartRunner(r);
            RefreshStates();
        }

        // --- settings surface (spec 014) ----------------------------

        internal IList<RunnerDef> Runners { get { return _runners; } }
        internal TraySettings Settings { get { return _settings; } }
        internal FleetStatus Fleet { get { return _fleet; } }
        internal ConnectionStatus Connection { get { return _conn; } }

        internal void LaneStart(RunnerDef r)
        {
            r.Faulted = false;
            r.RestartTimes.Clear();
            StartRunner(r);
            RefreshStates();
            RebuildMenu();
        }

        internal void LaneStop(RunnerDef r)
        {
            StopRunner(r);
            RefreshStates();
            RebuildMenu();
        }

        internal void LaneRestart(RunnerDef r)
        {
            RestartRunner(r);
            RebuildMenu();
        }

        internal void ApplySettings(TraySettings s)
        {
            _settings = s;
            _settings.Save();
            if (TraySettings.LastNote != null)
                Log("settings: " + TraySettings.LastNote);
            TraySettings.LastNote = null;
            if (s.Autostart != TraySettings.AutostartIsRegistered())
            {
                s.ApplyAutostart();
                if (TraySettings.LastNote != null)
                    Log("settings: " + TraySettings.LastNote);
                else
                    Log("autostart " +
                        (s.Autostart ? "enabled" : "disabled") +
                        " from the settings dialog");
            }
            _timer.Interval = _settings.TickSeconds * 1000;
            Log("settings saved: supervision=" +
                (_settings.SupervisionEnabled ? "on" : "off") +
                " cap=" + _settings.MaxRestartsPerHour +
                " tick=" + _settings.TickSeconds + "s" +
                " hideConsoles=" +
                (_settings.HideStrayConsoles ? "on" : "off"));
            _fleet = FleetStatus.Load(_settings.FleetStatusPath,
                _settings.FleetStaleSeconds);
            // A settings save can move the thresholds or the
            // heartbeat path (spec 018 FR-005): re-evaluate and
            // re-baseline silently - a change the owner just
            // made in the dialog is not a fleet transition and
            // must not log as one.
            _conn = ConnectionStatus.Evaluate(
                _settings.ConnectionHeartbeatPath, _fleet,
                _settings.ConnectionStaleSeconds,
                _settings.ConnectionDownSeconds);
            _lastConnState = _conn.State;
            RebuildMenu();
        }

        private void OpenSettings()
        {
            if (_settingsForm != null && !_settingsForm.IsDisposed)
            {
                _settingsForm.Activate();
                return;
            }
            _settingsForm = new SettingsForm(this);
            _settingsForm.FormClosed +=
                delegate { _settingsForm = null; };
            _settingsForm.Show();
        }

        private void RefreshSettingsDialog()
        {
            try
            {
                if (_settingsForm != null && !_settingsForm.IsDisposed)
                    _settingsForm.RefreshDynamic();
            }
            catch { }
        }

        // --- status and consoles surfaces (0.4.3.0) -----------
        // Both are single-instance, non-modal, and opened
        // only from the menu, exactly like the settings
        // dialog; both are display-only (the consoles
        // viewer's read-only discipline is its own note).

        private void OpenStatus()
        {
            if (_statusForm != null && !_statusForm.IsDisposed)
            {
                _statusForm.Activate();
                return;
            }
            _statusForm = new StatusForm(this);
            _statusForm.FormClosed +=
                delegate { _statusForm = null; };
            _statusForm.Show();
        }

        private void RefreshStatusDialog()
        {
            try
            {
                if (_statusForm != null && !_statusForm.IsDisposed)
                    _statusForm.RefreshDynamic();
            }
            catch { }
        }

        private void OpenConsoles()
        {
            if (_consolesForm != null && !_consolesForm.IsDisposed)
            {
                _consolesForm.Activate();
                return;
            }
            _consolesForm = new ConsolesForm(this);
            _consolesForm.FormClosed +=
                delegate { _consolesForm = null; };
            _consolesForm.Show();
        }

        internal string BuildStamp { get { return BuildId; } }
        internal string TrayLogPath { get { return _logPath; } }

        // The backchannel agent's log, when its path is
        // known: the agent's home is three directories
        // above the channel heartbeat file (channels\
        // <channel>\heartbeat.json -> repo -> agent home)
        // and the log sits in it as agent.log. Any other
        // layout simply yields no agent tab.
        internal string AgentLogPath()
        {
            try
            {
                string hb = _settings.ConnectionHeartbeatPath;
                if (string.IsNullOrEmpty(hb)) return null;
                string dir = Path.GetDirectoryName(hb);
                dir = Path.GetDirectoryName(dir);
                dir = Path.GetDirectoryName(dir);
                dir = Path.GetDirectoryName(dir);
                if (dir == null) return null;
                string p = Path.Combine(dir, "agent.log");
                return File.Exists(p) ? p : null;
            }
            catch { return null; }
        }

        // The newest listener diagnostic log in a runner
        // directory's _diag folder - the runner's own
        // continuous record of what it is doing, and the
        // file the consoles viewer tails for that lane.
        internal static string NewestDiagLog(string runnerDir)
        {
            try
            {
                string diag = Path.Combine(runnerDir, "_diag");
                if (!Directory.Exists(diag)) return null;
                string best = null;
                DateTime bestTs = DateTime.MinValue;
                foreach (var f in Directory.GetFiles(diag, "*.log"))
                {
                    DateTime ts = File.GetLastWriteTimeUtc(f);
                    if (ts > bestTs) { bestTs = ts; best = f; }
                }
                return best;
            }
            catch { return null; }
        }

        private void StopAll()
        {
            var ok = MessageBox.Show(
                "Stop ALL runners? Any jobs running on them will be killed.",
                "Syncyard Tray", MessageBoxButtons.OKCancel,
                MessageBoxIcon.Warning);
            if (ok != DialogResult.OK) return;
            foreach (var r in _runners)
                if (r.Running) StopRunner(r);
            RefreshStates();
            RebuildMenu();
        }

        // --- self-update (spec 021) --------------------------------
        // The tray's only network traffic, and only ever on the
        // owner's explicit menu click: a check reads the update
        // manifest published with the project's GitHub releases,
        // an apply downloads the named build and verifies its
        // SHA-256 before anything is swapped. Both run on a
        // background thread so the menu never blocks; results
        // come back to the UI thread through the synchronization
        // context captured at click time (or on the tick), and
        // the menu is rebuilt from the state fields, like every
        // other surface. A failed check or download changes
        // nothing on disk and is only a log line; the swap
        // itself is the helper mode's job (see Program).

        private static Version RunningVersion()
        {
            return System.Reflection.Assembly
                .GetExecutingAssembly().GetName().Version;
        }

        private string UpdateItemText()
        {
            if (_updateDownloading) return "Downloading update...";
            if (_updateBusy) return "Checking for updates...";
            if (_updateVersion != null)
                return "Update to v" + _updateVersion;
            if (_updateChecked) return "Up to date";
            return "Check for updates";
        }

        private void OnUpdateItemClick()
        {
            if (_updateBusy) return;
            if (_uiContext == null)
                _uiContext = SynchronizationContext.Current;
            if (_updateVersion != null) StartApplyUpdate();
            else StartUpdateCheck();
        }

        // The status window's update surface (0.4.3.0): the
        // same check and the same apply the menu item
        // drives, never a second path.
        internal bool UpdateBusyState { get { return _updateBusy; } }
        internal bool UpdateDownloadingState
        {
            get { return _updateDownloading; }
        }
        internal string AvailableUpdateVersion
        {
            get { return _updateVersion; }
        }

        internal string LastUpdateCheckLine()
        {
            if (_lastCheckResult == null)
                return "not checked yet this session";
            return _lastCheckAt.ToString("HH:mm:ss",
                CultureInfo.InvariantCulture) + " - " +
                _lastCheckResult;
        }

        internal void RequestUpdateCheck()
        {
            if (_updateBusy) return;
            if (_uiContext == null)
                _uiContext = SynchronizationContext.Current;
            StartUpdateCheck();
        }

        internal void RequestApplyUpdate()
        {
            if (_updateBusy || _updateVersion == null) return;
            if (_uiContext == null)
                _uiContext = SynchronizationContext.Current;
            StartApplyUpdate();
        }

        // The launch check's one announcement (0.4.3.0):
        // shown by whichever UI-thread path runs first
        // after the check completes (the posted completion,
        // or the tick as backstop), and at most once.
        private void MaybeShowUpdateBalloon()
        {
            if (!_launchBalloonPending || _updateBalloonShown)
                return;
            if (_updateVersion == null) return;
            _launchBalloonPending = false;
            _updateBalloonShown = true;
            try
            {
                _icon.ShowBalloonTip(10000, "Syncyard Tray",
                    "Syncyard tray update available: v" +
                    _updateVersion +
                    ". Open the tray menu to install.",
                    ToolTipIcon.Info);
            }
            catch { }
        }

        private void StartUpdateCheck()
        {
            _updateBusy = true;
            _updateDownloading = false;
            RebuildMenu();
            var t = new Thread(CheckForUpdatesWorker);
            t.IsBackground = true;
            t.Start();
        }

        private void CheckForUpdatesWorker()
        {
            string foundVersion = null;
            string foundSha = null;
            string foundUrl = null;
            bool failed = false;
            // The status window's record of this check
            // (0.4.3.0): set on every path, failures
            // included - a failed check is still a check,
            // and the window says so with the reason.
            string resultText = "check failed";
            bool launchCheck = _checkFromLaunch;
            try
            {
                string json = DownloadText(UpdateManifestUrl);
                string v = MiniJson.GetString(json, "version");
                string sha = MiniJson.GetString(json, "sha256");
                string url = MiniJson.GetString(json, "url");
                if (v == null || sha == null || url == null)
                {
                    Log("update check: manifest incomplete; " +
                        "ignored");
                    failed = true;
                    resultText = "check failed: manifest incomplete";
                }
                else
                {
                    string vtext = v.Trim();
                    if (vtext.StartsWith("v") ||
                        vtext.StartsWith("V"))
                        vtext = vtext.Substring(1);
                    Version mv;
                    if (!Version.TryParse(vtext, out mv))
                    {
                        Log("update check: manifest version " +
                            "unreadable (" + v + "); ignored");
                        failed = true;
                        resultText =
                            "check failed: manifest version unreadable";
                    }
                    else if (mv > RunningVersion())
                    {
                        foundVersion = vtext;
                        foundSha = sha;
                        foundUrl = url;
                        string notes =
                            MiniJson.GetString(json, "notes");
                        Log("update available: v" + vtext +
                            " (running " +
                            RunningVersion().ToString() + ")" +
                            (notes != null ? " - " + notes : ""));
                        resultText = "update available: v" + vtext;
                    }
                    else
                    {
                        Log("update check: up to date " +
                            "(manifest " + vtext + ", running " +
                            RunningVersion().ToString() + ")");
                        resultText = "up to date";
                    }
                }
            }
            catch (Exception ex)
            {
                Log("update check failed: " + ex.Message);
                failed = true;
                string msg = ex.Message ?? "";
                if (msg.Length > 80) msg = msg.Substring(0, 80);
                resultText = "check failed: " + msg;
            }
            var ui = _uiContext;
            if (ui != null)
            {
                ui.Post(delegate(object state)
                {
                    _updateBusy = false;
                    _checkFromLaunch = false;
                    _lastCheckAt = DateTime.Now;
                    _lastCheckResult = resultText;
                    if (!failed)
                    {
                        _updateChecked = true;
                        _updateVersion = foundVersion;
                        _updateSha256 = foundSha;
                        _updateUrl = foundUrl;
                    }
                    if (launchCheck && foundVersion != null)
                        _launchBalloonPending = true;
                    RebuildMenu();
                    MaybeShowUpdateBalloon();
                }, null);
            }
            else
            {
                _updateBusy = false;
                _checkFromLaunch = false;
                _lastCheckAt = DateTime.Now;
                _lastCheckResult = resultText;
                if (!failed)
                {
                    _updateChecked = true;
                    _updateVersion = foundVersion;
                    _updateSha256 = foundSha;
                    _updateUrl = foundUrl;
                }
                if (launchCheck && foundVersion != null)
                    _launchBalloonPending = true;
                // No UI context captured: the balloon waits
                // for the tick's backstop call.
            }
        }

        private void StartApplyUpdate()
        {
            if (_updateBusy || _updateVersion == null) return;
            _updateBusy = true;
            _updateDownloading = true;
            RebuildMenu();
            string url = _updateUrl;
            string sha = _updateSha256;
            string version = _updateVersion;
            var t = new Thread(new ThreadStart(
                delegate { ApplyUpdateWorker(url, sha, version); }));
            t.IsBackground = true;
            t.Start();
        }

        private void ApplyUpdateWorker(string url, string sha256,
            string version)
        {
            string dir = Path.GetDirectoryName(
                Application.ExecutablePath);
            string newPath = Path.Combine(dir,
                "SyncyardTray.exe.new");
            try
            {
                DownloadFile(url, newPath);
                string actual = Sha256Hex(newPath);
                if (!string.Equals(actual, sha256,
                        StringComparison.OrdinalIgnoreCase))
                {
                    try { File.Delete(newPath); } catch { }
                    Log("update aborted: hash mismatch");
                    EndUpdateBusy();
                    return;
                }
                Log("update v" + version +
                    " downloaded and hash verified");
                _pendingUpdatePath = newPath;
                var ui = _uiContext;
                if (ui != null)
                    ui.Post(delegate(object state)
                        { LaunchHelperAndExit(); }, null);
                // With no captured context the next tick is
                // the launcher instead (see TickCore).
            }
            catch (Exception ex)
            {
                Log("update download failed: " + ex.Message);
                try { if (File.Exists(newPath))
                        File.Delete(newPath); }
                catch { }
                EndUpdateBusy();
            }
        }

        private void EndUpdateBusy()
        {
            var ui = _uiContext;
            if (ui != null)
            {
                ui.Post(delegate(object state)
                {
                    _updateBusy = false;
                    _updateDownloading = false;
                    RebuildMenu();
                }, null);
            }
            else
            {
                _updateBusy = false;
                _updateDownloading = false;
            }
        }

        // Hand the verified download to the helper mode of
        // this same exe and exit so the swap can proceed.
        // Runs on the UI thread (posted completion, or the
        // tick backstop); idempotent, because both paths can
        // race to it.
        private void LaunchHelperAndExit()
        {
            string newPath = _pendingUpdatePath;
            if (newPath == null || _updateLaunched) return;
            _pendingUpdatePath = null;
            _updateLaunched = true;
            try
            {
                Log("tray exiting for update");
                var psi = new ProcessStartInfo();
                psi.FileName = Application.ExecutablePath;
                psi.Arguments =
                    "--apply-update \"" + newPath + "\"";
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.WindowStyle = ProcessWindowStyle.Hidden;
                Process.Start(psi);
            }
            catch (Exception ex)
            {
                Log("update launch failed: " + ex.Message);
                _updateLaunched = false;
                _updateBusy = false;
                _updateDownloading = false;
                RebuildMenu();
                return;
            }
            _icon.Visible = false;
            Application.Exit();
        }

        private static string DownloadText(string url)
        {
            ServicePointManager.SecurityProtocol |=
                SecurityProtocolType.Tls12;
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Timeout = 15000;
            req.ReadWriteTimeout = 15000;
            req.UserAgent = "SyncyardTray/" + BuildId;
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var stream = resp.GetResponseStream())
            using (var reader = new StreamReader(stream))
                return reader.ReadToEnd();
        }

        private static void DownloadFile(string url, string path)
        {
            ServicePointManager.SecurityProtocol |=
                SecurityProtocolType.Tls12;
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Timeout = 15000;
            req.ReadWriteTimeout = 120000;
            req.UserAgent = "SyncyardTray/" + BuildId;
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var input = resp.GetResponseStream())
            using (var output = new FileStream(path,
                FileMode.Create, FileAccess.Write))
            {
                var buf = new byte[81920];
                int n;
                while ((n = input.Read(buf, 0, buf.Length)) > 0)
                    output.Write(buf, 0, n);
            }
        }

        private static string Sha256Hex(string path)
        {
            using (var fs = File.OpenRead(path))
            using (var sha = SHA256.Create())
            {
                byte[] hash = sha.ComputeHash(fs);
                var sb = new StringBuilder();
                foreach (byte b in hash)
                    sb.Append(b.ToString("x2",
                        CultureInfo.InvariantCulture));
                return sb.ToString();
            }
        }

        // --- stray console hiding ----------------------------------------

        private void SetStrayConsoles(int cmd)
        {
            foreach (var name in new[] { "conhost", "cmd", "powershell", "pwsh" })
            {
                Process[] procs;
                try { procs = Process.GetProcessesByName(name); }
                catch { continue; }
                foreach (var p in procs)
                {
                    try
                    {
                        if (p.MainWindowHandle == IntPtr.Zero) continue;
                        string title = p.MainWindowTitle ?? "";
                        if (title.IndexOf("actions-runner",
                                StringComparison.OrdinalIgnoreCase) >= 0 ||
                            title.IndexOf("run.cmd",
                                StringComparison.OrdinalIgnoreCase) >= 0)
                        {
                            ShowWindow(p.MainWindowHandle, cmd);
                            if (cmd == SW_HIDE)
                                Log("hid stray console '" + title + "'");
                        }
                    }
                    catch { }
                }
            }
        }

        private void HideStrayConsoles() { SetStrayConsoles(SW_HIDE); }

        // --- tray UI ------------------------------------------------------

        // The menu's fleet header (spec 014 FR-007): the
        // gatekeeper's mode and queue at a glance, rendered from
        // the last snapshot read - text only, rebuilt with the
        // menu on the existing tick cadence, never an icon render.
        private string FleetMenuText()
        {
            if (_fleet == null) return "Fleet: not loaded yet";
            if (!_fleet.HasData)
                return "Fleet: unavailable (" + _fleet.Problem + ")";
            string s = "Fleet: " + _fleet.ModeLine() + " - " +
                _fleet.Waiting.Count + " waiting";
            if (_fleet.Stale)
                s += " - STALE (" + FleetStatus.FormatAge(
                    _fleet.AgeSeconds) + ")";
            return s;
        }

        // The menu's connection line (spec 018 FR-003): the state
        // in words. The settings dialog keeps the signal detail;
        // the menu header does not (owner trim, 2026-11-04: the
        // header is the build line, the fleet summary, and this
        // line; the per-job Next line and the signal detail line
        // were removed as clutter).
        private string ConnectionText()
        {
            if (_conn == null)
                return "Connection: unknown (no signal seen yet)";
            return _conn.Line();
        }

        // The queue line (0.4.3.0): one line over the lane
        // rows carrying the fleet's queue depth in the
        // four-state model - idle, waiting (work queued and
        // nothing running: the dangerous state, rendered
        // amber and bold by the menu builder), running, and
        // full (every busy lane at cap with work still
        // waiting). Counts ride with the state. A snapshot
        // whose publisher does not emit the depth fields
        // degrades honestly: waiting is counted from the
        // list it does carry and running is labelled
        // unknown, never guessed.
        internal string QueueLineText()
        {
            var f = _fleet;
            if (f == null) return "Queue: not loaded yet";
            if (!f.HasData)
                return "Queue: unavailable (" + f.Problem + ")";
            if (f.QueueState != null)
            {
                string state = f.QueueState;
                int r = f.RunningTotal >= 0
                    ? f.RunningTotal : f.Active.Count;
                int w = f.WaitingTotal >= 0
                    ? f.WaitingTotal : f.Waiting.Count;
                if (state == "idle") return "Queue: Idle";
                if (state == "waiting")
                    return "Queue: Waiting, " + w + " waiting, " +
                        r + " running";
                if (state == "full")
                {
                    int cap = 0;
                    foreach (var l in f.Lanes) cap += l.Cap;
                    string run = cap > 0
                        ? r + " of " + cap + " running"
                        : r + " running";
                    return "Queue: Full, " + run + ", " + w +
                        " waiting";
                }
                string runLine = "Queue: Running, " + r + " running";
                if (w > 0) runLine += ", " + w + " waiting";
                return runLine;
            }
            return "Queue: " + f.Waiting.Count +
                " waiting, running unknown";
        }

        internal bool QueueLineAlert()
        {
            var f = _fleet;
            return f != null && f.HasData &&
                f.QueueState == "waiting";
        }

        private void RebuildMenu()
        {
            // The strip itself is never replaced and never
            // disposed; only its items are rebuilt, in place.
            // The previous shape built a new strip per rebuild,
            // assigned it to the icon, and disposed the old one -
            // and the deferred rebuild inside the old strip's own
            // Closed handler ran while WinForms' modal menu
            // filter still held that strip as the active menu, so
            // the next activation change reparented a disposed
            // object and the tray died with an unhandled
            // ObjectDisposedException ('ContextMenuStrip',
            // TRISTEN-LAPTOP 2026-11-04). Items are the only
            // things a rebuild may dispose, and only once they
            // are detached from the strip (end of this method).
            //
            // Never rebuild while the owner has the menu open:
            // the 5 s supervision tick calls this every tick, and
            // mutating the items under an open menu would dismiss
            // it mid-use (owner report 2026-11-04). Defer; the
            // Closed handler rebuilds once on close.
            if (_menuOpen) { _menuDirty = true; return; }
            var menu = _menu;
            var stale = new List<ToolStripItem>();
            foreach (ToolStripItem item in menu.Items)
                stale.Add(item);
            menu.Items.Clear();
            var buildLine = new ToolStripMenuItem(
                "Syncyard Tray v" + BuildId);
            buildLine.Enabled = false;
            menu.Items.Add(buildLine);
            var fleetLine = new ToolStripMenuItem(FleetMenuText());
            fleetLine.Enabled = false;
            menu.Items.Add(fleetLine);
            var connLine = new ToolStripMenuItem(ConnectionText());
            connLine.Enabled = false;
            menu.Items.Add(connLine);
            // The queue line (0.4.3.0): the fleet's queue
            // depth at a glance. Clickable - it opens the
            // status window - and in the waiting state it
            // is the one loud item in the menu (amber and
            // bold), because queued work with nothing
            // running is how a stall hides.
            var queueLine = new ToolStripMenuItem(QueueLineText());
            if (QueueLineAlert())
            {
                queueLine.ForeColor = QueueAlertColor;
                queueLine.Font = QueueAlertFont;
            }
            queueLine.Click += delegate { OpenStatus(); };
            menu.Items.Add(queueLine);
            menu.Items.Add(new ToolStripSeparator());
            int running = 0;
            foreach (var r in _runners) if (r.Running) running++;
            var startAll = new ToolStripMenuItem("Start all runners");
            startAll.Enabled = running < _runners.Count;
            startAll.Click += delegate { StartAll(); };
            menu.Items.Add(startAll);
            var stopAll = new ToolStripMenuItem("Stop all runners");
            stopAll.Enabled = running > 0;
            stopAll.Click += delegate { StopAll(); };
            menu.Items.Add(stopAll);
            menu.Items.Add(new ToolStripSeparator());
            running = 0;
            foreach (var r in _runners)
            {
                if (r.Running) running++;
                string state = r.Faulted ? "FAULTED - owner needed"
                             : r.Running ? "running" : "stopped";
                var item = new ToolStripMenuItem(
                    "lane-" + r.Lane + ": " + state);
                var start = new ToolStripMenuItem("Start hidden");
                var captured = r;
                start.Click += delegate
                {
                    captured.Faulted = false;
                    captured.RestartTimes.Clear();
                    StartRunner(captured);
                    RefreshStates(); RebuildMenu();
                };
                var stop = new ToolStripMenuItem("Stop runner");
                stop.Click += delegate
                {
                    var ok = MessageBox.Show(
                        "Stop the lane-" + captured.Lane + " runner? " +
                        "A job running on it will be killed.",
                        "Syncyard Tray", MessageBoxButtons.OKCancel,
                        MessageBoxIcon.Warning);
                    if (ok == DialogResult.OK)
                    {
                        StopRunner(captured);
                        RefreshStates(); RebuildMenu();
                    }
                };
                item.DropDownItems.Add(start);
                item.DropDownItems.Add(stop);
                menu.Items.Add(item);
            }
            menu.Items.Add(new ToolStripSeparator());
            var hide = new ToolStripMenuItem("Hide runner consoles now");
            hide.Click += delegate { HideStrayConsoles(); };
            menu.Items.Add(hide);
            // The old Show-runner-consoles item is gone:
            // it un-hid windows by title, and the fleet's
            // runners run with no console window, so it
            // could never show a healthy runner (red team,
            // 2026-11-05). The consoles viewer replaces it:
            // the runners' own log files, tailed read-only.
            var show = new ToolStripMenuItem("Show consoles");
            show.Click += delegate { OpenConsoles(); };
            menu.Items.Add(show);
            menu.Items.Add(new ToolStripSeparator());
            var statusItem = new ToolStripMenuItem("Status");
            statusItem.Click += delegate { OpenStatus(); };
            menu.Items.Add(statusItem);
            var settingsItem = new ToolStripMenuItem("Settings...");
            settingsItem.Click += delegate { OpenSettings(); };
            menu.Items.Add(settingsItem);
            var log = new ToolStripMenuItem("Open tray log");
            log.Click += delegate
            {
                try { Process.Start("notepad.exe", _logPath); }
                catch { }
            };
            menu.Items.Add(log);
            var update = new ToolStripMenuItem(UpdateItemText());
            update.Enabled = !_updateBusy;
            update.Click += delegate { OnUpdateItemClick(); };
            menu.Items.Add(update);
            var exit = new ToolStripMenuItem(
                "Exit tray (runners keep running)");
            exit.Click += delegate
            {
                Log("tray exited by owner; runners left as-is");
                _icon.Visible = false;
                Application.Exit();
            };
            menu.Items.Add(exit);
            // The previous items, detached by the Clear() above
            // and held by no strip: safe to dispose now.
            foreach (var item in stale) item.Dispose();

            int iconState = AnyFaulted() || running == 0 ? 2
                          : running == _runners.Count ? 0 : 1;
            SetIcon(iconState, _conn.State);
            _icon.Text = TooltipText(running);
        }

        // The tooltip is composed SHORT (owner ruling 2026-11-04:
        // the previous build concatenated the full connection
        // and fleet lines and chopped the result at 63 characters
        // mid-word, so the hover text read as cut off). Segments
        // join only while the whole stays inside the 63-character
        // NotifyIcon limit, in priority order: the runner count,
        // the connection as one word, the waiting count, the
        // stale flag. A segment that does not fit is dropped
        // whole; nothing is ever truncated mid-word. The detail
        // lives in the menu's two header lines, not here.
        //
        // The cap is 63, not the shell's larger buffer:
        // .NET Framework's NotifyIcon.Text setter throws
        // ArgumentOutOfRangeException above 63 characters, and
        // RebuildMenu runs in the constructor, before the message
        // loop's exception guard applies (the 120 cap shipped in
        // specs 014/018 crashed the tray at startup on
        // 2026-11-03; the substring clamp that replaced it is
        // what this composition supersedes).
        private string TooltipText(int running)
        {
            string s = "Syncyard: " + running + "/" + _runners.Count +
                " runners up";
            var parts = new List<string>();
            if (_conn != null && _conn.HasSignal)
            {
                parts.Add(_conn.State == ConnectionStatus.Connected
                    ? "linked"
                    : _conn.State == ConnectionStatus.Stale
                        ? "link stale" : "unlinked");
            }
            if (_fleet != null && _fleet.HasData &&
                _fleet.Waiting.Count > 0)
                parts.Add(_fleet.Waiting.Count + " waiting");
            if (_fleet != null && _fleet.HasData && _fleet.Stale)
                parts.Add("fleet stale");
            foreach (var p in parts)
            {
                if ((s + " - " + p).Length > 63) break;
                s += " - " + p;
            }
            return s;
        }

        // Icon states (FR-007): 0 = all runners up, clean color
        // mark; 1 = some down, color mark with amber slash; 2 = none
        // up or faulted, GRAYSCALE mark with red slash. The slash is drawn with a white halo so it reads on
        // light and dark taskbars. The dot exists only if the mark
        // itself failed to load, which the embedded resource makes a
        // should-never-happen path.
        //
        // Spec 018 adds the second dimension WITHOUT touching the
        // runner semantics above: the slash stays runner-only, and
        // the connection state is drawn as a link mark in the
        // icon's bottom-right corner (DrawLinkMark) - solid when
        // connected, hollow amber when stale, broken when
        // disconnected, and a disconnected host's mark is
        // desaturated even when its runners are all up, because
        // from the owner's chair that host has fallen off the
        // fleet and must read as off at a glance.
        private void SetIcon(int state, int conn)
        {
            // Render only on a state change. The 2026-11-01 field
            // build re-rendered on every 5 s tick and leaked the
            // bitmap and the icon handle each time (neither was
            // released), exhausting the process GDI quota in about
            // 10.5 h; the tray then died with a System.Drawing
            // OutOfMemoryException raised while it was building its
            // own error dialog. Renders are now rare AND tight.
            if (state == _iconState && conn == _iconConnState && _currentIcon != null) return;
            _iconState = state;
            _iconConnState = conn;
            var bmp = new Bitmap(32, 32);
            using (var g = Graphics.FromImage(bmp))
            {
                g.SmoothingMode =
                    System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
                g.InterpolationMode =
                    System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                g.Clear(Color.Transparent);
                if (_logoImage != null)
                {
                    if (state == 2 || conn == ConnectionStatus.Disconnected)
                    {
                        // Fully offline reads as a dead icon: the mark
                        // in grayscale under the red slash, visibly
                        // different from the live color states. Spec
                        // 018: a disconnected host desaturates the
                        // same way, whatever its runners are doing.
                        var gray = new System.Drawing.Imaging.ColorMatrix(
                            new float[][] {
                                new float[] {0.33f, 0.33f, 0.33f, 0, 0},
                                new float[] {0.33f, 0.33f, 0.33f, 0, 0},
                                new float[] {0.33f, 0.33f, 0.33f, 0, 0},
                                new float[] {0, 0, 0, 1, 0},
                                new float[] {0, 0, 0, 0, 1} });
                        using (var attrs =
                            new System.Drawing.Imaging.ImageAttributes())
                        {
                            attrs.SetColorMatrix(gray);
                            g.DrawImage(_logoImage,
                                new Rectangle(0, 0, 32, 32),
                                0, 0, _logoImage.Width, _logoImage.Height,
                                GraphicsUnit.Pixel, attrs);
                        }
                    }
                    else
                    {
                        g.DrawImage(_logoImage, 0, 0, 32, 32);
                    }
                    if (state > 0)
                    {
                        Color slash = state == 2
                            ? Color.FromArgb(225, 45, 45)
                            : Color.FromArgb(235, 165, 25);
                        using (var halo = new Pen(Color.White, 9))
                            g.DrawLine(halo, 6, 26, 26, 6);
                        using (var pen = new Pen(slash, 5))
                            g.DrawLine(pen, 6, 26, 26, 6);
                    }
                    DrawLinkMark(g, conn);
                }
                else
                {
                    Color dot = state == 2 ? Color.Red
                              : state == 1 ? Color.Goldenrod
                              : Color.Green;
                    using (var b = new SolidBrush(dot))
                        g.FillEllipse(b, 1, 1, 30, 30);
                }
            }
            IntPtr hIcon = bmp.GetHicon();
            Icon fresh;
            try
            {
                // Icon.FromHandle does NOT own the handle: disposing
                // a FromHandle icon releases nothing, so the old code
                // leaked one HICON per render. Clone instead (the
                // clone owns its own handle) and destroy the borrowed
                // original immediately.
                using (var borrowed = Icon.FromHandle(hIcon))
                    fresh = (Icon)borrowed.Clone();
            }
            finally
            {
                DestroyIcon(hIcon);
            }
            var old = _currentIcon;
            _currentIcon = fresh;
            _icon.Icon = _currentIcon;
            if (old != null) old.Dispose();
            bmp.Dispose();
        }

        // The connection link mark (spec 018 FR-002), drawn in
        // the icon's bottom-right corner - the corner the runner
        // slash's diagonal (bottom-left to top-right) leaves
        // clear. Two interlocking rings: thick-walled (solid)
        // when connected, thin-walled (hollow) amber when stale;
        // when disconnected, a single ring with a gap, struck
        // through in the fault red. The wall thickness is the
        // solid/hollow distinction. A white halo sits under the
        // mark so it reads on light and dark taskbars, like the
        // slash. Primitives on the icon's existing bitmap only:
        // no new bitmap, no new handle (the PR #39 discipline).
        private static void DrawLinkMark(Graphics g, int conn)
        {
            Color link = conn == ConnectionStatus.Connected
                ? Color.FromArgb(34, 139, 34)
                : conn == ConnectionStatus.Stale
                ? Color.FromArgb(235, 165, 25)
                : Color.FromArgb(225, 45, 45);
            float wall = conn == ConnectionStatus.Connected ? 3f : 1.6f;
            if (conn == ConnectionStatus.Disconnected)
            {
                using (var halo = new Pen(Color.White, 3.6f))
                    g.DrawArc(halo, 19, 19, 11, 11, 35, 290);
                using (var pen = new Pen(link, 1.6f))
                    g.DrawArc(pen, 19, 19, 11, 11, 35, 290);
                using (var halo = new Pen(Color.White, 4.4f))
                    g.DrawLine(halo, 19, 30, 30, 19);
                using (var strike = new Pen(link, 2.4f))
                    g.DrawLine(strike, 19, 30, 30, 19);
            }
            else
            {
                using (var halo = new Pen(Color.White, wall + 2f))
                {
                    g.DrawEllipse(halo, 16, 20, 9, 9);
                    g.DrawEllipse(halo, 22, 14, 9, 9);
                }
                using (var pen = new Pen(link, wall))
                {
                    g.DrawEllipse(pen, 16, 20, 9, 9);
                    g.DrawEllipse(pen, 22, 14, 9, 9);
                }
            }
        }

        private void Log(string msg)
        {
            try
            {
                File.AppendAllText(_logPath,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") +
                    "  " + msg + Environment.NewLine);
            }
            catch { }
        }
    }

    // Status window (0.4.3.0): everything the menu compresses
    // into three lines, laid out in full - the fleet's queue
    // depth by lane, the running and waiting jobs behind it,
    // this machine's own state, and the update state with its
    // two buttons wired to the same check and install paths
    // the menu item drives. Display-only apart from those two
    // buttons and Refresh; it refreshes itself about every
    // five seconds while open (and each tray tick refreshes it
    // too), reading through the tray so both surfaces always
    // render the same snapshot.
    internal sealed class StatusForm : Form
    {
        private readonly TrayApp _app;
        private readonly Label _queueLabel;
        private readonly Label _fleetLabel;
        private readonly ListView _lanes;
        private readonly ListView _runningJobs;
        private readonly ListView _waitingJobs;
        private readonly Label _machineLabel;
        private readonly Label _updateLabel;
        private readonly Button _checkNow;
        private readonly Button _updateNow;
        private readonly System.Windows.Forms.Timer _timer;
        private readonly Font _boldFont;

        public StatusForm(TrayApp app)
        {
            _app = app;
            _boldFont = new Font(Control.DefaultFont,
                FontStyle.Bold);
            Text = "Syncyard status";
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(800, 704);
            MinimumSize = new Size(560, 480);

            var grpFleet = new GroupBox();
            grpFleet.Text = "Fleet";
            grpFleet.Location = new Point(12, 10);
            grpFleet.Size = new Size(776, 86);
            grpFleet.Anchor = AnchorStyles.Top |
                AnchorStyles.Left | AnchorStyles.Right;
            _queueLabel = new Label();
            _queueLabel.Location = new Point(12, 18);
            _queueLabel.Size = new Size(752, 20);
            _queueLabel.Anchor = AnchorStyles.Top |
                AnchorStyles.Left | AnchorStyles.Right;
            grpFleet.Controls.Add(_queueLabel);
            _fleetLabel = new Label();
            _fleetLabel.Location = new Point(12, 42);
            _fleetLabel.Size = new Size(752, 36);
            _fleetLabel.Anchor = AnchorStyles.Top |
                AnchorStyles.Left | AnchorStyles.Right;
            grpFleet.Controls.Add(_fleetLabel);
            Controls.Add(grpFleet);

            var grpQueues = new GroupBox();
            grpQueues.Text = "Queues by lane";
            grpQueues.Location = new Point(12, 104);
            grpQueues.Size = new Size(776, 124);
            grpQueues.Anchor = AnchorStyles.Top |
                AnchorStyles.Left | AnchorStyles.Right;
            _lanes = NewGrid(grpQueues, 12, 20, 752, 92);
            _lanes.Columns.Add("Lane", 150);
            _lanes.Columns.Add("Running", 110);
            _lanes.Columns.Add("Waiting", 110);
            _lanes.Columns.Add("State", 140);
            Controls.Add(grpQueues);

            var grpJobs = new GroupBox();
            grpJobs.Text = "Jobs";
            grpJobs.Location = new Point(12, 236);
            grpJobs.Size = new Size(776, 204);
            grpJobs.Anchor = AnchorStyles.Top |
                AnchorStyles.Left | AnchorStyles.Right;
            var lblRun = new Label();
            lblRun.Text = "Running";
            lblRun.Location = new Point(12, 18);
            lblRun.AutoSize = true;
            grpJobs.Controls.Add(lblRun);
            _runningJobs = NewGrid(grpJobs, 12, 38, 752, 68);
            _runningJobs.Columns.Add("Job", 300);
            _runningJobs.Columns.Add("Program", 170);
            _runningJobs.Columns.Add("Lane", 100);
            _runningJobs.Columns.Add("Run", 130);
            var lblWait = new Label();
            lblWait.Text = "Waiting";
            lblWait.Location = new Point(12, 114);
            lblWait.AutoSize = true;
            grpJobs.Controls.Add(lblWait);
            _waitingJobs = NewGrid(grpJobs, 12, 134, 752, 58);
            _waitingJobs.Columns.Add("Job", 300);
            _waitingJobs.Columns.Add("Program", 170);
            _waitingJobs.Columns.Add("Lane", 100);
            _waitingJobs.Columns.Add("Held", 100);
            Controls.Add(grpJobs);

            var grpMachine = new GroupBox();
            grpMachine.Text = "This machine";
            grpMachine.Location = new Point(12, 448);
            grpMachine.Size = new Size(776, 118);
            grpMachine.Anchor = AnchorStyles.Top |
                AnchorStyles.Left | AnchorStyles.Right;
            _machineLabel = new Label();
            _machineLabel.Location = new Point(12, 18);
            _machineLabel.Size = new Size(752, 92);
            _machineLabel.Anchor = AnchorStyles.Top |
                AnchorStyles.Left | AnchorStyles.Right;
            grpMachine.Controls.Add(_machineLabel);
            Controls.Add(grpMachine);

            var grpUpdates = new GroupBox();
            grpUpdates.Text = "Updates";
            grpUpdates.Location = new Point(12, 574);
            grpUpdates.Size = new Size(776, 78);
            grpUpdates.Anchor = AnchorStyles.Top |
                AnchorStyles.Left | AnchorStyles.Right;
            _updateLabel = new Label();
            _updateLabel.Location = new Point(12, 16);
            _updateLabel.Size = new Size(536, 52);
            grpUpdates.Controls.Add(_updateLabel);
            _checkNow = new Button();
            _checkNow.Text = "Check now";
            _checkNow.Location = new Point(556, 20);
            _checkNow.Size = new Size(92, 26);
            _checkNow.Click += delegate
            {
                _app.RequestUpdateCheck();
                RefreshDynamic();
            };
            grpUpdates.Controls.Add(_checkNow);
            _updateNow = new Button();
            _updateNow.Text = "Update now";
            _updateNow.Location = new Point(656, 20);
            _updateNow.Size = new Size(92, 26);
            _updateNow.Click += delegate
            {
                _app.RequestApplyUpdate();
                RefreshDynamic();
            };
            grpUpdates.Controls.Add(_updateNow);
            Controls.Add(grpUpdates);

            var hint = new Label();
            hint.Text = "Refreshes itself every 5 seconds " +
                "while open.";
            hint.Location = new Point(12, 668);
            hint.AutoSize = true;
            Controls.Add(hint);
            var refresh = new Button();
            refresh.Text = "Refresh";
            refresh.Location = new Point(688, 662);
            refresh.Size = new Size(100, 28);
            refresh.Anchor = AnchorStyles.Top |
                AnchorStyles.Right;
            refresh.Click += delegate
            {
                try
                {
                    _app.ReloadSignals();
                    RefreshDynamic();
                }
                catch { }
            };
            Controls.Add(refresh);

            _timer = new System.Windows.Forms.Timer();
            _timer.Interval = 5000;
            _timer.Tick += delegate
            {
                try
                {
                    _app.ReloadSignals();
                    RefreshDynamic();
                }
                catch { }
            };
            _timer.Start();
            RefreshDynamic();
        }

        private static ListView NewGrid(Control parent, int x,
            int y, int w, int h)
        {
            var lv = new ListView();
            lv.View = View.Details;
            lv.FullRowSelect = true;
            lv.GridLines = true;
            lv.HeaderStyle = ColumnHeaderStyle.Nonclickable;
            lv.Location = new Point(x, y);
            lv.Size = new Size(w, h);
            lv.Anchor = AnchorStyles.Top | AnchorStyles.Left |
                AnchorStyles.Right;
            parent.Controls.Add(lv);
            return lv;
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                if (_timer != null) _timer.Dispose();
                if (_boldFont != null) _boldFont.Dispose();
            }
            base.Dispose(disposing);
        }

        private static int LaneRank(string lane)
        {
            switch (lane)
            {
                case "quick": return 0;
                case "normal": return 1;
                case "long": return 2;
                case "gpu": return 3;
                case "laptop-quick": return 4;
                case "laptop-long": return 5;
                default: return 100;
            }
        }

        private static string StateWord(string state)
        {
            if (string.IsNullOrEmpty(state)) return state;
            return char.ToUpperInvariant(state[0]) +
                state.Substring(1);
        }

        // Re-reads the tray's current state into the
        // controls. Silent by contract: the caller (timer,
        // tick) must never see an exception from a refresh.
        public void RefreshDynamic()
        {
            var fleet = _app.Fleet;
            var conn = _app.Connection;
            var settings = _app.Settings;

            bool alert = _app.QueueLineAlert();
            _queueLabel.Text = _app.QueueLineText();
            _queueLabel.Font = alert ? _boldFont : Font;
            _queueLabel.ForeColor = alert
                ? Color.FromArgb(192, 120, 0)
                : SystemColors.ControlText;

            if (fleet == null)
                _fleetLabel.Text = "Fleet status: not loaded yet";
            else if (!fleet.HasData)
                _fleetLabel.Text = "Fleet status: unavailable (" +
                    fleet.Problem + ")";
            else
            {
                string mode = fleet.Draining &&
                    fleet.Mode != "draining"
                    ? fleet.Mode + " (draining)" : fleet.Mode;
                string line = "Mode: " + mode;
                if (fleet.Reason != null)
                    line += " - " + fleet.Reason;
                if (fleet.FocusPrograms.Count > 0)
                    line += " - focus: " + string.Join(", ",
                        fleet.FocusPrograms.ToArray());
                line += "\r\nSnapshot: " +
                    (fleet.HasGeneratedAt
                        ? "generated " +
                            FleetStatus.FormatAge(
                                fleet.AgeSeconds) + " ago" +
                            (fleet.Stale ? " - STALE" : "")
                        : "no timestamp");
                _fleetLabel.Text = line;
            }

            FillLanes(fleet);
            FillJobs(fleet);

            int up = 0;
            foreach (var r in _app.Runners) if (r.Running) up++;
            var lines = new List<string>();
            lines.Add("Tray build " + _app.BuildStamp +
                " - runners " + up + " of " + _app.Runners.Count +
                " running");
            lines.Add("Connection: " + (conn == null
                ? "unknown (no signal seen yet)"
                : conn.Line() +
                    (conn.HasSignal &&
                     conn.SignalDetail().Length > 0
                        ? " (" + conn.SignalDetail() + ")"
                        : "")));
            double hbAge;
            lines.Add("Backchannel heartbeat: " +
                (ConnectionStatus.HeartbeatAgeSeconds(
                    settings.ConnectionHeartbeatPath, out hbAge)
                    ? FleetStatus.FormatAge(hbAge) + " ago"
                    : "not seen"));
            lines.Add("Fleet snapshot: " +
                (fleet != null && fleet.HasData
                    ? fleet.HasGeneratedAt
                        ? FleetStatus.FormatAge(fleet.AgeSeconds) +
                            " old" + (fleet.Stale
                                ? " (stale)" : "")
                        : "no timestamp"
                    : "unavailable"));
            var enabled = new List<string>();
            foreach (var lane in TraySettings.Lanes)
                if (settings.LaneEnabled(lane)) enabled.Add(lane);
            lines.Add("Lanes enabled: " +
                (enabled.Count > 0
                    ? string.Join(", ", enabled.ToArray())
                    : "(none)"));
            lines.Add("Runner root: " + settings.RunnerRoot);
            _machineLabel.Text = string.Join("\r\n",
                lines.ToArray());

            string upd = "Current version: " + _app.BuildStamp +
                "\r\nLast check: " + _app.LastUpdateCheckLine();
            if (_app.AvailableUpdateVersion != null)
                upd += "\r\nUpdate available: v" +
                    _app.AvailableUpdateVersion;
            if (_app.UpdateDownloadingState)
                upd += "\r\nDownloading the update...";
            else if (_app.UpdateBusyState)
                upd += "\r\nUpdate check in progress...";
            _updateLabel.Text = upd;
            _checkNow.Enabled = !_app.UpdateBusyState;
            _updateNow.Enabled =
                _app.AvailableUpdateVersion != null &&
                !_app.UpdateBusyState;
        }

        private void FillLanes(FleetStatus fleet)
        {
            _lanes.BeginUpdate();
            try
            {
                _lanes.Items.Clear();
                if (fleet == null || !fleet.HasData ||
                    fleet.Lanes.Count == 0)
                {
                    _lanes.Items.Add(new ListViewItem(
                        "lane detail not published by " +
                        "this snapshot"));
                    return;
                }
                var lanes = new List<FleetLane>(fleet.Lanes);
                lanes.Sort(delegate(FleetLane a, FleetLane b)
                {
                    int ra = LaneRank(a.Lane);
                    int rb = LaneRank(b.Lane);
                    if (ra != rb) return ra - rb;
                    return string.Compare(a.Lane, b.Lane,
                        StringComparison.Ordinal);
                });
                foreach (var l in lanes)
                {
                    var item = new ListViewItem(l.Lane);
                    item.SubItems.Add(l.Running + " / " + l.Cap);
                    item.SubItems.Add(l.Waiting.ToString(
                        CultureInfo.InvariantCulture));
                    item.SubItems.Add(StateWord(l.State));
                    _lanes.Items.Add(item);
                }
            }
            finally { _lanes.EndUpdate(); }
        }

        private void FillJobs(FleetStatus fleet)
        {
            _runningJobs.BeginUpdate();
            try
            {
                _runningJobs.Items.Clear();
                if (fleet == null || !fleet.HasData)
                    _runningJobs.Items.Add(new ListViewItem(
                        "(no fleet data)"));
                else if (fleet.Active.Count == 0)
                    _runningJobs.Items.Add(new ListViewItem(
                        "(none running)"));
                else
                    foreach (var j in fleet.Active)
                    {
                        var item = new ListViewItem(j.Key);
                        item.SubItems.Add(j.Program);
                        item.SubItems.Add(j.Lane);
                        item.SubItems.Add(j.RunId ?? "");
                        _runningJobs.Items.Add(item);
                    }
            }
            finally { _runningJobs.EndUpdate(); }
            _waitingJobs.BeginUpdate();
            try
            {
                _waitingJobs.Items.Clear();
                if (fleet == null || !fleet.HasData)
                    _waitingJobs.Items.Add(new ListViewItem(
                        "(no fleet data)"));
                else if (fleet.Waiting.Count == 0)
                    _waitingJobs.Items.Add(new ListViewItem(
                        "(none waiting)"));
                else
                    foreach (var j in fleet.Waiting)
                    {
                        var item = new ListViewItem(j.Key);
                        item.SubItems.Add(j.Program);
                        item.SubItems.Add(j.Lane);
                        item.SubItems.Add(!j.HasHeld ? "unknown"
                            : j.Held ? "HELD" : "");
                        _waitingJobs.Items.Add(item);
                    }
            }
            finally { _waitingJobs.EndUpdate(); }
        }
    }

    // Consoles viewer (0.4.3.0): the replacement for the old
    // Show-runner-consoles item, which un-hid windows by
    // title and so could never show this fleet's runners -
    // they run with no window at all (red team, 2026-11-05).
    // What an operator actually wants from a runner's
    // terminal is its output, and the runner already writes
    // that continuously to its _diag logs. This viewer tails
    // those files (plus the tray's own log, plus the
    // backchannel agent's log where its path is known), one
    // tab per stream, in a monospaced read-only box. It is
    // READ-ONLY in the strong sense: it opens files for
    // reading only, appends nothing, executes nothing,
    // attaches to nothing, and offers no input path of any
    // kind - no stdin, no keystroke forwarding, no control
    // path back to a runner. Selection and copy work (that
    // is the point of a log view); typing does not exist.
    internal sealed class ConsolesForm : Form
    {
        private sealed class Tail
        {
            public string Path;        // file being tailed
            public string RunnerDir;   // runner tabs only:
                                       // the newest _diag log
                                       // is re-resolved each
                                       // pass, so a runner
                                       // restart moves the
                                       // tab to the new log
            public TextBox Box;
            public long Pos;           // next byte to read
            public bool Primed;
        }

        private readonly TrayApp _app;
        private readonly TabControl _tabs;
        private readonly CheckBox _follow;
        private readonly List<Tail> _tails = new List<Tail>();
        private readonly System.Windows.Forms.Timer _timer;

        // One shared terminal font for every tab, held for
        // the process's life like the tray's alert font.
        private static readonly Font MonoFont =
            new Font(FontFamily.GenericMonospace, 9f);

        public ConsolesForm(TrayApp app)
        {
            _app = app;
            Text = "Syncyard consoles (read-only)";
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(900, 600);
            MinimumSize = new Size(480, 320);

            var top = new Panel();
            top.Dock = DockStyle.Top;
            top.Height = 36;
            _follow = new CheckBox();
            _follow.Text = "Follow output";
            _follow.Checked = true;
            _follow.AutoSize = true;
            _follow.Location = new Point(10, 9);
            top.Controls.Add(_follow);
            var refresh = new Button();
            refresh.Text = "Refresh now";
            refresh.Location = new Point(122, 5);
            refresh.Size = new Size(96, 26);
            refresh.Click += delegate { BuildTabs(); };
            top.Controls.Add(refresh);
            var hint = new Label();
            hint.Text = "Read-only: these are the runners' " +
                "log files. Nothing here can reach a runner.";
            hint.AutoSize = true;
            hint.Location = new Point(230, 10);
            top.Controls.Add(hint);
            Controls.Add(top);

            _tabs = new TabControl();
            _tabs.Dock = DockStyle.Fill;
            Controls.Add(_tabs);
            _tabs.BringToFront();

            BuildTabs();
            _timer = new System.Windows.Forms.Timer();
            _timer.Interval = 2000;
            _timer.Tick += delegate
            {
                try { TailAll(); }
                catch { }
            };
            _timer.Start();
        }

        protected override void OnFormClosed(
            FormClosedEventArgs e)
        {
            if (_timer != null)
            {
                _timer.Stop();
                _timer.Dispose();
            }
            base.OnFormClosed(e);
        }

        private void BuildTabs()
        {
            _tails.Clear();
            var pages = new List<TabPage>();
            foreach (TabPage p in _tabs.TabPages) pages.Add(p);
            _tabs.TabPages.Clear();
            foreach (var p in pages) p.Dispose();

            foreach (var r in _app.Runners)
            {
                var tail = new Tail();
                tail.RunnerDir = r.Dir;
                AddTab("lane-" + r.Lane, tail);
            }
            var trayTail = new Tail();
            trayTail.Path = _app.TrayLogPath;
            AddTab("tray", trayTail);
            string agent = _app.AgentLogPath();
            if (agent != null)
            {
                var agentTail = new Tail();
                agentTail.Path = agent;
                AddTab("backchannel agent", agentTail);
            }
            TailAll();
        }

        private void AddTab(string title, Tail tail)
        {
            var page = new TabPage(title);
            var box = new TextBox();
            box.Multiline = true;
            box.ReadOnly = true;
            box.ScrollBars = ScrollBars.Both;
            box.WordWrap = false;
            box.HideSelection = false;
            box.Dock = DockStyle.Fill;
            box.Font = MonoFont;
            page.Controls.Add(box);
            tail.Box = box;
            _tabs.TabPages.Add(page);
            _tails.Add(tail);
        }

        private void TailAll()
        {
            foreach (var t in _tails) TailOne(t);
        }

        // One append-only read pass over one stream: prime
        // with the last 64 KB on first sight (or when the
        // file changes), then append only the bytes written
        // since the last pass. A file that shrinks (log
        // rotation) re-primes. A pass never writes.
        private void TailOne(Tail t)
        {
            string path = t.RunnerDir != null
                ? TrayApp.NewestDiagLog(t.RunnerDir)
                : t.Path;
            if (path == null || !File.Exists(path))
            {
                if (t.Box.TextLength == 0)
                    t.Box.Text =
                        "(no log file for this stream yet)";
                t.Path = path;
                t.Pos = 0;
                t.Primed = false;
                return;
            }
            // A live selection is the operator copying text
            // out: leave the box (and the position) alone
            // this pass and catch up when it clears.
            if (t.Primed && path == t.Path &&
                t.Box.SelectionLength > 0) return;
            bool prime = !t.Primed || path != t.Path;
            using (var fs = new FileStream(path, FileMode.Open,
                FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete))
            {
                long len = fs.Length;
                if (len < t.Pos) prime = true;
                if (prime)
                {
                    long start = len > 65536 ? len - 65536 : 0;
                    fs.Seek(start, SeekOrigin.Begin);
                    string text = ReadRest(fs);
                    if (start > 0)
                        text = "[... earlier output " +
                            "omitted ...]\r\n" + text;
                    t.Box.Text = text;
                    t.Pos = len;
                    t.Path = path;
                    t.Primed = true;
                }
                else if (len > t.Pos)
                {
                    long want = len - t.Pos;
                    string marker = "";
                    if (want > 262144)
                    {
                        // Fell far behind (the window was
                        // closed to a selection, or the
                        // machine slept): jump to the newest
                        // quarter megabyte and say so.
                        fs.Seek(len - 262144,
                            SeekOrigin.Begin);
                        marker = "[... output skipped " +
                            "...]\r\n";
                    }
                    else fs.Seek(t.Pos, SeekOrigin.Begin);
                    string text = ReadRest(fs);
                    t.Pos = len;
                    Append(t, marker + text);
                }
            }
            if (_follow.Checked && _tabs.SelectedTab != null &&
                t.Box.Parent == _tabs.SelectedTab &&
                t.Box.TextLength > 0)
            {
                t.Box.SelectionStart = t.Box.TextLength;
                t.Box.ScrollToCaret();
            }
        }

        private static string ReadRest(FileStream fs)
        {
            using (var sr = new StreamReader(fs,
                System.Text.Encoding.UTF8, true, 4096, true))
            {
                return sr.ReadToEnd();
            }
        }

        // Appends and keeps the box bounded: past roughly
        // half a million characters the oldest lines are
        // dropped at a line boundary (the file on disk
        // keeps everything; the view is a window, not the
        // record).
        private static void Append(Tail t, string text)
        {
            if (text.Length == 0) return;
            t.Box.AppendText(text);
            if (t.Box.TextLength > 524288)
            {
                string all = t.Box.Text;
                int cut = all.IndexOf('\n',
                    all.Length - 393216);
                if (cut > 0)
                    t.Box.Text = "[... earlier output " +
                        "trimmed ...]\r\n" +
                        all.Substring(cut + 1);
            }
        }
    }

    // The settings dialog (spec 014 Part B): the tray's settings
    // window, opened only from the tray menu, one instance
    // at a time. Checkboxes and fields are saved settings (applied
    // by Save through TrayApp.ApplySettings; Cancel discards them);
    // the lane buttons are immediate acts through the tray's
    // existing start/stop/restart paths and are NOT undone by
    // Cancel - the dialog says so on its face. Dynamic text (lane
    // states, the fleet section) refreshes by re-setting text on
    // these existing controls from the tray tick; the queue list
    // rebuilds only when the snapshot's generated_at changes.
    // The dialog allocates no icon or bitmap resources.
    internal sealed class SettingsForm : Form
    {
        private readonly TrayApp _app;
        private readonly CheckBox _chkSupervision;
        private readonly CheckBox _chkAutostart;
        private readonly CheckBox _chkHide;
        private readonly NumericUpDown _numCap;
        private readonly NumericUpDown _numTick;
        private readonly CheckBox[] _laneChk;
        private readonly Label[] _laneState;
        private readonly Label _connState;
        private readonly Label _connDetail;
        private readonly TextBox _txtHeartbeatPath;
        private readonly NumericUpDown _numConnStale;
        private readonly NumericUpDown _numConnDown;
        private readonly Label _fleetMode;
        private readonly Label _fleetDetail;
        private readonly Label _fleetSignal;
        private readonly TextBox _txtFleetPath;
        private readonly NumericUpDown _numStale;
        private readonly ListView _queue;
        private string _queueStamp;

        public SettingsForm(TrayApp app)
        {
            _app = app;
            Text = "Syncyard Tray Settings";
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterScreen;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(600, 826);

            var grpTray = new GroupBox();
            grpTray.Text = "Tray";
            grpTray.Location = new Point(12, 12);
            grpTray.Size = new Size(576, 150);
            Controls.Add(grpTray);

            _chkSupervision = new CheckBox();
            _chkSupervision.Text =
                "Supervise runners (auto-start and restart)";
            _chkSupervision.Location = new Point(16, 24);
            _chkSupervision.Size = new Size(280, 20);
            grpTray.Controls.Add(_chkSupervision);

            _chkHide = new CheckBox();
            _chkHide.Text = "Hide stray runner consoles";
            _chkHide.Location = new Point(308, 24);
            _chkHide.Size = new Size(250, 20);
            grpTray.Controls.Add(_chkHide);

            _chkAutostart = new CheckBox();
            _chkAutostart.Text = "Start Syncyard Tray at logon";
            _chkAutostart.Location = new Point(16, 50);
            _chkAutostart.Size = new Size(280, 20);
            grpTray.Controls.Add(_chkAutostart);

            var lblCap = new Label();
            lblCap.Text = "Restart cap (per runner per hour):";
            lblCap.Location = new Point(16, 84);
            lblCap.Size = new Size(230, 20);
            grpTray.Controls.Add(lblCap);
            _numCap = new NumericUpDown();
            _numCap.Minimum = 1;
            _numCap.Maximum = 20;
            _numCap.Location = new Point(250, 82);
            _numCap.Size = new Size(70, 20);
            grpTray.Controls.Add(_numCap);

            var lblTick = new Label();
            lblTick.Text = "Refresh cadence (seconds):";
            lblTick.Location = new Point(16, 112);
            lblTick.Size = new Size(230, 20);
            grpTray.Controls.Add(lblTick);
            _numTick = new NumericUpDown();
            _numTick.Minimum = 2;
            _numTick.Maximum = 60;
            _numTick.Location = new Point(250, 110);
            _numTick.Size = new Size(70, 20);
            grpTray.Controls.Add(_numTick);

            var grpConn = new GroupBox();
            grpConn.Text = "Connection (this host to the fleet)";
            grpConn.Location = new Point(12, 174);
            grpConn.Size = new Size(576, 132);
            Controls.Add(grpConn);

            _connState = new Label();
            _connState.Location = new Point(16, 24);
            _connState.Size = new Size(544, 20);
            grpConn.Controls.Add(_connState);

            _connDetail = new Label();
            _connDetail.Location = new Point(16, 46);
            _connDetail.Size = new Size(544, 20);
            grpConn.Controls.Add(_connDetail);

            var lblHb = new Label();
            lblHb.Text = "Heartbeat file:";
            lblHb.Location = new Point(16, 74);
            lblHb.Size = new Size(96, 20);
            grpConn.Controls.Add(lblHb);
            _txtHeartbeatPath = new TextBox();
            _txtHeartbeatPath.Location = new Point(116, 72);
            _txtHeartbeatPath.Size = new Size(444, 20);
            grpConn.Controls.Add(_txtHeartbeatPath);

            var lblConnStale = new Label();
            lblConnStale.Text = "Stale after (seconds):";
            lblConnStale.Location = new Point(16, 102);
            lblConnStale.Size = new Size(160, 20);
            grpConn.Controls.Add(lblConnStale);
            _numConnStale = new NumericUpDown();
            _numConnStale.Minimum = 10;
            _numConnStale.Maximum = 3600;
            _numConnStale.Location = new Point(180, 100);
            _numConnStale.Size = new Size(70, 20);
            grpConn.Controls.Add(_numConnStale);

            var lblConnDown = new Label();
            lblConnDown.Text = "Down after (seconds):";
            lblConnDown.Location = new Point(270, 102);
            lblConnDown.Size = new Size(160, 20);
            grpConn.Controls.Add(lblConnDown);
            _numConnDown = new NumericUpDown();
            _numConnDown.Minimum = 30;
            _numConnDown.Maximum = 86400;
            _numConnDown.Location = new Point(434, 100);
            _numConnDown.Size = new Size(70, 20);
            grpConn.Controls.Add(_numConnDown);

            var grpLanes = new GroupBox();
            grpLanes.Text = "Runner lanes";
            grpLanes.Location = new Point(12, 318);
            grpLanes.Size = new Size(576, 204);
            Controls.Add(grpLanes);

            var hint = new Label();
            hint.Text = "Checkboxes are saved settings. Buttons act " +
                "immediately and are not undone by Cancel.";
            hint.Location = new Point(16, 22);
            hint.Size = new Size(544, 20);
            grpLanes.Controls.Add(hint);

            _laneChk = new CheckBox[TraySettings.Lanes.Length];
            _laneState = new Label[TraySettings.Lanes.Length];
            for (int i = 0; i < TraySettings.Lanes.Length; i++)
            {
                string lane = TraySettings.Lanes[i];
                int y = 48 + i * 34;
                var chk = new CheckBox();
                chk.Text = "lane-" + lane;
                chk.Location = new Point(16, y);
                chk.Size = new Size(100, 20);
                grpLanes.Controls.Add(chk);
                _laneChk[i] = chk;

                var state = new Label();
                state.Location = new Point(124, y + 2);
                state.Size = new Size(170, 20);
                grpLanes.Controls.Add(state);
                _laneState[i] = state;

                var captured = lane;
                var btnStart = new Button();
                btnStart.Text = "Start";
                btnStart.Location = new Point(306, y - 2);
                btnStart.Size = new Size(78, 24);
                btnStart.Click += delegate
                {
                    var r = FindRunner(captured);
                    if (r != null) { _app.LaneStart(r); RefreshDynamic(); }
                };
                grpLanes.Controls.Add(btnStart);

                var btnStop = new Button();
                btnStop.Text = "Stop";
                btnStop.Location = new Point(392, y - 2);
                btnStop.Size = new Size(78, 24);
                btnStop.Click += delegate
                {
                    var r = FindRunner(captured);
                    if (r == null) return;
                    var ok = MessageBox.Show(
                        "Stop the lane-" + captured + " runner? " +
                        "A job running on it will be killed.",
                        "Syncyard Tray", MessageBoxButtons.OKCancel,
                        MessageBoxIcon.Warning);
                    if (ok == DialogResult.OK)
                    {
                        _app.LaneStop(r);
                        RefreshDynamic();
                    }
                };
                grpLanes.Controls.Add(btnStop);

                var btnRestart = new Button();
                btnRestart.Text = "Restart";
                btnRestart.Location = new Point(478, y - 2);
                btnRestart.Size = new Size(78, 24);
                btnRestart.Click += delegate
                {
                    var r = FindRunner(captured);
                    if (r != null) { _app.LaneRestart(r); RefreshDynamic(); }
                };
                grpLanes.Controls.Add(btnRestart);
            }

            var grpFleet = new GroupBox();
            grpFleet.Text =
                "Fleet (read-only - managed with the gk commands)";
            grpFleet.Location = new Point(12, 534);
            grpFleet.Size = new Size(576, 240);
            Controls.Add(grpFleet);

            _fleetMode = new Label();
            _fleetMode.Location = new Point(16, 24);
            _fleetMode.Size = new Size(544, 20);
            grpFleet.Controls.Add(_fleetMode);

            _fleetDetail = new Label();
            _fleetDetail.Location = new Point(16, 46);
            _fleetDetail.Size = new Size(544, 20);
            grpFleet.Controls.Add(_fleetDetail);

            var lblPath = new Label();
            lblPath.Text = "Snapshot path:";
            lblPath.Location = new Point(16, 74);
            lblPath.Size = new Size(96, 20);
            grpFleet.Controls.Add(lblPath);
            _txtFleetPath = new TextBox();
            _txtFleetPath.Location = new Point(116, 72);
            _txtFleetPath.Size = new Size(444, 20);
            grpFleet.Controls.Add(_txtFleetPath);

            var lblStale = new Label();
            lblStale.Text = "Treat as stale after (seconds):";
            lblStale.Location = new Point(16, 102);
            lblStale.Size = new Size(230, 20);
            grpFleet.Controls.Add(lblStale);
            _numStale = new NumericUpDown();
            _numStale.Minimum = 30;
            _numStale.Maximum = 3600;
            _numStale.Location = new Point(250, 100);
            _numStale.Size = new Size(70, 20);
            grpFleet.Controls.Add(_numStale);

            _queue = new ListView();
            _queue.View = View.Details;
            _queue.FullRowSelect = true;
            _queue.Location = new Point(16, 130);
            _queue.Size = new Size(544, 48);
            _queue.Columns.Add("Key", 205);
            _queue.Columns.Add("Program", 90);
            _queue.Columns.Add("Lane", 55);
            _queue.Columns.Add("Pri", 40);
            _queue.Columns.Add("Source", 70);
            _queue.Columns.Add("State", 60);
            grpFleet.Controls.Add(_queue);

            // The connection judgement's signal set, stated in
            // the fleet section too (spec 018 FR-004).
            _fleetSignal = new Label();
            _fleetSignal.Location = new Point(16, 186);
            _fleetSignal.Size = new Size(544, 16);
            grpFleet.Controls.Add(_fleetSignal);

            var note = new Label();
            note.Text = "Published by the gatekeeper and shown " +
                "read-only. Change fleet state with the gk commands " +
                "(spec 013), not from the tray.";
            note.Location = new Point(16, 206);
            note.Size = new Size(544, 28);
            grpFleet.Controls.Add(note);

            var btnSave = new Button();
            btnSave.Text = "Save";
            btnSave.Location = new Point(404, 786);
            btnSave.Size = new Size(84, 26);
            btnSave.Click += delegate { OnSave(); };
            Controls.Add(btnSave);

            var btnCancel = new Button();
            btnCancel.Text = "Cancel";
            btnCancel.Location = new Point(496, 786);
            btnCancel.Size = new Size(84, 26);
            btnCancel.Click += delegate { Close(); };
            Controls.Add(btnCancel);

            var s = app.Settings;
            _chkSupervision.Checked = s.SupervisionEnabled;
            _chkHide.Checked = s.HideStrayConsoles;
            // The checkbox tells the machine's actual truth at
            // open time (spec 014 FR-005), whatever set it last.
            _chkAutostart.Checked = TraySettings.AutostartIsRegistered();
            _numCap.Value = s.MaxRestartsPerHour;
            _numTick.Value = s.TickSeconds;
            _txtHeartbeatPath.Text = s.ConnectionHeartbeatPath;
            _numConnStale.Value = s.ConnectionStaleSeconds;
            _numConnDown.Value = s.ConnectionDownSeconds;
            _txtFleetPath.Text = s.FleetStatusPath;
            _numStale.Value = s.FleetStaleSeconds;
            for (int i = 0; i < TraySettings.Lanes.Length; i++)
                _laneChk[i].Checked =
                    s.LaneEnabled(TraySettings.Lanes[i]);
            RefreshDynamic();
        }

        private RunnerDef FindRunner(string lane)
        {
            foreach (var r in _app.Runners)
                if (string.Equals(r.Lane, lane,
                        StringComparison.OrdinalIgnoreCase))
                    return r;
            return null;
        }

        private void OnSave()
        {
            var cur = _app.Settings;
            // Spec 018 FR-005: the connection thresholds must
            // satisfy stale < down. An invalid pair is refused
            // here, with the fields named, and nothing is saved
            // - the prior values stay in force.
            int cStale = (int)_numConnStale.Value;
            int cDown = (int)_numConnDown.Value;
            if (cStale >= cDown)
            {
                MessageBox.Show(
                    "Connection stale threshold (" + cStale +
                    " s) must be less than the down threshold (" +
                    cDown + " s). Settings were not saved.",
                    "Syncyard Tray", MessageBoxButtons.OK,
                    MessageBoxIcon.Warning);
                return;
            }
            var next = new TraySettings();
            next.Autostart = _chkAutostart.Checked;
            next.SupervisionEnabled = _chkSupervision.Checked;
            next.MaxRestartsPerHour = (int)_numCap.Value;
            next.TickSeconds = (int)_numTick.Value;
            next.HideStrayConsoles = _chkHide.Checked;
            string hb = _txtHeartbeatPath.Text.Trim();
            next.ConnectionHeartbeatPath =
                hb.Length > 0 ? hb : cur.ConnectionHeartbeatPath;
            next.ConnectionStaleSeconds = cStale;
            next.ConnectionDownSeconds = cDown;
            string fp = _txtFleetPath.Text.Trim();
            next.FleetStatusPath =
                fp.Length > 0 ? fp : cur.FleetStatusPath;
            next.FleetStaleSeconds = (int)_numStale.Value;
            for (int i = 0; i < TraySettings.Lanes.Length; i++)
                next.SetLaneEnabled(TraySettings.Lanes[i],
                    _laneChk[i].Checked);
            _app.ApplySettings(next);
            Close();
        }

        // Called from the tray tick (and after this dialog's own
        // actions): re-set text on the existing controls only;
        // rebuild the queue list only when the snapshot's stamp
        // changed (spec 014 FR-010).
        public void RefreshDynamic()
        {
            for (int i = 0; i < TraySettings.Lanes.Length; i++)
            {
                var r = FindRunner(TraySettings.Lanes[i]);
                _laneState[i].Text = r == null ? "not registered"
                    : r.Faulted ? "FAULTED - owner needed"
                    : r.Running ? "running" : "stopped";
            }
            // The connection section (spec 018): the state in
            // words plus the signal set it rests on, refreshed
            // from the tray tick like the lane states.
            var c = _app.Connection;
            _connState.Text = c == null
                ? "Connection: unknown (no signal seen yet)"
                : c.Line();
            string sig = c == null
                ? "signal: backchannel heartbeat only"
                : c.SignalDetail();
            _connDetail.Text = sig;
            _fleetSignal.Text = sig;
            var f = _app.Fleet;
            if (f == null)
            {
                _fleetMode.Text = "Fleet: not loaded yet";
                _fleetDetail.Text = "";
                return;
            }
            if (!f.HasData)
            {
                _fleetMode.Text =
                    "Fleet: unavailable (" + f.Problem + ")";
                _fleetDetail.Text = "No snapshot at the path below. " +
                    "The section lights up when a publisher writes " +
                    "one (spec 014 FR-009).";
                if (_queueStamp != null)
                {
                    _queue.Items.Clear();
                    _queueStamp = null;
                }
                return;
            }
            _fleetMode.Text = "Fleet: " + f.ModeLine() + " - " +
                f.Waiting.Count + " waiting - snapshot " +
                FleetStatus.FormatAge(f.AgeSeconds) +
                (f.Stale ? " - STALE" : "");
            string detail = "";
            if (f.FocusPrograms.Count + f.FocusKeys.Count +
                f.FocusChats.Count > 0)
            {
                detail = "Focus: programs [" +
                    string.Join(", ", f.FocusPrograms.ToArray()) +
                    "] keys [" +
                    string.Join(", ", f.FocusKeys.ToArray()) +
                    "] chats [" +
                    string.Join(", ", f.FocusChats.ToArray()) + "]";
            }
            else if (f.Reason.Length > 0)
            {
                detail = f.Reason;
            }
            _fleetDetail.Text = detail;
            if (f.GeneratedAt != _queueStamp)
            {
                _queueStamp = f.GeneratedAt;
                _queue.Items.Clear();
                foreach (var j in f.Waiting)
                {
                    var item = new ListViewItem(j.Key);
                    item.SubItems.Add(j.Program);
                    item.SubItems.Add(j.Lane);
                    item.SubItems.Add(j.HasPriority
                        ? j.Priority.ToString(
                            CultureInfo.InvariantCulture) : "");
                    item.SubItems.Add(j.PrioritySource ?? "");
                    item.SubItems.Add(j.HasHeld
                        ? (j.Held ? "held" : "admitted") : "");
                    _queue.Items.Add(item);
                }
            }
        }
    }
}

#!/usr/bin/env python3
"""Static battery for the Syncyard tray source (specs 007 + 014 + 018 + 021).

Guards the 2026-11-01 field crash: the tray re-rendered its icon on
every 5 s supervision tick and leaked the bitmap and the icon handle
each render (an undisposed Bitmap, plus an Icon.FromHandle whose
Dispose releases nothing). Roughly 7,500 ticks exhausted the
per-process GDI quota, and the tray died with a System.Drawing
OutOfMemoryException while constructing its own error dialog.

The spec 014 section guards the settings UI and fleet display built
on top of that discipline: the settings writer must emit exactly
the keys its reader consumes (the persistence round-trip, proven at
source level — CI has no .NET Framework compiler, so this parity
extraction is the functional test the battery can honestly run),
the dialog must stay single-instance and allocation-free on the
tick path, the Run key must be written only from the dialog's save
path, and the fleet section must stay read-only (one WriteAllText
in the whole source: the settings save) with mode strings carried
verbatim, never whitelisted.

The spec 018 section guards the connection indicator: the state
function's bounds and missing-signal case, the FR-003 wording, the
FR-004 signal honesty, the icon consuming BOTH states while the
runner slash geometry stays untouched, and the connection path
performing no writes and no process acts (display only, FR-007).
Two spec 014 checks were widened, not weakened, when the icon
gained its second dimension: the render cache key is now the
(runner, connection) pair, and the SetIcon call site passes both
states - the runner-state value it always carried is unchanged.

The spec 021 section guards the self-update: the build stamp, the
manifest check, the hash verified before the swap is armed, and
the helper mode running before the singleton mutex with no UI in
its code path.

These are source-shape checks: they fail if the leak patterns, the
missing exception guards, or a settings/fleet regression ever come
back.
"""
import pathlib
import re
import sys

SRC = pathlib.Path(__file__).with_name("SyncyardTray.cs").read_text(
    encoding="utf-8")


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    return ok


def region(start_marker, end_marker=None):
    i = SRC.find(start_marker)
    if i < 0:
        return ""
    if end_marker is None:
        return SRC[i:]
    j = SRC.find(end_marker, i + len(start_marker))
    return SRC[i:] if j < 0 else SRC[i:j]


def seticon_body():
    m = re.search(
        r"private void SetIcon\(int state, int conn\)\n(.*?)\n        \}\n",
        SRC, re.S)
    return m.group(1) if m else ""


def settings_keys():
    """(written, read) flat-key sets of the settings file.

    Written keys are the literal first arguments of the writer's
    Line(...) calls in Serialize(); read keys are the literals the
    reader passes to MiniJson.Get* in Load(). The per-lane keys are
    dynamic ("laneEnabled_" + lane) on both sides and are checked
    separately.
    """
    ser = region("public string Serialize()",
                 "private static void Line(")
    written = set(re.findall(r'Line\(sb, "([A-Za-z]+)",', ser))
    settings = region("internal sealed class TraySettings",
                      "internal sealed class FleetJob")
    read = set(re.findall(
        r'MiniJson\.Get(?:Bool|Int|String)\(text,\s*\n?\s*"([A-Za-z]+)"',
        settings))
    return written, read


def main():
    body = seticon_body()
    settings = region("internal sealed class TraySettings",
                      "internal sealed class FleetJob")
    fleet = region("internal sealed class FleetStatus",
                   "internal sealed class ConnectionStatus")
    conn = region("internal sealed class ConnectionStatus",
                  "internal static class Program")
    tooltip = region("private string TooltipText(int running)",
                     "private void SetIcon")
    onsave = region("private void OnSave()", "public void RefreshDynamic")
    form = region("internal sealed class SettingsForm")
    ctor = region("public TrayApp()", "// --- supervision")
    startmissing = region("private void StartMissing(bool manual)",
                          "private void StartRunner")
    restart = region("private void RestartRunner(RunnerDef r)",
                     "// --- settings surface")
    helper = region("private static int ApplyUpdateHelper",
                    "private static void Main")
    updworker = region("private void ApplyUpdateWorker",
                       "private void EndUpdateBusy")
    written, read = settings_keys()
    results = [
        # ---- spec 007: the GDI leak and exception guards ----------
        check("SetIcon exists", bool(body)),
        # The icon renders only when a state it displays changes:
        # the cache key is the (runner, connection) pair since
        # spec 018 - a connection-only change re-renders, and a
        # tick where neither changed renders nothing.
        check("SetIcon caches by runner and connection state",
              "if (state == _iconState && conn == _iconConnState "
              "&& _currentIcon != null) return;" in body),
        # The rendered bitmap is released.
        check("SetIcon disposes the bitmap", "bmp.Dispose();" in body),
        # The borrowed HICON is destroyed, not leaked through
        # Icon.FromHandle (whose Dispose is a no-op for the handle).
        check("SetIcon destroys the borrowed HICON",
              "DestroyIcon(hIcon);" in body),
        check("SetIcon clones the icon",
              "(Icon)borrowed.Clone();" in body),
        check("no bare FromHandle assignment remains",
              "_currentIcon = Icon.FromHandle(" not in SRC),
        # The per-tick menu rebuild disposes the previous menu.
        check("RebuildMenu disposes the previous menu",
              "oldMenu.Dispose();" in SRC),
        # Supervision ticks are guarded: log, never the modal dialog.
        check("OnTick guards TickCore",
              "try { TickCore(); }" in SRC
              and "tick error (caught)" in SRC),
        # The UI thread exception mode is catch-and-log, set in Main.
        check("Main sets CatchException mode",
              "UnhandledExceptionMode.CatchException" in SRC
              and "Application.ThreadException +=" in SRC),

        # ---- spec 014 FR-001: settings persistence ----------------
        check("TraySettings class exists", bool(settings)),
        check("settings file is tray-settings.json beside the exe",
              '"tray-settings.json"' in settings
              and "BaseDirectory" in settings),
        # The persistence round-trip: every key the reader consumes
        # is a key the writer emits, and the only written-but-unread
        # key is the format version.
        check("settings writer/reader key parity",
              read <= written and written - read <= {"version"}),
        check("per-lane keys written and read with the same prefix",
              '"laneEnabled_" + Lanes[i]' in settings
              and '"laneEnabled_" + lane' in settings),
        check("settings defaults are the pre-014 behavior",
              "public bool SupervisionEnabled = true;" in settings
              and "public int MaxRestartsPerHour = 3;" in settings
              and "public int TickSeconds = 5;" in settings
              and "public int FleetStaleSeconds = 300;" in settings),
        check("settings values are clamped on load",
              ", 1, 20)" in settings and ", 2, 60)" in settings
              and ", 30, 3600)" in settings),
        # The settings save is the ONLY WriteAllText in the tray:
        # the fleet snapshot and everything else stay read-only.
        check("single WriteAllText, inside TraySettings.Save",
              SRC.count("File.WriteAllText(") == 1
              and "File.WriteAllText(" in settings),

        # ---- spec 014 FR-002/FR-004/FR-006: live application ------
        check("timer interval comes from settings (ctor + apply)",
              SRC.count(
                  "_timer.Interval = _settings.TickSeconds * 1000;")
              == 2),
        check("supervision pass consults the settings",
              "_settings.SupervisionEnabled" in startmissing
              and "_settings.LaneEnabled(r.Lane)" in startmissing
              and "_settings.MaxRestartsPerHour" in startmissing),
        check("stray-console auto-hide is gated by its setting",
              "if (_tick % 6 == 0 && _settings.HideStrayConsoles)"
              in SRC),
        check("RestartRunner composes the existing stop/start acts",
              "StopRunner(r);" in restart
              and "StartRunner(r);" in restart),

        # ---- spec 014 FR-005: autostart confined to save ---------
        check("Run key path is the tray's own value",
              "CurrentVersion\\Run" in settings
              and '"SyncyardTray"' in settings),
        check("Run key is written once and removed once",
              SRC.count("key.SetValue(") == 1
              and SRC.count("key.DeleteValue(") == 1),
        check("startup path never writes the Run key",
              "SetValue(" not in ctor and "DeleteValue(" not in ctor),

        # ---- spec 014 FR-003: the dialog --------------------------
        check("SettingsForm is the only Form in the source",
              "internal sealed class SettingsForm : Form" in SRC
              and SRC.count(": Form") == 1),
        check("dialog is single-instance with an Activate path",
              "if (_settingsForm != null && !_settingsForm.IsDisposed)"
              in SRC and "_settingsForm.Activate();" in SRC),
        check("menu offers the dialog as Settings...",
              '"Settings..."' in SRC),
        check("open dialog refreshes from the tick",
              "_settingsForm.RefreshDynamic();" in SRC),

        # ---- spec 014 FR-007/FR-008: the fleet display ------------
        check("FleetStatus reader exists", bool(fleet)),
        check("fleet parser covers the spec 013 status shape",
              all(k in fleet for k in (
                  '"generated_at"', '"mode"', '"draining"',
                  '"reason"', '"focus"', '"keys"', '"programs"',
                  '"chats"', '"waiting"', '"priority"',
                  '"priority_source"', '"held"'))),
        check("fleet unavailability reasons are named",
              '"missing"' in fleet and '"unparseable"' in fleet),
        check("mode is carried verbatim, never whitelisted",
              "st.Mode = mode;" in fleet
              and '"PAUSED"' not in SRC and '"FOCUSED"' not in SRC
              and '"RUNNING"' not in SRC),
        check("default fleet snapshot path is the contract path",
              "fleet-status.json" in settings
              and "Syncyard\\status" in settings),
        check("dialog queue rebuild is stamp-gated",
              "if (f.GeneratedAt != _queueStamp)" in form
              and "_queue.Items.Clear();" in form),

        # ---- spec 014 FR-010/FR-011: no new churn, no console -----
        check("icon path is still the only handle/bitmap churn",
              SRC.count("GetHicon()") == 1
              and SRC.count("new Bitmap(") == 2
              and SRC.count("Graphics.FromImage(") == 1),
        # Spec 018 widened this call: the runner-state value is
        # unchanged, the connection state rides beside it.
        check("SetIcon is called with runner and connection states",
              "SetIcon(iconState, _conn.State);" in SRC),
        check("no console use anywhere in the tray",
              "Console." not in SRC),

        # ---- spec 018 FR-001: the connection state function -------
        check("ConnectionStatus class exists", bool(conn)),
        check("connection states are the three named states",
              '"CONNECTED"' in conn and '"STALE"' in conn
              and '"DISCONNECTED"' in conn),
        check("no signal ever seen is disconnected",
              "if (!hasSignal) return Disconnected;" in conn),
        check("state bounds straddle stale then down, in order",
              "if (ageSeconds <= staleSeconds) return Connected;"
              in conn
              and "if (ageSeconds <= downSeconds) return Stale;"
              in conn),
        check("newest signal wins in Evaluate",
              "hbAge <= snapAge" in conn
              and 'cs.SignalName = "heartbeat";' in conn
              and 'cs.SignalName = "fleet snapshot";' in conn),
        check("heartbeat age reads the agent ts stamp",
              'MiniJson.GetString(text, "ts")' in conn),
        check("heartbeat age falls back to the file write time",
              "File.GetLastWriteTimeUtc(path)" in conn),
        check("signal read retries once, then treats as missing",
              "attempt < 2" in conn),

        # ---- spec 018 FR-002: the icon's link mark ----------------
        check("link mark is drawn inside the icon render",
              "DrawLinkMark(g, conn);" in body),
        check("link mark is primitives only (arc + rings)",
              "DrawArc(" in SRC and "DrawEllipse(" in SRC),
        check("wall thickness is the solid/hollow distinction",
              "conn == ConnectionStatus.Connected ? 3f : 1.6f"
              in SRC),
        check("broken link is struck in the fault red",
              "Color.FromArgb(225, 45, 45)" in SRC
              and "g.DrawArc(pen, 19, 19, 11, 11, 35, 290);" in SRC),
        check("disconnected desaturates alongside runner state 2",
              "if (state == 2 || conn == ConnectionStatus.Disconnected)"
              in body),
        check("runner slash geometry is untouched by spec 018",
              "g.DrawLine(halo, 6, 26, 26, 6);" in body
              and "g.DrawLine(pen, 6, 26, 26, 6);" in body),

        # ---- spec 018 FR-003/FR-004: words and honesty ------------
        check("FR-003 wording: the four menu/tooltip lines",
              '"Connection: connected ("' in conn
              and '"Connection: stale ("' in conn
              and '"Connection: disconnected (no "' in conn
              and '"Connection: unknown (no signal seen yet)"'
              in conn),
        check("wording names the signal it rests on",
              '"heartbeat"' in conn and '"fleet snapshot"' in conn),
        check("FR-004 signal honesty detail names the signal set",
              '"signal: backchannel heartbeat only"' in conn
              and '"signals: backchannel heartbeat + fleet snapshot"'
              in conn
              and '"signal: fleet snapshot only"' in conn),
        check("menu carries the connection line",
              "ConnectionText()" in SRC),
        # Owner trim (2026-11-04): the menu header is the build
        # line, the fleet line, and the connection line. The
        # per-job Next line and the signal detail line are gone
        # from the menu (the detail lives in the settings dialog).
        check("menu header is trimmed (no Next line, no detail line)",
              "FleetNextText" not in SRC
              and "ConnectionDetailText" not in SRC),
        check("tooltip carries the connection as one word",
              '"linked"' in tooltip and '"link stale"' in tooltip
              and '"unlinked"' in tooltip),
        # .NET Framework's NotifyIcon.Text setter throws
        # ArgumentOutOfRangeException above 63 characters (the
        # 120 cap shipped in specs 014/018 crashed the tray in
        # its constructor on 2026-11-03). The tooltip must fit
        # by composition, never by truncation: an earlier build
        # chopped the composed string at 63 mid-word, which is
        # the cut-off hover text the owner rejected 2026-11-04.
        check("tooltip fits 63 by composition, never truncation",
              "Substring" not in tooltip
              and 'if ((s + " - " + p).Length > 63) break;' in tooltip),
        check("dialog states the signal set in both sections",
              "_connDetail.Text = sig;" in form
              and "_fleetSignal.Text = sig;" in form),

        # ---- spec 018 FR-005: thresholds are settings -------------
        check("connection defaults are the FR-001 numbers",
              "public int ConnectionStaleSeconds = 180;" in settings
              and "public int ConnectionDownSeconds = 900;" in settings),
        check("connection settings keys round-trip",
              {"connectionStaleSeconds", "connectionDownSeconds",
               "connectionHeartbeatPath"} <= read
              and {"connectionStaleSeconds", "connectionDownSeconds",
                   "connectionHeartbeatPath"} <= written),
        check("default heartbeat path is the deployed agent's file",
              "syncyard-backchannel\\repo\\channels\\syncyard-dev"
              "\\heartbeat.json" in settings
              and "wsl.localhost" in settings),
        check("an inverted loaded pair falls back to the defaults",
              "if (s.ConnectionStaleSeconds >= s.ConnectionDownSeconds)"
              in settings),
        check("dialog connection fields exist",
              "_numConnStale" in form and "_numConnDown" in form
              and "_txtHeartbeatPath" in form),
        check("dialog refuses stale >= down before any save",
              "must be less than the down threshold" in onsave
              and onsave.find("must be less than the down threshold")
              < onsave.find("_app.ApplySettings(next);")),

        # ---- spec 018 FR-006: transition logging ------------------
        check("transitions are compare-and-logged on the tick",
              'Log("connection "' in SRC and '" -> "' in SRC
              and '" age "' in SRC
              and "if (_conn.State != _lastConnState)" in SRC),
        check("startup state is a baseline, not a logged transition",
              "_lastConnState = _conn.State;" in SRC),

        # ---- spec 018 FR-007: display only -------------------------
        check("connection path performs no writes or process acts",
              "Process." not in conn and "WriteAll" not in conn
              and ".Kill(" not in conn and "File.Write" not in conn
              and "ProcessStartInfo" not in conn),
        check("connection evaluation never gates supervision",
              "_conn" not in startmissing
              and "ConnectionStatus" not in startmissing),

        # ---- menu-open rebuild deferral (owner report 2026-11-04) --
        check("rebuild defers while the context menu is open",
              "if (_menuOpen) { _menuDirty = true; return; }" in SRC),
        check("menu tracks its own open and closed state",
              "menu.Opened +=" in SRC and "menu.Closed +=" in SRC
              and "_menuOpen = false;" in SRC),
        check("a deferred rebuild runs once when the menu closes",
              "if (_menuDirty) { _menuDirty = false; RebuildMenu(); }"
              in SRC),

        # ---- exit forensics (owner order 2026-11-04) --------------
        check("fatal non-UI thread faults are logged before death",
              "AppDomain.CurrentDomain.UnhandledException +=" in SRC
              and "unhandled appdomain exception" in SRC),
        check("orderly message-loop exits are logged",
              "Application.ApplicationExit +=" in SRC
              and "tray process exiting" in SRC),
        check("a per-minute alive line bounds any silent death",
              'if (_tick % 12 == 0)' in SRC
              and 'Log("tray alive")' in SRC),

        # ---- spec 021: build stamp and self-update ----------------
        check("assembly version is the 0.4.1.0 build stamp",
              'AssemblyVersion("0.4.1.0")' in SRC
              and 'AssemblyFileVersion("0.4.1.0")' in SRC),
        check("BuildId constant carries the stamp",
              'BuildId = "0.4.1.0"' in SRC),
        check("menu header shows the build stamp",
              '"Syncyard Tray v" + BuildId' in SRC),
        check("startup log carries the build stamp",
              'Log("tray build " + BuildId)' in SRC),
        check("update check reads the published manifest",
              '"https://github.com/AXIOVEX/syncyard/releases/'
              'latest/download/tray-update.json"' in SRC),
        check("update menu item texts exist",
              '"Check for updates"' in SRC
              and '"Update to v" + _updateVersion' in SRC),
        # The download's hash must be computed and compared
        # before the verified path is handed to the launcher;
        # a mismatch deletes the download and arms nothing.
        check("hash is verified before the swap is armed",
              updworker.find("Sha256Hex(newPath)") >= 0
              and updworker.find("Sha256Hex(newPath)")
              < updworker.find("_pendingUpdatePath = newPath;")),
        check("a hash mismatch aborts and deletes the download",
              '"update aborted: hash mismatch"' in updworker
              and "File.Delete(newPath)" in updworker),
        # The helper branch must be reached before the singleton
        # mutex exists, or the helper could never replace the
        # exe of a running tray.
        check("apply-update runs before the singleton mutex",
              SRC.find('args[0] == "--apply-update"') >= 0
              and SRC.find('args[0] == "--apply-update"')
              < SRC.find('"SyncyardTraySingleton"')),
        # The helper's own code path (from its entry to Main)
        # may hold no UI tokens at all.
        check("helper mode is UI-free (no icon, no message box)",
              bool(helper)
              and "NotifyIcon" not in helper
              and "MessageBox" not in helper),
        check("helper waits, swaps with a .bak, and relaunches",
              "OtherInstanceRunning(exePath)" in helper
              and 'exePath + ".bak"' in helper
              and "File.Move(newPath, exePath)" in helper
              and "Process.Start(exePath)" in helper),
        check("tray hands off and exits for the update",
              "--apply-update " in SRC
              and '"tray exiting for update"' in SRC),
        # ---- spec 021 FR-007: per-machine runner root ------------
        # The lane dirs come from the settings runner root so a
        # second machine (the laptop) supervises its own lanes;
        # the hardcoded desktop lane paths must not come back.
        check("runner root is a setting with the desktop default",
              "public string RunnerRoot" in SRC
              and '@"C:\\Users\\trist\\Development\\Syncyard\\runners"'
              in SRC
              and '"runnerRoot"' in SRC),
        check("lane dirs are built from the settings runner root",
              'Path.Combine(_settings.RunnerRoot, "long")' in SRC
              and 'Path.Combine(_settings.RunnerRoot, "quick")' in SRC
              and 'Path.Combine(_settings.RunnerRoot, "normal")' in SRC
              and 'Path.Combine(_settings.RunnerRoot, "gpu")' in SRC
              and '@"C:\\Users\\trist\\Development\\Syncyard\\runners\\long"'
              not in SRC
              and '@"C:\\Users\\trist\\Development\\Syncyard\\runners\\gpu"'
              not in SRC),
        check("settings load before the runner table is built",
              SRC.find("_settings = TraySettings.Load();") >= 0
              and SRC.find("_settings = TraySettings.Load();")
              < SRC.find("_runners.Add")),
    ]
    print("TRAY_SOURCE %s" % ("OK" if all(results) else "FAILURES"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

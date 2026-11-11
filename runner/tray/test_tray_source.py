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
path, and the fleet section must stay read-only (the settings
save and the tray's own two state files, its heartbeat and its
stopped-by-user marker, are the only writes in the whole
source: three WriteAllText sites, battery-pinned) with
mode strings carried verbatim, never whitelisted.

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

The spec 027 section pins one version truth at the source: the
.nsi VERSION define, both assembly version attributes, and the
BuildId string are extracted and asserted equal, so the stamps
the gatekeeper's drift judge compares can only move together.

The spec 028 section pins settings resilience: the save is
atomic with a .bak and the load falls back live file -> .bak
-> machine profile -> compiled defaults, logging the source
that won; the profile is read as installed data beside the
exe (never compiled constants naming another machine); the
machine profiles shipped in profiles/ are parsed and checked
(the laptop names only its own lanes, the desktop's normal
lane names its three instances); the runner table is built
from the settings' lane set with per-instance directories;
the supervising-nothing note rides the menu header and the
heartbeat; restart state persists beside the log; and
listener identity is the exact runner directory in the tray
and in runner/runner-watchdog.ps1.

The invisibility follow-through section (2026-11-06) pins the
stray-console hider's owner-and-class decision, the guarded
WindowsTerminal branch, and the silent duplicate launch: the
desktop window audit proved the old title-substring test blind
to the window classes that actually appear, and the duplicate
launch's modal dialog was itself a visible window in the
owner's session.

The menu-lifetime section guards the 2026-11-04 laptop crash:
RebuildMenu used to build a new ContextMenuStrip per rebuild,
assign it to the NotifyIcon, and dispose the previous strip - and
the deferred rebuild inside the old strip's own Closed handler
disposed it while WinForms' modal menu filter still held it as
the active menu, so the next activation change reparented a
disposed object and the tray died with an unhandled
ObjectDisposedException. The strip is now created once, bound
once, and never disposed; only detached items are. CI cannot run
WinForms, so the battery pins that shape at source level - the
strongest drive the harness has.

These are source-shape checks: they fail if the leak patterns, the
missing exception guards, or a settings/fleet regression ever come
back.
"""
import json
import pathlib
import re
import sys

SRC = pathlib.Path(__file__).with_name("SyncyardTray.cs").read_text(
    encoding="utf-8")
NSI = pathlib.Path(__file__).with_name("SyncyardTray.nsi").read_text(
    encoding="utf-8")
RUNNER_WATCHDOG = (pathlib.Path(__file__).parent.parent /
    "runner-watchdog.ps1").read_text(encoding="utf-8")
_TRAY_DIR = pathlib.Path(__file__).parent
PROFILE_DESKTOP = json.loads((_TRAY_DIR /
    "profiles/desktop/tray-profile.json").read_text(
    encoding="utf-8"))
PROFILE_LAPTOP = json.loads((_TRAY_DIR /
    "profiles/laptop/tray-profile.json").read_text(
    encoding="utf-8"))
WATCHDOG = pathlib.Path(__file__).with_name(
    "tray-watchdog.ps1").read_text(encoding="utf-8")
TASKXML = pathlib.Path(__file__).with_name(
    "tray-watchdog-task.xml").read_text(encoding="utf-8")


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


def profile_lanes(profile):
    return {entry["lane"]: entry["instances"]
            for entry in profile["lanes"]}


def region_of(class_marker, start_marker, end_marker=None):
    """A slice of SRC inside one class: from start_marker at or
    after class_marker to end_marker (the spec 028 regions must
    not leak across class boundaries - the heartbeat writer and
    the restart-state writer share the TrayApp class body, so
    their regions are cut inside it)."""
    base = SRC.find(class_marker)
    if base < 0:
        return ""
    i = SRC.find(start_marker, base)
    if i < 0:
        return ""
    if end_marker is None:
        return SRC[i:]
    j = SRC.find(end_marker, i + len(start_marker))
    return SRC[i:] if j < 0 else SRC[i:j]


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
    rebuild = region("private void RebuildMenu()",
                     "private string TooltipText")
    updworker = region("private void ApplyUpdateWorker",
                       "private void EndUpdateBusy")
    consoles = region("internal sealed class ConsolesForm",
                      "internal sealed class SettingsForm")
    heartbeat = region("private void WriteTrayHeartbeat()",
                       "// The one fleet read plus")
    tickcore = region("private void TickCore()",
                      "// The one fleet read plus")
    # Spec 028 regions, cut inside their owning classes.
    save028 = region_of("internal sealed class TraySettings",
                        "public void Save()",
                        "public string Serialize()")
    load028 = region_of("internal sealed class TraySettings",
                        "public static TraySettings Load()",
                        "private static void ParseInto(")
    profile028 = region("internal sealed class TrayProfile",
                        "internal sealed class TraySettings")
    supervise028 = region_of("internal sealed class TrayApp",
                             "internal string SupervisionNote()",
                             "private void LoadRestartState()")
    restart028 = region_of("internal sealed class TrayApp",
                           "private void LoadRestartState()",
                           "private void OnTick(")
    refresh028 = region_of("internal sealed class TrayApp",
                           "private void RefreshStates()",
                           "private static string SafePath(")
    beat028 = region_of("internal sealed class TrayApp",
                        "private void WriteTrayHeartbeat()",
                        "private static string IsoUtc(")
    written, read = settings_keys()
    strays = region("private void SetStrayConsoles(int cmd)",
                    "private void HideStrayConsoles()")
    dupmain = region("private static void Main",
                     "private static void ProgramLog")
    # Spec 027 (H3) FR-004: the tray's version truths, EXTRACTED
    # rather than literal-pinned — the drift judge compares the
    # beat's build against the published release, so the stamps
    # it reads meaning from can only be trusted if they cannot
    # move apart. All five stamps must extract and must be one
    # value; any one moving alone fails, all moving together
    # passes (the literal build-value pins above stay as the
    # spec 021 record of the current build).
    def _grab(pattern, text):
        m = re.search(pattern, text)
        return m.group(1) if m else None

    version_truths = {
        "nsi VERSION define": _grab(
            r'!define VERSION "([^"]+)"', NSI),
        "nsi VIProductVersion": _grab(
            r'VIProductVersion "([^"]+)"', NSI),
        "AssemblyVersion": _grab(
            r'AssemblyVersion\("([^"]+)"\)', SRC),
        "AssemblyFileVersion": _grab(
            r'AssemblyFileVersion\("([^"]+)"\)', SRC),
        "BuildId": _grab(r'BuildId = "([^"]+)"', SRC),
    }
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
        # The per-tick menu rebuild leaks nothing. This check
        # replaces "RebuildMenu disposes the previous menu":
        # disposing the strip itself turned out to be the
        # 2026-11-04 crash (menu-lifetime section below), so
        # the leak guard now rides on the rebuild capturing the
        # items it replaces and clearing them off the one
        # long-lived strip; their disposal once detached is
        # pinned by the menu-lifetime checks.
        check("RebuildMenu captures and clears replaced items",
              "var stale = new List<ToolStripItem>();" in rebuild
              and "menu.Items.Clear();" in rebuild),
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
        check("settings defaults are the aligned behavior",
              "public bool SupervisionEnabled = true;" in settings
              and "public int MaxRestartsPerHour = 3;" in settings
              and "public int TickSeconds = 5;" in settings
              and "public int FleetStaleSeconds = 900;" in settings),
        check("settings values are clamped on load",
              ", 1, 20)" in settings and ", 2, 60)" in settings
              and ", 30, 3600)" in settings),
        # The settings save, the tray heartbeat, the
        # stopped-by-user marker (spec 025), and the persisted
        # restart state (spec 028) are the ONLY WriteAllText
        # sites in the tray: the fleet snapshot and everything
        # else stay read-only. Spec 028 widened the count from
        # three to four for the restart-state store, the same
        # way spec 025 widened it for the marker.
        check("four WriteAllText sites only: TraySettings.Save, "
              "the tray heartbeat writer, the stopped-by-user "
              "marker, the restart-state store",
              SRC.count("File.WriteAllText(") == 4
              and "File.WriteAllText(" in settings
              and "File.WriteAllText(" in heartbeat
              and "File.WriteAllText(" in restart028),

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
        check("the tray has exactly three forms",
              "internal sealed class SettingsForm : Form" in SRC
              and "internal sealed class StatusForm : Form" in SRC
              and "internal sealed class ConsolesForm : Form" in SRC
              and SRC.count(": Form") == 3),
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
              "public int ConnectionStaleSeconds = 600;" in settings
              and "public int ConnectionDownSeconds = 900;" in settings),
        check("connection stale fallbacks use the aligned default",
              SRC.count("ConnectionStaleSeconds = 600;") == 3
              and "ConnectionStaleSeconds = 180;" not in SRC),
        check("staleness defaults match the shipped profiles",
              PROFILE_DESKTOP["fleetStaleSeconds"] == 900
              and PROFILE_LAPTOP["fleetStaleSeconds"] == 900
              and PROFILE_DESKTOP["connectionStaleSeconds"] == 600
              and PROFILE_LAPTOP["connectionStaleSeconds"] == 600),
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
        check("assembly version is the 0.4.4.0 build stamp",
              'AssemblyVersion("0.4.4.0")' in SRC
              and 'AssemblyFileVersion("0.4.4.0")' in SRC),
        check("BuildId constant carries the stamp",
              'BuildId = "0.4.4.0"' in SRC),
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

        # ---- 0.4.3.0: launch check, queue line, status window,
        # ---- consoles viewer -------------------------------------
        check("update check runs once at launch",
              '"update check at launch"' in SRC
              and "_checkFromLaunch = true;" in SRC
              and "_launchTimer.Interval = 4000;" in SRC),
        check("launch check announces by balloon, never installs",
              '"Syncyard tray update available: v"' in SRC
              and '". Open the tray menu to install."' in SRC
              and "_icon.ShowBalloonTip(10000" in SRC
              and "_launchBalloonPending = true;" in SRC),
        check("every check records its time and result",
              "_lastCheckAt = DateTime.Now;" in SRC
              and "_lastCheckResult = resultText;" in SRC
              and '"not checked yet this session"' in SRC),
        check("fleet parser reads the queue depth fields",
              all(k in fleet for k in (
                  '"active"', '"lanes"', '"queue_state"',
                  '"running_total"', '"waiting_total"',
                  '"run_id"', '"cap"', '"state"'))),
        check("queue line renders the four states with counts",
              "internal string QueueLineText()" in SRC
              and '"Queue: Idle"' in SRC
              and '"Queue: Waiting, "' in SRC
              and '"Queue: Full, "' in SRC
              and '"Queue: Running, "' in SRC),
        check("queue line degrades honestly without depth fields",
              '" waiting, running unknown"' in SRC),
        check("waiting queue line is styled as the alert state",
              "internal bool QueueLineAlert()" in SRC
              and "queueLine.ForeColor = QueueAlertColor;" in SRC
              and "queueLine.Font = QueueAlertFont;" in SRC),
        check("status window is a singleton form of its own",
              "internal sealed class StatusForm : Form" in SRC
              and '"Syncyard status"' in SRC
              and "if (_statusForm != null && "
                  "!_statusForm.IsDisposed)" in SRC
              and "_statusForm.Activate();" in SRC),
        check("status window refreshes itself while open",
              '"Refreshes itself every 5 seconds "' in SRC
              and "_timer.Interval = 5000;" in SRC),
        check("status window drives the same update paths",
              "_app.RequestUpdateCheck();" in SRC
              and "_app.RequestApplyUpdate();" in SRC
              and "internal void RequestUpdateCheck()" in SRC
              and "internal void RequestApplyUpdate()" in SRC),
        check("status window shows lanes, jobs, machine, updates",
              '"Queues by lane"' in SRC
              and '"This machine"' in SRC
              and '"Backchannel heartbeat: "' in SRC
              and 'Last check: ' in SRC),
        check("consoles viewer tails the runner logs read-only",
              "internal sealed class ConsolesForm : Form" in SRC
              and '"Show consoles"' in SRC
              and "box.ReadOnly = true;" in consoles
              and "NewestDiagLog" in consoles
              and '"_diag"' in SRC),
        check("consoles viewer has no input path of any kind",
              "FileAccess.Read" in consoles
              and "File.WriteAllText" not in consoles
              and "Process." not in consoles
              and "StandardInput" not in consoles),
        check("the window-hunting show path is gone",
              '"Show runner consoles"' not in SRC
              and "SetStrayConsoles(SW_SHOW)" not in SRC
              and "SW_SHOW" not in SRC),
        # ---- spec 021 FR-007: per-machine runner root ------------
        # The lane dirs come from the settings runner root so a
        # second machine (the laptop) supervises its own lanes;
        # the hardcoded desktop lane paths must not come back.
        check("runner root is a setting with the desktop default",
              "public string RunnerRoot" in SRC
              and '@"C:\\Users\\trist\\Development\\Syncyard\\runners"'
              in SRC
              and '"runnerRoot"' in SRC),
        # Spec 028 FR-004/FR-005 supersedes the four hardcoded
        # Combine calls this check used to pin: instance dirs
        # are built from the settings runner root and the
        # machine's instance names, in the runner-table loop
        # (pinned in the spec 028 section below). The guard
        # that matters from the original check stands: no
        # hardcoded desktop lane path may come back.
        check("no hardcoded desktop lane paths remain",
              '@"C:\\Users\\trist\\Development\\Syncyard\\runners\\long"'
              not in SRC
              and '@"C:\\Users\\trist\\Development\\Syncyard\\runners\\gpu"'
              not in SRC),
        check("settings load before the runner table is built",
              SRC.find("_settings = TraySettings.Load();") >= 0
              and SRC.find("_settings = TraySettings.Load();")
              < SRC.find("_runners.Add")),
        # ---- spec 025: tray liveness heartbeat (H1) --------------
        # The tray beats beside its log; the backchannel agent
        # merges the file into its heartbeat as `tray`, and the
        # gatekeeper judges agent-alive/tray-dead from it. These
        # pins are the writer's contract: the fields, the atomic
        # write, the cadence, and the Exit marker the packaged
        # watchdog honors.
        check("heartbeat file lives beside the tray log",
              '_heartbeatPath = Path.Combine(baseDir, '
              '"tray-heartbeat.json");' in ctor),
        check("a first beat is written at startup and the "
              "stopped-by-user marker is cleared there",
              "WriteTrayHeartbeat();" in ctor
              and "ClearStoppedByUserMarker();" in ctor),
        check("the beat carries the spec 025 fields",
              all(k in heartbeat for k in (
                  '\\"host\\"', '\\"build\\"', '\\"pid\\"',
                  '\\"session\\"', '\\"started_at\\"', '\\"ts\\"',
                  '\\"tick\\"', '\\"lanes\\"', '\\"faulted\\"'))),
        check("the beat's host is the heartbeatHost setting "
              "(guard convention, default desktop)",
              "_settings.HeartbeatHost" in heartbeat
              and 'public string HeartbeatHost = "desktop";'
              in settings),
        check("the beat's lanes are the tray's real runner "
              "states at write time",
              "foreach (var r in _runners)" in heartbeat
              and "r.Running" in heartbeat and "r.Faulted" in heartbeat),
        check("the heartbeat write is atomic (temp + replace, "
              "move when absent)",
              '".tmp"' in heartbeat and "File.Replace(" in heartbeat
              and "File.Move(" in heartbeat),
        check("the cadence is a 30 s wall clock off the tick, "
              "not a tick count",
              "TotalSeconds >= 30" in tickcore
              and "WriteTrayHeartbeat();" in tickcore),
        check("a failed beat write is logged once, not per "
              "beat, and recovery is logged",
              "_heartbeatWriteFailed" in heartbeat),
        check("owner Exit writes the stopped-by-user marker "
              "the watchdog honors",
              "WriteStoppedByUserMarker();" in rebuild
              and '"tray-stopped-by-user.json"' in SRC),
        check("version truth moves together: assembly, BuildId, "
              "and the .nsi defines all read 0.4.4.0",
              'AssemblyVersion("0.4.4.0")' in SRC
              and 'AssemblyFileVersion("0.4.4.0")' in SRC
              and 'BuildId = "0.4.4.0"' in SRC
              and '!define VERSION "0.4.4.0"' in NSI
              and 'VIProductVersion "0.4.4.0"' in NSI),
        # ---- spec 027: one version truth, extracted (FR-004) --
        # The value-independent form of the pin above: extract
        # every stamp the fleet reads meaning from (the .nsi
        # VERSION define that becomes the installer's
        # DisplayVersion, both assembly attributes, the BuildId
        # the beats and update comparisons carry) and assert
        # they are ONE value. Fails when any one moves alone;
        # passes when all move together to a new build.
        check("spec 027: every version stamp extracts (nsi "
              "define, assembly attributes, BuildId)",
              all(v is not None for v in version_truths.values())),
        check("spec 027: all extracted version stamps are equal "
              "(one version truth)",
              len(set(version_truths.values())) == 1),
        check("packaged watchdog: a missing exe is an "
              "owner-needed marker, never a launch attempt",
              "tray-watchdog-owner-needed.json" in WATCHDOG
              and "tray exe missing" in WATCHDOG
              and WATCHDOG.find("tray exe missing")
              < WATCHDOG.find("Start-Process")),
        check("packaged watchdog: honors the stopped-by-user "
              "marker the tray's Exit writes",
              '"tray-stopped-by-user.json"' in WATCHDOG
              and "stand down: tray stopped by user" in WATCHDOG),
        check("packaged watchdog: relaunch cap of 5 per hour, "
              "persisted in its state file beside the tray log",
              "RelaunchCapPerHour = 5" in WATCHDOG
              and "tray-watchdog-state.json" in WATCHDOG),
        check("packaged watchdog: the tray is identified by "
              "its full exe path only",
              "ExecutablePath.Equals($TrayExe" in WATCHDOG),
        check("watchdog task template: 1-minute repetition, "
              "windowless, tokens unsubstituted in the shipped "
              "template",
              "<Interval>PT1M</Interval>" in TASKXML
              and "-WindowStyle Hidden" in TASKXML
              and "__USER_SID__" in TASKXML
              and "__TRAY_DIR__" in TASKXML),
        # ---- spec 028: settings resilience, per-machine lane
        # ---- sets, multi-instance supervision -----------------
        # FR-001: the save is atomic (temp beside the live file,
        # replace with the previous file kept as .bak), and the
        # live path itself is never written in place.
        check("spec 028: settings save writes a temp file, "
              "never the live path in place",
              'string tmp = FilePath + ".tmp";' in save028
              and "File.WriteAllText(tmp, Serialize());" in save028
              and "File.WriteAllText(FilePath" not in SRC),
        check("spec 028: settings save replaces, keeping the "
              "previous file as .bak, and moves when absent",
              'File.Replace(tmp, FilePath, FilePath + ".bak");'
              in save028
              and "File.Move(tmp, FilePath);" in save028),
        check("spec 028: load falls back live file -> .bak -> "
              "machine profile -> compiled defaults",
              'string bak = FilePath + ".bak";' in load028
              and "ParseInto(b, File.ReadAllText(bak));" in load028
              and "BaseSettings()" in load028),
        check("spec 028: the fallback source is logged whenever "
              "it is not the live file",
              '"); last good .bak in use"' in load028
              and '"machine profile"' in load028
              and '"no settings file; "' in load028),
        # FR-002: the profile is installed data beside the exe,
        # read tolerantly; nothing compiled names another
        # machine's shape as the fallback.
        check("spec 028: the profile file lives beside the exe",
              "internal sealed class TrayProfile" in SRC
              and '"tray-profile.json"' in profile028
              and "BaseDirectory" in profile028),
        check("spec 028: the profile carries the FR-002 fields",
              all(k in profile028 for k in (
                  '"runnerRoot"', '"fleetStatusPath"',
                  '"connectionHeartbeatPath"', '"heartbeatHost"',
                  '"fleetStaleSeconds"',
                  '"connectionStaleSeconds"',
                  '"connectionDownSeconds"', '"lanes"',
                  '"instances"'))),
        check("spec 028: the installer installs and removes "
              "the profile",
              'File "tray-profile.json"' in NSI
              and 'Delete "$INSTDIR\\tray-profile.json"' in NSI),
        check("spec 028: the shipped profiles parse with the "
              "FR-002 fields",
              all(k in PROFILE_DESKTOP and k in PROFILE_LAPTOP
                  for k in ("runnerRoot", "fleetStatusPath",
                            "connectionHeartbeatPath",
                            "heartbeatHost", "fleetStaleSeconds",
                            "connectionStaleSeconds",
                            "connectionDownSeconds", "lanes"))),
        check("spec 028: the laptop profile names only its "
              "own lanes (no normal, no gpu)",
              set(profile_lanes(PROFILE_LAPTOP))
              == {"long", "quick"}
              and PROFILE_LAPTOP["heartbeatHost"] == "laptop"),
        check("spec 028: the desktop profile carries four "
              "lanes and the three-instance normal lane",
              set(profile_lanes(PROFILE_DESKTOP))
              == {"long", "quick", "normal", "gpu"}
              and profile_lanes(PROFILE_DESKTOP)["normal"]
              == ["normal", "normal2", "normal3"]),
        # FR-004/FR-005: the runner table comes from the
        # settings' lane set and instance map.
        check("spec 028: the runner table is built from the "
              "machine lane set, per instance directory",
              "foreach (var lane in _settings.Lanes)" in ctor
              and "foreach (var inst in "
                  "_settings.LaneInstances(lane))" in ctor
              and "Dir = Path.Combine(_settings.RunnerRoot, inst)"
              in ctor),
        check("spec 028: the compiled floor is the fallback "
              "only, and directory derivation strips instance "
              "digits",
              "public static readonly string[] DefaultLanes ="
              in settings
              and "DeriveLaneSetFromDirs" in settings
              and "TrimEnd(" in settings),
        check("spec 028: the settings dialog draws the "
              "machine's lanes, not the compiled floor",
              "_lanes = app.Settings.Lanes;" in SRC
              and "TraySettings.Lanes" not in SRC),
        # FR-003: supervising nothing is said, in the menu
        # header and in the heartbeat, and the icon never
        # presents all-up under it.
        check("spec 028: the supervising-nothing note names "
              "the missing runner root",
              '"supervising nothing: runner root missing"'
              in supervise028),
        check("spec 028: the menu header carries the "
              "supervising-nothing note",
              "var supLine = new ToolStripMenuItem(supNote);"
              in rebuild),
        check("spec 028: the heartbeat carries the supervision "
              "state on every beat",
              '\\"supervision\\"' in beat028
              and "SupervisionNote()" in beat028),
        check("spec 028: the icon never shows all-up while "
              "supervising nothing",
              "SupervisionNote() != null ? 2" in rebuild),
        # FR-005: the heartbeat reports each instance, with
        # its lane, instance name, and directory.
        check("spec 028: the heartbeat reports per-instance "
              "state under its lane",
              '\\"instance\\"' in beat028
              and '\\"dir\\"' in beat028
              and "foreach (var r in _runners)" in beat028),
        check("spec 028: the dialog lane buttons act on every "
              "instance of the lane",
              "foreach (var r in RunnersOf(captured))" in SRC
              and "_app.LaneStart(r);" in SRC
              and "_app.LaneStop(r);" in SRC
              and "_app.LaneRestart(r);" in SRC),
        check("spec 028: the dialog lane state aggregates "
              "the lane's instances",
              "_app.LaneStateText(_lanes[i])" in SRC
              and '" instances running"' in SRC),
        # FR-006: restart history and Faulted persist beside
        # the log, restored at startup and saved on change.
        check("spec 028: restart state lives beside the log",
              '"tray-restart-state.json"' in ctor
              and "LoadRestartState();" in ctor),
        check("spec 028: restart state restores history and "
              "Faulted per instance directory",
              'MiniJson.GetObjects(text,' in restart028
              and '"faulted"' in restart028
              and "r.RestartTimes.Add(t);" in restart028
              and "r.Faulted = true;" in restart028),
        check("spec 028: restart state is saved on start and "
              "on fault",
              SRC.count("SaveRestartState();") >= 5),
        # FR-007: listener identity is the exact directory,
        # in the tray and in runner/runner-watchdog.ps1.
        check("spec 028: tray listener identity is the exact "
              "runner directory, never a prefix",
              "Path.GetDirectoryName(path)" in refresh028
              and "string.Equals(parent, dir," in refresh028
              and "StartsWith(r.Dir" not in SRC
              and "ListenerInDir(p, r.Dir)" in SRC),
        check("spec 028: runner watchdog listener identity "
              "is the exact directory too",
              "Split-Path -Parent $p.ExecutablePath"
              in RUNNER_WATCHDOG
              and ".Equals($Dir," in RUNNER_WATCHDOG
              and "StartsWith($Dir" not in RUNNER_WATCHDOG),

        # ---- menu lifetime: the disposed-strip crash ------------
        # The 2026-11-04 laptop crash (see the module docstring):
        # the modal menu filter must never be left holding a
        # strip this process disposed. The strip is created once,
        # bound once, and never disposed; RebuildMenu rebuilds
        # its items in place and disposes only detached items.
        check("the menu strip is created exactly once",
              SRC.count("new ContextMenuStrip()") == 1),
        check("the icon is bound to the strip once, in the ctor",
              SRC.count("_icon.ContextMenuStrip =") == 1
              and "_icon.ContextMenuStrip = _menu;" in ctor),
        check("the Opened/Closed wiring lives in the constructor",
              "_menu.Opened +=" in ctor
              and "_menu.Closed +=" in ctor
              and ".Opened +=" not in rebuild
              and ".Closed +=" not in rebuild),
        check("RebuildMenu creates, swaps, and disposes no strip",
              "new ContextMenuStrip()" not in rebuild
              and "_icon.ContextMenuStrip" not in rebuild
              and "oldMenu" not in rebuild
              and not re.search(r"\b\w*[Mm]enu\w*\.Dispose\(\)",
                                SRC)),
        check("RebuildMenu rebuilds the items in place",
              "var menu = _menu;" in rebuild
              and "menu.Items.Clear();" in rebuild
              and "foreach (var item in stale) item.Dispose();"
              in rebuild),
        check("the open-menu rebuild deferral survives",
              "if (_menuOpen) { _menuDirty = true; return; }"
              in rebuild),

        # ---- invisibility follow-through (2026-11-06) ----------
        # The desktop window audit (run 42239836804) found the
        # two visible consoles were classes the old title-only
        # test could not see: an empty-title PseudoConsoleWindow
        # and a Terminal-hosted CASCADIA window. The hider now
        # decides by owner process and window class, with the
        # terminal host itself guarded (below).
        check("stray hider: the console-class set is pinned "
              "(classic, pseudoconsole, Terminal hosting)",
              '"ConsoleWindowClass"' in SRC
              and '"PseudoConsoleWindow"' in SRC
              and '"CASCADIA_HOSTING_WINDOW_CLASS"' in SRC
              and "FleetConsoleWindowClasses" in SRC),
        check("stray hider: window class is read via GetClassName",
              "GetClassName(hWnd, sb, sb.Capacity)" in SRC
              and "WindowClassName(p.MainWindowHandle)" in strays),
        check("stray hider: owner-based branch hides a fleet "
              "console process on class alone, title or class",
              "FleetConsoleProcesses" in SRC
              and 'new[] { "conhost", "cmd", "powershell", "pwsh" }'
              in SRC
              and "IsConsoleWindowClass(cls) ||" in strays
              and "TitleMatchesFleet(title))" in strays),
        check("stray hider: the original title matches survive",
              "TitleMatchesFleet" in SRC
              and '"actions-runner"' in SRC
              and '"run.cmd"' in SRC),
        check("stray hider: WindowsTerminal guard present - the "
              "terminal host is consulted separately and hidden "
              "only on a fleet title match",
              'Process.GetProcessesByName("WindowsTerminal")'
              in strays
              and strays.find(
                  'Process.GetProcessesByName("WindowsTerminal")')
              > strays.find("FleetConsoleProcesses")
              and "if (TitleMatchesFleet(title))" in strays),
        check("stray hider: each hide logs process, pid, class "
              "and title",
              '"hid stray console \'"' in SRC
              and '"\' process=" + p.ProcessName' in SRC
              and '" pid=" + p.Id' in SRC
              and '" class=" + cls' in SRC),
        check("stray hider: cadence and setting are unchanged",
              "if (_tick % 6 == 0 && _settings.HideStrayConsoles)"
              in SRC
              and "private void HideStrayConsoles() "
              "{ SetStrayConsoles(SW_HIDE); }" in SRC),

        # ---- duplicate launch silence (2026-11-06) -------------
        # A duplicate launch used to raise a modal MessageBox in
        # the owner's session (the field popups); it now logs one
        # line through ProgramLog and returns. The mutex logic
        # itself is untouched.
        check("duplicate launch logs and exits silently",
              'ProgramLog("duplicate launch: tray already " +'
              in dupmain
              and '"running, exiting silently");' in dupmain),
        check("duplicate launch shows no dialog",
              'MessageBox.Show("Syncyard Tray is already running."'
              not in SRC
              and "SyncyardTraySingleton" in dupmain
              and "if (!created)" in dupmain),
    ]
    print("TRAY_SOURCE %s" % ("OK" if all(results) else "FAILURES"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Launch-discipline battery (owner hard constraint, 2026-11-06).

NO VISIBLE CONSOLE WINDOWS, EVER: no console window (cmd,
PowerShell, conhost, Windows Terminal) may appear in the owner's
interactive session on any machine, not even minimized, not even
for a second. Every process launch in the fleet's own code must
therefore be windowless BY CONSTRUCTION, and this battery is the
CI source test that keeps it so: it scans every launch site in
runner/, backchannel/ and gatekeeper/ and fails any site whose
surrounding block lacks window discipline -

  PowerShell Start-Process ....... -WindowStyle Hidden
  C# ProcessStartInfo ............ CreateNoWindow = true, or
                                   WindowStyle = Hidden
  Python subprocess / os.system .. CREATE_NO_WINDOW, or an
                                   explicit exemption below

A direct C# Process.Start with no ProcessStartInfo cannot carry
discipline, so it passes only through an exemption. Exemptions
are explicit, per site, and each carries its reason: a launch is
exempt only when it provably cannot own a console window in the
owner's interactive session (a GUI-subsystem exe, a POSIX-only
host tool, test-only code). An exemption whose site disappears
is reported as stale, so the list cannot silently grow teeth.

Falsifier, run at introduction (2026-11-06): against the tree
before PR #84 (5188aaa), this battery FAILS on exactly the three
sites #84 fixed - runner/tray/tray-watchdog.ps1,
runner/tray/install-tray.ps1 and runner/guard/install-guard.ps1,
each a Start-Process with no -WindowStyle Hidden. Against the
#84 tree and this one it passes.

This battery is a source-shape check in the fleet's established
style: it reads text, it launches nothing itself, and it is
excluded from its own scan (its pattern strings are not launch
sites).
"""
import fnmatch
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCAN_ROOTS = ("runner", "backchannel", "gatekeeper")
SELF = pathlib.Path(__file__).resolve()

# (path relative to repo root, marker in the launch block,
#  one-line reason). A marker of "" exempts the whole file.
ALLOWED_EXEMPTIONS = [
    # --- C#: direct Process.Start of GUI-subsystem exes --------
    ("runner/tray/SyncyardTray.cs", "Process.Start(exePath)",
     "the tray exe itself: a WinForms GUI-subsystem binary, it "
     "owns no console by construction"),
    ("runner/tray/SyncyardTray.cs", 'Process.Start("notepad.exe"',
     "notepad.exe is a GUI editor launched on the owner's own "
     "menu click; it never owns a console"),
    # --- Python: test-only code --------------------------------
    ("runner/test_*.py", "",
     "test-only: runs in CI or a local battery, never in the "
     "owner's interactive session"),
    ("backchannel/test_*.py", "",
     "test-only: runs in CI or a local battery, never in the "
     "owner's interactive session"),
    ("gatekeeper/test_*.py", "",
     "test-only: runs in CI or a local battery, never in the "
     "owner's interactive session"),
    # --- Python: POSIX host tools ------------------------------
    # The gatekeeper, the publisher and the backchannel client
    # run on the Linux VM (systemd / cron) or a POSIX shell:
    # there is no Windows interactive session and no console
    # window to hide, and CREATE_NO_WINDOW is a Windows-only
    # creation flag that a POSIX subprocess call cannot carry.
    ("backchannel/bc.py", '["gh", "auth", "token"]',
     "POSIX client tool: gh token lookup with captured output, "
     "child of a non-interactive agent process"),
    ("gatekeeper/fleet_status.py", "snapshot_command()",
     "POSIX publisher on the Linux VM under systemd: the "
     "gatekeeper snapshot command, output captured"),
    ("gatekeeper/fleet_status.py", '["gh", "auth", "token"]',
     "POSIX publisher on the Linux VM under systemd: gh token "
     "lookup with captured output"),
    ("gatekeeper/gk.py", '"/bin/sh", "-c"',
     "POSIX-only fallback: /bin/sh rm on the Linux VM; the "
     "command cannot exist, let alone open a console, on "
     "Windows"),
    ("gatekeeper/gk.py", "subprocess.run(args, capture_output=True",
     "gatekeeper run_cmd helper: POSIX VM tool; children are "
     "gh/git with captured output in a non-interactive session"),
    ("gatekeeper/gk.py", '"gh", "api", "repos/%s/actions/runs/%s/logs"',
     "log-archive fetch: POSIX VM tool calling gh with "
     "captured output in a non-interactive session"),
]


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    return ok


def norm(text):
    return re.sub(r"\s+", " ", text)


def exemptions_for(rel, block):
    out = []
    for path_pat, marker, reason in ALLOWED_EXEMPTIONS:
        if fnmatch.fnmatch(rel, path_pat) and (
                marker == "" or marker in block):
            out.append((path_pat, marker, reason))
    return out


def scan_files():
    for root_name in SCAN_ROOTS:
        base = ROOT / root_name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if "__pycache__" in path.parts:
                continue
            if path.suffix.lower() not in (".ps1", ".cs", ".py"):
                continue
            if path.resolve() == SELF:
                continue
            yield path


def ps1_sites(lines):
    """(lineno, block) per Start-Process statement. Comment-only
    lines are not launch sites; a statement continues across
    trailing-backtick lines."""
    sites = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        if "Start-Process" not in line:
            continue
        block_lines = [line]
        j = i
        while block_lines[-1].rstrip().endswith("`") and \
                j + 1 < len(lines):
            j += 1
            block_lines.append(lines[j])
        sites.append((i + 1, norm(" ".join(block_lines))))
    return sites


def cs_sites(lines):
    """(lineno, block, kind) per C# launch site. A
    ProcessStartInfo construction is checked over the block up
    to and including its Process.Start; a Process.Start whose
    argument is a ProcessStartInfo variable is covered by that
    construction's check and is not double-counted."""
    text = "\n".join(lines)
    psi_vars = set(re.findall(
        r"(\w+)\s*=\s*new ProcessStartInfo", text))
    sites = []
    for i, line in enumerate(lines):
        if "new ProcessStartInfo" in line:
            block_lines = [line]
            j = i
            while "Process.Start(" not in block_lines[-1] and \
                    j + 1 < len(lines) and j - i < 40:
                j += 1
                block_lines.append(lines[j])
            sites.append((i + 1, norm(" ".join(block_lines)),
                          "ProcessStartInfo"))
            continue
        m = re.search(r"Process\.Start\(\s*(\"[^\"]*\"|[A-Za-z_][\w.]*)",
                      line)
        if m and m.group(1) not in psi_vars:
            sites.append((i + 1, norm(line), "Process.Start"))
    return sites


def py_sites(text):
    """(lineno, block) per Python launch call, the block being
    the full call statement (parentheses balanced)."""
    pat = re.compile(
        r"subprocess\.(?:run|Popen|call|check_call|check_output)"
        r"\s*\(|os\.(?:system|popen)\s*\(")
    sites = []
    for m in pat.finditer(text):
        depth = 0
        end = m.end() - 1
        while end < len(text):
            ch = text[end]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        lineno = text.count("\n", 0, m.start()) + 1
        sites.append((lineno, norm(text[m.start():end + 1])))
    return sites


def main():
    results = []
    used_exemptions = set()
    site_count = 0
    for path in scan_files():
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if path.suffix.lower() == ".ps1":
            for lineno, block in ps1_sites(lines):
                site_count += 1
                ok = "-windowstyle hidden" in block.lower()
                ex = exemptions_for(rel, block)
                if ex:
                    used_exemptions.update(ex)
                    ok = True
                if "-windowstyle hidden" in block.lower():
                    label = "carries -WindowStyle Hidden"
                elif ex:
                    label = "EXEMPT (%s)" % ex[0][2]
                else:
                    label = "lacks -WindowStyle Hidden (no exemption)"
                results.append(check(
                    "%s:%d Start-Process %s" % (rel, lineno, label),
                    ok))
        elif path.suffix.lower() == ".cs":
            for lineno, block, kind in cs_sites(lines):
                site_count += 1
                if kind == "ProcessStartInfo":
                    disciplined = (
                        "CreateNoWindow" in block
                        or ("WindowStyle" in block
                            and "Hidden" in block))
                    label = ("carries CreateNoWindow / WindowStyle "
                             "Hidden" if disciplined else
                             "lacks window discipline")
                else:
                    disciplined = False
                    label = "direct Process.Start"
                ex = exemptions_for(rel, block)
                if ex:
                    used_exemptions.update(ex)
                    disciplined = True
                    label += " - EXEMPT (%s)" % ex[0][2]
                results.append(check(
                    "%s:%d %s %s" % (rel, lineno, kind, label),
                    disciplined))
        else:
            for lineno, block in py_sites(text):
                site_count += 1
                disciplined = "CREATE_NO_WINDOW" in block
                label = ("carries CREATE_NO_WINDOW" if disciplined
                         else "no CREATE_NO_WINDOW")
                ex = exemptions_for(rel, block)
                if ex:
                    used_exemptions.update(ex)
                    disciplined = True
                    label += " - EXEMPT (%s)" % ex[0][2]
                results.append(check(
                    "%s:%d subprocess %s" % (rel, lineno, label),
                    disciplined))
    results.append(check(
        "launch sites were found to scan (%d)" % site_count,
        site_count > 0))
    stale = [e for e in ALLOWED_EXEMPTIONS
             if e not in used_exemptions]
    for path_pat, marker, reason in stale:
        results.append(check(
            "exemption still names a live site: %s [%s]"
            % (path_pat, marker or "whole file"), False))
    print("LAUNCH_DISCIPLINE %s (%d sites, %d exemptions used)"
          % ("OK" if all(results) else "FAILURES", site_count,
             len(used_exemptions)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

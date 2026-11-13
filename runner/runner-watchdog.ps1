#Requires -Version 7.0
# runner-watchdog.ps1 - Desktop runner watchdog for the desktop host.
#
# Runs every 5 minutes as a user scheduled task under the operator account (see the
# install note in gatekeeper/README.md). For each configured runner
# directory it checks whether that runner's listener process
# (Runner.Listener.exe living under the runner directory) is alive.
# If the listener is absent and run.cmd exists, it starts the runner
# with Start-Process. The scheduled-task context is NOT an Actions job,
# so the started process survives.
#
# Discipline mirrors the relay fail-safe: restart cap of 3 per runner
# per rolling hour; when the cap is hit it logs OWNER NEEDED and stops
# restarting that runner until restarts age out of the window.
#
# Writes C:\Users\you\Development\Syncyard\watchdog\status.json and appends to
# C:\Users\you\Development\Syncyard\watchdog\watchdog.log.
#
# ASCII only (Windows PowerShell 5.1 mis-parses UTF-8-no-BOM scripts
# containing non-ASCII characters - recorded fleet lesson).

$ErrorActionPreference = "Stop"

# --- Config: one entry per runner registration on this machine. -------
# Add the lane runner directories here at lane stand-up, e.g.
# "C:\Users\you\Development\Syncyard\runners\quick", "C:\Users\you\Development\Syncyard\runners\normal",
# "C:\Users\you\Development\Syncyard\runners\gpu".
$RunnerDirs = @(
    "C:\Users\you\Development\Syncyard\runners\long"
)

$StatusDir       = "C:\Users\you\Development\Syncyard\watchdog"
$StatusFile      = Join-Path $StatusDir "status.json"
$LogFile         = Join-Path $StatusDir "watchdog.log"
$RestartFile     = Join-Path $StatusDir "restarts.json"
$RestartCap      = 3        # per runner per rolling hour
$RestartWindowMin = 60

function Write-WatchLog($Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line
}

function Get-RestartHistory {
    if (Test-Path $RestartFile) {
        try {
            $raw = Get-Content $RestartFile -Raw
            if ($raw) { return ($raw | ConvertFrom-Json) }
        } catch { }
    }
    return New-Object PSObject
}

function Test-ListenerAlive($Dir) {
    # Listener identity is the exact runner directory (
    # red team H14): the listener exe's own directory must
    # EQUAL the runner directory - never a path-prefix match,
    # under which the listener of a longer sibling directory
    # (for example runners\normal2) also counted as the shorter
    # directory's (runners\normal) listener, and a dead runner
    # was reported alive.
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'Runner.Listener.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        if ($p.ExecutablePath -and
            (Split-Path -Parent $p.ExecutablePath).Equals($Dir, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

if (-not (Test-Path $StatusDir)) {
    New-Item -ItemType Directory -Path $StatusDir -Force | Out-Null
}

$history = Get-RestartHistory
$cutoff = (Get-Date).AddMinutes(-$RestartWindowMin)
$results = @()

foreach ($dir in $RunnerDirs) {
    $entry = New-Object PSObject
    $entry | Add-Member NoteProperty dir $dir
    $entry | Add-Member NoteProperty listener_alive $false
    $entry | Add-Member NoteProperty last_action "none"
    $entry | Add-Member NoteProperty owner_needed $false
    $entry | Add-Member NoteProperty checked_at (Get-Date -Format "yyyy-MM-ddTHH:mm:ss")

    try {
        $runCmd = Join-Path $dir "run.cmd"
        if (-not (Test-Path $runCmd)) {
            $entry.last_action = "run.cmd missing - runner not installed here"
            Write-WatchLog ("{0}: run.cmd missing, skipping" -f $dir)
            $results += $entry
            continue
        }

        $alive = Test-ListenerAlive $dir
        $entry.listener_alive = $alive
        if ($alive) {
            $entry.last_action = "none (listener alive)"
            $results += $entry
            continue
        }

        # Listener is down. Apply the restart cap over the rolling window.
        $stamps = @()
        $prop = $history.PSObject.Properties[$dir]
        if ($prop) {
            foreach ($s in @($prop.Value)) {
                $t = [DateTime]::Parse($s)
                if ($t -gt $cutoff) { $stamps += $s }
            }
        }
        if ($stamps.Count -ge $RestartCap) {
            $entry.owner_needed = $true
            $entry.last_action = "OWNER NEEDED - restart cap reached ($RestartCap per hour)"
            Write-WatchLog ("{0}: listener down, restart cap reached - OWNER NEEDED" -f $dir)
            $results += $entry
            continue
        }

        Write-WatchLog ("{0}: listener down, starting run.cmd" -f $dir)
        Start-Process -FilePath $runCmd -WorkingDirectory $dir -WindowStyle Hidden
        $stamps += (Get-Date -Format "yyyy-MM-ddTHH:mm:ss")
        if ($history.PSObject.Properties[$dir]) {
            $history.$dir = $stamps
        } else {
            $history | Add-Member NoteProperty $dir $stamps
        }
        $entry.last_action = "restarted listener"
        Write-WatchLog ("{0}: restart issued ({1} in window)" -f $dir, $stamps.Count)
    } catch {
        $entry.last_action = "error: $($_.Exception.Message)"
        Write-WatchLog ("{0}: error {1}" -f $dir, $_.Exception.Message)
    }
    $results += $entry
}

$history | ConvertTo-Json | Set-Content -Path $RestartFile
$results | ConvertTo-Json | Set-Content -Path $StatusFile

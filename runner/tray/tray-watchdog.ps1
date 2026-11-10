#Requires -Version 7.0
# tray-watchdog.ps1 - the packaged Syncyard tray watchdog (spec 025).
#
# One-shot: a scheduled task runs this every minute
# (runner/tray/tray-watchdog-task.xml renders the task). Each
# run makes exactly one decision and logs it:
#
#   exe missing        -> write the owner-needed marker, launch
#                         nothing. A quarantined or half-swapped
#                         exe is an alarm, never a relaunch loop.
#   stopped-by-user    -> the tray's Exit wrote its marker and
#                         no tray has started since: stand down.
#                         Exit means exit (spec 023 FR-007).
#   tray process alive -> stand down.
#   past relaunch cap  -> write the owner-needed marker, launch
#                         nothing. A crash loop becomes an alarm.
#   otherwise          -> relaunch the tray, record the launch.
#
# State lives beside the tray log (the SyncyardTray directory
# under %LOCALAPPDATA%): tray-watchdog-state.json carries the
# relaunch timestamps the cap counts, tray-watchdog.log is the
# decision log, tray-watchdog-owner-needed.json is the marker
# the gatekeeper-visible surfaces (and the owner) read.
#
# The tray process is identified by its full exe path only —
# the fleet's standing rule for process identification.
#
# Exit codes: 0 = decision made (including stand-downs),
# 2 = a relaunch was attempted and the process did not appear.
[CmdletBinding()]
param(
    [string]$TrayExe =
        "$env:LOCALAPPDATA\Programs\SyncyardTray\SyncyardTray.exe",
    [string]$StateDir =
        "$env:LOCALAPPDATA\SyncyardTray"
)

$ErrorActionPreference = "Stop"

$script:LogPath = Join-Path $StateDir "tray-watchdog.log"
$script:StatePath = Join-Path $StateDir "tray-watchdog-state.json"
$script:MarkerPath = Join-Path $StateDir "tray-watchdog-owner-needed.json"
$script:RelaunchCapPerHour = 5

function Write-WatchLog([string]$Msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Msg
    try {
        Add-Content -LiteralPath $script:LogPath -Value $line `
            -Encoding UTF8
    } catch { }
}

function Write-OwnerNeeded([string]$Reason) {
    $rec = [ordered]@{
        ts     = (Get-Date).ToUniversalTime().ToString(
            "yyyy-MM-ddTHH:mm:ssZ")
        reason = $Reason
        exe    = $TrayExe
    }
    try {
        ($rec | ConvertTo-Json) | Set-Content -LiteralPath `
            $script:MarkerPath -Encoding UTF8
    } catch { }
    Write-WatchLog "OWNER-NEEDED: $Reason"
}

function Clear-OwnerNeeded {
    try {
        if (Test-Path -LiteralPath $script:MarkerPath) {
            Remove-Item -LiteralPath $script:MarkerPath -Force
        }
    } catch { }
}

function Get-RelaunchTimes {
    if (Test-Path -LiteralPath $script:StatePath) {
        try {
            $st = Get-Content -LiteralPath $script:StatePath -Raw |
                ConvertFrom-Json
            if ($st.relaunches) { return @($st.relaunches) }
        } catch { }
    }
    return @()
}

function Save-RelaunchTimes([object[]]$Times) {
    $rec = [ordered]@{ relaunches = $Times }
    try {
        ($rec | ConvertTo-Json) | Set-Content -LiteralPath `
            $script:StatePath -Encoding UTF8
    } catch { }
}

function Test-TrayRunning {
    $procs = Get-CimInstance Win32_Process -Filter "Name='SyncyardTray.exe'" `
        -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        if ($p.ExecutablePath -and
            $p.ExecutablePath.Equals($TrayExe,
                [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

try {
    if (-not (Test-Path -LiteralPath $StateDir)) {
        New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    }

    if (-not (Test-Path -LiteralPath $TrayExe)) {
        Write-OwnerNeeded "tray exe missing at $TrayExe"
        exit 0
    }

    $stoppedMarker = Join-Path $StateDir "tray-stopped-by-user.json"
    if ((Test-Path -LiteralPath $stoppedMarker) -and
        -not (Test-TrayRunning)) {
        Write-WatchLog "stand down: tray stopped by user (marker stands)"
        exit 0
    }

    if (Test-TrayRunning) {
        Clear-OwnerNeeded
        exit 0
    }

    $nowUtc = (Get-Date).ToUniversalTime()
    $cutoff = $nowUtc.AddHours(-1).ToString("yyyy-MM-ddTHH:mm:ssZ")
    $recent = @(Get-RelaunchTimes | Where-Object { $_ -ge $cutoff })
    if ($recent.Count -ge $script:RelaunchCapPerHour) {
        Write-OwnerNeeded ("relaunch cap reached ({0} in the last " +
            "hour); the tray is crash-looping or cannot stay up" `
            -f $recent.Count)
        Save-RelaunchTimes $recent
        exit 0
    }

    Write-WatchLog "relaunching tray (no process for $TrayExe)"
    $proc = Start-Process -FilePath $TrayExe -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 3
    if (Test-TrayRunning) {
        $recent += $nowUtc.ToString("yyyy-MM-ddTHH:mm:ssZ")
        Save-RelaunchTimes $recent
        Clear-OwnerNeeded
        Write-WatchLog ("relaunched tray pid={0} (relaunches in " +
            "the last hour: {1})" -f $proc.Id, $recent.Count)
        exit 0
    }
    Write-WatchLog "relaunch attempted but no tray process appeared"
    exit 2
} catch {
    Write-WatchLog "watchdog error: $($_.Exception.Message)"
    exit 0
}

#Requires -Version 7.0
# setup-runner.ps1 - Syncyard runner setup (version 0.1.1)
#
# Downloads the GitHub Actions runner package, registers ONE runner
# against the Syncyard repository, and (with -Start) launches it as a
# user-window process under the signed-in user - NOT as a Windows
# service. One runner per directory; run this script once per lane.
#
# TOKEN HANDLING - read before use:
#   -RegistrationToken must be a fresh runner registration token for
#   the repository (they expire in about an hour). Fetch it at setup
#   time, for example:
#     gh api -X POST repos/AXIOVEX/syncyard/actions/runners/registration-token --jq .token
#   This script never writes the token to disk and never prints it.
#   (The runner's own config stores its credentials next to the runner;
#   that is the runner's normal mechanism.)
#   Never commit a token, and never paste one into a shared log.
#
# Usage:
#   .\setup-runner.ps1 -Name "myhost-quick" -Labels "lane-quick" `
#       -RunnerDir "C:\actions-runner-quick" -RegistrationToken <token> -Start
#
# ASCII only (Windows PowerShell 5.1 mis-parses UTF-8-no-BOM scripts
# containing non-ASCII characters).

param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    [Parameter(Mandatory = $true)]
    [string[]]$Labels,

    [Parameter(Mandatory = $true)]
    [string]$RunnerDir,

    [Parameter(Mandatory = $true)]
    [string]$RegistrationToken,

    [string]$RepoUrl = "https://github.com/AXIOVEX/syncyard",

    [string]$RunnerVersion = "2.338.2",

    [switch]$Start
)

$ErrorActionPreference = "Stop"

# The runner always carries the built-in labels; fleet labels (for
# example the lane label) are added on top. "desktop" and "gpu" are
# fleet conventions from the founding deployment; keep or adjust them
# in one place here.
$FleetLabels = @("desktop", "gpu")
$AllLabels = (@("self-hosted", "Windows", "X64") + $FleetLabels + $Labels) -join ","

Write-Host "Syncyard runner setup"
Write-Host "  name    : $Name"
Write-Host "  dir     : $RunnerDir"
Write-Host "  repo    : $RepoUrl"
Write-Host "  labels  : $AllLabels"
Write-Host "  version : $RunnerVersion"

if (-not (Test-Path $RunnerDir)) {
    New-Item -ItemType Directory -Path $RunnerDir -Force | Out-Null
}

$zipName = "actions-runner-win-x64-$RunnerVersion.zip"
$zipPath = Join-Path $RunnerDir $zipName
$configCmd = Join-Path $RunnerDir "config.cmd"

if (-not (Test-Path $configCmd)) {
    $url = "https://github.com/actions/runner/releases/download/v$RunnerVersion/$zipName"
    Write-Host "Downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $zipPath
    Write-Host "Extracting into $RunnerDir"
    Expand-Archive -Path $zipPath -DestinationPath $RunnerDir -Force
    Remove-Item $zipPath -Force
} else {
    Write-Host "Runner package already present in $RunnerDir - skipping download."
}

Write-Host "Registering runner (token is used once and never stored by this script)"
Push-Location $RunnerDir
try {
    & .\config.cmd --unattended --replace --url $RepoUrl --token $RegistrationToken --name $Name --labels $AllLabels --work "_work"
    if ($LASTEXITCODE -ne 0) { throw "config.cmd exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}

Write-Host "Registered: $Name"

if ($Start) {
    # Start as a user-window process under the signed-in user. Do NOT
    # start the runner from inside an Actions job - job teardown kills
    # child processes. A scheduled-task watchdog (runner-watchdog.ps1)
    # keeps the listener alive afterwards; see SETUP.md.
    $runCmd = Join-Path $RunnerDir "run.cmd"
    Start-Process -FilePath $runCmd -WorkingDirectory $RunnerDir -WindowStyle Hidden
    Write-Host "Started run.cmd hidden (no console window). Stop it via Task Manager (Runner.Listener) or the watchdog's OWNER NEEDED path."
} else {
    Write-Host "Not started. Start it later with:  & `"$RunnerDir\run.cmd`""
}

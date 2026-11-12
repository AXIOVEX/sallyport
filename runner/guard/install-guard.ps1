#Requires -Version 7.0
<#
.SYNOPSIS
  Install and start the Syncyard host shutdown guard (spec 011 Phase 3).

.DESCRIPTION
  Downloads SyncyardGuard.cs from the Syncyard repo, compiles it with
  the csc.exe that ships with Windows (no SDK install), installs the
  exe under C:\Users\you\Development\Syncyard\guard, writes the
  default guard-settings.json if none exists (an existing settings
  file is never overwritten), registers the exe in the HKCU Run key
  so it starts at logon, and launches it now. The guard is
  windowless by construction (a winexe whose one form is never
  shown) and has no user interface and no exit command.

  This script is the owner's install step, run at the machine
  (the spec 011 T006/T008 field sequence): pause the fleet first,
  install, then let the gatekeeper's `guard beat` line in
  `gk status` prove the guard is alive. Nothing here needs admin.

.EXAMPLE
  .\install-guard.ps1
  .\install-guard.ps1 -Ref spec/011-drain-and-guard
#>
[CmdletBinding()]
param(
    [string]$Ref = "main",
    [string]$InstallDir = "$env:USERPROFILE\Development\Syncyard\guard"
)

$ErrorActionPreference = "Stop"
$repoRaw = "https://raw.githubusercontent.com/electrohire/syncyard/$Ref/runner/guard"

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
$src = Join-Path $InstallDir "SyncyardGuard.cs"
$exe = Join-Path $InstallDir "SyncyardGuard.exe"

Write-Host "Fetching SyncyardGuard.cs (ref $Ref)"
Invoke-WebRequest "$repoRaw/SyncyardGuard.cs" -OutFile $src

$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    $csc = "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path $csc)) { throw "csc.exe not found - cannot compile." }

Write-Host "Compiling with $csc"
& $csc /nologo /target:winexe /out:$exe `
    /reference:System.Windows.Forms.dll $src
if ($LASTEXITCODE -ne 0) { throw "csc exited with code $LASTEXITCODE" }

$settings = Join-Path $InstallDir "guard-settings.json"
if (-not (Test-Path $settings)) {
    Write-Host "Writing default guard-settings.json (fleet values)"
    @'
{
  "host": "desktop",
  "window_s": 120,
  "distro": "Ubuntu-24.04",
  "syncyard_dir": "C:\\Users\\you\\Development\\Syncyard",
  "holds_dir_win": "C:\\Users\\you\\Development\\Syncyard\\holds",
  "holds_dir_wsl": "/home/you/.syncyard/holds"
}
'@ | Set-Content -Path $settings -Encoding UTF8
} else {
    Write-Host "guard-settings.json exists - left untouched"
}

Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
    -Name "SyncyardGuard" -Value "`"$exe`""

Write-Host "Launching the guard (windowless)"
Start-Process -FilePath $exe -WorkingDirectory $InstallDir -WindowStyle Hidden

$size = (Get-Item $exe).Length
Write-Host "GUARD_INSTALLED exe=$exe bytes=$size run_key=HKCU\...\Run\SyncyardGuard"

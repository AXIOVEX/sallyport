<#
.SYNOPSIS
  Install and start the Syncyard tray supervisor (spec 007).

.DESCRIPTION
  Downloads SyncyardTray.cs from the Syncyard repo, compiles it with
  the csc.exe that ships with Windows (no SDK install), installs the
  exe under C:\Users\trist\Development\Syncyard\tray, registers it in
  the HKCU Run key so it starts at logon with no window, and launches
  it now. The tray starts every registered runner hidden, restarts a
  dead runner (capped, then it asks for the owner), and hides stray
  runner console windows.

  Run in a regular PowerShell window. Nothing here needs admin.

.EXAMPLE
  .\install-tray.ps1
  .\install-tray.ps1 -Ref feat/windowless-tray-supervisor
#>
[CmdletBinding()]
param(
    [string]$Ref = "main",
    [string]$InstallDir = "$env:USERPROFILE\Development\Syncyard\tray"
)

$ErrorActionPreference = "Stop"
$repoRaw = "https://raw.githubusercontent.com/AXIOVEX/syncyard/$Ref/runner/tray"

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
$src = Join-Path $InstallDir "SyncyardTray.cs"
$exe = Join-Path $InstallDir "SyncyardTray.exe"

Write-Host "Fetching SyncyardTray.cs (ref $Ref)"
Invoke-WebRequest "$repoRaw/SyncyardTray.cs" -OutFile $src
Invoke-WebRequest "$repoRaw/logo.ico" -OutFile (Join-Path $InstallDir "logo.ico")
$repoRoot = "https://raw.githubusercontent.com/AXIOVEX/syncyard/$Ref"
Invoke-WebRequest "$repoRoot/assets/logo.png" -OutFile (Join-Path $InstallDir "logo.png")

$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    $csc = "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path $csc)) { throw "csc.exe not found - cannot compile." }

Write-Host "Compiling with $csc"
& $csc /nologo /target:winexe /out:$exe `
    /win32icon:(Join-Path $InstallDir "logo.ico") `
    /resource:(Join-Path $InstallDir "logo.png"),SyncyardLogo.png `
    /reference:System.Windows.Forms.dll `
    /reference:System.Drawing.dll $src
if ($LASTEXITCODE -ne 0) { throw "csc exited with code $LASTEXITCODE" }

Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
    -Name "SyncyardTray" -Value "`"$exe`""

Write-Host "Launching the tray (windowless)"
Start-Process -FilePath $exe -WorkingDirectory $InstallDir -WindowStyle Hidden

$size = (Get-Item $exe).Length
Write-Host "TRAY_INSTALLED exe=$exe bytes=$size run_key=HKCU\...\Run\SyncyardTray"

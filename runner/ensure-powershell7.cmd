@echo off
rem ============================================================================
rem ensure-powershell7.cmd - Syncyard PowerShell 7 bootstrap (owner order
rem 2026-11-08: Syncyard requires PowerShell 7 on every Windows host;
rem Windows PowerShell 5.1 is not supported anywhere in Syncyard).
rem
rem What it does:
rem   1. Checks for PowerShell 7 (pwsh.exe) at the canonical install path,
rem      then anywhere on PATH.
rem   2. If found and version >= 7 / edition Core: prints SYNCYARD_PS7_OK
rem      and exits 0. Nothing else happens.
rem   3. If missing: ASKS (prompt, default yes), then installs:
rem        a. winget install Microsoft.PowerShell (preferred), or
rem        b. fallback: downloads the pinned official MSI from the
rem           PowerShell GitHub release, verifies its SHA256 against the
rem           release's own hashes.sha256, and installs it with msiexec.
rem   4. Re-checks and prints the verdict.
rem
rem Usage:
rem   ensure-powershell7.cmd           interactive: check, ask, install
rem   ensure-powershell7.cmd /y        unattended yes (no prompt)
rem   ensure-powershell7.cmd /check    check only, never install
rem
rem Exit codes: 0 = PowerShell 7 present and verified
rem             10 = missing and not installed (check mode, or declined)
rem             2  = install attempted but failed
rem             3  = install ran but verification still failed
rem
rem This script is cmd on purpose: it runs BEFORE PowerShell 7 exists on
rem the machine, so it cannot itself be a .ps1. It never invokes
rem Windows PowerShell 5.1. Installs are interactive owner-side actions
rem (an Actions job never installs the shell it runs under); the
rem installers may show their own UAC prompt, which is expected here.
rem ASCII only.
rem ============================================================================
setlocal EnableDelayedExpansion

set "MODE=ask"
if /i "%~1"=="/y" set "MODE=yes"
if /i "%~1"=="-y" set "MODE=yes"
if /i "%~1"=="/check" set "MODE=check"
if /i "%~1"=="--check" set "MODE=check"

set "PS7_MSI_VERSION=7.4.6"

call :find_ps7
if defined PS7_EXE goto :verify_found
goto :missing

:verify_found
"%PS7_EXE%" -NoProfile -Command "if ($PSVersionTable.PSEdition -ne 'Core' -or $PSVersionTable.PSVersion.Major -lt 7) { exit 1 }"
if errorlevel 1 goto :missing
for /f "delims=" %%V in ('"%PS7_EXE%" -NoProfile -Command "$PSVersionTable.PSVersion.ToString()"') do set "PS7_VER=%%V"
echo SYNCYARD_PS7_OK version=%PS7_VER% path=%PS7_EXE%
exit /b 0

:missing
echo SYNCYARD_PS7_MISSING: PowerShell 7 (pwsh.exe) is not installed on this machine.
echo Syncyard requires PowerShell 7 on every Windows host. Windows PowerShell 5.1 is not supported.
if "%MODE%"=="check" exit /b 10
if "%MODE%"=="yes" goto :install
set "ANSWER="
set /p "ANSWER=Install PowerShell 7 now? [Y/n] "
if /i "%ANSWER%"=="n" (
  echo Not installed. Syncyard jobs will fail closed on this host until PowerShell 7 is installed.
  exit /b 10
)
goto :install

:install
where winget >nul 2>&1
if not errorlevel 1 goto :install_winget
goto :install_msi

:install_winget
echo Installing PowerShell 7 with winget (Microsoft.PowerShell)...
winget install --id Microsoft.PowerShell --source winget --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo winget install failed, falling back to the MSI route.
  goto :install_msi
)
goto :recheck

:install_msi
set "MSI_NAME=PowerShell-%PS7_MSI_VERSION%-win-x64.msi"
set "MSI_URL=https://github.com/PowerShell/PowerShell/releases/download/v%PS7_MSI_VERSION%/%MSI_NAME%"
set "HASH_URL=https://github.com/PowerShell/PowerShell/releases/download/v%PS7_MSI_VERSION%/hashes.sha256"
set "WORK=%TEMP%\syncyard-ps7"
if not exist "%WORK%" mkdir "%WORK%"
echo Downloading %MSI_URL% ...
curl.exe -fL -o "%WORK%\%MSI_NAME%" "%MSI_URL%"
if errorlevel 1 (
  echo SYNCYARD_PS7_INSTALL_FAIL: download of the PowerShell 7 MSI failed.
  exit /b 2
)
curl.exe -fL -o "%WORK%\hashes.sha256" "%HASH_URL%"
if errorlevel 1 (
  echo SYNCYARD_PS7_INSTALL_FAIL: download of the release hash list failed.
  exit /b 2
)
set "EXPECTED="
for /f "tokens=1" %%H in ('findstr /i /c:"%MSI_NAME%" "%WORK%\hashes.sha256"') do set "EXPECTED=%%H"
if not defined EXPECTED (
  echo SYNCYARD_PS7_INSTALL_FAIL: the MSI name was not found in the release hash list.
  exit /b 2
)
set "ACTUAL="
for /f "skip=1 tokens=*" %%H in ('certutil -hashfile "%WORK%\%MSI_NAME%" SHA256') do if not defined ACTUAL set "ACTUAL=%%H"
set "ACTUAL=%ACTUAL: =%"
if /i not "%ACTUAL%"=="%EXPECTED%" (
  echo SYNCYARD_PS7_INSTALL_FAIL: SHA256 mismatch on the downloaded MSI. Refusing to install.
  echo   expected: %EXPECTED%
  echo   actual:   %ACTUAL%
  exit /b 2
)
echo SHA256 verified. Installing %MSI_NAME% ...
msiexec /i "%WORK%\%MSI_NAME%" /passive /norestart REGISTER_MANIFEST=1
if errorlevel 1 (
  echo SYNCYARD_PS7_INSTALL_FAIL: msiexec returned an error.
  exit /b 2
)
goto :recheck

:recheck
call :find_ps7
if not defined PS7_EXE (
  echo SYNCYARD_PS7_INSTALL_FAIL: install ran but pwsh.exe is still not found.
  exit /b 3
)
"%PS7_EXE%" -NoProfile -Command "if ($PSVersionTable.PSEdition -ne 'Core' -or $PSVersionTable.PSVersion.Major -lt 7) { exit 1 }"
if errorlevel 1 (
  echo SYNCYARD_PS7_INSTALL_FAIL: pwsh.exe found but it is not PowerShell 7 Core.
  exit /b 3
)
for /f "delims=" %%V in ('"%PS7_EXE%" -NoProfile -Command "$PSVersionTable.PSVersion.ToString()"') do set "PS7_VER=%%V"
echo SYNCYARD_PS7_OK version=%PS7_VER% path=%PS7_EXE%
echo NOTE: runners and scheduled tasks started before this install may need a restart to pick up the new PATH.
exit /b 0

:find_ps7
set "PS7_EXE="
if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "PS7_EXE=%ProgramFiles%\PowerShell\7\pwsh.exe"
if defined PS7_EXE goto :eof
for /f "delims=" %%P in ('where pwsh 2^>nul') do if not defined PS7_EXE set "PS7_EXE=%%P"
goto :eof

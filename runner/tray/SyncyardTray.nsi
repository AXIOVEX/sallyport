; SyncyardTray.nsi - NSIS installer for the Syncyard Tray supervisor.
; Spec: electrohire/syncyard spec 007 (windowless supervision).
;
; Per-user install (no admin): files under
; %LOCALAPPDATA%\Programs\SyncyardTray, autostart via the HKCU Run
; key, a Start Menu shortcut, and a registered uninstaller.
; Silent install supported:  SyncyardTray-Setup-0.1.0.exe /S
; Build with:  makensis SyncyardTray.nsi   (expects SyncyardTray.exe,
; logo.png, logo.ico next to this script)

Unicode true
!include "MUI2.nsh"

Name "Syncyard Tray"
OutFile "SyncyardTray-Setup-0.1.0.exe"
InstallDir "$LOCALAPPDATA\Programs\SyncyardTray"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!define PRODUCT "Syncyard Tray"
!define VERSION "0.4.4.0"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\SyncyardTray"

VIProductVersion "0.4.4.0"
VIAddVersionKey "ProductName" "Syncyard Tray"
VIAddVersionKey "FileDescription" "Syncyard runner tray supervisor (spec 007)"
VIAddVersionKey "FileVersion" "0.4.4.0"
VIAddVersionKey "CompanyName" "ElectroHire"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Syncyard Tray" SecMain
  SetOutPath "$INSTDIR"

  ; Stop a running tray so its files can be replaced. The runners it
  ; supervises are independent processes and keep running.
  nsExec::Exec 'taskkill /IM SyncyardTray.exe /F'
  Pop $0
  Sleep 500

  File "SyncyardTray.exe"
  File "logo.png"
  File "logo.ico"

  ; The per-machine profile (spec 028 FR-002): this machine's
  ; own paths, stale bounds, host name, and lane set, as data
  ; beside the exe. The build machine picks the profile for
  ; the machine being installed (profiles\<machine>\ in the
  ; repo; see the build README) and stages it next to this
  ; script as tray-profile.json before makensis runs; a
  ; release built without one installs none, and the tray
  ; then falls back to its settings and compiled defaults.
  ; IfFileExists keeps a staged profile optional and keeps
  ; an absent one from failing the install.
  IfFileExists "$EXEDIR\tray-profile.json" 0 +2
  File "tray-profile.json"

  ; Autostart at logon, windowless by design (winexe, no console).
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
    "SyncyardTray" '"$INSTDIR\SyncyardTray.exe"'

  ; Start Menu shortcut.
  CreateDirectory "$SMPROGRAMS\Syncyard"
  CreateShortcut "$SMPROGRAMS\Syncyard\Syncyard Tray.lnk" \
    "$INSTDIR\SyncyardTray.exe" "" "$INSTDIR\logo.ico"

  ; Uninstaller + Add/Remove Programs entry.
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "Syncyard Tray"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "ElectroHire"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\logo.ico"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  nsExec::Exec 'taskkill /IM SyncyardTray.exe /F'
  Pop $0
  Sleep 500

  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "SyncyardTray"
  DeleteRegKey HKCU "${UNINST_KEY}"

  Delete "$SMPROGRAMS\Syncyard\Syncyard Tray.lnk"
  RMDir "$SMPROGRAMS\Syncyard"

  Delete "$INSTDIR\SyncyardTray.exe"
  Delete "$INSTDIR\logo.png"
  Delete "$INSTDIR\logo.ico"
  Delete "$INSTDIR\tray-profile.json"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd

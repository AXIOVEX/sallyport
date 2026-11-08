# Syncyard conformance-kit payload (PowerShell) — spec 034 section 7.
# Emits the full progress line set to $env:SYNCYARD_PROGRESS_FILE
# and exits 0. The kit's three payloads (bash, Python,
# PowerShell) must produce IDENTICAL parsed event streams.
$ErrorActionPreference = 'Stop'
if (-not $env:SYNCYARD_PROGRESS_FILE) {
    Write-Error 'SYNCYARD_PROGRESS_FILE must be set by the agent'
    exit 2
}
$lines = @(
    'STARTED',
    'MARKER kit-start',
    'PROGRESS 1/2 first-half',
    'HEARTBEAT',
    'PROGRESS 2/2 second-half',
    'COMPLETED done'
)
foreach ($line in $lines) {
    Add-Content -Path $env:SYNCYARD_PROGRESS_FILE -Value $line -Encoding utf8
}
Write-Output 'conformance payload (powershell): full line set emitted'
exit 0

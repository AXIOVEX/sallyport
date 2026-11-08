#!/usr/bin/env bash
# Syncyard conformance-kit payload (bash) — spec 034 section 7.
# Emits the full progress line set to $SYNCYARD_PROGRESS_FILE
# and exits 0. The kit's three payloads (bash, Python,
# PowerShell) must produce IDENTICAL parsed event streams.
set -u
: "${SYNCYARD_PROGRESS_FILE:?SYNCYARD_PROGRESS_FILE must be set by the agent}"
{
  printf 'STARTED\n'
  printf 'MARKER kit-start\n'
  printf 'PROGRESS 1/2 first-half\n'
  printf 'HEARTBEAT\n'
  printf 'PROGRESS 2/2 second-half\n'
  printf 'COMPLETED done\n'
} >> "$SYNCYARD_PROGRESS_FILE"
echo "conformance payload (bash): full line set emitted"
exit 0

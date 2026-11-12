#!/bin/bash
# desktop-run.sh - Syncyard client: run a PowerShell command on a self-hosted
# runner machine via GitHub Actions (electrohire/syncyard, workflow run.yml).
#
# PREFERRED ROUTE: submit jobs through the gatekeeper (gatekeeper/gk.py).
# The gatekeeper enforces the shared API budget, duplicate checks, and lane
# discipline for the whole fleet. This client dispatches and watches
# directly, which spends API calls outside that budget - keep it for
# interactive one-offs only, and never leave it watching unattended (its
# watch polls at gh's default fast interval; see docs/STANDARD.md).
#
# Usage: desktop-run.sh "<powershell command>" [cwd]
#   cwd defaults to C:\Users\trist
# Companion: desktop-wsl.sh for bash inside WSL2.
REPO="electrohire/syncyard"
CMD="$1"
CWD="${2:-C:\\Users\\trist}"
if [ -z "$CMD" ]; then echo "Usage: $0 \"<powershell command>\" [cwd]" >&2; exit 2; fi
B64=$(printf '%s' "$CMD" | base64 -w0)
BEFORE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
gh workflow run run.yml -R "$REPO" -f cmd_b64="$B64" -f cwd="$CWD" || exit 1
ID=""
for _ in $(seq 1 40); do
  ID=$(gh run list -R "$REPO" --workflow run.yml -L 3 --json databaseId,createdAt \
        --jq "[.[] | select(.createdAt >= \"$BEFORE\")] | first | .databaseId // empty" 2>/dev/null)
  [ -n "$ID" ] && break
  sleep 4
done
if [ -z "$ID" ]; then echo "ERROR: dispatched run not found (is the self-hosted runner online?)" >&2; exit 1; fi
echo "[dispatched run $ID — https://github.com/$REPO/actions/runs/$ID]" >&2
gh run watch "$ID" -R "$REPO" --exit-status >/dev/null 2>&1
CONCLUSION=$(gh run view "$ID" -R "$REPO" --json conclusion --jq .conclusion)
echo "[run $ID conclusion: $CONCLUSION]"
gh run view "$ID" -R "$REPO" --log 2>/dev/null | grep -P "\tExecute\t" | sed -E 's/^.*\tExecute\t[0-9T:.Z-]+ //'

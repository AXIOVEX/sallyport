#!/usr/bin/env bash
# install-publisher.sh - install the Syncyard fleet-status
# publisher (gatekeeper/fleet_status.py) as a
# systemd system timer, from this checkout.
#
# Two strict phases:
#   1. preflight - every declared dependency is checked and a
#      missing one refuses BY NAME before anything is written
#      (no half-installs);
#   2. install - the root-only env file captured from THIS
#      shell (values are never printed, only variable names),
#      the root-owned publisher copy with its sha256 asserted
#      against the checkout source, the repo units rendered
#      and installed, the timer enabled, and a first publish
#      proven from the publisher's own state record before
#      success is reported.
#
# Idempotent: re-running is the repair path; artifacts that
# already match are reported UNCHANGED and left byte-identical.
#
# Usage:
#   gatekeeper/install-publisher.sh [--gk-dir DIR]
#                                   [--channels a,b,c]
#   --gk-dir    the live gatekeeper state directory the
#               publisher reads (default: this script's own
#               directory, i.e. a checkout that is the live
#               copy; env FLEETPUB_GK_DIR)
#   --channels  comma-separated backchannel channel names;
#               the publisher publishes one fleet-status.json
#               per channel (default: syncyard-dev; env
#               FLEETPUB_CHANNELS)
#
# Sandbox overrides (the install battery uses these so a test
# install can never touch a live service):
#   FLEETPUB_ETC_DIR      install dir (default /etc/fleet-status-publish)
#   FLEETPUB_SYSTEMD_DIR  unit dir    (default /etc/systemd/system)
#   FLEETPUB_SYSTEMCTL    systemctl command (default: systemctl)
#
# Prints FLEETPUB_INSTALL_* markers. Exit codes:
#   0 installed and first publish gate-verified
#   2 preflight refusal (a declared dependency is missing)
#   3 an installed artifact failed its assertion
#   4 the first-publish gate failed (install NOT VERIFIED)

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GK_DIR="${FLEETPUB_GK_DIR:-$SCRIPT_DIR}"
CHANNELS="${FLEETPUB_CHANNELS:-syncyard-dev}"
ETC_DIR="${FLEETPUB_ETC_DIR:-/etc/fleet-status-publish}"
SYSTEMD_DIR="${FLEETPUB_SYSTEMD_DIR:-/etc/systemd/system}"
SYSTEMCTL="${FLEETPUB_SYSTEMCTL:-systemctl}"
SERVICE="fleet-status-publish.service"
TIMER="fleet-status-publish.timer"

while [ $# -gt 0 ]; do
    case "$1" in
        --gk-dir) GK_DIR="$2"; shift 2 ;;
        --channels) CHANNELS="$2"; shift 2 ;;
        *) echo "FLEETPUB_INSTALL_REFUSED argument $1" >&2; exit 2 ;;
    esac
done

# Channel names become contents paths and unit text; hold them
# to the backchannel naming rule (lowercase, digits, hyphens).
PATH_ARGS=""
OLD_IFS="$IFS"; IFS=','
for ch in $CHANNELS; do
    IFS="$OLD_IFS"
    if ! printf '%s' "$ch" | grep -Eq '^[a-z0-9][a-z0-9-]*$'; then
        echo "FLEETPUB_INSTALL_REFUSED channel-name $ch" >&2
        exit 2
    fi
    PATH_ARGS="$PATH_ARGS --path channels/$ch/fleet-status.json"
    IFS=','
done
IFS="$OLD_IFS"

# ------------------------------------------------------------------
# Phase 1: preflight. Nothing is written in this phase.
# ------------------------------------------------------------------
MISSING=""

preflight() {  # preflight <name> <ok:0|1>
    if [ "$2" -eq 0 ]; then
        echo "FLEETPUB_INSTALL_PREFLIGHT $1 ok"
    else
        echo "FLEETPUB_INSTALL_PREFLIGHT $1 MISSING"
        MISSING="$MISSING $1"
    fi
}

# python3, at the version floor the publisher's source needs.
ok=1
if command -v python3 >/dev/null 2>&1 && python3 -c \
    'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
    2>/dev/null; then ok=0; fi
preflight python3 "$ok"

# systemd: the systemctl command must resolve.
ok=1
if command -v "$SYSTEMCTL" >/dev/null 2>&1; then ok=0; fi
preflight systemd "$ok"

# coreutils: the tools this installer itself uses.
ok=1
if command -v install >/dev/null 2>&1 \
    && command -v sha256sum >/dev/null 2>&1; then ok=0; fi
preflight coreutils "$ok"

# write-access: target dirs the caller can already write (the
# sandbox case, checked first so a privileged helper never
# stands between the caller and its own directories), or root,
# or passwordless sudo for the real system paths.
PRIV=""
ok=1
can_write=0
for d in "$ETC_DIR" "$SYSTEMD_DIR"; do
    while [ ! -e "$d" ]; do d="$(dirname "$d")"; done
    [ -w "$d" ] && can_write=$((can_write + 1))
done
if [ "$can_write" = "2" ]; then
    ok=0
elif [ "$(id -u)" = "0" ]; then
    ok=0
elif sudo -n true 2>/dev/null; then
    PRIV="sudo"; ok=0
fi
preflight write-access "$ok"

# proxy-env: the egress proxy must be in this shell so it can
# be captured into the service env file.
ok=1
[ -n "${HTTPS_PROXY:-}" ] && ok=0
preflight proxy-env "$ok"

# github-token: a token in the environment, or an
# authenticated gh the publisher can fall back to.
ok=1
if [ -n "${GH_TOKEN:-}" ] || [ -n "${GITHUB_TOKEN:-}" ]; then
    ok=0
elif command -v gh >/dev/null 2>&1 \
    && [ -n "$(gh auth token 2>/dev/null)" ]; then
    ok=0
fi
preflight github-token "$ok"

# gk-dir: the gatekeeper state directory must already exist
# (the publisher reads queue/ and writes its state record
# there; creating it is the gatekeeper install's business).
ok=1
[ -d "$GK_DIR" ] && ok=0
preflight gk-dir "$ok"

# publisher-source: the publisher and both units, in this
# checkout. An install never reads artifacts from anywhere
# else.
ok=1
if [ -f "$SCRIPT_DIR/fleet_status.py" ] \
    && [ -f "$SCRIPT_DIR/systemd/$SERVICE" ] \
    && [ -f "$SCRIPT_DIR/systemd/$TIMER" ]; then ok=0; fi
preflight publisher-source "$ok"

if [ -n "$MISSING" ]; then
    echo "FLEETPUB_INSTALL_REFUSED dependencies missing:$MISSING" >&2
    echo "FLEETPUB_INSTALL_OK 0"
    exit 2
fi

# ------------------------------------------------------------------
# Phase 2: install.
# ------------------------------------------------------------------
OWN_ARGS=""
if [ "$(id -u)" = "0" ] || [ -n "$PRIV" ]; then
    OWN_ARGS="-o root -g root"
fi

STAGE="$(mktemp)"
trap 'rm -f "$STAGE"' EXIT

$PRIV mkdir -p "$ETC_DIR" "$SYSTEMD_DIR"

install_if_changed() {  # <stage> <dest> <mode> <label>
    local new_sha old_sha=""
    new_sha="$(sha256sum "$1" | awk '{print $1}')"
    [ -f "$2" ] && old_sha="$(sha256sum "$2" | awk '{print $1}')"
    if [ "$new_sha" = "$old_sha" ]; then
        echo "FLEETPUB_INSTALL_$4 $2 UNCHANGED sha256=$new_sha"
    else
        # shellcheck disable=SC2086
        $PRIV install -m "$3" $OWN_ARGS "$1" "$2"
        echo "FLEETPUB_INSTALL_$4 $2 sha256=$new_sha"
    fi
}

# Env capture: values from this shell, names only in output.
ENV_VARS="HTTPS_PROXY https_proxy HTTP_PROXY http_proxy \
NO_PROXY no_proxy GH_TOKEN GITHUB_TOKEN FLEET_STATUS_API_BASE"
: > "$STAGE"
CAPTURED=""
for var in $ENV_VARS; do
    val="${!var:-}"
    if [ -n "$val" ]; then
        printf '%s=%s\n' "$var" "$val" >> "$STAGE"
        CAPTURED="${CAPTURED:+$CAPTURED,}$var"
    fi
done
install_if_changed "$STAGE" "$ETC_DIR/env" 600 ENV
echo "FLEETPUB_INSTALL_ENV_VARS $CAPTURED"

# The publisher copy: root-owned, hash asserted either way.
SRC_SHA="$(sha256sum "$SCRIPT_DIR/fleet_status.py" | awk '{print $1}')"
cp "$SCRIPT_DIR/fleet_status.py" "$STAGE"
install_if_changed "$STAGE" "$ETC_DIR/fleet_status.py" 644 COPY
DST_SHA="$(sha256sum "$ETC_DIR/fleet_status.py" | awk '{print $1}')"
if [ "$SRC_SHA" != "$DST_SHA" ]; then
    echo "FLEETPUB_INSTALL_REFUSED copy-hash $SRC_SHA != $DST_SHA" >&2
    echo "FLEETPUB_INSTALL_OK 0"
    exit 3
fi

# The units: render the service template, install both.
sed_escape() { printf '%s' "$1" | sed 's/[&|]/\\&/g'; }
sed -e "s|@ETC_DIR@|$(sed_escape "$ETC_DIR")|g" \
    -e "s|@GK_DIR@|$(sed_escape "$GK_DIR")|g" \
    -e "s|@PATH_ARGS@|$(sed_escape "$PATH_ARGS")|g" \
    "$SCRIPT_DIR/systemd/$SERVICE" > "$STAGE"
if grep -Eq '@[A-Z_]+@' "$STAGE"; then
    echo "FLEETPUB_INSTALL_REFUSED unit-template unsubstituted token" >&2
    echo "FLEETPUB_INSTALL_OK 0"
    exit 3
fi
install_if_changed "$STAGE" "$SYSTEMD_DIR/$SERVICE" 644 UNIT
cp "$SCRIPT_DIR/systemd/$TIMER" "$STAGE"
install_if_changed "$STAGE" "$SYSTEMD_DIR/$TIMER" 644 UNIT

$PRIV "$SYSTEMCTL" daemon-reload
$PRIV "$SYSTEMCTL" enable --now "$TIMER"
echo "FLEETPUB_INSTALL_TIMER $TIMER enabled"

# ------------------------------------------------------------------
# First-publish gate: the service runs once, in the foreground,
# and the publish is proven from the publisher's state record.
# ------------------------------------------------------------------
GATE_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
$PRIV "$SYSTEMCTL" start "$SERVICE"
RESULT="$($PRIV "$SYSTEMCTL" show "$SERVICE" -p Result --value 2>/dev/null)"
STATUS="$($PRIV "$SYSTEMCTL" show "$SERVICE" -p ExecMainStatus --value 2>/dev/null)"
LAST_SUCCESS="$(python3 - "$GK_DIR/.fleet-status-publisher.json" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        print(json.load(f).get("last_success") or "")
except Exception:
    print("")
PYEOF
)"
if [ "$RESULT" = "success" ] && [ "$STATUS" = "0" ] \
    && [ -n "$LAST_SUCCESS" ] && [ ! "$LAST_SUCCESS" \< "$GATE_START" ]; then
    echo "FLEETPUB_INSTALL_GATE published $LAST_SUCCESS"
    echo "FLEETPUB_INSTALL_OK 1"
    exit 0
fi
echo "FLEETPUB_INSTALL_GATE FAILED result=$RESULT status=$STATUS last_success=$LAST_SUCCESS" >&2
journalctl -u "$SERVICE" -n 10 --no-pager >&2 2>/dev/null || true
echo "FLEETPUB_INSTALL_OK 0"
exit 4

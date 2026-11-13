#!/usr/bin/env bash
# install-backchannel.sh - install the Syncyard backchannel agent
# as a WSL systemd user service.
#
# Run INSIDE WSL2 (Ubuntu-24.04) as the WSL user on the Windows host.
# Idempotent: re-running refreshes the agent copy and the unit,
# reloads systemd, and restarts the service.
#
# Prints SYNCBC_INSTALL_* markers. Exiting / stopping the agent is
# done with: systemctl --user stop syncyard-backchannel.service
# (and the channel STOP file is the remote kill switch).

set -eu

BASE="${SYNCBC_BASE:-$HOME/syncyard-backchannel}"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/syncyard-backchannel.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$BASE" "$UNIT_DIR"

# Agent copy: when run from a checkout, install alongside the clone.
if [ -f "$SCRIPT_DIR/agent.sh" ] && [ "$SCRIPT_DIR/agent.sh" != "$BASE/agent.sh" ]; then
    cp "$SCRIPT_DIR/agent.sh" "$BASE/agent.sh"
fi
chmod +x "$BASE/agent.sh"
echo "SYNCBC_INSTALL_AGENT $BASE/agent.sh"

cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Syncyard failsafe backchannel agent (syncyard-dev channel)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/bin/bash $BASE/agent.sh
Restart=on-failure
RestartSec=15
Environment=SYNCBC_BASE=$BASE

[Install]
WantedBy=default.target
EOF
echo "SYNCBC_INSTALL_UNIT $UNIT_FILE"

systemctl --user daemon-reload
systemctl --user enable --now syncyard-backchannel.service

# Give the agent a few seconds to clone/pull and write a heartbeat.
heartbeat_state="pending"
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if [ -f "$BASE/repo/channels/syncyard-dev/heartbeat.json" ]; then
        heartbeat_state="written"
        break
    fi
    sleep 2
done

service_state="$(systemctl --user is-active syncyard-backchannel.service || true)"
echo "SYNCBC_INSTALL_SERVICE $service_state"
echo "SYNCBC_INSTALL_HEARTBEAT $heartbeat_state"
echo "SYNCBC_INSTALL_LOG $BASE/agent.log"
if [ "$service_state" = "active" ]; then
    echo "SYNCBC_INSTALL_OK 1"
else
    echo "SYNCBC_INSTALL_OK 0"
    echo "service not active; check: systemctl --user status syncyard-backchannel.service" >&2
    exit 1
fi

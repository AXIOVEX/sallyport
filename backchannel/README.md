# Syncyard failsafe backchannel — operator doc

Spec: `specs/009-failsafe-backchannel/spec.md`. Read that first for
the rules; this file is the how-to.

## What this is

A narrow break-glass channel for when Syncyard itself — the GitHub
Actions runners, the Syncyard tray, or gatekeeper dispatch — is
down or blocked. It replaces the terminated desktop-relay's
emergency role in a much narrower form.

- **Channel**: `syncyard-dev`, in the **private** repo
  `tbitcs/syncyard-backchannel`:
  `channels/syncyard-dev/cmd.json` (Desktop Access → desktop),
  `channels/syncyard-dev/out.json` (desktop → Desktop Access,
  matched by command id), and
  `channels/syncyard-dev/heartbeat.json` (desktop, every poll).
- **Desktop side**: `agent.sh`, running as the WSL systemd user
  service `syncyard-backchannel.service` under `tpierson` on
  TRISTEN-DESKTOP. It is independent of the runners, the tray, and
  the gatekeeper, syncs over git (no REST quota), polls about
  every 60 seconds, and logs to `~/syncyard-backchannel/agent.log`
  on the desktop.
- **VM side**: `bc.py` (this directory), Python stdlib only. It
  talks to the channel through the GitHub contents API, so it
  works even during VM write waves.

## Who may use it

**The Desktop Access chat
(`01e9a059-0626-4470-b012-2784f420fe5a`) only.** It is the sole
poster to `cmd.json` and the owner of the channel.

- No program job traffic, ever — not for convenience, not for
  speed, not because the queue is long. Programs use the
  gatekeeper in all circumstances.
- Other agents may read the heartbeat / status (that is part of
  their FR-005 duty, below) and must report problems to the
  Desktop Access chat. They never post commands.

## How the Desktop Access chat sends and checks

```bash
# Is the desktop side alive? (exit 3 = stale, report it)
python3 backchannel/bc.py heartbeat
python3 backchannel/bc.py status

# Send a command file (bash; may call '/mnt/c/Program Files/PowerShell/7/pwsh.exe' / /mnt/c/...)
python3 backchannel/bc.py send --file recovery-command.sh
# prints: BC_SENT id=<id> sha256=<hash>

# Wait for its out.json
python3 backchannel/bc.py wait <id>
```

Rules that `bc.py` enforces for you:

- A send refuses to overwrite an unacknowledged pending command
  (one whose id has no matching `out.json`) unless `--force` is
  passed. A refusal means the earlier command is still in flight
  — wait for it; do not force past it casually.
- Every command carries a unique id and the sha256 of its body,
  and a send is only reported after `cmd.json` has been read back
  and matched. A failed read-back is **not sent**, however the API
  call itself looked.
- The agent executes only a command id it has not executed
  before, and only when the body's sha256 matches the packed
  hash. Re-pushing an old `cmd.json` re-runs nothing.

## Periodic status-check duty (FR-005 — every agent)

Every agent that uses Syncyard:

1. Checks Syncyard status **before** submitting desktop work, and
   **at least once per hour** during an active desktop campaign.
2. Uses `gk status` when the gatekeeper is reachable; otherwise
   `bc.py heartbeat` / `bc.py status` (read-only).
3. Reports a stale heartbeat (older than 5 minutes), a down tray,
   or a down runner **to the Desktop Access chat** — and never
   works around it or improvises another route to the machine.

## Recovery use cases (FR-007)

Pre-written command shapes; adapt paths/ids, keep them read-only
unless the recovery itself is the act ordered.

1. **Restart the packaged tray in the owner's session** — the
   tray lives in the owner's interactive session, so start it via
   the one-shot scheduled task (the proven route), from a bash
   command calling PowerShell:

   ```bash
   '/mnt/c/Program Files/PowerShell/7/pwsh.exe' -NoProfile -Command "Start-ScheduledTask -TaskName 'SyncyardTrayStart'"
   '/mnt/c/Program Files/PowerShell/7/pwsh.exe' -NoProfile -Command "Get-Process SyncyardTray | Select-Object Id,Path"
   ```

   Verify from the second command: the process path is the
   packaged exe under
   `%LOCALAPPDATA%\Programs\SyncyardTray\`. Presence of the
   process is not the owner's icon read — report both separately.

2. **Read tray.log**:

   ```bash
   tail -n 100 "/mnt/c/Users/trist/AppData/Local/SyncyardTray/tray.log"
   ```

3. **Restart a runner lane** — stop the lane's listener, let the
   tray restart it (its normal job), or start it hidden from the
   lane directory if the tray itself is the casualty:

   ```bash
   '/mnt/c/Program Files/PowerShell/7/pwsh.exe' -NoProfile -Command "Get-Process -Name 'Runner.Listener' | Select-Object Id,Path"
   ```

   Then act on the specific lane only, and verify with a fresh
   identity/status check afterwards.

4. **Read-only network diagnostics** (the wired-interface case):

   ```bash
   '/mnt/c/Program Files/PowerShell/7/pwsh.exe' -NoProfile -Command "Get-NetAdapter | Select-Object Name,Status,LinkSpeed; Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias,IPAddress,PrefixOrigin; Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object InterfaceAlias,NextHop,RouteMetric,InterfaceMetric"
   ping -c 3 10.42.0.2 || true   # board leg, from WSL
   ```

   Diagnostics are read-only. Changing addresses, metrics, or
   DHCP state is elevated owner-session work: prepare the script,
   have the owner run it, and read the result back.

## STOP kill switch

Create `channels/syncyard-dev/STOP` in the backchannel repo (any
content) and the agent stops executing commands from its next
poll cycle. The heartbeat keeps advancing with `"stopped": true`,
so you can tell a halted agent from a dead one. Remove the file
to resume execution. Stopping the service itself is a desktop
act: `systemctl --user stop syncyard-backchannel.service`.

## Job protection (FR-006)

Any Syncyard change — deploying this backchannel included —
starts with a gatekeeper queue snapshot (record count, aggregate
sha256 of the queue files, open-job list) and ends with
reconciliation against it. The open-job set after must equal the
set before, or every difference is named and explained. The
backchannel agent never writes gatekeeper queue records; its
queue access is read-only status unless a command from the
Desktop Access chat explicitly directs otherwise.

## Install (desktop, WSL)

From a checkout of electrohire/syncyard inside WSL2 on the desktop:

```bash
bash backchannel/install-backchannel.sh
# SYNCBC_INSTALL_AGENT / _UNIT / _SERVICE / _HEARTBEAT / _OK markers
systemctl --user status syncyard-backchannel.service
tail -f ~/syncyard-backchannel/agent.log
```

Note: for the service to run while no WSL session is open, the
owner's WSL user lingering must be enabled
(`loginctl enable-linger tpierson`) — an owner-session act,
checked at install time by the Desktop Access chat.

## Recovery

Failure triage and the four pre-written recovery cases (tray
restart, tray.log read, runner-lane restart, network
diagnostics) live in [RUNBOOK.md](RUNBOOK.md).

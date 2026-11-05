# Installing the backchannel agent on a machine

One agent per machine, one channel per machine. The first install
(TRISTEN-DESKTOP, 2026-10-29) owns the default channel
`channels/syncyard-dev` in the private repo
`tbitcs/syncyard-backchannel`. Every additional machine gets its own
channel name (TRISTEN-LAPTOP: `syncyard-laptop`) so two agents never
share cmd/out/heartbeat state. The Desktop Access chat owns every
channel and is the only operator of the client (`bc.py`).

## Prerequisites on the target machine

- WSL2 (Ubuntu-24.04) with systemd user services, the machine's WSL
  user matching the fleet record, and `git`, `python3`, `curl`
  present.
- Working network and DNS from inside WSL (check first:
  `getent hosts github.com` must answer; a wedged WSL network plane
  will look exactly like a dead agent later).
- Linger enabled for the WSL user (`loginctl show-user $USER` shows
  `Linger=yes`) so the agent survives logoff. Enabling linger for
  one's own user normally works unprivileged; if it refuses, the
  owner runs `sudo loginctl enable-linger <user>` once.

## 1. Machine credential (deploy key)

In the target's WSL:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
ssh-keygen -t ed25519 -N '' -C 'syncyard-backchannel agent <host>' -f ~/.ssh/bc-<host>
cat >> ~/.ssh/config <<'CFG'
Host github.com
  IdentityFile ~/.ssh/bc-<host>
  IdentitiesOnly yes
CFG
chmod 600 ~/.ssh/config ~/.ssh/bc-<host>
cat ~/.ssh/bc-<host>.pub
```

Hand the PUBLIC key to the Desktop Access chat, which registers it
as a read/write deploy key on `tbitcs/syncyard-backchannel` titled
for the machine. The private key never leaves the machine. (The
desktop's original install predates this convention and syncs over
HTTPS with the owner's credential; new installs use deploy keys.)

## 2. Clone and agent placement

```bash
mkdir -p ~/syncyard-backchannel
git clone git@github.com:tbitcs/syncyard-backchannel.git ~/syncyard-backchannel/repo
```

Place `agent.sh` from this repo (AXIOVEX/syncyard `backchannel/`,
at a pinned merged commit; verify its sha256 against the merge
record) at `~/syncyard-backchannel/agent.sh`, `chmod +x`.

## 3. The systemd user unit

Write `~/.config/systemd/user/syncyard-backchannel.service`:

```ini
[Unit]
Description=Syncyard failsafe backchannel agent (<channel> channel)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/bin/bash %h/syncyard-backchannel/agent.sh
Restart=on-failure
RestartSec=15
Environment=SYNCBC_BASE=%h/syncyard-backchannel
Environment=SYNCBC_CHANNEL=<channel>
Environment=SYNCBC_REPO_URL=git@github.com:tbitcs/syncyard-backchannel.git

[Install]
WantedBy=default.target
```

The desktop's unit sets no `SYNCBC_CHANNEL` (the default is its
channel) and no `SYNCBC_REPO_URL` (HTTPS default). New installs set
both as shown.

```bash
systemctl --user daemon-reload
systemctl --user enable --now syncyard-backchannel.service
```

## 4. Verification (all four, from records, not liveness)

1. `systemctl --user is-active syncyard-backchannel.service` =
   active on the target.
2. From the VM: `python3 bc.py --channel <channel> status` shows a
   fresh heartbeat whose host is the target machine (the agent
   writes `channels/<channel>/heartbeat.json` on its first cycle and
   creates the channel dir itself; nothing needs seeding).
3. A round trip: `bc.py --channel <channel> send --cmd 'echo BC_PROBE $(hostname)'`,
   then `bc.py --channel <channel> wait <id>` returns exit 0 with
   the host's name in the output.
4. The STOP switch: the agent honors `channels/<channel>/STOP`
   exactly like the desktop channel (spec 009 FR-004).

## 5. Optional: the tray fleet panel

A Syncyard tray on the same machine can read
`fleetStatusPath = \\wsl.localhost\<distro>\home\<user>\syncyard-backchannel\repo\channels\<channel>\fleet-status.json`
provided the fleet-status publisher writes `fleet-status.json` into
that channel as well as `syncyard-dev`. Since spec 024 that is a
real setting of the packaged publisher install: the publisher is
installed by `gatekeeper/install-publisher.sh` (units and
installer ship in this repo), and its `--channels` option lists
every channel that receives a copy, e.g.
`--channels syncyard-dev,syncyard-laptop`.

## Rules that do not change per machine

- Never run two agents on one channel, and never point a second
  machine at `syncyard-dev`.
- Never send a command while a previous one is pending or executing
  on that channel (the lost-command race); read `bc.py status`
  first.
- A heartbeat that stops advancing means "the agent cannot sync"
  until proven otherwise; check the target's WSL network before
  blaming the agent (the 2026-11-04 desktop outage was a wedged WSL
  networking plane behind a healthy agent).

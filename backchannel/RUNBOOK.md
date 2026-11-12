# Backchannel recovery runbook

The failsafe backchannel exists for the moments the normal door
cannot help: Syncyard itself is down or blocked, the tray is the
casualty, or a runner process cannot do what only the owner's
interactive session can. It is a **development and recovery
channel only** — never a job route. The gatekeeper remains the
sole dispatch door; the agent never writes queue records.

Channel facts: private repo `tbitcs/syncyard-backchannel`,
channel dir `channels/syncyard-dev/`, files `cmd.json` /
`out.json` / `heartbeat.json` / `STOP`. Client: `backchannel/
bc.py` (`heartbeat`, `status`, `send`, `wait`). Agent:
`backchannel/agent.sh`, a WSL systemd user service
(`syncyard-backchannel.service`) on the desktop, ~60 s cycle,
git-protocol sync only (no REST quota).

## Health checks (run these first)

- `python3 backchannel/bc.py heartbeat` — prints the heartbeat,
  its age, and the agent's start-up self-test
  (`BC_SELFTEST ok=...`). **Stale = age > 300 s.**
- `gk status` prints the same heartbeat age as its second line
  and raises `!! BACKCHANNEL STALE` past 300 s.
- On the desktop (WSL): `systemctl --user status
  syncyard-backchannel.service` and the tail of
  `~/syncyard-backchannel/agent.log`.

## The four recovery cases

Send each body with `bc.py send --file body.sh` (it stamps a
fresh id + sha256), then `bc.py wait --id <id>`.

### 1. Restart the Syncyard tray in the owner's session

A runner job cannot start a process in the owner's interactive
session when the tray itself is the casualty; a one-shot
scheduled task can. This body registers, runs, and removes it
(paths are the packaged install's; adjust the user if needed):

```bash
'/mnt/c/Program Files/PowerShell/7/pwsh.exe' -NoProfile -Command "
  \$exe = \"\$env:LOCALAPPDATA\Programs\SyncyardTray\SyncyardTray.exe\";
  \$a = New-ScheduledTaskAction -Execute \$exe;
  \$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1);
  Register-ScheduledTask -TaskName SyncyardTrayRestart -Action \$a -Trigger \$t -Force | Out-Null;
  Start-ScheduledTask -TaskName SyncyardTrayRestart;
  Start-Sleep -Seconds 5;
  Unregister-ScheduledTask -TaskName SyncyardTrayRestart -Confirm:\$false
"
```

Verify: the tray icon is back, and `tray.log` (case 2) shows a
fresh "tray started".

### 2. Read tray.log

```bash
tail -n 200 "/mnt/c/Users/you/AppData/Local/SyncyardTray/tray.log"
```

### 3. Restart a runner lane

The tray supervises the runners and restarts a dead one (it
caps restarts at 3 per runner per hour, then raises OWNER
NEEDED). Stopping the lane's runner process is enough:

```bash
'/mnt/c/Program Files/PowerShell/7/pwsh.exe' -NoProfile -Command "
  Get-CimInstance Win32_Process -Filter \"Name='actions.runner.exe'\" |
    Where-Object { \$_.CommandLine -like '*Syncyard\runners\lane-normal*' } |
    ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }
"
```

(Name the lane you mean — `lane-quick`, `lane-normal`,
`lane-long`, `lane-gpu`.) Verify in `tray.log` and with the
runner's service state; if the tray has hit its restart cap,
that is an owner decision, not a retry loop.

### 4. Read-only network diagnostics

```bash
'/mnt/c/Program Files/PowerShell/7/pwsh.exe' -NoProfile -Command "
  Get-NetAdapter | Format-Table Name, Status, LinkSpeed;
  Get-NetIPAddress -AddressFamily IPv4 | Format-Table InterfaceAlias, IPAddress, PrefixOrigin;
  Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' | Format-Table InterfaceAlias, NextHop, RouteMetric
"
```

## Going-down records and the guard heartbeat

The agent's message set grew by exactly two **merged fields**
on the heartbeat payload; the agent originates neither. The
host guard (`runner/guard/`) writes two
files on the Windows side, and `write_heartbeat` merges each
into the heartbeat verbatim when it holds a non-empty JSON
object:

- `going_down` ← `C:\Users\you\Development\Syncyard\
  going-down.json` (WSL view: `/mnt/c/Users/you/
  Development/Syncyard/going-down.json`, override
  `SYNCBC_GOING_DOWN_FILE`). The Phase 1 going-down record:
  `{host, ts, trigger, active_run_ids, supervised,
  uncheckpointed}`. The gatekeeper's sweep detects it on the
  payload and ingests it exactly once per (host, ts) — the
  file riding every later heartbeat is a no-op by design.
  The guard clears the file (writes `{}`) when it starts, so
  a record from a previous boot cannot re-ingest as a fresh
  event after a gatekeeper state reset.
- `guard` ← `...\guard-heartbeat.json` (override
  `SYNCBC_GUARD_FILE`). The guard's beat: `{host, ts, active,
  draining}`. The gatekeeper stores it on the host's liveness
  record and judges staleness against the carrying
  heartbeat's own `ts` (`drain.guard_stale_after_s`, default
  180 s): a stale beat ledgers `guard_stale` once per
  transition and shows in `gk status`, so a stopped guard is
  a visible loss of protection, never a silent one.

This merge is the going-down writer's transport because it is
the fastest channel the shipped ingest contract supports:
the sweep already spends one contents read of this payload
per pass, and the channel syncs over the git protocol only —
no new API class, no new heartbeat format (the Phase 1
reader is unchanged). The agent remains a dumb merger: it
never interprets either record, and queue access stays
read-only (the rule above is unchanged).

Triage additions: a going-down record that never ingests —
check the heartbeat payload actually carries `going_down`
(the file exists on the Windows side, parses as JSON, and
names the config host, e.g. `desktop`, in its `host` field).
A `guard_stale` ledger line with a live heartbeat — the
guard process died or its beat file stopped updating; the
guard's own log sits beside its executable
(`C:\Users\you\Development\Syncyard\guard\guard.log`).

## Failure triage

- **Heartbeat stale, service inactive** — start it:
  `systemctl --user start syncyard-backchannel.service`. If it
  fails, `agent.log` holds the reason; the common one is a git
  credential problem on the channel repo.
- **Heartbeat stale, service active** — read the tail of
  `agent.log`: a hung `git pull` (network) or a long-running
  command body (default timeout 600 s) are the usual causes.
  The agent is single-threaded by design; a command that must
  run longer should say so (`timeout_s` in cmd.json).
- **`BC_SELFTEST ok=False`** — the failing check names itself:
  `repo_reachable` (network/credential), `channel_writable`
  (clone damaged — delete `~/syncyard-backchannel/repo` and
  restart the service; it re-clones), `log_writable` /
  `base_writable` (disk/permissions on the WSL home).
- **STOP present** — execution is halted on purpose
  (`heartbeat stopped=true`). To resume, delete
  `channels/syncyard-dev/STOP` in a channel commit; the agent
  resumes on its next cycle.
- **A command was not executed** — check `agent.log` for
  `SHA MISMATCH` (recompute the sha256 over the exact body and
  re-send with a FRESH id; ids are never reused), and confirm
  the id differs from `last_executed_id` in the heartbeat (a
  replayed id is refused by design).
- **Reading an `out.json` `exit_code`** — the code is the
  executed body's real exit status. Special values: **65** =
  the agent refused the command (sha256 mismatch — the body
  never ran); **124** = the wrapper's timeout fired and the
  body died on SIGTERM; **137** = death by SIGKILL — the
  body's own, or the wrapper's escalation (a body that
  ignores SIGTERM is killed `SYNCBC_KILL_AFTER_S`, default
  10, seconds after its `timeout_s` expires); **143** = death
  by a SIGTERM the body sent or received itself. A
  wrapper-killed body is never recorded as 0.
- **A short stored output is not proof a body died early** —
  `stdout_tail` / `stderr_tail` hold only the **last 8000
  bytes** of each stream by design: a tail window. If a
  result looks cut off, first check whether the full output
  was simply larger than the window; confirm completion from
  the body's side effects and the agent log's `finished`
  line, not from the tail's length.
- **Corrupt cmd.json** — harmless by design (the agent skips
  it and keeps heartbeating); overwrite it with a fresh send.
- **Reinstall** — from a checkout of electrohire/syncyard, run
  `backchannel/install-backchannel.sh` inside WSL. It is
  idempotent: it refreshes the agent copy, rewrites the unit,
  restarts the service, and prints `SYNCBC_INSTALL_OK 1` when
  the service is active.

Every backchannel problem is reported to the **Desktop Access
chat**, which owns Syncyard (owner's order, 2026-10-27). Do not
work around a sick backchannel in a program chat.

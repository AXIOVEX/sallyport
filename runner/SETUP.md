# Runner setup — the owner session runbook

Everything in this runbook happens **in one sitting, in the owner's own
signed-in session on the machine** — not from inside an Actions job
(job teardown kills child processes, so a job can never install or
start the runner that would replace it) and not under any other user's
profile. Runners run as **user-window processes** under the owner's
account, never as a Windows service.

You will: register four lane runners to this repository, install the
tray supervisor, and prove each lane with an identity job. Budget about
half an hour of mostly waiting.

## Before you start

- The machine is powered on, signed in as the owner, and will not sleep
  or reboot during the session.
- You have `gh` authenticated on some machine where you can run one
  command at step 2 (the control machine is fine — the token travels by
  your hands only, is used once, and is never written down anywhere
  that persists).
- Any jobs queued against a previous runner repository have finished
  or been accounted for by their programs (see
  [docs/MIGRATION.md](../docs/MIGRATION.md), gate 5).
- PowerShell 7 (`pwsh`) is installed — the dispatch workflows run their
  steps under it. PowerShell 5.1 also works for the setup scripts
  scripts (they are ASCII-only on purpose).

## Step 1 — Lay out the lanes

Four registrations, one directory each:

| Lane | Runner name (example) | Directory | Extra label |
|---|---|---|---|
| quick | `desktop-quick` | `C:\Users\<you>\Development\Syncyard\runners\quick` | `lane-quick` |
| normal | `desktop-normal` | `C:\Users\<you>\Development\Syncyard\runners\normal` | `lane-normal` |
| long | `desktop-long` | `C:\Users\<you>\Development\Syncyard\runners\long` | `lane-long` |
| gpu | `desktop-gpu` | `C:\Users\<you>\Development\Syncyard\runners\gpu` | `lane-gpu` |

If the machine already has a runner from a previous setup, it becomes
the **long** lane: re-register its existing directory under this
repository with the `lane-long` label (step 3 with `--replace` handles
the re-registration; the directory stays).

Names above are examples — use a consistent `<machine>-<lane>` scheme
of your own. What matters is the label, the directory, and one runner
per directory.

## Step 2 — Fetch a registration token

Registration tokens **expire in about an hour**, and steps 2–4 are one
sitting for exactly that reason. From the control machine:

```bash
gh api -X POST repos/AXIOVEX/syncyard/actions/runners/registration-token --jq .token
```

Keep the value in the terminal only. If it expires mid-session, fetch
a fresh one and continue — nothing is harmed. One token can register
several runners while it lives.

## Step 3 — Register and start each lane

From this repository's checkout, in PowerShell on the machine, once per
lane (substitute your token, names, and directories from step 1):

```powershell
.\runner\setup-runner.ps1 -Name "desktop-quick"  -Labels "lane-quick"  -RunnerDir "C:\Users\<you>\Development\Syncyard\runners\quick"  -RegistrationToken <token> -Start
.\runner\setup-runner.ps1 -Name "desktop-normal" -Labels "lane-normal" -RunnerDir "C:\Users\<you>\Development\Syncyard\runners\normal" -RegistrationToken <token> -Start
.\runner\setup-runner.ps1 -Name "desktop-long"   -Labels "lane-long"   -RunnerDir "C:\Users\<you>\Development\Syncyard\runners\long"   -RegistrationToken <token> -Start
.\runner\setup-runner.ps1 -Name "desktop-gpu"    -Labels "lane-gpu"    -RunnerDir "C:\Users\<you>\Development\Syncyard\runners\gpu"    -RegistrationToken <token> -Start
```

Each run downloads the pinned runner package, registers the runner to
this repository only, and opens its `run.cmd` in a user window. Leave
the four windows open — closing one stops that lane's runner (the
tray supervisor from step 4 restarts it within seconds, but an open window is
the expected resting state).

Confirm all four show as **online** (one API call):

```bash
gh api repos/AXIOVEX/syncyard/actions/runners --jq '.runners[] | "\(.name) \(.status) labels=\([.labels[].name] | join(","))"'
```

## Step 4 — Install the tray supervisor (spec 007)

The runners run windowless under the **Syncyard Tray** supervisor: a
per-user tray app that starts every runner hidden, shows per-lane
state, restarts a dead runner (cap: 3 restarts per runner per rolling
hour; past the cap it alerts OWNER NEEDED), and hides stray runner
console windows. It replaces the older watchdog scheduled task — do
not install that task.

**Installer package (recommended).** Download
`SyncyardTray-Setup-0.1.0.exe` from the project's GitHub Releases and
run it. It is a per-user install (no admin): files go to
`%LOCALAPPDATA%\Programs\SyncyardTray`, autostart is registered in
the HKCU Run key, a Start Menu shortcut is created, and an
uninstaller is registered in Add/Remove Programs. Silent install:
`SyncyardTray-Setup-0.1.0.exe /S`.

**Manual install (from source).** Clone this repository, then in
PowerShell:

```powershell
.\runner\tray\install-tray.ps1
```

The script fetches the tray source and assets, compiles them with
the csc.exe that ships with Windows (no SDK needed), installs under
`%USERPROFILE%\Development\Syncyard\tray`, registers the same Run
key, and launches the tray. Verify: the yard-mark icon appears in
the tray (green badge when all lanes are up) and
`%LOCALAPPDATA%\SyncyardTray\tray.log` shows each runner started
hidden.

**Connection indicator (spec 018).** The icon's corner carries a
link mark — solid when this host is connected to the fleet, hollow
amber when stale, broken (with the mark desaturated) when
disconnected — judged only from the backchannel agent's heartbeat
file on this host (default
`\\wsl.localhost\Ubuntu-24.04\home\tpierson\syncyard-backchannel\repo\channels\syncyard-dev\heartbeat.json`;
the path and the stale/down thresholds, 180 s / 900 s, are tray
settings in the dialog's Connection section). The tray menu and
tooltip state the connection in words, the dialog names the exact
signal set the judgement rests on, and every transition is logged
to `tray.log` with its signal age. The indicator is display-only:
a disconnected host's tray keeps supervising its runners exactly
as before.

**Uninstall.** Use Add/Remove Programs ("Syncyard Tray"), or run
`%LOCALAPPDATA%\Programs\SyncyardTray\Uninstall.exe`. Uninstalling
the tray never stops the runners themselves; stop a runner from the
tray menu before uninstalling if you want it down.

## Step 5 — Prove each lane (identity jobs)

No real dispatch routes to a lane before its identity proof is on
record. For each lane, dispatch the matching workflow through the
gatekeeper (or, for this proof only, directly) with a script that
prints markers:

- Windows proof (`run.yml`): prints
  `IDENTITY host=<computername> user=<domain\user>` from
  `$env:COMPUTERNAME` and `whoami`.
- WSL proof (`wsl.yml`): prints `IDENTITY_WSL user=<user> home=$HOME`
  from `whoami` and the home path.
- GPU lane extra (Windows side): prints both graphics cards, for
  example `GPU_LIST <name 1> | <name 2>` from the display-driver query.

Read the verdicts from the marker lines in the run logs — never from
the green checkmark alone. Record, per lane: runner name, machine
name, Windows user, WSL user, and the run ids. File the record with the
fleet's program notes. A lane whose proof fails carries no real work
until it passes.

## Troubleshooting

- **Registration token rejected / expired.** Fetch a fresh token
  (step 2) and re-run the failing lane's setup. The script skips the
  download when the package is already extracted.
- **A runner shows offline.** Check its window first — most "offline"
  runners are a closed window. Start it again with
  `& "C:\Users\<you>\Development\Syncyard\runners\<lane>\run.cmd"`, and confirm the tray's
  `status.json` sees the listener.
- **Watchdog says OWNER NEEDED.** The restart cap tripped: the runner
  is dying repeatedly, which is a real fault, not a supervisor fault.
  Read `%LOCALAPPDATA%\SyncyardTray\tray.log` and the runner's own
  `_diag` logs in its directory before restarting anything by hand.
- **A dispatch queues forever on one lane.** Check the runner is
  online *and carries the exact lane label* — a label mismatch queues
  silently forever. `gh api .../actions/runners` (step 3) shows every
  label.
- **WSL-side jobs cannot reach a device on the machine's local
  network.** Some networks are visible only from the owner's own WSL
  distribution, not from the runner's default WSL context. Hardware
  scenario jobs handle this by invoking the owner's distribution
  explicitly — see the board-job rules in [SKILL.md](../SKILL.md).

# Quickstart

From nothing to a first verdict in three steps: register a
runner on a machine you own, submit a job through the
gatekeeper, and read the marker verdict the gatekeeper
harvests. This page assumes the gatekeeper is installed (any
route in [Installation](installation.md)); the command below
is written as `syncyard`, and `gh syncyard` or
`python3 gatekeeper/gk.py` take the same arguments.

## 1. Register a runner

On the machine that will do the work, signed in as the user
the jobs should run as, fetch a registration token (valid
about an hour) and run the setup once per lane. On Windows:

```powershell
gh api -X POST repos/<you>/<door-repo>/actions/runners/registration-token --jq .token
.\runner\setup-runner.ps1 -Name "myhost-quick" -Labels "lane-quick" `
    -RunnerDir "C:\Users\<you>\Development\Syncyard\runners\quick" `
    -RegistrationToken <token> -Start
```

The door repository is **private** and yours: it holds the
dispatch workflows (`run.yml`, `wsl.yml`, `grab.yml`, plus the
POSIX and Docker transports) that carry jobs to the runners.
Linux, macOS, WSL2, and Docker venues follow the same pattern
with their own labels and markers — the repository's
`runner/SETUP.md` is the full runbook, and the README's
per-host quickstarts carry the honest limits of each venue.

Prove the venue before trusting it: a first job that prints
its identity markers (`LID_HOST`, `LID_WINUSER` / `LID_WSLUSER`
/ `LID_USER`, `LID_OS`) and nothing else.

## 2. Submit a job

From the operator machine, through the gatekeeper — never by
dispatching the workflow yourself:

```bash
# a Windows PowerShell job
syncyard submit --program myprogram --workflow run.yml \
    --lane quick --script ./probe.ps1 \
    --cwd 'C:\Users\me' --key probe-2026-01-01

# a bash job on a WSL2 / Linux / macOS lane
syncyard submit --program myprogram --workflow wsl.yml \
    --lane normal --script ./build.sh --key build-2026-01-01

# return one file from the machine as an artifact
syncyard submit --program myprogram --workflow grab.yml \
    --lane quick --grab-path 'C:\Users\me\result.txt' \
    --key grab-2026-01-01
```

Every job carries a **unique `--key`**; submitting a key that
is already active is refused, and that refusal is the
duplicate-dispatch protection working. Declare
`--expected-min N` for long jobs — health watermarks are
judged against the declaration.

A scheduled sweep (every 5 minutes is the fleet standard)
does the rest:

```bash
syncyard sweep
```

The sweep checks API headroom first, dispatches what fits,
and watches every tracked run in one batched pass.

## 3. Read the verdict

```bash
syncyard status
```

The board shows every tracked job, its state, and the verdict
**markers** harvested from its log. Write your job scripts to
print unambiguous marker lines with their results, for
example `PROBE_DONE gpus=2 free_gb=212` or
`BUILD_VERDICT: PASS` — lines beginning with an `ALL_CAPS_`
token. A run's success color is not a verdict; the markers
are the record.

## Holding the fleet

Two controls every operator uses in the first week:

```bash
syncyard pause --reason "working on the machines"
syncyard resume        # dispatch flows again; the watch never stopped

syncyard stop --reason "fleet maintenance"
syncyard start         # the hard halt, and its release
```

While paused, the sweep keeps watching (verdicts and
watermarks stay current) but dispatches no program jobs;
operator maintenance jobs submitted with `--maintenance`
still dispatch. While stopped, sweeps no-op entirely. Neither
state ever touches a job already dispatched — the gatekeeper
never cancels a run.

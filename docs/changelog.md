# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Changed
- Project home and attribution moved from AXIOVEX/Axiovex Systems to
  the ElectroHire org (electrohire/syncyard, electrohire/gh-syncyard,
  electrohire/desktop-runner) by owner order 2026-11-12 (#99). Repo
  path strings in code, config defaults, docs and installer metadata
  now name the electrohire repos; LICENSE attribution names
  ElectroHire, Inc. Historical spec records keep the names in force
  when they were written.
### Added
- Tray settings resilience, per-machine lane sets, and
  multi-instance runner supervision (spec 028, H4): the
  settings save is atomic (temp file + replace, the previous
  file kept as tray-settings.json.bak) and the load falls
  back live file -> .bak -> the machine profile
  (tray-profile.json beside the exe, installed data carrying
  this machine's runner root, fleet path, heartbeat path,
  host name, stale bounds, and lane set; shipped profiles
  under runner/tray/profiles/ for the desktop and the
  laptop) -> compiled defaults, logging which source won. The
  runner table is built from the machine's lane set (the
  profile's, else the directories under the runner root), so
  the laptop's tray draws only its long and quick lanes; a
  lane may name several instance directories (the desktop's
  normal lane: normal, normal2, normal3), each supervised,
  restarted, and reported per instance, with the settings
  dialog and heartbeat aggregating per lane. Restart history
  and Faulted state persist beside the log
  (tray-restart-state.json), so a tray restart no longer
  zeroes a crash-looping instance's cap. A tray whose runner
  root is missing, or under which no enabled lane directory
  exists, says "supervising nothing" in its menu header and
  heartbeat. Listener identity is the exact runner directory
  in the tray and in runner/runner-watchdog.ps1 (no more
  path-prefix matching). Source coverage:
  runner/tray/test_tray_source.py (spec 028 section); the
  falsifier legs are recorded in
  specs/028-settings-resilience/falsifier-record.md.
- Tray version drift detection (spec 027, H3): each sweep,
  the gatekeeper reads the release manifest the tray updater
  itself consumes (tray-update.json on the latest release,
  one read per sweep, cached to tray-release.json) and judges
  every host's recorded tray beat (spec 025) against the
  published version, storing tray_drift (current / behind /
  unknown, with both versions) on the host record. Entering
  behind ledgers tray_drift once, entering current ledgers
  tray_current once, and a failed or absent manifest read
  judges unknown, never current. `gk status` shows the drift
  beside the beat on the host line (BEHIND with both versions
  when behind). The tray source battery additionally
  extracts the .nsi VERSION define, both assembly version
  attributes, and the BuildId string and asserts they are
  one value, so the stamps the judge compares can only move
  together. Scenario coverage: gatekeeper/test_spec027.py.
- Gatekeeper snapshot queue fields on the primary path:
  gk.py's build_snapshot now emits the tray queue model
  (active, lanes, queue_state, running_total,
  waiting_total) with the same shapes and four-state
  rules as fleet_status.py's fallback derivation, so a
  gatekeeper-sourced snapshot no longer sends the tray
  down its degraded path. test_fleet_status pins the
  fields on the gatekeeper emission for the idle,
  waiting, running and full cases.
- Tray liveness heartbeat (spec 025, H1): tray 0.4.4.0 writes
  tray-heartbeat.json beside its log about every 30 s and at
  startup (host, build, pid, session, started_at, tick,
  per-lane running/faulted), atomically; the backchannel
  agent merges it into its heartbeat as `tray`
  (SYNCBC_TRAY_FILE); the gatekeeper stores the beat per
  host and judges tray staleness against the carrying
  heartbeat past liveness.tray_stale_after_s (300 s),
  ledgering tray_stale / tray_recovered and marking the
  status host line TRAY STALE when the agent is alive and
  the tray is not reporting. The owner's Exit now writes a
  stopped-by-user marker any tray start clears, and the
  tray package gains tray-watchdog.ps1 plus its scheduled
  task template: a bounded restart (about 60 s via the
  1-minute task) with a 5-per-hour relaunch cap and an
  owner-needed marker past the cap or when the exe is
  missing. Not yet field-deployed; the owner pulls tray
  updates himself.
- Tray hardening batch, specs 025-031 (ratified 2026-11-05):
  spec 025 tray liveness heartbeat (H1), spec 026 CI compiles
  the tray (H2 remainder), spec 027 version drift detection
  (H3), spec 028 settings resilience with the per-machine
  lane-set fix and multi-instance runner supervision (H4),
  spec 029 one packaged tray watchdog (H5), spec 030 the
  gatekeeper queue-starvation fix, and spec 031 the gh
  extension refresh (stub). Spec files only in this entry;
  implementation lands per spec, H1 first.

- Tray 0.4.3.0: update check at launch, queue line, status
  window, consoles viewer. A few seconds after startup the
  tray now runs the same update check the menu item runs
  (spec 021): it downloads and installs nothing on its own;
  a found update is announced once by balloon, the menu
  item shows the available version, and install stays the
  owner's click. The menu gains a queue line over the lane
  rows rendering the fleet's queue depth in four states
  (idle, waiting, running, full) with counts; the waiting
  state (work queued, nothing running) renders amber and
  bold. A Status menu item opens a status window: fleet
  mode and snapshot age, per-lane queues, the running and
  waiting job lists, this machine's connection and
  heartbeat state, and the update state with Check now /
  Update now buttons wired to the same paths as the menu.
  "Show consoles" opens a read-only tabbed viewer tailing
  each supervised runner's newest _diag log, the tray log,
  and the backchannel agent log where known: append-only
  file reads, no input path of any kind.
- Fleet snapshot queue depth (publisher fallback
  derivation, gatekeeper/fleet_status.py): the snapshot
  gains `active` (jobs in dispatching/dispatched/watching,
  with run_id when recorded), `lanes` (per-lane cap,
  running, waiting, and a four-state word), `queue_state`
  (idle/waiting/running/full, fleet-wide), and
  `running_total` / `waiting_total`. All additive; every
  existing field is unchanged, and a consumer meeting a
  snapshot without them degrades rather than guessing.

### Removed
- Tray: the "Show runner consoles" menu item and its
  SW_SHOW window hunt. It un-hid session windows by title,
  and the fleet's runners run with no console window, so
  it could never show a healthy runner (red team,
  2026-11-05). The read-only consoles viewer replaces it.
  "Hide runner consoles now" (the stray-window janitor) is
  unchanged.

### Added
- Gatekeeper-emitted fleet snapshot + publisher v2 (spec
  014 FR-008/FR-009, the spec 013 fleet deploy's step 2).
  New `gk snapshot` command: the FR-008 snapshot as one
  JSON object on stdout, derived by the gatekeeper's own
  code paths — the FR-001-normalized pause record, the
  FR-012 dispatch order the sweep itself uses, the sweep's
  admission rule for the held marks (stop / pause / focus
  with the maintenance exemption, plus host dispatch holds
  with the FR-015 signal-job exemption), and the
  host-state file for draining. One derivation, two
  surfaces: `gk status` at the terminal, the tray panel
  via the publisher. fleet_status.py becomes the
  transporter: it runs the snapshot command
  ($SYNCYARD_GK_SNAPSHOT_CMD, else the gk.py beside it)
  under a bounded timeout, validates the JSON against the
  contract, and publishes it verbatim with a top-level
  `source: "gatekeeper"` label; on any failure it falls
  back to the v1.1 state-file derivation, labeled
  `source: "fallback"`, with all v1.1 behavior (budget
  preflight, failure counters, lockfile, contents-API
  publish) unchanged. The PUBLISHED line now carries the
  source. VERSION is deliberately not bumped (the spec
  013 stack owns 0.4.0).
- Fleet-status publisher in the install (spec 024): the publisher's systemd units are repo files (gatekeeper/systemd/) and gatekeeper/install-publisher.sh installs the publisher as a package — full dependency preflight with named refusals before anything is written, the proxy/token env captured from the installing shell into a root-only env file (values never printed), a root-owned publisher copy whose sha256 is asserted against the checkout, the timer enabled, and a first publish proven from the publisher's state record before success is reported. Previously the publisher's units and installer existed only as loose local files, so a VM replacement silently removed the service and the tray fleet panel went stale. The publisher now also publishes one snapshot byte-identically to every configured channel path (--path is repeatable; the installer's --channels option, default syncyard-dev), which is the per-channel setting backchannel/INSTALL.md already referred to; a FLEET_STATUS_API_BASE env override (default unchanged) lets install tests run against a local stand-in. Battery: gatekeeper/test_install_publisher.py runs the real installer in a sandbox (stub systemctl that executes the installed unit, stub contents API) and proves fresh one- and two-channel installs, idempotency, and named refusals that install nothing.
- Spec 017: standalone distribution. Syncyard now builds as
  a standalone Python package alongside the gh extension:
  a root `pyproject.toml` defines the single distribution
  `syncyard`, its version read dynamically from the repo
  VERSION file (no packaging edit on a version bump), zero
  runtime dependencies, and a `syncyard` console command
  that is a direct entry point to the gatekeeper's own
  `main()` — no re-implemented verbs. The package-dir
  mapping installs the literal `gatekeeper/` and
  `backchannel/` files as the `syncyard` and
  `syncyard.backchannel` namespace packages, so the
  packaged code is the checkout code byte-for-byte (the new
  packaging battery compares them, installs the wheel into
  a fresh venv offline, and smokes the console command and
  the config load). A tag-triggered release workflow
  builds, `twine check`s, smoke-installs, and publishes to
  PyPI via OIDC trusted publishing — no stored tokens; a
  manual dry run does everything except publish. The first
  publish waits on the owner's one-time trusted-publisher
  registration on pypi.org (the name `syncyard` was checked
  available, 2026-11-02). Documentation gains a MkDocs
  site (stock theme, strict build wired into CI) and a
  `.readthedocs.yaml`, ready for the owner's Read the Docs
  import; the site excludes the unsent pitch brief and
  labels spec-stage features as specified, not shipped.
  The skills pack and this repo's SKILL.md now name the
  standalone route beside the checkout and `gh syncyard`
  routes wherever a command form is given. VERSION is
  deliberately not bumped in this change (the release
  mechanics ride the next feature version).
- Backchannel, one channel per machine: the agent's channel is now selected by the SYNCBC_CHANNEL environment variable (agent.sh) and the client's by --channel or SYNCBC_CHANNEL (bc.py), defaulting to the original syncyard-dev channel so TRISTEN-DESKTOP's install is unchanged. A second host (TRISTEN-LAPTOP, channels/syncyard-laptop) can now run its own agent against the same private backchannel repo without sharing cmd/out/heartbeat state. Install procedure for additional machines: backchannel/INSTALL.md (deploy key per machine, systemd user unit with the channel set, four-step verification from records).
- Spec 023 (fleet tray convention): one shared surface for every project tray in the fleet (Syncyard, EdgeLab, RealLoom) — tooltip composed short under a 63-character budget with parts dropped whole and never chopped, one status vocabulary (up/down; linked / link stale / unlinked; N waiting), one menu order ending in an always-enabled Exit, Start/Stop as the only verbs for supervised things (Enable/Disable reserved for supervisor switches), greyed items meaning "unavailable in the current state" with the reason visible, status always from direct observation, and exit finality via a stopped-by-user marker every relauncher must honor. The spec carries a source-read conformance checklist for all three trays; the gaps it names (Syncyard tray watchdog ignoring a user exit, EdgeLab tooltip chop and missing Start item, RealLoom unbounded tooltip and unlogged quit, per-item greying gaps) are findings for each tray's own follow-on work, not changes in this spec.
### Fixed
- Backchannel agent envelope-timeout edges (robustness offensive, 2026-11-04): a `timeout_s` of 0 no longer reaches GNU timeout, which treats a 0 duration as "no timeout" and would have run one body unbounded, stalling the single-threaded agent and its heartbeat; 0 now falls back to the default. A timeout with 10+ digits is clamped on length before any arithmetic (bash `[ -gt ]` errors on 19+ digit values and reads as not-clamped, defeating the 0.2.2 max clamp). The replay guard is re-seeded from out.json when `.last_executed_id` is lost, so FR-004(b) (a re-pushed old command never re-runs) survives a wiped base or a reinstall that kept the clone. Chaos scenarios 15 and 17 pin all three (failing before, passing after); scenarios 14, 16, 18, 19 pin push-rejection recovery, SSH-transport failure visibility, at-least-once re-execution after a mid-execution kill, and STOP arriving mid-execution.
- Backchannel client (bc.py) corrupt-channel handling: a cmd.json / out.json / heartbeat.json that does not parse as a JSON object now reads as unreadable channel state on every command path (heartbeat, status, send, wait) instead of an AttributeError traceback, and a future-dated heartbeat beyond a 30s tolerance reports BC_HEARTBEAT_SKEW with an unjudgeable verdict instead of FRESH (the host's WSL clock and the VM clock are different clocks; a dead agent could otherwise hide behind a skewed clock for the skew plus the whole stale window). Batteries: test_bc.py unit battery.
- Tray crash on menu activation change fixed: RebuildMenu built a new ContextMenuStrip on every rebuild, assigned it to the NotifyIcon, and disposed the previous strip, and the deferred rebuild inside the old strip's own Closed handler disposed it while WinForms' modal menu filter still held it as the active menu, so the next activation change reparented a disposed object and the tray died with an unhandled ObjectDisposedException (TRISTEN-LAPTOP, 2026-11-04). The strip is now created once, bound to the icon once, and never disposed while the tray lives; rebuilds replace its items in place and dispose only the detached items. Menu order, item states, and the open-menu rebuild deferral are unchanged. Tray build stamp moves to 0.4.2.0.
- Tray tooltip no longer reads as cut off: the old composition concatenated the full connection and fleet lines and chopped the result at the 63-character NotifyIcon limit mid-word. The tooltip is now composed short (runner count, the connection as one word, the waiting count, the stale flag) and a segment that would cross the limit is dropped whole; nothing is truncated. Tray build stamp moves to 0.4.1.0.
- Tray menu header trimmed (owner ruling): the per-job "Next:" line and the connection signal-detail line are removed from the menu's grayed header, which is now the build line, the fleet summary, and the connection line. The signal detail remains in the Settings dialog.
- Tray updater: the apply-update thread start is now wrapped in an explicit ThreadStart. The in-box .NET Framework csc (C# 5) rejects the bare anonymous method as ambiguous between ThreadStart and ParameterizedThreadStart (CS0121), which failed the r13 tray build on both fleet machines; the method-group start used by the update check was unaffected.
- Per-host default cwd: the gatekeeper built its default
  working directory by merging every host's cwd map into one
  global dict, so the last host in the config set the default
  for all of them. The day TRISTEN-LAPTOP enrolled as a
  second Windows host, desktop run.yml jobs submitted without
  --cwd inherited the laptop's directory and their payloads
  never ran. default_cwd() now resolves per machine (the
  lane's host, then its `via` hosts, then the built-in
  table); --lane auto submits defer the resolution to the
  routing pass. Battery: gatekeeper/test_per_host_cwd.py.
- Dispatch isolation: a job whose script file is missing or
  unreadable at dispatch time (classically staged in /tmp and
  wiped by a VM replacement between submit and dispatch) no
  longer kills the sweep. dispatch_one catches the read error,
  returns the job to submitted with the error recorded as a
  dispatch_failed_retryable ledger event, and the pass continues
  to the next waiting job; a restaged script revives the job on
  a later sweep with no operator surgery. Field case
  2026-11-04: spec053-f0-build-repair-2026-11-04 (sweep exit 1,
  record wedged in dispatching, repaired by hand). Battery:
  gatekeeper/test_dispatch_isolation.py.
### Added
- Spec 016 follow-on (2026-11-04): `gk profile suggest
  [--host H] [--name P] [--out FILE]` drafts a lane profile
  for one host from its recorded capability snapshot, with
  the reasoning printed as YAML comments in the fragment.
  Deterministic rules: quick and long always; normal only
  when the snapshot measures cpu_cores >= 8 and
  ram_total_mb >= 16384; gpu only when a GPU is actually
  measured present (name or VRAM), with the largest VRAM
  named; concurrency 1 everywhere, a starting draft an
  operator merges by hand. Unknown fields justify nothing.
  A missing or stale snapshot is refused (exit 2) with the
  probe refresh named. The command never applies anything:
  it writes only to an explicit --out path, refused when
  the path exists or is the live config file. Note:
  specs/016-adaptive-lanes/profile-suggest.md. Battery:
  gatekeeper/test_profile_suggest.py.
- Spec 021 (2026-11-04): tray self-update. The tray now
  carries a build stamp (assembly version 0.4.0.0, shown as
  a disabled header item in the menu and written to the
  tray log at startup) and a "Check for updates" menu item:
  on the owner's click it reads the `tray-update.json`
  manifest published with the project's GitHub releases,
  and when a newer build is published the item becomes
  "Update to v<version>". Applying an update downloads the
  exe, verifies its SHA-256 against the manifest before
  anything is swapped (a mismatch deletes the download and
  aborts), then hands off to a UI-free `--apply-update`
  helper mode of the same exe, which waits for the old tray
  to exit, renames the installed exe to `.bak`, renames the
  verified download into place, and relaunches, with
  rollback to the `.bak` if the swap fails. Checks happen
  only on an explicit click; the tray never polls for
  updates, and the spec 018 connection indicator is
  unchanged. The runner root the tray supervises is now a
  per-machine setting (`runnerRoot`, defaulting to the
  original desktop layout) so the same build runs on a
  second machine whose lanes live elsewhere.
- Spec 016 (2026-11-02): adaptive lanes. Lanes now live in
  named **profiles** (`lanes.profiles.<name>` in config, one
  `lanes.active_profile`): a profile is a lane table, and a
  lane may be defined from scratch with a host, runner label,
  concurrency, watermarks, a resource class (`cpu`|`gpu`|
  `any`), an expected-duration band, an optional workflow pin,
  and an enabled flag — validation is fail-closed, and the
  pre-016 flat `lanes:` shape now fails at load with the
  migration named. Three profiles ship: `fleet-default`
  (today's four lanes with today's numbers — the resolved
  table and watermarks are unchanged), `single-host-light`,
  and `gpu-farm`. `gk profile --list` / `gk profile --show
  [name]` render them; switching is a config edit that takes
  effect at the next sweep. **Auto routing**: `gk submit
  --lane auto` (or a lane-less submit under the new
  `lanes.auto_default`, default off) lets the sweep pick the
  lane from the job's declared hints (new `--needs-cpu`,
  `--needs-ram-mb`, `--needs-storage-mb`, `--needs-gpu
  [VRAM_MB]`, `--venue` flags), the program's recorded
  history (median actual duration + dominant resource class
  of its last ten completed jobs), current queue load, and
  the host's **capability snapshot** — scored by a
  deterministic pure function whose full input snapshot is
  written onto the job record and ledgered (`auto_routed`).
  Auto never overrides an explicit lane, a lock (spec 015),
  focus (spec 013), a host hold, or the budget guard; a hard
  hint no lane can meet waits with `auto_no_fit` and the
  unmet reasons on the record, never a silent downgrade.
  Estimates are advisory: nothing is killed, cancelled, or
  migrated for overrunning. Capability snapshots come from
  `gk probe-host` — dispatched as a maintenance probe job
  where the door has a transport (Windows, WSL2), or
  `--ingest` of a report from the shipped per-platform probe
  scripts (`gatekeeper/probes/`, Windows CIM + nvidia-smi,
  Linux /proc, macOS sysctl + system_profiler; Apple-silicon
  GPUs record as unified memory, never discrete) — stored in
  hosts.json with a timestamp; a snapshot older than
  `lanes.capability_max_age_min` (default 60) degrades auto
  to band routing with the degradation ledgered, and unknown
  fields are neutral, never zero. `gk status` renders the
  active profile, a CAPABILITY section, and each auto job's
  resolved lane + score or no-fit reasons. New scenario
  battery `gatekeeper/test_spec016.py` (54 checks), wired
  into CI. VERSION unchanged — spec 013 owns 0.4.0 on this
  stack. Repo-side only: fleet deployment (including
  converting the fleet config to the profiles shape) is a
  separate, later act.
- Spec 018: tray connection indicator. The tray icon gains a
  second dimension beside runner health: a link mark in the
  icon's corner (solid when connected, hollow amber when
  stale, broken with the mark desaturated when disconnected)
  judged only from signals already present on the host — the
  backchannel agent's heartbeat file (its embedded `ts`; the
  deployed agent's WSL path is the default, reached through
  the `\\wsl.localhost` share) and, when a publisher exists,
  the fleet snapshot's stamp — newest signal wins. Three
  states from signal age with tray-settings thresholds
  (`connectionStaleSeconds` 180, `connectionDownSeconds` 900,
  the down bound mirroring the gatekeeper's host liveness
  limit); no signal ever seen reads as disconnected on the
  icon and "unknown" in words. The menu, tooltip, and the
  settings dialog's new Connection section state the state
  in words and name the exact signal set (until the spec 014
  FR-009 publisher ships: "backchannel heartbeat only").
  Every transition is logged to tray.log with its signal
  age. Rendering stays render-on-change, now keyed on the
  (runner, connection) pair; the runner slash geometry and
  semantics are untouched, and the link mark is primitives
  on the existing bitmap (no new handle churn). Display
  only: no network probe, no signal-file writes, no gating
  of supervision. VERSION is deliberately not bumped (the
  spec 013 stack owns 0.4.0).
- Spec 014: tray settings UI and dynamic fleet display. The
  tray gains a single-instance Settings dialog (opened from
  the tray menu — the only window it ever shows) managing the
  settings it owns on the desktop: supervision on/off, the
  restart cap (was a compile-time constant 3/hour), the
  refresh cadence (was a constant 5 s), stray-console hiding,
  autostart (shown from the live HKCU Run value and written
  only from the dialog), and per-lane supervision toggles
  with immediate Start / Stop / Restart acts. Settings
  persist as flat JSON in `tray-settings.json` beside the
  tray exe and take effect on save — no reinstall; the
  hand-rolled reader/writer adds no compile reference, so
  the in-box csc route is unchanged. The tray menu header,
  tooltip, and the dialog's fleet section now render the
  gatekeeper's published status (spec 013: mode RUNNING /
  PAUSED / FOCUSED / DRAINING, focus selectors, waiting
  queue in dispatch order with priority and human/auto
  source) read-only from a snapshot file, showing
  unavailable-with-reason when no snapshot exists (the
  publisher is a gated follow-up, spec 014 FR-009); unknown
  modes display verbatim. The PR #39 discipline is pinned
  by new source-battery checks: the icon still renders only
  on runner-state change, fleet data never renders, the
  queue list rebuilds only on a snapshot stamp change, and
  the settings save is the tray's only file write besides
  its log. VERSION is deliberately not bumped in this
  change (spec 013 owns the next bump; see spec 014 FR-012).

### Fixed
- Tray GDI handle leak (field crash, 2026-11-01): the tray
  re-rendered its status icon on every 5 s supervision tick and
  leaked the rendered bitmap and the icon handle each time (the
  bitmap was never disposed, and `Icon.FromHandle` handles are not
  released by `Dispose`). After about 10.5 h (roughly 7,500 ticks,
  15,000 handles against the 10,000 per-process GDI quota) the tray
  died with a `System.Drawing` `OutOfMemoryException` raised while
  it was constructing its own error dialog. The icon now renders
  only on a state change, the bitmap is disposed, the borrowed
  HICON is cloned and destroyed, the replaced context menu is
  disposed, supervision ticks run under a catch-and-log guard, and
  UI-thread exceptions are caught and logged instead of surfacing
  the modal .NET dialog. A static source battery
  (`runner/tray/test_tray_source.py`, wired into CI) pins the
  patterns. Spec 007.

### Added
- Spec 011 Phases 2–3 (2026-11-02, Amendment 2 — the owner's
  speed directive): the host drain and the shutdown guard.
  `gk drain --host` signals a host's running
  checkpoint-capable jobs through spec 013's hold machinery,
  sets a per-host **drain hold** distinct from the FR-006
  going-down hold (`gk host-resume` clears both), waits out
  each lane's checkpoint window driving the sweep's own
  harvest, and prints a per-job CLEAN / INTERRUPTING verdict
  (exit 0/1; 2 = unknown host). New `drain` config section
  (`window_s` 120, `per_lane_window_s`,
  `guard_stale_after_s` 180), fail-closed validated and
  mirrored in the example config. The guard
  (`runner/guard/SyncyardGuard.cs` + installer + source
  battery): a windowless tray sibling with no UI and no exit
  command that pre-registers the OS shutdown block while
  checkpoint-capable jobs run (discovered from the
  running-job registry the run/wsl workflows now maintain),
  and on a session-end signal writes the going-down record
  first — verified write, the Phase 1 shape — raises hold
  files locally, vetoes, drains, then releases the block and
  re-issues the pending action (restart unless logoff). The
  backchannel agent merges the guard's `going_down` and
  `guard` files into the heartbeat payload (the ingest
  contract's fastest channel; reader unchanged), the
  runbook's message-set section documents both fields, and
  a stale guard beat ledgers `guard_stale` and shows in
  `gk status`. Enabling fix: the gatekeeper's own
  `gk-hold-*`/`gk-unhold-*` signal jobs now flow under a
  host hold, as `gk-reboot-*` already did. Batteries:
  `gatekeeper/test_spec011.py`, `runner/guard/
  test_guard_source.py`, `backchannel/test_agent_source.py`.
  Repo-side only — T008 (production deploy) and T005's
  owner oracle pass remain field steps. Spec 011.
- Spec 015 (2026-11-02): lane priority lock. `gk lock
  --lane <lane> --program <program> [--ttl-min N]
  [--reason TEXT]` gives one program exclusive dispatch on
  a lane; while the lock is ACTIVE only the holder's jobs
  are dispatch-eligible there, and priority never trumps a
  lock (a non-holder's human priority 200 waits; the
  holder's auto priority 0 dispatches). Non-holder jobs are
  not refused or reordered — they wait, watermarks
  escalating. State lives in `LOCKS.json` beside
  `PAUSED.json`, written only through the FR-013
  verified-write path (a failed write leaves the prior
  state byte-identical; no file = no locks = pre-015
  behavior). One holder per lane: a second acquire is
  refused (exit 3) naming holder, state, and expiry;
  same-holder re-acquire renews the TTL and preserves
  state. `gk unlock` releases by the holder, or by the
  operator with `--force` (ledgered
  `lock_released_override`, holder notified via the
  outbox); `gk lock --suspend` / `--resume` pause and
  restore enforcement without surrendering the claim (a
  suspended lock still refuses new acquires). Every lock
  expires on its TTL (new config `lock.default_ttl_min`
  240, `lock.max_ttl_min` 1440), evaluated at dispatch
  time and behaving exactly like a release (`lock_expired`
  ledgered, holder notified), suspended locks included.
  Maintenance jobs bypass locks as they bypass the pause,
  each bypass ledgered `lock_bypassed`. Fleet pause/focus
  never mutate lock state; host drains neither block nor
  clear locks. `gk status` renders a LOCKS section in all
  modes plus a `[lock-held by ...]` waiting mark;
  `gk lock --list` prints it standalone. New scenario
  battery `gatekeeper/test_spec015.py` (25 checks), wired
  into CI. VERSION unchanged — spec 013 owns 0.4.0 on this
  stack. Repo-side only: fleet deployment is a separate,
  owner-gated act.
- Spec 011 Amendment 1 (2026-10-31): owner ruling on boot
  autonomy. The FR-009 owner gate is satisfied by directive —
  Syncyard must start automatically on boot with **no
  interactive logon**; autologon is excluded by the ruling's
  own words. Selected shape: service-mode runners running as
  the owner account (the boot-scheduled launcher is the named
  fallback if the one-lane proof fails on WSL reachability),
  per-lane `boot-runnable` / `logon-bound` honesty labels
  unchanged, the backchannel agent in the boot set (linger +
  distro-start ensure), and the tray's logon autostart
  retired in the conversion. New FR-015: `gk reboot` — a
  controlled, drain-aware host reboot (going-down record
  before any signal, backchannel route first, honest
  unreachable report otherwise). Implementation follows in
  Phase 5 as amended (T011–T014, T021, T022).
- Spec 011 T021 (2026-11-01): `gk reboot --host <host>
  [--force] [--delay <seconds>] [--requested-by <chat-id>]`
  (FR-015) implemented in the gatekeeper. Route determination
  is reads-only and first: a fresh backchannel heartbeat
  (<= 300 s) routes via the backchannel agent, else any
  online runner of the host routes via a maintenance
  dispatch, else the host is reported unreachable (exit 4)
  and nothing else is written. Active jobs on the host's
  lanes refuse the reboot by name (exit 3) unless --force.
  On acceptance the FR-003 going-down record (trigger
  `remote-reboot`) goes through the existing ingest —
  annotations, dispatch hold, and outbox pushes are the
  Phase 1 semantics — then a `host_reboot_requested` ledger
  event names the requesting chat, and only then is the
  host signaled with the grace delay (new config
  `reboot.grace_s`, default 60, validated positive, fail
  closed). The maintenance-route signal job is exempt from
  the going-down hold it rides under; every other job on
  the host stays held. The gatekeeper still never cancels
  and never re-dispatches. Golden scenario 7 pins the
  refusal, the record-before-signal order, both routes,
  and the unreachable path; the live fire drill is T022,
  still open.
- Spec 011 Phase 1 (T001–T003, gatekeeper side, 2026-10-31):
  host liveness and the going-down record. New config
  `liveness.down_after_s` (default 900, validated positive,
  fail closed). Each sweep reads the backchannel heartbeat
  once and keeps a per-host last-seen record (`hosts.json`,
  verified writes); `gk status` renders every host UP/DOWN
  with its heartbeat age. A going-down record arriving as
  the heartbeat's `going_down` field is ingested exactly
  once per (host, ts): a `host_going_down` ledger event,
  `interrupted_by: "host-shutdown"` annotations on the
  affected job records, one outbox push per affected notify
  registration, and a dispatch hold on the host's lanes
  until `gk host-resume <host>` clears it (the Phase-1
  stand-in for the FR-010 recovery handshake). The
  gatekeeper still never cancels and never re-dispatches.
  The protection level is unchanged and stated plainly in
  the spec: delay + drain + checkpoint + notify, never
  prevent.
- Spec 010 Phase 6 (T021, 2026-10-31): `docs/github-pitch.md`
  — the GitHub adoption pitch brief (gh-extension-first
  shape). Prepared only; not sent — the owner's explicit
  word is required before it leaves the repo.

### Fixed
- Backchannel agent body handling (issue #36, 2026-11-01):
  the agent extracted the command body through a bash command
  substitution, which strips trailing newlines — so it hashed
  and executed different bytes than the ones `bc.py` signed,
  and **every `send --file` of an ordinary file** (which ends
  in a newline) was sha-refused with exit 65 (field case: a
  2,767-byte script, claimed `1e7ba777…`, agent computed the
  hash of the same bytes minus the final newline). The agent
  now extracts the body byte-exactly to the command file with
  Python, hashes that file, and executes that file; the wire
  format, envelope schema, STOP and idempotence semantics are
  unchanged, and the mismatch refusal (exit 65) is preserved —
  it now fires only on genuine mismatches. The scalar
  `json_get` fields (id, sha256, timeout_s) were verified
  unaffected: their values never end in a newline.
- Backchannel execution bound (issue #36, 2026-11-01): the
  execution wrapper ran plain `timeout`, whose SIGTERM does
  not bound a body that traps or ignores it — measured on a
  TERM-ignoring `sleep 60` under `timeout_s=3`: the body ran
  its full 60 s, stalling the single-threaded agent (and its
  heartbeat) behind it. The wrapper now runs
  `timeout --kill-after=$SYNCBC_KILL_AFTER_S` (default 10 s):
  a stubborn body is SIGKILLed after its grace and recorded
  as 137 instead of running unbounded; cooperative timeouts
  still record 124, and a body's own status or signal death
  propagates unchanged — a wrapper-killed body is never
  recorded as 0. Same battery pass also pinned what was
  already honest: the stored `stdout_tail` is byte-for-byte
  the last `SYNCBC_TAIL_BYTES` (8000) of the full output — a
  tail window by design, not evidence a body died early. The
  field diagnostics that stopped mid-script around a
  `docker.exe` interop call (2026-11-01) could not be
  reproduced off the WSL host or pinned to a wrapper line;
  the record keeps the narrowing above and claims no more.
- `backchannel/test_chaos.py` gains four cases: a multi-line
  body ending in a newline executing with byte-exact fidelity
  (fails pre-fix with the field's exit 65), an oversized-output
  tail-semantics case, exit-code pins (own status 3, SIGKILL
  137, cooperative timeout 124, TERM-ignoring body bounded),
  and a genuine-mismatch refusal case proving the security
  property survived the fix.

## [0.4.0] - 2026-11-02
### Added
- Spec 013 — live priority and pause control for the
  gatekeeper. **Focus mode** (`gk focus --except-key /
  --except-program / --except-chat`, repeatable selectors):
  pause everything except the named jobs; the pause record's
  schema is a superset of the legacy shape, and a legacy
  single-`except_key` record (the fleet's 2026-11-01 pause)
  reads as a one-key focus with identical effect for the
  standing fleet queue. **Live priorities**: a `priority`
  field on every job record, settable at submit
  (`--priority`) and changeable while the job waits
  (`gk priority`; dispatched or finished jobs refuse, exit 3).
  **Auto-optimization with human precedence** (FR-012):
  waiting jobs without a human-set priority are ordered by a
  deterministic optimizer (declared expectation, lane fit,
  +1 per 10 minutes of aging, capped at 100); human values
  are pinned, win ties against auto values, and values above
  the cap outrank every auto job permanently. **Drain**
  (`gk pause --drain`, `gk focus --drain`): executing work
  finishes, nothing new starts outside the admitted set, and
  the sweep settles the record itself. **Checkpoint holds**
  (`gk hold`, `gk resume --job`): a per-job hold-signal file
  whose path reaches the job as `$SYNCYARD_HOLD_FILE` (new
  optional `hold_file` input on the run/wsl workflows); a job
  that polls it, prints `CHECKPOINT_PAUSED`, and exits 86 is
  harvested as the new state `held_at_checkpoint` — never
  reported as a completion or a failure. The signal contract
  is the file, not the writer: a host-local actor (the spec
  011 host guard, when a shutdown is imminent) can create
  the same per-job file directly with no gatekeeper
  round-trip, and the harvest is identical either way. All
  mutations ride
  the FR-013 verified writes and are ledgered; `gk status`
  shows the pipeline mode (RUNNING / PAUSED / FOCUSED /
  DRAINING), the waiting set in effective dispatch order with
  priorities and sources, and held-at-checkpoint jobs.
  Battery: `gatekeeper/test_spec013.py` (wired into CI), plus
  config, golden, and workflow-contract extensions. Not yet
  deployed to the fleet gatekeeper; that follows owner review
  of the spec 013 PR.

## [0.3.0] - 2026-10-31
### Added
- Spec 010 Phase 5 docs (T017 + T018, 2026-10-31):
  `SECURITY.md` is now the shipped security posture — the
  FR-009 invariant verbatim (the same sentence every skill
  edition carries), the posture in plain language (CI on
  GitHub-hosted runners only; self-hosted runners reachable
  solely behind the gatekeeper's `workflow_dispatch`; the
  gatekeeper's verified-write and exactly-once dispatch
  rules; the backchannel as dev/recovery only, carrying no
  job traffic), and adopter checklists for token scoping and
  runner isolation — alongside the existing reporting policy.
  `README.md` opens with the two-minute story FR-010
  requires (the problem, the scars that shaped the design,
  what Syncyard deliberately does not do), the security
  section links `SECURITY.md` above the quickstart, and
  per-host quickstarts cover Windows, WSL2 (via a Windows
  host), Linux, macOS, and Docker — including the fleet rule
  that cross-job payloads are never staged in a WSL venue's
  `/tmp` (it does not persist between jobs; stage in the WSL
  home directory or the job's working tree instead).
- Spec 011 — shutdown protection and recovery (owner
  direction, 2026-10-31): a host-side shutdown guard that
  delays a non-forced shutdown while active work drains
  (bounded checkpoint window, going-down record written
  first with the affected run ids, outbox notification),
  boot recovery that returns runners without an interactive
  logon (boot shape is an owner gate — service-mode would
  amend spec 007 FR-003), a generalized supervised-work
  vehicle from the production anchor pattern, gatekeeper
  reconciliation of every interrupted run against GitHub's
  terminal state, and a recovery report that states loss
  plainly. The spec's protection level is delay + drain +
  checkpoint + notify, never prevent — forced shutdowns and
  power loss are named outside the envelope. Specified only
  in this change; implementation follows in phases.
- Spec 010 Phase 4 (FR-006) — the agent skills pack:
  `skills/` ships eight editions generated from one shared
  source (`skills/_source/contract.md` via
  `skills/build_skills.py`) in the Amendment 1 ranking —
  Meta Muse (primary), Claude Code, GitHub Copilot, OpenAI
  Codex/ChatGPT, Cursor, Google Gemini/Antigravity,
  generic-agent, and a CLI-only fallback. Each edition carries
  the FR-009 security invariant verbatim; the T016 currency
  battery (`skills/test_skills.py`, in CI) pins every edition
  to the repo VERSION, the invariant, and the shared source
  (drift fails the build).
- Spec 010 — public release program (owner direction,
  2026-10-30): configurability as a single config file (lane
  count/names, watermarks, budgets, endpoints, transport
  ceiling), a tested host matrix (Windows, WSL2, macOS, Linux,
  Docker), permanent backchannel hardening (self-test, stale
  alarm, chaos battery), an agent skills pack with Meta Muse
  as the primary vehicle, per-requirement test batteries, a
  shipped security posture, and release 0.3.0 when green. A
  GitHub `gh`-adoption pitch brief is prepared under FR-011
  and is not sent without the owner's explicit word. Specified
  only in this change; implementation follows in phases.
- Spec 010 Amendment 1 (2026-10-30): FR-012 ships Syncyard as
  a GitHub CLI extension (`AXIOVEX/gh-syncyard`, thin Go
  dispatcher, official precompile action); FR-006 agent-skill
  editions ranked by market share (Meta Muse primary, Claude
  Code, Copilot, Codex/ChatGPT, Cursor, Gemini/Antigravity,
  generic). Phase 1 slice 1: `config/syncyard.example.yaml` +
  `gatekeeper/config.py` loader/validator with the
  `test_config.py` golden battery (25 checks, all passing);
  additive — the gatekeeper does not read config yet.
- Spec 010 Phase 7 (FR-012) SHIPPED 2026-10-31:
  `AXIOVEX/gh-syncyard` v0.1.0 — the GitHub CLI extension.
  Thin Go dispatcher over the installed gatekeeper
  (lookup: $SYNCYARD_GK / $SYNCYARD_HOME / ~/syncyard /
  ~/.syncyard / beside the binary / ./gatekeeper);
  precompiled binaries via the official precompile action;
  install proof: `gh extension install AXIOVEX/gh-syncyard`
  then `gh syncyard status` against the live fleet.
- Spec 010 Phase 1 slice 2 (T003/T004, 2026-10-31): the
  gatekeeper now reads its effective configuration at
  startup — lane set, runner labels, per-lane concurrency,
  watermark tables, budgets, and dispatch knobs all bind
  from `syncyard.yaml` deep-merged over the built-in
  defaults (the pre-config constants verbatim); invalid
  config fails closed with a named error.
  `gatekeeper/test_golden.py` proves byte-identical
  dispatch decisions under defaults vs the fleet config,
  and runs in CI.
- Spec 010 Phase 1 COMPLETE (T005, 2026-10-31): the fleet
  gatekeeper now runs on the configuration system — wired
  code deployed behind a queue snapshot (534 records,
  open-set 5) with exact reconciliation, and the fleet
  `syncyard.yaml` in place beside the gatekeeper.
- Spec 010 Phase 2 begins (T006, 2026-10-31): hosts declare
  the transport workflows they serve (execute/grab) and
  their identity markers (a per-type contract); the
  gatekeeper routes by host and refuses, at submit/adopt,
  any job addressed to a venue that does not serve its
  workflow — no silent fallback. Config validation covers
  the new shape; the golden battery pins the routing
  matrix across the fleet hosts plus a config-only macOS
  host.
- Spec 010 T007 (2026-10-31): the shipped transport
  workflows are generalized — a `lane` input addresses the
  runner, hardcoded label lists are gone, and each file
  states its host contract; execution bodies unchanged
  from the proven production shape. New contract battery
  in CI pins dispatch-only triggers and lane binding.
- Spec 010 Phase 2 COMPLETE (T008/T009, 2026-10-31):
  native Linux and macOS transports (one shared script
  body, drift-checked), a Docker transport (caller image,
  declared mounts, results land on the host for grab
  return), and gatekeeper `--image`/`--mounts` submit
  options with host-config image fallback — all pinned by
  golden argv checks and a hosted smoke battery
  (ubuntu + macOS + a docker-required job).
- Spec 010 Phase 3 COMPLETE (T010–T013, 2026-10-31): the
  backchannel agent self-tests at start-up and reports
  the result in every heartbeat; `gk status` shows the
  heartbeat age with a stale alarm past five minutes;
  a 16-check chaos battery (duplicates, corruption, STOP,
  kill/restart) runs the real agent in CI; and the
  recovery runbook ships the four FR-007 cases with
  pre-written command bodies.
- Spec 009 — failsafe backchannel (owner override order,
  2026-10-29: Syncyard must have a failsafe backchannel for when
  Syncyard itself is down or blocked, with a dedicated channel
  just for Syncyard development in the Desktop Access chat).
  A dedicated channel `syncyard-dev` in the private repo
  `tbitcs/syncyard-backchannel` (`channels/syncyard-dev/cmd.json`,
  `out.json`, `heartbeat.json`), reserved exclusively for
  Syncyard development/recovery by the Desktop Access chat — no
  program job traffic, ever, and no other chat may post to it.
  The desktop agent (`backchannel/agent.sh`) runs as a WSL
  systemd user service independent of the runners, the tray, and
  the gatekeeper, syncs over git (no REST quota), executes only
  new, hash-verified command ids, honours a STOP kill switch,
  and never writes gatekeeper queue records. The VM-side client
  (`backchannel/bc.py`, stdlib only) sends through the GitHub
  contents API (works during VM write waves), refuses to
  overwrite an unacknowledged pending command unless `--force`,
  and verifies every send by read-back; offline tests live in
  `backchannel/test_bc.py`. Named recovery use cases: restart
  the packaged tray in the owner's session, read `tray.log`,
  restart a runner lane, read-only network diagnostics.
- Periodic Syncyard status-check duty, fleet-wide (spec 009
  FR-005): every agent that uses Syncyard checks status before
  submitting desktop work and at least once per hour during an
  active desktop campaign (`gk status`, otherwise the
  backchannel heartbeat/status); a stale heartbeat (>5 min) or a
  down tray/runner is reported to the Desktop Access chat, never
  worked around. SKILL.md carries the duty and a Failsafe
  Backchannel section.
- Job protection on any Syncyard change (spec 009 FR-006):
  begin with a queue snapshot (record count, aggregate sha256
  of queue files, open-job list) and end with reconciliation —
  the open-job set after must equal the set before, or every
  difference is named and explained. No job may be lost
  silently.

### Fixed
- Gatekeeper record updates are atomic swaps (spec 001 FR-013):
  a job record's file is only ever replaced by renaming a fully
  verified temp file over it — the record path is never opened
  for writing. The pre-FR-013 direct-write fallback truncated
  the standing record before verification could fail, which is
  how records were lost in the 2026-10-28 write waves despite
  verified-retry. A failed save now leaves the prior record
  standing untouched (or nothing, for a new record — no husk
  can exist), sweep-internal saves go through a safe wrapper
  that ledgers `save_failed` and continues the pass, and a job
  whose pre-dispatch save fails is skipped rather than
  dispatched unrecorded. Proven by the fault-injection battery
  in gatekeeper/test_fr013.py.
- Gatekeeper FR-012 refinement (same night): the failed-write
  cleanup removes only content that is not a valid record (0-byte
  or partial husks); a failed UPDATE leaves the last valid record
  standing and fails loudly. The first cleanup compared bytes and
  deleted a live record on a failed dispatch update - found and
  fixed under the pause-first fix discipline.
- Gatekeeper durable writes (spec 001 FR-012): every queue, ledger
  and control-file write is now write-verify-retry — unbuffered
  write, read-back byte verification, bounded retries with backoff,
  loud failure when unverifiable. The loader skips unreadable
  records with a warning, and a submission reports queued only
  after the loader verifies the record written. Defect escalation
  2026-10-27: the host VM intermittently refuses writes and can
  silently drop a buffered write, leaving a 0-byte file.


### Added

- Tray offline state is unmistakable: with no runners up (or one
  faulted) the tray mark renders in grayscale under its red slash;
  the color states mean at least one runner is live (FR-007).
- Tray icon and menu (spec 007 FR-007/FR-008): the notification
  icon is the official mark loaded from a copy embedded in the
  program itself — clean when all runners are up, amber slash when
  some are down, red slash when none are up or one has faulted. The
  menu gains Start all and Stop all above the per-lane controls.
- Runner directories move under the user profile
  (`%USERPROFILE%\Development\Syncyard\runners\<lane>`) — nothing
  Syncyard-owned sits in the drive root. Tray and watchdog defaults
  and the setup docs follow; the tray installer is rebuilt against
  the new locations (spec 007 T010).
- Spec 007 T007 — a real Windows installer for the tray:
  `SyncyardTray-Setup-0.1.0.exe` (NSIS; per-user, silent `/S` mode,
  registered uninstaller), published as a GitHub Release asset with
  a SHA-256, plus `assets/logo.ico` and the tray icon now carrying
  the project mark with a state badge. README and SETUP.md document
  the download route and manual from-source installation.
- Spec 007 — windowless runner supervision: the Syncyard Tray app
  (`runner/tray/SyncyardTray.cs` + `install-tray.ps1`) supervises the
  runners from the owner's tray — hidden starts, per-lane state,
  capped restarts with an OWNER NEEDED alert, stray-console hiding,
  logon autostart. `setup-runner.ps1` now starts runners hidden too,
  and the tray subsumes the planned watchdog scheduled task.
- Gatekeeper push notifications: jobs register a chat with
  `--notify-chat` (on submit or adopt) and receive started (with
  expected length), completed (marker verdict), and W2-escalation
  pushes via the new `gk outbox` command — no polling and no new
  infrastructure (spec 001 FR-011).
- Spec 006 closed: the polish pull request merged 2026-10-27 under
  the owner's one-time protection pause, with protection verified
  identical afterward.
- New project logo: a marshalling-yard mark (three lanes converging through
  one gate) as `assets/logo.svg`, with rendered `assets/logo.png` and
  `assets/social-preview.png` alongside it. Replaces the previous emblem.
- `CONTRIBUTING.md` (development setup, the self-hosted security invariant
  for contributions, review and changelog discipline) and
  `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).
- README badges for CI status and the latest release.
- `gk.py status` now leads with queued/running counts and an ISSUES
  section (watermark breaches, failed or pending log harvests, stale
  dispatches, completions without a marker verdict), and takes a
  `--repo` flag that adds project-repo health: latest CI runs, open
  Dependabot alerts, and open Dependabot pull requests.
- A daily health check reviews the status board, the ledger, CI, and
  Dependabot, and reports issues to the operating chat.
- Spec-kit + AEE governance: a project constitution
  (`.specify/memory/constitution.md`) and numbered specs under
  `specs/` (001 gatekeeper dispatch, 002 runner lanes, 003 status /
  logging / health, 004 WSL2 management, 005 fleet cutover,
  006 repository and governance), each with an `aee-claims.json`
  recording every requirement as a claim with evidence and a
  falsification test.
- The SKILL.md currency rule, stated in SKILL.md and CONTRIBUTING.md:
  SKILL.md is updated in the same change as any behavior change.
- Gatekeeper pause/resume: `gk pause --reason <text>` holds dispatch
  of program jobs (the watch pass continues; status shows the pause
  banner; sweep summaries print PAUSED) and `gk resume` clears it.
  Jobs submitted with `--maintenance` dispatch during a pause,
  bypassing the active cap and lane serialization but never the
  duplicate check — for operator work on the fleet itself.
- Gatekeeper stop/start and purge, completing the lifecycle CLI:
  `gk stop` is the hard halt (sweeps return before spending any API
  budget — no watch, no dispatch) and `gk start` clears both a stop
  and a pause. `gk purge` removes terminal (completed/cancelled) job
  records, dry-run unless `--yes`, with an optional
  `--older-than-days` filter and `--ledger` to archive the ledger
  to a fresh one.

### Fixed

- Log harvest now falls back to the REST logs archive when
  `gh run view --log` cannot read a run (observed in the field: some
  runs' logs are unreadable through the CLI but fine through the API).
  The harvest records which source supplied the log.
- Job-state saves fall back to a shell write when the host denies the
  native temp-file write path, so sweeps keep persisting state through
  intermittent write restrictions.

### Changed (logging)

- Sweeps now also write their one-line summary to a human-readable
  rotating log (`gatekeeper.log`, 1 MB, one backup) alongside the
  append-only JSON ledger.

### Changed

- **License: the project is now licensed under the Apache License,
  Version 2.0** (owner decision, 2026-10-27), superseding the
  all-rights-reserved terms the 0.1.x releases shipped with. The
  historical release entries below are unchanged.
- Project renamed from Sallyport to Syncyard. The repository is now
  `AXIOVEX/syncyard`; GitHub redirects the previous repository URL.
  Documentation, the agent skill, workflow and client repository
  constants, setup defaults, templates, and release metadata now use
  the Syncyard name.
- README now describes the project in terms of synchronized runner
  lanes: a marshalling yard for CPU and GPU jobs dispatched through
  GitHub Actions.
- The version stays at 0.1.1 for now, by owner decision: no version
  bump until the project is fully landed, passes full CI, and has
  served as the fleet's daily driver for a few days.

## [0.2.0] - 2026-10-29

### Added

- Spec 009 — failsafe backchannel (see the `[Unreleased]` entry
  above for the full description): the dedicated `syncyard-dev`
  channel in the private `tbitcs/syncyard-backchannel` repo, the
  WSL systemd agent and its installer, the `bc.py` contents-API
  client with offline tests, the fleet-wide periodic
  status-check duty (FR-005), and queue snapshot/reconciliation
  job protection on any Syncyard change (FR-006). Released by
  the owner's explicit "deploy new version" order of 2026-10-29,
  which overrides the earlier version hold.

## [0.1.1] - 2026-10-27

### Fixed

- Clients: the run-discovery polling loop used an unused loop variable
  (`for i in $(seq ...)`), which failed the ShellCheck CI step
  (SC2034). The loops now use the conventional throwaway variable;
  behavior is unchanged. Caught by the first CI run of the scaffold.

## [0.1.0] - 2026-10-27

### Added

- Project scaffold: README with the architecture, security model,
  quickstart, and lane table; the project logo
  (`assets/logo.png` — a fortress gate with a raised portcullis and a
  dispatch arrow passing through).
- `SKILL.md`, the agent operating skill: gatekeeper commands, lane
  routing rules, the marker-verdict rule, API discipline, watermark
  escalation etiquette, hardware scenario-job rules, and the
  break-glass note.
- The gatekeeper (`gatekeeper/gk.py` with its README): one
  dispatch/watch door with API-headroom stand-down, duplicate-dispatch
  refusal, lane serialization, batched watching, marker harvesting,
  and watermark recording that never cancels.
- Runner kit (`runner/`): the dispatch-only workflows `run`, `wsl`,
  and `grab`; `setup-runner.ps1` for per-lane runner registration as a
  user-window process; `runner-watchdog.ps1` to keep runner listeners
  alive; `SETUP.md`, the owner-session runbook ending in per-lane
  identity proofs.
- Convenience clients (`clients/`) for interactive one-offs, with the
  gatekeeper named as the preferred route.
- `docs/STANDARD.md` — the fleet standard: API budget discipline,
  runner lanes, and job health (watermarks and the check-before-cancel
  ladder).
- `docs/MIGRATION.md` — the cutover plan with its gates: re-register
  runners under this repository during the setup session, prove each
  lane by identity job, delete the predecessor runner repositories
  only after that proof, and archive (never delete) the predecessor
  administration repository that holds the frozen emergency path.
- `SECURITY.md` — the security model for a public repository with
  self-hosted runners (dispatch-only triggers, hosted-only CI,
  repository-scoped registration, no secrets in the repo) and the
  private reporting route.
- Continuous integration on GitHub-hosted runners: Python syntax and
  an offline gatekeeper smoke test, ShellCheck on the clients, a
  PowerShell parse check on the runner scripts, a changelog/version
  check, and a guard that fails any workflow combining self-hosted
  runners with unprivileged triggers.
- Repository policies: pull request and issue templates, Dependabot
  for GitHub Actions, CODEOWNERS, and branch protection requiring a
  pull request, an approving review, and green CI.
- License: all rights reserved, © Axiovex Systems, LLC.

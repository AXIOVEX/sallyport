# Feature Specification: Rust Core Architecture

**Feature Branch**: `feat/038-rust-core-architecture`
**Created**: 2026-11-07
**Status**: **RATIFIED 2026-11-07 as a block with specs 034-039 (owner decision, Desktop Access chat). Implementation may proceed under the program sequence.**
**Direction**: Owner language policy 2026-11-06: Rust for core work and as much as possible; from there, the best language for the job, with written justification required for any non-Rust core component. Decisions Q1-Q7 (2026-11-06) as amended: one-move cutover; macOS full host (spec 039, superseding the Q2 deferral); Axiovex OV code-signing certificate as an owner purchase act; job control successor-only (spec 037); specs 029/030 folded in; fleet paused except iSMART until the acceptance gate is green; one version number, one release train.
**Companions**: Spec 032 (daemon design, inherited wholesale in section 2), spec 033 (Docker-native execution, inherited in section 4), spec 030 (settle-first occupancy and work-conserving scan, folded in section 3), spec 029 (tray watchdog package, folded into the app/update sections), spec 036 (the binding test gate).
**Sources folded in**: REDTEAM-RUST-PROGRAM-2026-11-06.md — all 24 findings (C1-C5, H1-H7, M1-M8, L1-L4), all 33 robustness requirements (ERR-01..09, REP-01..07, FIX-01..08, UPD-01..09), and all 8 missing program items appear below as FRs with named tests.

## 1. Cutover (Q1, red-team C1)

The Python gatekeeper is replaced in **one move**. The word "rollback" is **removed** from the cutover plan unless a rehearsed reverse migration exists: before cutover day, a rehearsal on a byte-copy of live state MUST prove the full round trip (Python state to Rust state to Python rollback) with zero record loss and zero double dispatch, executed and recorded; the drill re-runs on every release train thereafter. If the round trip cannot be proven, the cutover plan MUST instead carry a state-freeze window in which in-flight jobs (including iSMART's exception work) complete before the switch, named as such. An unrehearsed "keep the old binary on hand" is a hypothesis, not a capability, and MUST NOT be described as rollback.

## 2. State: RAM-authoritative daemon (spec 032 inherited)

The gatekeeper is a long-lived Rust daemon (`gk serve` successor) holding live job records in RAM as the authority, serving a Unix domain socket; CLI verbs are thin socket clients with a direct-file fallback that is never worse than today. Single writer: the daemon is the only writer of queue state while it runs, enforced by an exclusive flock the fallback path also takes. Write-behind cache flushes dirty state every 60 s and on every state transition using verified writes (read-back verification unchanged); a failed flush is a non-event, retried next cycle, and flush health is a snapshot field. History stays file-backed, compacted to an archive with a SHA-256 manifest (`migrate-history` successor). The pre-dispatch record is written off-VM (door-repo state branch via the Contents API) and confirmed **before** dispatch, so dispatch works mid-wave.

Spec 032's four kickoff decisions, inherited and decided here: (1) the fleet mirror's lift leg migrates to a `gk dispatch` CLI verb **before or with** cutover (the mirror tolerates a failed cycle; the failure mode is safe); (2) control files (PAUSED / STOPPED / hosts / locks) ARE a fifth RAM class (otherwise pause/stop stay wave-blocked); (3) the born-denied-daemon cure rule is ratified: a daemon whose flush failures span a sampler-reported clear window restarts itself once; (4) the daemon MUST be on the memory-watchdog exclusion set before cutover, tested (red-team C2).

**Fencing (red-team H1)**: the off-VM record carries a monotonic epoch, acquired by compare-and-swap on the Contents API blob sha. A daemon may dispatch only while it holds the current epoch. Two daemons, one record: exactly one dispatches.

**Dispatch protocol (red-team C2)**: dispatch is a two-phase ledgered protocol with an idempotency key verifiable from GitHub-side evidence alone. Boot reconciliation is mandatory after every start (VM replacement cadence ~2 h makes it the daemon's most-executed path): load disk state plus off-VM records, reconcile against GitHub run states, resolve disagreements **in GitHub's favor**, ledger the divergence, and never overwrite a payload verdict (ERR-07).

**State-home separation**: gatekeeper state lives in a state home independent of the code's install location, so a code update never moves, resets, or shadows state (field lesson: BASE derived from the file's directory welded state to the checkout on mini-server).

## 3. Lanes bind to venues (spec 030 folded; venue model)

A **lane binds to a venue, not a runner label**. Venues: `native` (host OS, hardware jobs permanently), `docker` (section 4), `windows-vm` (section 5). A host offers the venues its capability probe proves. Dispatch occupancy is computed settle-first (finished runs never hold a lane), the scan is work-conserving (a blocked head never stops later jobs), caps bind exactly, and starvation past `dispatch.starvation_after_s` (default 900) ledgers `dispatch_starved` / `dispatch_unstarved` exactly once per episode (spec 030, folded).

**Mini-server battery legs 2-3 are acceptance cases for this model**: a desktop-submitted container job executing on mini-server's local Docker venue (leg 2), and the same with the desktop off (leg 3). Both were deferred under the old lane table because it had no shape for a local-Docker venue; under this spec they must pass before the lane model is called done.

**Resource honesty (red-team H3)**: declared job needs are enforced as hard limits (cgroup or venue equivalent) so an under-declared job kills itself, not its host or co-tenants. Measured-versus-declared use is recorded per program in the ledger; repeat under-declaration automatically widens that program's enforced envelopes.

**Capability registry (red-team M7)**: every capability record carries its observation time; the scheduler refuses placement on records older than a stated threshold, ledgering the refusal as a named constraint; unknown is recorded as unknown, never zero. Opportunistic nodes (red-team M2) are classed non-requireable: no job may hard-require one, and distributed-inference jobs declare their node set as hard requirements with mid-job node loss defined as job failure.

## 4. Docker-native execution (spec 033 inherited)

Four shared images only — `linux-dev`, `data-py`, `zephyr`, `node-web` — pinned by digest, publishing multi-arch manifests. Warm containers per (host, image) with persistent named volumes. Containers run non-root, read-only rootfs, drop all capabilities; secrets are injected per job via tmpfs and removed at job end; the Docker socket is **never** mounted into a job container; egress is deny-by-default with per-image allowlists. Job-start asserts a clean environment including **secret absence** over every location a prior job could have written (red-team M1): a planted leftover secret must make the next job refuse to start, and the leftover must never be readable by it. No project job may require WSL2; WSL exceptions are named, owned, and carry exit conditions (laptop backchannel agent converts last, in a jointly approved iSMART window).

## 5. Windows VM venue

One Windows guest per physical host under KVM/libvirt on a Linux-native host, with ACPI MSDM/SLIC + SMBIOS passthrough so the host's own OEM key activates the guest on that machine. Guests never move hosts. A per-host **activation proof** runs before the guest's first job — and activation state is thereafter a **continuously probed** capability field on the guest's heartbeat (red-team H2): deactivation takes the venue offline with a named constraint. The libvirt machine type and SMBIOS presentation are pinned in version control; changes only by reviewed migration. The guest's patch-and-reboot policy is stated in its host profile with fleet visibility. Guest clock drift is bounded by the record discipline in section 9 (monotonic sequence over wall clock).

## 6. Ghost-record reconciliation (first-class mechanism)

A record whose run is terminal on GitHub but whose queue record still reads dispatched/running is a **ghost**. Reconciliation is a built-in daemon function, not a hand edit: census from GitHub-side evidence, complete each ghost record truthfully from its run's log and conclusion, ledger an `operator_record_repair` per record. **Hold-the-waiting-queue-first (backlog item (o), 2026-11-07)**: before any ghost record is completed, a pause record or per-job holds MUST cover every waiting non-maintenance job, because completing ghosts frees lanes and would otherwise dispatch held work with no human decision (the 2026-11-07 state: 8 ghosts accidentally enforcing the owner's pause over two waiting jobs, one of them a destructive delete the owner handles himself). The holds are released only by a named operator act, per job or per program.

## 7. Publishing, locks, and readiness

- **Per-destination verified publishing** (defect: single-path publisher, 2026-11-06): every multi-destination publish writes **every** destination and verifies each by reading it back and matching content identity; success is reported only when all destinations verify (ERR-04).
- **Dead-owner lock takeover** (defect: stranded lock, 2026-11-06): every lock is taken over automatically when its owner is provably dead on the same host, regardless of lock age, and never while its owner lives (ERR-05).
- **Tailnet-address readiness**: a service bound to a tailnet IP is systemd-owned with a readiness wait for the address to exist on tailscale0 (defect: fleet-registry lost on reboot, 2026-11-06, mini-server).
- **Hash-pin discipline**: pins are compared mechanically from source records; no verification may rest on eyeballing a rendered string.
- **Log-harvest fidelity**: see spec 034 FR-034-08; the daemon side quarantines foreign lines at harvest.

## 8. Robustness requirements (red-team ERR / REP / FIX / UPD, verbatim or stronger)

**Recovery (ERR)**: ERR-01 fault-injection at every protocol step, zero double / zero lost dispatches. ERR-02 VM loss loses no dispatched job, verdict, or acknowledged-and-recorded submit; unrecorded acknowledged submits are reported by name at reconciliation. ERR-03 host power loss leaves jobs in named recoverable states, reconciled from GitHub evidence before new work on that lane. ERR-04 per-destination verified publish (section 7). ERR-05 dead-owner lock takeover (section 7). ERR-06 lossless migration round trip with per-record manifest; cutover blocked on any failure. ERR-07 GitHub wins reconciliation disagreements; verdicts never overwritten. ERR-08 a detected write wave switches writers to fail-fast with a named ledger constraint; bounded retries; no pass dies on an uncaught write error. ERR-09 every component self-starts from its own state after any restart and announces recovered state and gaps by name.

**Reporting (REP)**: REP-01 every verdict-reaching failure, crash, or failed repair produces exactly one report per configured channel (GitHub issues + Muse notification); nothing fails silently. REP-02 dedupe on a structured key (component, defect class, normalized signature with run ids, timestamps, paths, counts excluded by construction); repeats append with a count (red-team H5). REP-03 daily cap on new issues; overflow and below-high severity batch into one digest; cap breach is visible in the tray. REP-04 every report carries a diagnostics bundle (train versions, identity, last N ledger events, payload verdict where one exists, capability record). REP-05 severity routing is mechanical from a published mapping; no component picks its own severity. REP-06 reports go only to private destinations; bundles are scanned for credential-shaped content and filing is refused and escalated on any hit. REP-07 the reporting credential can create and comment on issues and nothing else; reporting loss never blocks execution; backlog bounded and visible.

**Credentials (red-team C5)**: dispatch, state-branch write, status publish, and issue creation use **separate least-privilege credentials**; the reporting credential cannot dispatch; the state credential cannot write code or workflows. An automated token-scope audit is a named release test (spec 036 FR-036-12).

**Auto-fix bounds (FIX)**: FIX-01 repairs are an enumerated recipe list, each with named defect, precondition, and post-repair verification read from the repaired thing's own record. FIX-02 at most one repair per component per cycle, a stated daily maximum, exponential backoff. FIX-03 two consecutive failed repairs latch the recipe off until a human or the supervising agent clears it. FIX-04 the supervision graph is acyclic; no component repairs/restarts/updates another in a cycle (red-team H4). FIX-05 never auto-fixed: job payload code/content, git history/branches, queue-record verdicts, security policy, host firmware/activation, any deletion of data, volumes, or backups. FIX-06 hardware-affecting repairs (GPU power limits, unlock re-application) verify after apply and revert to known-safe on failure, no retry in the same boot. FIX-07 every repair attempt is ledgered with recipe, precondition evidence, verification result; an unledgered repair is a defect in the repairer. FIX-08 a global repair kill switch exists (tray + CLI, spec 037 FR-037-10), halting self-healing but not job execution until manually cleared.

**Updates (UPD)**: UPD-01 one version number from one VERSION source across all components; every binary and the app report the train number; disagreement is a defect state. UPD-02 staged rollout: exactly one canary host first; fleet rollout blocked until the canary passes the health gate for a stated soak. UPD-03 the health gate is identity stamp + capability probe refresh + one synthetic end-to-end job with a payload verdict of success; liveness alone never satisfies it (red-team C3). UPD-04 a failed gate rolls back automatically from the locally retained prior artifact, without network, without a visible console window. UPD-05 the updater never updates itself in the same train as components it must recover; updater releases travel alone. UPD-06 no update interrupts a running job; hosts with long jobs defer and report the deferral. UPD-07 until code signing is live, artifacts are accepted only when sha256 matches a pin in the off-VM state record; mismatch is refused, ledgered, reported high severity (red-team C4). UPD-08 a per-release compatibility matrix names legal mixed-version combinations; illegal combinations are refused with a message naming the required version; refusal never corrupts state (red-team M6). UPD-09 every update, deferral, refusal, and rollback is ledgered with before/after train numbers per host.

**Signing (Q3)**: the Axiovex OV code-signing certificate is an **owner purchase act**. Until it exists, builds are CI-built and identity-stamped, and UPD-07's dual-channel pinning is mandatory; auto-update is disabled on any host where neither signing nor pinning is in force.

## 9. Records, ordering, and contracts

Every record carries a monotonic per-source sequence number alongside wall-clock time; ordering in reconciliation uses sequence, wall clock is display only (red-team L4). History compaction preserves a verifiable hash chain (spec 036 FR-036-13). The payload contract, door-workflow pinning, and CLI/daemon contract versioning are per spec 034 FR-034-09..11. The `gh syncyard` extension is a thin shim generated from the CLI schema (red-team L2; spec 036 FR-036-14) and is made solid **before** any Muse connector wraps it; the Rust core exposes an MCP server (job submission/status, capability registry, streaming progress, incident reporting, log harvest) for Muse integration.

## 10. Hosts, profiles, and the test matrix

Fleet naming (ratified 2026-11-06): **tower-compute** (was tristen-desktop), **laptop-bench** (tristen-laptop), **mini-server**, **sbc-inference** (Jetson), **tower-offsite**, **air-inference** (Mac). Registry and anchors adopt each name as the machine (re)joins under its install hostname; old-name mappings persist until each cutover lands. Anchor resolution failure is a named constraint that stops placement, never a fallback to another address form (red-team M5). The Tailscale ACL matrix is a versioned artifact with automated probes from each node class proving the deny cases; backup encryption keys are held **off** the backup target (red-team M5).

Host profiles are declarative and **transactional** (red-team M4): pre-change restore point or snapshot, post-change health gate with automatic revert on failure, one machine at a time under canary discipline.

**Host test matrix**: tower-compute (Windows, then Pop!_OS), laptop-bench (Windows, pre-reformat), mini-server (Ubuntu Server; battery legs 2-3 per section 3), **tower-offsite as the Linux tray test host** with its two box-specific proofs: (a) windowless COSMIC coexistence — the Syncyard app and agent run on the COSMIC desktop with no visible console window, proven from systemctl + loginctl records; (b) timer firing on schedule under masked sleep targets + linger with no local login. sbc-inference joins the matrix when handed over; air-inference per spec 039.

**Writer-route defects (2026-11-06), as requirements**: (a) a non-elevated runner cannot raw-write a physical drive — any job needing raw device access must route through the elevated kit path and say so in its record; (b) the elevated kit's raw-device open must use a proper device handle, not the plain FileStream constructor that failed with "The parameter is incorrect". Both carry defect-named tests.

## 11. Staged iSMART fold-in (red-team H7)

When the acceptance gate (spec 036) is green, iSMART migrates **first** (pre-registered order) — staged by consequence: read-only and build jobs first; flash-class jobs only after a named count of clean hardware-adjacent jobs plus a native-venue simulation scenario in the acceptance suite; flash jobs stay on explicit per-job owner go until that count is met. This chat monitors the migration live.

## 12. Missing program items, owned here

(1) Restore verification — spec 036 FR-036-10. (2) **Threat model document**: a named deliverable of this spec covering token scopes, the tailnet, the public repo beside private state, door-repo script execution, and per-host keys, with named adversaries (repo compromise, lost laptop, malicious payload from a compromised program chat). (3) **Diagnostics privacy/retention rule**: what may leave the machines, where it may be filed, when it is deleted (beyond REP-06's credential scan). (4) **Door-repo ownership**: tbitcs/desktop-runner workflows are owned by this spec for versioning and pinning (section 9) until any replacement is specced. (5) **Control-plane end-state**: the daemon's eventual move to the always-on server (mini-server) is owned by this spec, with its fencing story (section 2); two control-plane homes is a worse state than either one and is never a steady state. (6) **Python decommissioning plan**: installed copies, the publisher's two homes, stale clones, and the rollback binary's retention period are enumerated in a named plan under this spec. (7) **GitHub API budget**: the Rust system carries a stated budget (account-wide 5,000/hour bucket), per-component shares, 3-5 minute minimum polling, and stand-down behavior on 403 as requirements, not habits. (8) Owner recovery runbook — spec 036 FR-036-08.

## Requirements

| FR | Requirement | Named test case(s) | Acceptance mode |
|---|---|---|---|
| FR-038-01 | Cutover MUST be one move; "rollback" MUST NOT be claimed unless the rehearsed round-trip proof of section 1 exists and is current for the train (C1). | T038-01 cutover-roundtrip-rehearsal | gate |
| FR-038-02 | If the round trip cannot be proven, the cutover plan MUST carry a named state-freeze window instead of any rollback claim (C1). | T038-02 freeze-window-fallback-named | gate |
| FR-038-03 | The daemon MUST hold live records RAM-authoritative, serve a Unix socket, and enforce single-writer via flock shared with the fallback path (spec 032). | T038-03 daemon-single-writer-socket | test |
| FR-038-04 | Dirty state MUST flush every 60 s and on transition with verified writes; flush health MUST be a snapshot field (spec 032). | T038-04 write-behind-flush-60s | test |
| FR-038-05 | The pre-dispatch record MUST be written off-VM and confirmed before dispatch; dispatch MUST work mid-wave (spec 032). | T038-05 dispatch-mid-wave | test |
| FR-038-06 | Control files MUST be a fifth RAM class; the mirror lift leg MUST migrate to a `gk dispatch` verb before/with cutover; the born-denied cure rule (one self-restart across a clear window) MUST hold (spec 032 kickoff decisions). | T038-06 fifth-ram-class-and-cure-rule | test |
| FR-038-07 | The daemon MUST be on the memory-watchdog exclusion set before cutover (C2). | T038-07 watchdog-exclusion-proven | gate |
| FR-038-08 | Dispatch MUST be a two-phase ledgered protocol with a GitHub-verifiable idempotency key (C2 / ERR-01 support). | T038-08 dispatch-idempotency-key | test |
| FR-038-09 | A daemon MUST dispatch only while holding the current fencing epoch, acquired by CAS on the off-VM record's blob sha (H1). | T038-09 two-daemons-one-dispatches | test |
| FR-038-10 | Boot reconciliation MUST be mandatory, MUST resolve disagreements in GitHub's favor, MUST ledger divergences, and MUST never overwrite a payload verdict (ERR-07). | T038-10 boot-reconciliation-github-wins | test |
| FR-038-11 | Gatekeeper state MUST live in a state home independent of code location. | T038-11 state-home-separation | test |
| FR-038-12 | Lanes MUST bind to venues, not runner labels; occupancy MUST be settle-first, the scan work-conserving, caps exact, starvation ledgered once per episode (spec 030 fold-in). | T038-12 venue-lane-settle-first-scan | test |
| FR-038-13 | Mini-server battery legs 2 and 3 (desktop-submitted container job on the local Docker venue; same with desktop off) MUST pass before the lane model is done. | T038-13 mini-server-leg2-container-job; T038-14 mini-server-leg3-desktop-off | test |
| FR-038-14 | Declared job needs MUST be enforced as hard limits; measured-vs-declared MUST be ledgered per program; repeat under-declaration MUST widen enforced envelopes (H3). | T038-15 resource-limits-enforced; T038-16 underdeclaration-widens-envelope | test |
| FR-038-15 | Capability records MUST carry observation time; placement MUST refuse stale records with a ledgered named constraint; unknown MUST record as unknown (M7). | T038-17 stale-capability-refused | test |
| FR-038-16 | Opportunistic nodes MUST be classed non-requireable; distributed-inference node loss mid-job MUST be job failure, never silent degradation (M2). | T038-18 opportunistic-node-not-requireable | test |
| FR-038-17 | Docker execution MUST use only the four shared images pinned by digest with multi-arch manifests; warm containers; persistent named volumes (spec 033). | T038-19 four-images-pinned-multiarch | test |
| FR-038-18 | Job containers MUST be non-root, read-only rootfs, drop-all-caps, secrets via per-job tmpfs removed at job end, deny-by-default egress with per-image allowlists; the Docker socket MUST never be mounted (spec 033). | T038-20 container-isolation-posture | test |
| FR-038-19 | Job start MUST assert secret absence everywhere a prior job could have written; a planted leftover MUST block start and MUST never be readable (M1). | T038-21 planted-secret-blocks-start | test |
| FR-038-20 | The Windows VM venue MUST use KVM/libvirt with MSDM/SLIC + SMBIOS passthrough, guests pinned per host, activation proof before first job (section 5). | T038-22 windows-vm-activation-proof | test + human-as-oracle |
| FR-038-21 | Guest activation MUST be continuously probed on heartbeat; deactivation MUST take the venue offline with a named constraint; machine type/SMBIOS MUST be pinned in version control (H2). | T038-23 activation-deactivation-offlines-venue | test |
| FR-038-22 | Every binary MUST ship linux-aarch64 alongside linux-x86_64 and windows-x86_64; the four images MUST publish multi-arch manifests. | T038-24 aarch64-artifacts-in-release | gate |
| FR-038-23 | Ghost reconciliation MUST be a built-in function completing records truthfully from GitHub evidence with a ledgered repair event per record (section 6). | T038-25 ghost-reconcile-from-github-truth | test |
| FR-038-24 | Before any ghost is completed, a pause record or per-job holds MUST cover every waiting non-maintenance job; holds MUST release only by named operator act (backlog (o)). | T038-26 hold-queue-before-ghost-completion | test |
| FR-038-25 | Every multi-destination publish MUST write and read-back-verify every destination before reporting success (ERR-04; defect-named test in FR-038-42). | T038-27 publish-verifies-every-destination | test |
| FR-038-26 | Every lock MUST be taken over when its owner is provably dead, regardless of age, and never while its owner lives (ERR-05). | T038-28 dead-owner-lock-takeover | test |
| FR-038-27 | Tailnet-IP-bound services MUST be systemd-owned with a readiness wait for the tailnet address. | T038-29 tailnet-readiness-wait | test |
| FR-038-28 | Pins MUST be compared mechanically from source records; eyeball comparison MUST NOT be a verification step anywhere. | T038-30 hash-pins-compared-mechanically | gate |
| FR-038-29 | Fleet names MUST be tower-compute, laptop-bench, mini-server, sbc-inference, tower-offsite, air-inference; anchor resolution failure MUST stop placement as a named constraint (M5). | T038-31 fleet-naming-and-anchor-failure | test |
| FR-038-30 | The ACL matrix MUST be a versioned artifact with automated deny-case probes per node class; backup keys MUST be held off the backup target (M5). | T038-32 acl-deny-probes; T038-33 backup-keys-off-target | test |
| FR-038-31 | Harvest MUST quarantine foreign marker lines (with spec 034 FR-034-08). | T038-34 harvest-foreign-line-quarantine | test |
| FR-038-32 | Killing the daemon at any dispatch step MUST yield zero double and zero lost dispatches after reconciliation (ERR-01). | T038-35 err01-fault-injection | test |
| FR-038-33 | VM loss MUST lose no dispatched job, verdict, or recorded submit; unrecorded acknowledged submits MUST be named at reconciliation (ERR-02). | T038-36 err02-vm-loss-accounting | test |
| FR-038-34 | Host power loss MUST leave jobs in named recoverable states, reconciled from GitHub evidence before new lane work (ERR-03). | T038-37 err03-power-loss-recovery | test |
| FR-038-35 | Migration MUST round-trip losslessly with a per-record manifest; any failure MUST block cutover (ERR-06). | T038-38 err06-migration-manifest | gate |
| FR-038-36 | A detected write wave MUST switch writers to fail-fast with a named ledger constraint and bounded retries; no pass may die on an uncaught write error (ERR-08). | T038-39 err08-wave-fail-fast | test |
| FR-038-37 | Every component MUST self-start from its own state after any restart and announce recovered state and gaps by name (ERR-09). | T038-40 err09-self-start-announce | test |
| FR-038-38 | All components MUST share one version number from one VERSION source on one release train; settings MUST live inside the one app as a separate surface (Q3/Q7). | T038-41 one-version-one-train; T038-42 settings-in-app | gate |
| FR-038-39 | Reporting MUST satisfy REP-01..07: exactly-one-per-channel, structured dedupe, daily cap with digest, diagnostics bundles, mechanical severity, private-only with credential scan, issue-only reporting credential (H5). | T038-43 rep01-exactly-one-report; T038-44 rep02-structured-dedupe; T038-45 rep03-daily-cap-digest; T038-46 rep06-credential-scan-refusal | test |
| FR-038-40 | Dispatch, state, publish, and reporting MUST use separate least-privilege credentials; the reporting credential MUST NOT dispatch and the state credential MUST NOT write code/workflows (C5). | T038-47 separate-credentials-least-privilege | test |
| FR-038-41 | Auto-fix MUST satisfy FIX-01..08: enumerated recipes, per-cycle/daily budgets with backoff, latch-off after two failures, acyclic supervision, never-fix list, hardware verify-and-revert, ledgered attempts, global kill switch (H4). | T038-48 fix02-repair-budgets; T038-49 fix03-latch-off; T038-50 fix05-never-fix-list; T038-51 fix06-gpu-verify-revert | test |
| FR-038-42 | The two 2026-11-06 publisher defects MUST be regression-tested by defect name: single-path publish and stranded lock. | T038-52 single-path-publisher-defect-2026-11-06; T038-53 stranded-lock-defect-2026-11-06 | test |
| FR-038-43 | Updates MUST satisfy UPD-01..09: one version, canary-first staged rollout, identity+synthetic-job health gate, local rollback without network or console window, updater travels alone, no interruption of running jobs, dual-channel hash pinning until signing, compatibility matrix, full ledgering (C3/C4/M6). | T038-54 upd02-canary-gate; T038-55 upd03-health-gate-is-identity; T038-56 upd07-tampered-artifact-refused; T038-57 upd08-mixed-version-refusal | test |
| FR-038-44 | Until the OV certificate (owner purchase act) is live, auto-update MUST be disabled on any host with neither signing nor dual-channel pinning in force (C4). | T038-58 unsigned-unpinned-host-no-autoupdate | test |
| FR-038-45 | Host profiles MUST apply transactionally: restore point/snapshot first, health gate after, automatic revert on failure, one machine at a time (M4). | T038-59 profile-transactional-revert | test |
| FR-038-46 | Every record MUST carry a monotonic per-source sequence beside wall-clock time; reconciliation ordering MUST use sequence (L4). | T038-60 sequence-orders-reconciliation | test |
| FR-038-47 | Tower-offsite MUST be in the host test matrix as the Linux tray test host, passing (a) windowless COSMIC coexistence and (b) timer-under-masked-sleep proofs from box records. | T038-61 tower-offsite-cosmic-windowless-proof; T038-62 tower-offsite-timer-masked-sleep-proof | test + human-as-oracle |
| FR-038-48 | Raw-drive jobs MUST route through the elevated kit path and say so in the record; the kit MUST open devices via a proper device handle (writer-route defects, 2026-11-06). | T038-63 non-elevated-raw-write-2026-11-06; T038-64 elevated-raw-device-open-2026-11-06 | test |
| FR-038-49 | The iSMART fold-in MUST stage by consequence: read-only/build first, flash only after the named clean-job count plus the native-venue simulation scenario, per-job owner go for flash until then (H7). | T038-65 ismart-staged-fold-in | gate |
| FR-038-50 | The program MUST deliver: a threat model document, a diagnostics privacy/retention rule, a Python decommissioning plan, and a stated GitHub API budget with per-component shares and 403 stand-down (missing items 2, 3, 6, 7). | T038-66 program-deliverables-exist | gate |
| FR-038-51 | The control plane's end-state MUST be one home: the server migration (with fencing) is specced under this spec; two control-plane homes MUST never be a steady state (missing item 5). | T038-67 single-control-plane-home | gate |
| FR-038-52 | The door repo's workflows MUST be versioned and pinned under this spec's ownership until a replacement is specced (missing item 4). | T038-68 door-repo-versioning-owned | gate |
| FR-038-53 | The Rust core MUST expose an MCP server (submission/status, capability registry, streaming progress, incident reporting, log harvest); the `gh syncyard` extension MUST be solid first, the Muse connector a thin wrapper after (sequencing ruling). | T038-69 mcp-surface-after-extension | gate |
| FR-038-54 | No component of the core MAY open a visible console window on any platform, ever (fleet hard constraint). | T038-70 core-windowless-everywhere | test |

## Falsifiers

- Two daemons against one off-VM record both dispatch: this spec has failed (H1).
- A tampered update artifact installs on any host: this spec has failed (C4).
- Completing ghost records dispatches a held waiting job: this spec has failed (backlog (o)).
- A health gate passes on liveness alone: this spec has failed (C3).
- Mini-server leg 3 (desktop off) cannot run a desktop-submitted container job: the venue lane model has failed.

## Tasks

- T001: This spec.
- T002: Threat model + privacy/retention rule + decommissioning plan + API budget (section 12 deliverables).
- T003-T012 (after ratification): daemon, fencing, venues, Docker posture, Windows VM venue, reconciliation, publishing/locks, reporting, repair discipline, updater — each landing behind spec 036's gate.

## Out of scope

Implementation of any of the above before ratification of the block. Tray presentation detail (spec 035), tray job-control surface (spec 037), macOS host detail (spec 039). Non-Rust core components without written justification in their owning spec.

# Tasks: Spec 011 — Shutdown Protection and Recovery

Sequenced so the production fleet is never at risk: the
record-keeping (Parts A/B gatekeeper side) lands before any
host component can act on it, the guard is proven on a test
host before it touches the production host, and the boot
shape is an owner gate (FR-009) that no phase may pre-decide.
The fleet's existing disciplines apply throughout: pause-first
for any change to live components, queue snapshot +
reconciliation around every fleet deploy (spec 009 FR-006),
and SKILL.md / skills-pack updates in the same change as any
behavior (spec 011 FR-014).

## Phase 1 — Liveness and the going-down record (FR-001, FR-003, FR-006)
- [x] T001 Host liveness model: per-host last-seen record at
      the gatekeeper; sweep reads the host heartbeat (backchannel
      transport) and `gk status` renders host UP/DOWN against a
      configurable threshold. Golden battery pins the sweep's
      read cost and the status rendering.
- [x] T002 Going-down record format + verified-write writer
      (host side) and gatekeeper ingest: ledger `host_going_down`
      event, per-record `interrupted_by` annotation, dispatch
      hold for the host. Golden battery pins ledger + annotations
      byte-for-byte on fixtures.
- [x] T003 Outbox notification on going-down (FR-004): one group
      per affected notify registration + owning chat; duplicate
      suppression on re-sweep pinned by test.

## Phase 1 progress
- [x] T001–T003 (gatekeeper side, 2026-10-31): `liveness.
      down_after_s` config (default 900, positive number,
      fail-closed validation; mirrored in the example config and
      the config battery). Every sweep makes ONE backchannel
      contents read — the read class the sweep already spends —
      refreshing the per-host last-seen record in `hosts.json`
      beside the queue (verified writes); the heartbeat proves
      the WSL2 host and its `via` chain. `gk status` renders
      each host UP/DOWN + heartbeat age from that record and
      spends no API doing it. A merely stale heartbeat records
      DOWN but never blocks dispatch in this phase; only a
      going-down ingest sets a dispatch hold.
      Going-down record format (JSON): `host`, `ts` (the
      drain's start), `trigger` (shutdown | logoff | update |
      unknown), `active_run_ids`, `supervised` (list of
      `{id, checkpoint}`), `uncheckpointed` (list of ids). The
      host delivers it as a `going_down` field on its heartbeat
      payload; the sweep detects it there and calls
      `ingest_going_down`, which ledgers `host_going_down`,
      annotates each affected job `interrupted_by:
      "host-shutdown"` + `interrupted_at` (a ledger
      `interrupted` line per job is what the outbox turns into
      one push per affected notify registration), and sets the
      host's dispatch hold. Ingest is exactly-once per
      (host, ts) — the dedupe lives in the liveness state
      file. The hold clears via `gk host-resume <host>`, the
      Phase-1 stand-in for the FR-010 recovery handshake
      (Phase 5). The gatekeeper never cancels and never
      re-dispatches. Two honest gaps, named: (1) the
      owning-chat copy of the FR-004 notification — the outbox
      has no default/owning target concept today (groups are
      keyed by per-job notify registrations only), so Phase 1
      notifies the registered targets and the owning chat sees
      the event on the status board and in the ledger; an
      owning-chat route needs a config decision. (2) The
      host-side verified-write writer + backchannel push of
      the record (FR-003 host side) ships with the guard
      (Phase 3); the spec 009 runbook's message-set update
      rides that same change — this phase changes no agent
      behavior and invents no second heartbeat format (the
      reader is the one `gk status` already used).
      `gatekeeper/test_golden.py` scenario 6 pins all of it
      (aging -> DOWN, ledger lines, annotations, hold,
      host-resume, single outbox group, duplicate suppression);
      `test_config.py` covers the new key valid/invalid.
      **Phase 3 update (2026-11-02)**: the host-side writer
      and the runbook message-set update named in gap (2)
      shipped with the guard below — the agent now merges the
      guard's `going_down` and `guard` files into the
      heartbeat payload verbatim, the reader unchanged, and
      `backchannel/RUNBOOK.md` carries the new message-set
      section. Gap (1) stands.

## Phase 2 — The drain protocol (FR-005, FR-007)

> **Numbering note (2026-11-02).** The build brief for this
> phase pair spoke of "T004–T009" spanning drain + guard;
> the repo's numbering — authoritative here — is Phase 2 =
> T004–T005, Phase 3 = T006–T008, and the supervised vehicle
> = T009–T010 (Phase 4, untouched by this build). The mapping
> is by scope, and the PR body carries it too.

- [x] T004 Drain signal + checkpoint window mechanics: guard →
      jobs/campaigns signal path, per-lane window from config,
      checkpointed / uncheckpointed accounting into the
      going-down record. Test-host battery with fixture units
      (cooperative, slow, and ignoring).
      **Built repo-side (2026-11-02, spec Amendment 2):** the
      signal path is spec 013's FR-008 hold contract, consumed
      two ways — `gk drain` raises holds through the sweep's
      own signal-job machinery (the same `_request_checkpoint_hold`
      `gk hold` uses, so the harvest is identical whoever
      raised the file), and the guard raises them locally
      (T006). The per-lane window is config `drain.window_s`
      (default 120) + `drain.per_lane_window_s`, fail-closed
      validated and mirrored in the example config. Accounting:
      the drain's per-job verdict classifies from the SAME
      `watch_pass` harvest a sweep runs, and the going-down
      record's checkpoint accounting stays with that harvest —
      the record itself is never rewritten (a same-(host, ts)
      rewrite is deduped away by design; its `uncheckpointed`
      at write time is the at-risk set). The fixture battery —
      cooperative, slow/ignoring, finishing, no-venue — is
      `gatekeeper/test_spec011.py` scenarios 5–8 against a
      stubbed venue, ALL PASS. The live test-host battery
      (fixture units on the real venue) rides T006's field
      sequence; the repo-side battery is the evidence until
      then, stated as such.
- [x] T005 `gk drain --host <host>`: pause-host, signal, wait,
      plain-language CLEAN / INTERRUPTING verdict. Oracle pass
      by the owner in a quiet and a busy window before it is
      documented as the restart protocol.
      **Built repo-side (2026-11-02):** `gk drain --host`
      signals first, then sets the host's **drain hold** — a
      distinct per-host field beside the FR-006 hold, so the
      two holds never clear or impersonate each other — then
      waits per lane and prints CLEAN (exit 0) or INTERRUPTING
      (exit 1) with every job named; `gk host-resume` clears
      both holds in one act (the golden battery's pinned
      output prefix is unchanged). **Reconciliation with spec
      013's `pause --drain`**: 013's drain is pipeline-scoped
      and passive (it signals nothing; the sweep notices the
      executing set empty and stamps the pause record
      settled); 011's is host-scoped and active. Admission is
      the conjunction of both layers, and each layer's verbs
      clear only its own state — proven in the battery
      (scenario 10: a host drain leaves the global pause
      record untouched, and `gk resume` does not clear the
      drain hold). One enabling fix rides along: the
      gatekeeper's own `gk-hold-*` / `gk-unhold-*` signal
      jobs now flow under a host hold exactly as its
      `gk-reboot-*` job already did (spec 013's signal-job
      docstring claimed that exemption; the dispatch code did
      not implement it — without it, a drain could never
      deliver the signals it exists to raise once its own
      hold is set). **Still owed: the owner's oracle pass**
      (quiet + busy window) before this is documented as the
      restart protocol — the task's repo-side half is done;
      the oracle half is a field step at the machine.

## Phase 3 — The shutdown guard (FR-002)
- [x] T006 Guard component on the test host: shutdown block
      reason registration/release tied to active work,
      `WM_QUERYENDSESSION` veto bounded by the drain window.
      Oracle evidence: held restart with the reason visible in
      the Windows shutdown UI; unheld restart when idle.
      **Built repo-side (2026-11-02):** `runner/guard/
      SyncyardGuard.cs` — a sibling of the tray (FR-002's
      sanctioned shape), single file, C# 5, in-box csc,
      winexe, never shown. It discovers running
      checkpoint-capable jobs from the running-job registry
      the transport workflows now maintain (an entry written
      before the job runs and removed when it ends —
      `runner/test_workflow_contract.py` pins both sides),
      pre-registers the OS shutdown block with a
      count-naming reason while work runs, and on
      `WM_QUERYENDSESSION`: writes the going-down record
      FIRST (verified write; the exact Phase 1 shape; the
      backchannel agent merges it into the heartbeat
      payload — the fastest channel the shipped ingest
      reads, per Amendment 2 FR-003-A), raises the running
      jobs' hold files locally, vetoes, drains until the
      registry empties or `window_s` (default 120) expires,
      then releases the block, writes a drain-outcome record,
      and re-issues the pending action (logoff via
      ExitWindowsEx; otherwise a restart — the named design
      decision in Amendment 2, FR-005-A). `WM_ENDSESSION` is
      the last-chance record write. Evidence here is the
      source battery (`runner/guard/test_guard_source.py`,
      GUARD_SOURCE OK) plus the writer-contract scenarios in
      `test_spec011.py`: this VM cannot compile Windows
      targets, so the component is uncompiled until the
      owner's install run — the same posture the tray shipped
      under. **Still owed: the test-host oracle evidence**
      (held restart with the reason visible; unheld restart
      when idle) — a field step, sequenced with T008's
      deploy discipline.
- [x] T007 Guard hardening: the guard's lifecycle is harder to
      exit than the tray's (spec 007 tension, named in the
      spec); a stopped guard is a host-health ISSUES entry at
      the gatekeeper, never a silent loss of protection.
      **Built repo-side (2026-11-02):** the guard has no UI
      at all — no icon, no menu, no exit command; the one
      form is never shown, a programmatic user-close is
      refused and logged, and a duplicate instance exits
      silently. The gatekeeper half: the guard beats every
      ~10 s into `guard-heartbeat.json`; the agent merges the
      beat as `guard` on the heartbeat; the sweep stores it
      on the host's liveness record, judges staleness against
      the carrying heartbeat's own timestamp
      (`drain.guard_stale_after_s`, default 180), ledgers
      `guard_stale` / `guard_recovered` exactly once per
      transition, and `gk status` renders the beat plus
      "STALE (protection not reporting)" — the ISSUES-surface
      equivalent for a per-host protection signal, riding the
      liveness lines every status reader already sees.
      Battery: `test_spec011.py` scenarios 12–13 (fresh,
      stale-once-across-repeat-sweeps, recovered).
- [ ] T008 Production-host guard deploy, fleet shape: pause,
      snapshot, deploy, reconcile, one clean sweep.
      **Explicitly NOT done by the 2026-11-02 repo-side
      build**: deployment is a field act under the pause-first
      discipline and the owner's at-machine sequence (install
      via `runner/guard/install-guard.ps1`, T006's oracle
      evidence, one clean sweep). The build ships everything
      the deploy needs; the deploy itself stays open.

## Phase 4 — Supervised work vehicle (FR-008)
- [ ] T009 Supervisor + ensure + heartbeat, generalized from
      the production anchor pattern: campaign registration at
      the gatekeeper with the checkpoint contract recorded;
      relaunch skips completed units. Battery: kill mid-unit,
      observe remaining-units-only completion.
- [ ] T010 First real campaign adoption (gate): one production
      program's coordinator states its checkpoint contract and
      registers; the anchor's existing campaigns migrate
      without a behavior change to their science.

## Phase 5 — Boot recovery (FR-009, FR-010)
- [ ] T011 Boot-shape proof, narrowed by Amendment 1: on one
      lane of the production host, convert the runner to a
      Windows service running as the owner account and prove,
      with no interactive logon present, that the lane returns
      online and honestly runs its job classes — the WSL
      reachability check is the decisive one. If the owner's
      WSL distro is unreachable from the service session, the
      evidence goes to the fallback shape (boot-scheduled
      launcher in the owner's name) and the same proof runs
      there. Evidence pack either way.
- [x] T012 **Owner gate**: boot shape chosen.
      **Satisfied by owner directive, 2026-10-31 ~23:57 ET
      (Amendment 1)**: boot-start with no interactive logon is
      required; autologon (shape (b)) is excluded by the
      ruling's own words. Selected shape: service-mode
      runners running as the owner account, with the
      boot-scheduled launcher as the named fallback if the
      T011 proof fails on WSL reachability. Choosing
      service-mode amends spec 007 FR-003; that amendment
      rides with the T013 implementation change below, as
      this task requires, together with SKILL.md's
      Windowless Supervision section.
- [ ] T013 Implement the chosen shape; per-lane `boot-runnable`
      vs `logon-bound` labels in config; lanes that cannot
      honestly run pre-logon render DOWN, never online.
      Conversion scope (Amendment 1): all four runner lanes
      to services running as the owner account; the
      backchannel agent joins the boot set (linger for its
      user + the T014 distro-start ensure pulled forward);
      the tray's logon autostart is retired in the same
      change so no runner is ever started twice.
- [ ] T014 WSL ensure + recovery handshake sequence:
      distro start, backchannel self-test fresh, runners per
      T013, campaign relaunch per T009, handshake to the
      gatekeeper; dispatch stays held until the handshake.
      Test: a withheld handshake leaves the host DOWN.
- [x] T021 `gk reboot` (FR-015, Amendment 1): drain-aware
      refusal naming active jobs, going-down record written
      before any signal (trigger `remote-reboot`),
      `host_reboot_requested` ledger event naming the
      requesting chat, signal via a fresh backchannel first,
      a maintenance dispatch second, an honest unreachable
      report otherwise; dispatch hold after signaling.
      Batteries pin the refusal, the order, and the route
      selection.
- [ ] T022 Venue conversion + no-logon boot proof on
      TRISTEN-DESKTOP: the T013 conversion applied under
      pause-first discipline, then one live fire of
      `gk reboot` (T021) with **no logon afterwards** —
      runners return per their labels, the backchannel
      heartbeat resumes fresh, queued jobs execute in order.
      Counted as a drill under FR-013; the evidence pack
      (runners API state, heartbeat age, job completions)
      lands with the change.

## Phase 5 progress
- [x] T021 (gatekeeper side, 2026-11-01): `gk reboot --host
      <host> [--force] [--delay <seconds>] [--requested-by
      <chat-id>]`. Route determination is reads-only and
      happens first: a fresh backchannel heartbeat (age
      <= 300 s, from the one shared reader
      `fetch_backchannel_heartbeat`) that proves the host
      selects the backchannel route; otherwise any online
      runner of the door repo selects the maintenance route
      (runner names are not in config — any online runner
      proves the lane-owning host, and only that host may
      take the route); otherwise the command prints
      `REBOOT UNREACHABLE`, ledgers `host_reboot_unreachable`,
      exits 4, and writes nothing else. With jobs in
      submitted / dispatching / dispatched on the host's
      lanes and no --force it prints the refusal naming the
      job ids, ledgers `host_reboot_refused`, and exits 3 —
      no record, no signal. On acceptance it builds the
      FR-003 going-down record (trigger `remote-reboot`;
      `remote-reboot` joins the ingest's trigger vocabulary)
      and passes it through the existing `ingest_going_down`,
      so the `host_going_down` ledger line, the
      `interrupted_by` annotations, the dispatch hold, and
      the outbox pushes are Phase 1 semantics exactly; the
      record's `uncheckpointed` carries the active job ids
      (the ingest ledgers their count). It then ledgers
      `host_reboot_requested` (host, requested_by, route,
      delay_s) and only then signals: backchannel route =
      `bc.py send --cmd` with the Windows shutdown.exe
      invoked through its /mnt/c path from the host's WSL2
      distro (`/r /t <delay>`, delay defaulting to the new
      config `reboot.grace_s` = 60, validated positive,
      fail closed, mirrored in the example config and the
      config battery); maintenance route = a normal
      maintenance job through the existing submit machinery
      (run.yml, the host's first configured lane, key
      `gk-reboot-<host>-<ts>`, PowerShell shutdown with the
      same delay) that the next sweep dispatches. One
      reconciliation the implementation had to make, named:
      the FR-006 hold blocks every lane of a going-down
      host, maintenance included, so the signal job is
      exempt from the hold in `dispatch_pass` by its
      maintenance flag + key prefix — it is the going-down
      mechanism, not new work on the host; all other jobs
      stay held, and golden scenario 7 pins the exemption
      alongside the refusal, the record-before-signal
      order, both routes, and the unreachable path. A live
      route whose signal itself fails exits 1 with a
      `host_reboot_signal_failed` ledger line (the record
      and hold stand; the failure is stated, never silent).
      Named gaps: (1) the fleet deploy of this verb rides
      the normal gatekeeper deploy discipline (pause-first,
      queue snapshot, hash verify, one clean sweep) and has
      not happened in this change; (2) the live fire drill
      is T022, still open — nothing here has rebooted a
      real host yet.

## Phase 6 — Reconciliation and the recovery report (FR-011, FR-012)
- [ ] T015 Sweep reconciliation of interrupted runs: adopt
      GitHub terminal state exactly once (completed / failed /
      cancelled / never-started), interrupted annotation on
      host-killed runs, marker harvest where a log exists, no
      re-dispatch side effects. Golden battery covers all four
      states.
- [ ] T016 Recovery report: shutdown account, interrupted /
      resumed / lost by name, emitted to the owning chat after
      reconciliation; host-never-returns closure path (no
      handshake required) with the same report shape.

## Phase 7 — Drills, docs, release (FR-013, FR-014)
- [ ] T017 Three drills scripted and run on the test host:
      cooperative drain, forced kill mid-run, host-never-
      returns. Results recorded here before any tag.
- [ ] T018 Config keys documented in the example config
      (drain window, liveness threshold, lane session labels);
      validation battery covers the new pairs fail-closed.
- [ ] T019 SECURITY.md shutdown-and-recovery section
      (protection level in the spec's words; the chosen boot
      shape's tradeoff as chosen); SKILL.md + skills pack gain
      drain, supervised work, and host-DOWN states; currency
      battery covers the new text.
- [ ] T020 CHANGELOG + version decision with the owner (this
      spec lands across the 0.3.0 program of spec 010; the
      version it ships in is the owner's call at tag time).

## Notes for the implementing phases
- The 2026-10-31 production anchor campaigns are the FR-008
  reference implementation; their supervisor/ensure/heartbeat
  hashes and the restart-resume evidence live in the Desktop
  Access chat's records and are the starting fixtures for T009.
- The going-down record's backchannel push (T002) extends the
  spec 009 agent's message set; update the spec 009 runbook in
  the same change.
- Nothing in any phase dispatches through a new surface: the
  dispatch-only invariant (spec 010 FR-009) binds every task
  above.

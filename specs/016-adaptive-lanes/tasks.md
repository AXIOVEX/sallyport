# Tasks: Spec 016 — Adaptive Lanes

All tasks land in one repo change on `spec/016-adaptive-lanes`,
built stacked on `spec/015-lane-priority-lock` (PR #43, merged
into the spec 013 branch the same day; this PR targets
`spec/013-live-priority-and-pause`). Fleet deployment is
NOT part of this change except as the named, unchecked,
owner-gated T017. The fleet's disciplines apply throughout:
every state mutation through the verified-write path
(FR-012/FR-013 of spec 001), every transition ledgered,
SKILL.md / skills-pack currency in the same change (FR-009),
and the CI-green rule — a phase with red CI is not done.

## Phase 1 — Profiles in config (FR-001, FR-002, FR-009)
- [x] T001 `lanes` section restructured in config.py
      DEFAULTS: `active_profile` (`fleet-default`),
      `auto_default` (false), `capability_max_age_min` (60),
      and `profiles` shipping `fleet-default` (today's four
      lanes, exact), `single-host-light`, and `gpu-farm`;
      per-lane optional fields `resource_class`,
      `duration_band`, `enabled`, `workflow`.
- [x] T002 Fail-closed validation of the whole table in the
      spec 011/013/015 style: name charsets, unknown keys
      under `lanes` (the pre-016 flat shape fails with the
      migration named), unknown host / workflow pin,
      concurrency, resource class, band shape, `enabled`
      type, active naming a real profile, ≥1 enabled lane
      per profile; every profile validated, active or not.
- [x] T003 Resolution in `load_config()`: the active
      profile's enabled lanes become `cfg["lanes"]` (the
      flat mapping all consumers read); the table and knobs
      exposed as `cfg["lane_profiles"]`,
      `cfg["active_lane_profile"]`, `cfg["lanes_auto_default"]`,
      `cfg["lanes_capability_max_age_min"]`.
- [x] T004 Mirror the section exactly in
      config/syncyard.example.yaml (profiles shape, adopter
      examples updated to it, operator comments); gk.py
      rebinds the new constants beside the existing ones;
      lane workflow pin joins `route_check`.

## Phase 2 — Hints and auto submission (FR-003, FR-004)
- [x] T005 Submit flags `--needs-cpu`, `--needs-ram-mb`,
      `--needs-storage-mb`, `--needs-gpu [VRAM_MB]`,
      `--venue`; negative values refused (exit 2); hints and
      venue recorded on the record for any submission.
- [x] T006 `--lane auto` (and a lane-less submit under
      `lanes.auto_default`): the record lands lane-less with
      `lane_request: "auto"`; routing checks that need a
      lane are deferred to resolution; the read-back
      verification and duplicate refusal are unchanged.

## Phase 3 — Capability probes (FR-005)
- [x] T007 Probe report protocol + the three script bodies
      (Windows CIM + nvidia-smi; Linux /proc + df +
      nvidia-smi; Darwin sysctl + vm_stat + system_profiler)
      as gk.py constants, with the same bytes shipped as
      files under gatekeeper/probes/ (drift-pinned by the
      battery).
- [x] T008 The three platform parsers producing the
      normalized snapshot (units converted; Apple-silicon
      GPU class `unified` with `vram_shared`; Intel discrete
      VRAM parsed; venues only what was found — never `wsl`
      on Darwin); unparseable input yields no snapshot.
- [x] T009 Storage + freshness: snapshots under the host's
      `capability` key in hosts.json through
      `save_hosts_state`; `capability_view()` staleness
      against `lanes.capability_max_age_min`.
- [x] T010 `gk probe-host`: the dispatch route (maintenance
      probe job via `cmd_submit`, `probe_host` stamped,
      watch-pass completion ingest from the harvested log)
      for Windows and WSL2 hosts; the `--ingest <file>`
      route for any host; the no-transport refusal naming
      the ingest route (exit 3).

## Phase 4 — Auto routing (FR-006, FR-007)
- [x] T011 `program_history()`: the program's ten most
      recent completed jobs → sample count, median run
      duration, dominant resource class, last lane.
- [x] T012 `resolve_auto_lane()`: the deterministic pure
      scorer — hard eligibility (routing/pin, host hold,
      spec 015 lock, hard hints, venue) then the integer
      score (load, band fit, history affinity, venue match,
      RAM headroom), ties to the first lane name; capability
      consulted only when fresh, else neutral + degraded.
- [x] T013 `_resolve_auto_jobs()` in the dispatch pass:
      placement writes `lane` + `auto_route` and ledgers
      `auto_routed` (+ `auto_degraded`); non-placement
      records `auto_wait` and ledgers `auto_no_fit` once per
      distinct reason set; the waiting loop skips lane-less
      jobs; resolved jobs are never re-resolved.

## Phase 5 — Visibility (FR-001, FR-008)
- [x] T014 `gk profile --list` / `--show [name]`.
- [x] T015 `gk status`: the profile line, the CAPABILITY
      section (fresh/STALE/unknown per host), and the
      waiting-list auto marks (`[auto: no fit yet — …]`,
      `[auto→<lane> score=<n>]`); the job table renders a
      lane-less job's lane as `auto`.

## Phase 6 — Battery, docs, CI (FR-009)
- [x] T016 gatekeeper/test_spec016.py: profile validation
      (each named rejection), fleet-default equivalence,
      custom profile dispatch end-to-end, hint recording,
      scoring determinism, hint satisfaction + `auto_no_fit`,
      history influence, lock respect, focus conjunction,
      host-hold exclusion, staleness degradation +
      unknown-field neutrality, the three probe parsers
      against fixtures, probe ingest + probe-job ingest,
      venue eligibility, explicit-beats-auto, `auto_routed`
      completeness, status/profile rendering, probe-file
      drift. CI gains the battery beside the others;
      test_config updated only as plan.md decision 10
      states; the full battery set green.
- [x] T017 Docs currency in-change: SKILL.md, the
      skills-pack source + regenerated editions,
      gatekeeper/README.md, CHANGELOG [Unreleased], citing
      spec 016. VERSION not bumped (spec 013 owns 0.4.0 on
      this stack).

## Owner-gated follow-ups (not this change)
- [ ] T018 Fleet deployment of the stacked gatekeeper
      (013 + 015 + 016) under the standing deploy procedure,
      including conversion of any fleet `syncyard.yaml` in
      the pre-016 flat `lanes:` shape to the profile shape —
      the loader refuses the old shape by design (FR-002).
- [ ] T019 Live probing of a real Mac (FR-005's
      human-as-oracle follow-up): run
      gatekeeper/probes/probe-darwin.sh on the machine,
      `gk probe-host --host <mac> --ingest <file>`, and
      judge the stored snapshot against the machine's known
      hardware.

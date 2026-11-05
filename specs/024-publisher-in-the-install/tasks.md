# Tasks: Spec 024 — The Fleet-Status Publisher in the Install

- [x] T001: Spec (this directory's spec.md): publisher scoped
  to the operator-side install; new spec rather than a spec 022
  amendment, with the reason recorded in the spec.
- [x] T002: fleet_status.py: `--path` repeatable, one snapshot
  published byte-identically to all configured paths;
  `FLEET_STATUS_API_BASE` env override (default
  https://api.github.com unchanged); coverage added to
  gatekeeper/test_fleet_status.py (multi-path publish,
  identical bodies, API base default/override).
- [x] T003: Repo units: gatekeeper/systemd/
  fleet-status-publish.service (template, tokens @ETC_DIR@,
  @GK_DIR@, @PATH_ARGS@) and fleet-status-publish.timer
  (2-minute cadence).
- [x] T004: gatekeeper/install-publisher.sh: two-phase
  (preflight-all, then install), FLEETPUB_INSTALL_* markers,
  FR-002 refusal set, root-only env capture (names printed,
  values never), root-owned copy with sha256 assertion,
  rendered-unit token check, timer enable, first-publish gate
  from the publisher state record, UNCHANGED reporting for
  idempotent re-runs, sandbox overrides (FLEETPUB_ETC_DIR,
  FLEETPUB_SYSTEMD_DIR, FLEETPUB_SYSTEMCTL, FLEETPUB_GK_DIR).
- [x] T005: gatekeeper/test_install_publisher.py sandbox
  battery (stub systemctl executing the real ExecStart, stub
  contents-API server): fresh one-channel install publishes;
  fresh two-channel install publishes byte-identical bodies;
  second run idempotent; proxy-env refusal; github-token
  refusal; both refusals install nothing. Wired into
  .github/workflows/ci.yml.
- [x] T006: gatekeeper/README.md publisher section + full
  operator-side dependency table (FR-009);
  backchannel/INSTALL.md section 5 now names the packaged
  installer and its --channels option; CHANGELOG [Unreleased]
  entry.
- [ ] T007 (field follow-up, Desktop Access chat): replace the
  live loose install on the operator VM with the packaged
  installer (idempotent over the working manual install);
  add the syncyard-laptop channel to the publisher's channels
  once the laptop backchannel agent is live.

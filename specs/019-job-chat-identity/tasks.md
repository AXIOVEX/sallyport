# Tasks: Spec 019 — Job–Chat Identity and Routing Integrity

- [ ] T001 Registry in gatekeeper config (routing section);
      golden fixture + `test_config` exact mirror updated in
      the same change.
- [ ] T002 `gk submit`: derive chat from registry; refuse a
      disagreeing `--notify-chat` and any unregistered program
      (exit 2, named error).
- [ ] T003 Ledger identity event at submit (key, program,
      chat, script SHA-256), written before dispatch.
- [ ] T004 Outbox three-way check (registry / ledger / queue
      record) per completed job; agreement emits today's
      outbox shape unchanged.
- [ ] T005 Discrepancy quarantine + `routing_discrepancy`
      ledger event + material surfacing in the sweep summary.
- [ ] T006 Red-team release path: `routing_released` /
      `routing_voided` ledger events as the only release.
- [ ] T007 `test_spec019` battery covering every falsifier in
      the spec; CI wiring beside the other spec batteries.
- [ ] T008 Migration seed PR: registry seeded from the
      2026-11-04 census (26 programs), anomalies documented,
      `cleanup-coord` retired as a program name.
- [ ] T009 Fleet adoption at the next fleet deploy (rides the
      control-stack swap); interim procedural check (FR-009)
      already in force from 2026-11-04.

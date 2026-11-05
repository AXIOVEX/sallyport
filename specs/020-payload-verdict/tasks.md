# Tasks: Spec 020 — Payload Verdict as a First-Class Record

- [ ] T001 Door workflows (run.yml, wsl.yml, grab.yml): capture
      the payload exit code; emit `GK_PAYLOAD_EXIT <code>` as
      the log's final line; wrapper exit stays transport-only.
- [ ] T002 Harvest: parse the terminal marker into
      `payload_exit_code`; derive `payload_verdict` (PASS /
      FAIL / UNVERIFIED) with the reason recorded.
- [ ] T003 Submit contract: `--expect-exit` (default 0) and
      `--expect-marker`; stored on the queue record at submit.
- [ ] T004 Presentation: `gk status` and outbox completion
      lines lead with the payload verdict; pre-spec records
      render UNVERIFIED.
- [ ] T005 `test_spec020` battery incl. the owner's deliberate
      exit-127 acceptance case; CI wiring beside the other
      spec batteries.
- [ ] T006 Docs: job contract section (README / operator
      notes) stating wrapper success is not job success.
- [ ] T007 Fleet adoption at the next fleet deploy (rides the
      control-stack swap). Interim operator rule (FR-008) in
      force from 2026-11-04.

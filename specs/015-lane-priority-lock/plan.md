# Plan: Spec 015 — Lane Priority Lock

Implementation plan for the change on
`spec/015-lane-priority-lock`, stacked on
`spec/013-live-priority-and-pause` (7418b19). One repo change;
no fleet deploy (owner-gated, see tasks T016).

## Design decisions

1. **State shape.** `LOCKS.json` beside `PAUSED.json`: a flat
   mapping `lane -> {program, state, acquired_at, expires_at,
   ttl_min, reason, notify_chat}`. One record per lane makes
   "one holder per lane" structural — there is no list to
   disagree with itself. `state` is the lowercase stored form
   (`active` / `suspended`), rendered uppercase in status.
2. **Every mutation through the standing path.** Reads are
   plain (`load_locks()`: missing or corrupt file reads as no
   locks, mirroring `load_hosts_state`'s tolerance). Writes go
   through `_write_text_file` (FR-013 verified write + atomic
   swap) via a `_write_locks_record()` wrapper shaped exactly
   like `_write_pause_record` — False + printed error on
   failure, the command exits 1, nothing is ledgered as done.
3. **Uniform actor discipline.** `--program` on `lock` /
   `unlock` names the *actor*. Actor == holder: the normal
   path. Actor != holder: refused (exit 3, naming holder,
   state, expiry) unless `--force` — the operator override,
   following `gk reboot --force`'s precedent — which ledgers
   distinctly and notifies the holder. This one rule covers
   acquire-refusal, release, suspend, and resume, so there is
   no verb with a special-case authority story.
4. **Expiry is evaluated, not timed.** `expire_locks()` runs
   at the head of `dispatch_pass` and on entry to
   `cmd_lock` / `cmd_unlock`. It removes past-due locks of
   both states, persists the removal first, and only then
   ledgers `lock_expired` per lock. If the persist write
   fails, it returns the unexpired map (fail-closed: the lock
   still enforces this pass) and logs — an unlanded expiry
   must not silently free a lane.
5. **Admission placement.** In `dispatch_pass`, after the
   pause/focus/stop gate and the host-hold gate, before the
   active cap: a non-maintenance job whose lane is
   ACTIVE-locked by another program is skipped (`continue`).
   No ledger line per skip — the pass runs every 5 minutes
   and the record of the wait is the job's own watermark
   escalation, as spec 013 chose for focus holds. The bypass
   ledger (`lock_bypassed`) fires only on an actual
   maintenance *dispatch* under an ACTIVE foreign lock,
   after `dispatch_one` succeeds.
6. **Notices ride the existing outbox.** Lock ledger events
   carry `notify_chat` directly on the event when (and only
   when) a notice is owed: `lock_expired` (holder's chat,
   resolved from the record's stored chat, else the holder's
   most recent registered job), `lock_released_override`
   (holder's chat), and `lock_suspended` / `lock_resumed`
   with `override: true` (holder's chat). `cmd_outbox` gains
   a branch ahead of its job lookup that renders these four
   events from the event's own fields — lock events have no
   single job to look up. Same-holder actions carry no
   `notify_chat`, so nothing self-notifies.
7. **Renewal preserves state.** Re-acquire by the holder
   extends `expires_at` from now and refreshes `ttl_min`
   (given value wins), `reason` (only when given), and
   `notify_chat` (re-resolved), but never flips
   SUSPENDED -> ACTIVE: enforcement resumes only via the
   explicit `--resume`.
8. **No new sweep token, no VERSION bump.** Status gains a
   LOCKS section; the waiting list gains a lock mark. The
   sweep summary line is unchanged (locks do not change the
   pipeline mode). VERSION stays 0.4.0 — spec 013 owns it on
   this stack.

## Code changes

- `gatekeeper/config.py`: `lock` section in DEFAULTS
  (`default_ttl_min` 240, `max_ttl_min` 1440) + fail-closed
  validation (integer, non-bool, positive, default <= max),
  in the checkpoint section's style; docstring line.
- `config/syncyard.example.yaml`: the same section, mirrored
  exactly, with operator comments.
- `gatekeeper/gk.py`:
  - `LOCKS_FILE` constant; `LOCK_DEFAULT_TTL_MIN` /
    `LOCK_MAX_TTL_MIN` rebinds from `_CFG["lock"]`.
  - `load_locks()`, `_write_locks_record()`,
    `_holder_chat()`, `expire_locks()`, `locks_section_lines()`.
  - `cmd_lock()` (acquire / renew / suspend / resume /
    `--list`) and `cmd_unlock()` (holder release / operator
    override), CLI parsers `lock` and `unlock`.
  - `dispatch_pass()`: expiry at entry; the lane-lock gate;
    the `lock_bypassed` ledger on maintenance dispatch.
  - `cmd_status()`: LOCKS section in all modes; waiting-list
    `[lock-held by ...]` mark.
  - `cmd_outbox()`: lock-event branch rendering
    NOTIFY groups from event-carried `notify_chat`.
- `gatekeeper/test_spec015.py`: the scenario battery (style
  of `test_spec013.py` — temp-dir state, copied gk.py +
  config.py, stubbed ledger capturing full events, stubbed
  `dispatch_one`, fixed evaluation instant, rebindable clock
  for expiry scenarios).
- `gatekeeper/test_config.py`: FR-011 additions only —
  golden lock values, defaults==example including `lock`, a
  valid override, named rejections.
- `.github/workflows/ci.yml`: `test_spec015.py` added to the
  Python batteries step beside `test_spec013.py`.
- Docs: SKILL.md (commands + prose), skills/_source/contract.md
  (commands + prose) with editions regenerated via
  `skills/build_skills.py`, gatekeeper/README.md (command
  list + prose), CHANGELOG.md under [Unreleased].

## Verification

- New battery: `python3 gatekeeper/test_spec015.py`.
- Full local set, each reported verbatim: test_config,
  test_fr013, test_golden, test_spec013, test_spec015,
  runner/test_workflow_contract, runner/test_transport_smoke,
  runner/tray/test_tray_source, backchannel test_bc +
  test_chaos, skills/test_skills, and the CI offline smoke
  sequence run by hand. (test_spec011 is not present on this
  base — spec 011 phases 2–3 live on PR #42's branch.)
- CI green on the PR head is the finish line; no merge, no
  deploy from this change.

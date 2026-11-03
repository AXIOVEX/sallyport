# Tasks: Spec 015 — Lane Priority Lock

All tasks land in one repo change on
`spec/015-lane-priority-lock`, stacked on
`spec/013-live-priority-and-pause`. Fleet deployment is NOT
part of this change except as the named, unchecked,
owner-gated T016. The fleet's disciplines apply throughout:
every state mutation through the verified-write path
(FR-012/FR-013 of spec 001), every transition ledgered,
SKILL.md / skills-pack currency in the same change (FR-011),
and the CI-green rule — a phase with red CI is not done.

## Phase 1 — Config for the lock TTLs (FR-011, FR-007)
- [x] T001 `lock` section in config.py DEFAULTS
      (`default_ttl_min` 240, `max_ttl_min` 1440) +
      fail-closed validation in the spec 011/013 style
      (named errors for non-integer / boolean / non-positive
      values and for default > max).
- [x] T002 Mirror the section exactly in
      config/syncyard.example.yaml with operator comments;
      wire the constants into gk.py beside the other config
      rebindings.

## Phase 2 — Lock state + verbs (FR-001, FR-003, FR-005)
- [x] T003 Lock state helpers: `load_locks()` (missing or
      corrupt file = no locks), `_write_locks_record()` via
      the FR-013 verified write (False + printed error on
      failure), `_holder_chat()` (the holder's registered
      notify chat from its most recent job).
- [x] T004 `gk lock` acquire + renew: one holder per lane;
      second-holder refusal (exit 3) naming holder, state,
      expiry, in both ACTIVE and SUSPENDED states; same-holder
      re-acquire renews the TTL, preserves state, ledgers
      `lock_acquired` / `lock_renewed`; TTL bounds refused.
- [x] T005 `gk lock --suspend` / `--resume`: ACTIVE <->
      SUSPENDED through the verified write, ledgered
      `lock_suspended` / `lock_resumed`, under the uniform
      actor discipline (holder free; anyone else `--force`,
      ledgered as override); no-lock and already-in-state
      invocations are printed no-ops (exit 0).
- [x] T006 `gk unlock`: holder release ledgers
      `lock_released` with the actor; non-holder release
      refused (exit 3); operator override (`--force`)
      ledgers `lock_released_override` with the holder's
      notify chat on the event; unlocked lane is a printed
      no-op (exit 0).

## Phase 3 — Expiry (FR-007)
- [x] T007 `expire_locks()`: past-due locks of both states
      removed at the head of the dispatch pass and on entry
      to the lock/unlock commands; the removal persists
      before `lock_expired` ledgers (with the holder's
      notify chat when known); a failed persist leaves the
      lock enforcing (fail-closed) and logs.

## Phase 4 — Admission (FR-002, FR-004, FR-006, FR-008)
- [x] T008 The lane-lock gate inside `dispatch_pass`, after
      the pause/focus/stop gate and the host-hold gate:
      a non-maintenance non-holder job on an ACTIVE-locked
      lane waits (no refusal, no reorder, no per-pass
      ledger); priority is never consulted to override it.
- [x] T009 Maintenance bypass (FR-008, RECOMMENDATION):
      maintenance jobs skip the lock gate as they skip the
      pause; a maintenance dispatch under an ACTIVE foreign
      lock ledgers `lock_bypassed` with the job key and
      holder.
- [x] T010 Conjunction proofs ride the battery (T013):
      focus ^ lock in both directions, pause/resume leaving
      `LOCKS.json` byte-identical, going-down ingest +
      host-resume + drain settle leaving lock state
      untouched. (No code beyond T008: these paths never
      read or write lock state — the battery pins that.)

## Phase 5 — Visibility (FR-009)
- [x] T011 `gk status` LOCKS section in all modes (lane,
      holder, state, acquired_at, expires_at, reason; `none`
      when empty) + the waiting-list `[lock-held by ...]`
      mark; `gk lock --list` prints the section standalone.
- [x] T012 Outbox: a lock-event branch in `cmd_outbox`
      rendering NOTIFY groups from the event-carried
      `notify_chat` for `lock_expired`,
      `lock_released_override`, and override
      `lock_suspended` / `lock_resumed`; same-holder actions
      carry no address and never self-notify.

## Phase 6 — Battery, docs, release bookkeeping (FR-010, FR-011)
- [x] T013 New scenario battery gatekeeper/test_spec015.py
      in the spec 013 style: acquire + holder-only dispatch;
      second-holder refusal naming the holder; priority-200
      non-holder waits while priority-0 holder dispatches;
      release at the sweep boundary; running run untouched
      by acquire/release; expiry = release semantics +
      ledger + notice (ACTIVE and SUSPENDED); suspend ->
      lane flows + new acquires blocked; resume restores
      enforcement; global pause preserves lock state;
      focus ^ lock conjunction; maintenance bypass +
      `lock_bypassed`; drain leaves locks untouched;
      persistence round-trip across a simulated restart;
      legacy no-file behavior; TTL bounds refused; FR-013
      write-failure leaves prior state intact. Wired into
      CI beside test_spec013.py.
- [x] T014 Config battery additions: golden lock values,
      defaults==example incl. the new section, override,
      named rejections.
- [x] T015 Docs currency: SKILL.md (commands + prose),
      skills/_source/contract.md (commands + prose) with
      editions regenerated via skills/build_skills.py,
      gatekeeper/README.md (commands + prose), CHANGELOG
      entry under [Unreleased]. VERSION NOT bumped — spec
      013 owns 0.4.0 on this stack.
- [ ] T016 **Fleet deployment — NOT done here, owner-gated.**
      Deploying the merged gatekeeper to the fleet tree
      (and the owner's ratification of FR-008's maintenance
      bypass and FR-011's TTL defaults) is a separate, later
      act on the owner's review of the PR, under the
      standing deploy procedure. This task stays unchecked
      until the owner orders it in the owning chat.

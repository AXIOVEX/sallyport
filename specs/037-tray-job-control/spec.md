# Feature Specification: Tray Job Control (Successor App Only)

**Feature Branch**: `feat/037-tray-job-control`
**Created**: 2026-11-07
**Status**: **RATIFIED 2026-11-07 as a block with specs 034-039 (owner decision, Desktop Access chat). Implementation may proceed under the program sequence.**
**Direction**: Owner ruling Q4 (2026-11-06): job control exists in the successor app only. The current tray never gains job control.
**Companions**: Spec 035 (the surface this lives in), spec 038 (the gatekeeper that executes the acts; the repair kill switch, FIX-08).

## The gap

Today every job-control act (lift, cancel, hold, reprioritize) is an agent action in chat. The owner at his own machine has no direct control surface, and the one design on record — signed command files carried by the backchannel, executed by the gatekeeper as ledgered operator acts — carries a proven hazard: the laptop backchannel agent was idle from 2026-11-05T21:53:21Z while its commands could still have been delivered a day late (red-team H6). A stale command executing against a changed queue is worse than no command.

## The design

### 1. Command files

A control action in the successor app writes a command file: a signed JSON record carrying command id, target job id (or lane/host where the act is not job-scoped), action (cancel, hold, release-hold, reprioritize, lift), issuer host, issuer identity, a **nonce**, an **issued-at** timestamp, and an **expiry** (default 10 minutes, maximum 60). The file travels through the existing backchannel to the gatekeeper host. Repository write access alone never authenticates a command: the signature is verified against a **per-host key** whose public half the gatekeeper holds in its config (red-team H6).

### 2. Execution as ledgered operator acts

The gatekeeper verifies signature, nonce freshness (a nonce is single-use; a replayed file is refused), and expiry before acting. Every execution ledgers an `operator_*` event naming the command id, the issuing host, and the act — the same record class as today's operator lifts — so a tray act and an agent act are indistinguishable in the audit trail except for their named issuer. Refusals (bad signature, replay, expired, unknown job, illegal state transition) are ledgered too, with the reason.

### 3. Confirmation and the PENDING state

The app shows an issued command as **PENDING** until the gatekeeper's confirmation (the ledgered act or refusal) is observed in the published state. An expired-unconfirmed command renders as **EXPIRED — not executed**, with the command's age, in the app (red-team H6). PENDING never renders as done. Confirmation is read from records, never assumed from the write succeeding.

### 4. Scope of acts

Tray job control covers: cancel a waiting or running job (via the gatekeeper's normal cancel path, including the GitHub-side cancel where a run exists), hold / release-hold a waiting job, reprioritize within the waiting queue, and lift (priority dispatch) a waiting job. It does NOT cover: editing job content or scripts, deleting records, changing verdicts, or any act in spec 038's never-auto-fix list (FIX-05) — those are not tray acts under any signature.

### 5. The repair kill switch

The app and the CLI both expose the global repair kill switch (FIX-08): halting all self-healing (never job execution) until manually cleared. The switch state is a gatekeeper record, displayed in the app, and ledgered on every change.

## Requirements

| FR | Requirement | Named test case(s) | Acceptance mode |
|---|---|---|---|
| FR-037-01 | Job control MUST exist only in the successor app; the current tray MUST NOT gain any job-control act (owner ruling Q4). | T037-01 control-surface-successor-only | gate |
| FR-037-02 | Every command MUST be a signed file carrying command id, target, action, issuer host, nonce, issued-at, and expiry (default 10 min, max 60 min). | T037-02 command-file-schema | test |
| FR-037-03 | Command authentication MUST bind to per-host keys; repository write access alone MUST NOT authenticate a command (red-team H6). | T037-03 repo-access-alone-refused | test |
| FR-037-04 | A nonce MUST be single-use: a replayed command file MUST be refused and the refusal ledgered (red-team H6). | T037-04 nonce-replay-refused | test |
| FR-037-05 | An expired command MUST be refused, never executed, and the refusal MUST surface in the app with the command's age (red-team H6). | T037-05 expired-command-refused-and-shown | test |
| FR-037-06 | Every executed command MUST ledger as an operator act naming command id, issuing host, and action; every refusal MUST ledger with its reason. | T037-06 acts-and-refusals-ledgered | test |
| FR-037-07 | The app MUST show a command as PENDING until its act or refusal is observed in published gatekeeper state; an expired-unconfirmed command MUST render EXPIRED — not executed. | T037-07 pending-until-confirmed | test |
| FR-037-08 | Tray acts MUST be limited to cancel, hold, release-hold, reprioritize, and lift; content edits, record deletion, and verdict changes MUST NOT be expressible as tray commands (FIX-05 boundary). | T037-08 act-scope-closed-set | test |
| FR-037-09 | A cancel of a running job MUST go through the gatekeeper's normal cancel path (GitHub-side cancel plus ledgered operator event); the app MUST NOT signal runners or hosts directly. | T037-09 cancel-via-gatekeeper-only | test |
| FR-037-10 | The repair kill switch (FIX-08) MUST be reachable from the app and the CLI, MUST halt all self-healing without halting job execution, and MUST stay set until manually cleared. | T037-10 kill-switch-reach-and-hold | test |
| FR-037-11 | Commands MUST be refused with a version-naming message when the app and gatekeeper are in an illegal mixed-version combination (UPD-08 at this surface). | T037-11 illegal-version-command-refused | test |
| FR-037-12 | No job-control path MAY open a visible console window on any platform (fleet hard constraint). | T037-12 control-path-windowless | test |

## Falsifiers

- A command file written yesterday executes today: this spec has failed.
- The same command file delivered twice acts twice: this spec has failed.
- The app displays an act as done that the ledger does not carry: this spec has failed.

## Tasks

- T001: This spec.
- T002: Command schema, per-host key enrollment, signing/verification (after ratification).
- T003: Gatekeeper execution path + ledger vocabulary.
- T004: App surface: act dialogs, PENDING/EXPIRED rendering, kill switch.
- T005: Battery per FR table, including the day-stale backchannel scenario from H6.

## Out of scope

Agent-side operator acts (unchanged; they share the ledger vocabulary). Any control surface outside the successor app (no web UI, no CLI-only tray acts beyond the kill switch).

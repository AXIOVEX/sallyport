# Threat model

This page is the fleet's threat model for the Syncyard control
plane (spec 038 section 10, SEC-01). It names the assets, the
actors, and the rules that hold even when a component is
compromised.

## Assets

- **Job records and the ledger.** The queue is the fleet's
  memory of what was asked, what ran, and what the payload's
  own verdict was. A forged or rewritten record spends the
  owner's credibility; integrity outranks availability here.
- **Credentials.** Four scoped roles (door, mirror, reports,
  backup), each able to do its one job and nothing else. There
  is no fleet-wide credential.
- **The control plane itself.** Whoever can dispatch can run
  code on every fleet host. Dispatch is the crown jewel.

## Actors

- **A compromised payload.** Payloads are arbitrary code by
  design. The machinery around them (verdict from the payload's
  own exit code, preflight credential scan, container posture)
  assumes the payload may be hostile or broken.
- **A compromised host.** One host's keys and records must not
  be leverage over the fleet: host keys are per-host, control
  commands are signed per host, and capability records are
  observations, never instructions.
- **A network attacker on the tailnet.** All fleet traffic
  rides the tailnet; the daemon's socket is local-only, and
  the off-VM witness store is the recovery anchor when the
  VM itself is the thing that was lost.
- **The operator, tired.** Most real incidents are honest
  mistakes at 2 AM. The machinery is built so a tired operator
  cannot silently do the irreversible thing: two-phase
  dispatch, fencing epochs, hold-first reconciliation, repair
  budgets, and a kill switch that never auto-releases.

## Rules that hold

1. **Payload verdicts come from payloads.** Wrapper success is
   never job success; a missing verdict record is UNVERIFIED,
   never green.
2. **Writes are verified or they did not happen.** State
   writes read back and hash-compare; a write-denial wave
   freezes mutation rather than corrupting state.
3. **Control is signed and expiring.** Tray-issued commands
   carry an Ed25519 signature from the issuing host's key, a
   nonce, and an expiry; the daemon verifies before acting.
4. **No default broad grants.** Host agents request no broad
   OS permissions by default; a grant a job class needs is
   named in that class's record with its reason.
5. **Secrets never ride payloads.** Job-start preflight scans
   refuse credential-shaped content; reports pipelines refuse
   to carry it; refusals name the shape and line, never the
   secret.
6. **Raw device access is a named venue**, never a flag:
   only `elevated-kit`, only when the job record states the
   need.

## Out of scope

Physical access to a fleet host, compromise of the owner's
GitHub account itself, and the GitHub platform's own
availability. The first two are owner-held risks; the third
is why the state-freeze fallback window exists (see the
cutover plan).

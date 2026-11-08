# Job control (spec 037)

Job control — acting on jobs from the machine you are sitting
at — exists **only in the successor tray app** (owner ruling
Q4). The current tray never gains it, and there is no web UI.
This page describes the protocol the app and the gatekeeper
share.

## Command files

A control action in the app writes one **command file**: a
signed JSON record carrying

- command id,
- target (a job id, or a lane/host where the act is not
  job-scoped),
- action — a closed set of exactly five: **cancel**, **hold**,
  **release-hold**, **reprioritize**, **lift**,
- issuer host and issuer identity,
- a **nonce**,
- issued-at timestamp and **expiry** (default 10 minutes,
  maximum 60).

The file travels through the existing backchannel to the
gatekeeper host. Repository write access alone never
authenticates a command: signatures are Ed25519, verified
against a **per-host public key** the gatekeeper holds in its
control configuration. The gatekeeper never holds a private
key; the private half lives only in the issuing host's own
key file.

Why the expiry is short: a command delivered a day late by an
idle backchannel agent, executed against a changed queue, is
worse than no command (red-team H6). Stale commands are
refused, by construction.

## Verification and execution

The daemon verifies in a fixed order — enrolled host,
signature, version legality (an illegal app/gatekeeper version
combination is refused with a message naming the required
contract version), expiry shape, not expired, nonce unused —
and burns the nonce **only after** authentication, so a forged
file cannot deny service to the real one by spending its
nonce. A replayed file is refused and the refusal ledgered.

Acts then run through the normal paths, never a side door: a
tray cancel shares the one cancel implementation with the
`cancel` verb, including the GitHub-side cancel where a run
exists; lift goes through the same priority dispatch an
operator lift uses. Every execution ledgers an `operator_*`
event naming the command id and the issuing host; every
refusal ledgers `operator_command_refused` with its reason. A
tray act and an agent act are indistinguishable in the audit
trail except for their named issuer.

## Confirmation: PENDING is not done

The app shows an issued command as **PENDING** until the
gatekeeper's confirmation — the ledgered act or refusal — is
observed in the published outcomes. Confirmation is read from
records, never assumed from the write succeeding. The states
are:

- **Pending** — filed, not yet confirmed.
- **Confirmed** — the act is in the gatekeeper's published
  outcomes.
- **Refused** — with the gatekeeper's reason.
- **Expired, not executed** — the expiry passed with no
  confirmation; rendered with the command's age.

## Scope boundary

Tray acts are limited to the five actions above. Editing job
content or scripts, deleting records, and changing verdicts
are **not expressible** as tray commands under any signature
(the FIX-05 boundary). No job-control path may open a visible
console window on any platform.

## The repair kill switch (FIX-08)

The app and the CLI both expose the global repair kill switch:

```bash
gh syncyard kill-switch          # read the current state
gh syncyard kill-switch on       # halt all self-healing
gh syncyard kill-switch off      # resume self-healing
```

While set, all self-healing (for example the fleet-status
publisher's self-repair) is halted, and a self-heal mechanism's
one allowed restart is **not** spent. Job execution is
unaffected — the switch stops repairs, never work. The switch
is a persisted gatekeeper record, displayed in the app and in
every snapshot, ledgered on every change with its source, and
it stays set until manually cleared.

## Current state

The full protocol is implemented and tested end-to-end over
the daemon's real socket: a signed cancel filed by the tray
client renders Pending, executes through the daemon's cancel
path, and only then renders Confirmed. Two legs are still
field wiring, named honestly: the backchannel carriage of
command files from a tray host to the gatekeeper host (the
protocol is proven same-host today), and host-key enrollment,
which is currently a configuration act — provisioning writes
the gatekeeper's control configuration; there is no enrollment
ceremony yet.

# Feature Specification: The Fleet-Status Publisher in the Install

**Feature Branch**: `feat/publisher-in-install`

**Created**: 2026-11-04

**Status**: **Specified and implemented** (this change carries
the spec, the implementation, and the install battery).

**Direction (owner, 2026-11-04 18:54 ET)**: "The fleet publisher
must also be integrated into the install like the back channel
and other things. Make sure we have a full solid package with
no missing dependencies and we test install in test
environments."

## Why a new spec, not an amendment to spec 022

Spec 022 (Syncyard host setup) is the per-machine installer:
the Windows setup that lays down the tray, the backchannel
agent, and the runner directories on a fleet host. It excludes
this component by its own text: the setup "does not run the
gatekeeper (that lives with the operator's Muse VM)". The
fleet-status publisher is a gatekeeper-side component: it runs
on the operator side, beside the gatekeeper's state, on a
systemd system timer. It therefore belongs to the operator-side
install, which no spec covered until this one. This spec
extends spec 022's completeness principle (one install, every
component enumerated, each verified from records) to the
operator side, and is written to compose with 022, not to
amend it.

## The gap

The fleet-status publisher (spec 014 FR-009,
`gatekeeper/fleet_status.py`) writes the snapshot the tray's
fleet panel reads. It is the only fleet component whose install
lived entirely outside this repository: its systemd units and
its installer existed only as loose files in the fleet's local
working tree. System units do not survive a VM replacement, so
every replacement silently removed the publisher; nothing in
the packaged product could put it back, and the tray fleet
panel went stale for hours at a time (field case 2026-11-04:
last successful publish 18:59:50Z, panel reading STALE at five
hours old, found by the owner, not by the system).

Three further gaps the loose install hid:

1. **Undeclared dependencies.** The publisher needs an egress
   proxy in its environment on the operator VM and a GitHub
   token source (token env or an authenticated `gh`). Neither
   was declared anywhere in this repository; both were encoded
   only in the loose installer's refusals and in the
   publisher's own token fallback chain.
2. **A setting that did not exist.** `backchannel/INSTALL.md`
   (section 5) says a second machine's tray can read its fleet
   panel "provided the fleet-status publisher writes
   `fleet-status.json` into that channel as well" and calls
   that "a publisher setting". The publisher had no such
   setting: it published exactly one hard-defaulted path.
3. **No install test.** The publisher had a unit battery for
   the snapshot and publish logic, but nothing exercised an
   install: no fresh-install proof, no idempotency proof, no
   refusal proof. The first time the loose installer failed on
   a fresh host would have been the first time anyone ran it
   there.

## The design

The publisher joins the packaged install the way the
backchannel did (spec 009, PR #66): repo-carried artifacts, one
installer script in the component's own directory, an install
document section, and a battery that runs the real installer
against local stand-ins.

- `gatekeeper/systemd/fleet-status-publish.service` — the
  system unit as a repo file (a template: the installer renders
  the install dir, the gatekeeper state dir, and one `--path`
  per configured channel into it; a rendered unit with an
  unsubstituted token left in it is an install failure).
- `gatekeeper/systemd/fleet-status-publish.timer` — the
  cadence unit as a repo file (2-minute cycle, matching the
  publisher's documented cadence against the tray's 300 s
  staleness bound).
- `gatekeeper/install-publisher.sh` — the packaged installer,
  in `install-backchannel.sh`'s marker style
  (`FLEETPUB_INSTALL_*`). It runs in two strict phases:
  **preflight**, which checks every declared dependency and
  refuses by name before anything is written; and **install**,
  which lays down the root-only env file (captured from the
  installing shell; values never printed, only variable
  names), the root-owned publisher copy (sha256 asserted equal
  to the checkout's source), and the units; enables the timer;
  and proves a first publish from the publisher's own state
  record before it reports success.
- The publisher itself gains the missing setting: `--path` is
  repeatable, one snapshot is built once and published
  byte-identically to every configured channel path, and the
  installer configures the paths from its `--channels` list
  (`channels/<channel>/fleet-status.json` per channel,
  defaulting to `syncyard-dev` alone, so today's behavior is
  the default case).
- `gatekeeper/test_install_publisher.py` — the sandbox install
  battery: redirected install roots and a stub `systemctl`
  that really executes the installed unit's `ExecStart`, plus
  a stub contents-API HTTP server, so a fresh packaged install
  publishes a real snapshot end to end without touching any
  live service. A `FLEET_STATUS_API_BASE` environment override
  (default unchanged) is what lets the publisher address the
  stub; it is also the GitHub Enterprise base-URL knob.
- `gatekeeper/README.md` gains the publisher section and the
  operator-side dependency table: every dependency of the full
  install, the component that needs it, and which installer
  preflights it — or an explicit "declared, not preflighted"
  where that is still the truth.

## Requirements

- **FR-001**: The publisher's systemd service and timer units
  MUST be repo files under `gatekeeper/systemd/`, and the
  packaged installer MUST install the units and the publisher
  from the checkout it runs in. No install step may read an
  artifact from outside the checkout and the operator's own
  state directories.
- **FR-002**: The installer MUST preflight every dependency it
  declares before modifying anything, and MUST refuse by name
  (the missing dependency named in its output, a non-zero
  exit, and zero artifacts installed) when one is absent. The
  declared set for the publisher install is: python3 >= 3.8
  (`python3`); a resolvable `systemctl` (`systemd`); the
  `install` and `sha256sum` tools (`coreutils`); write access
  to the install and unit directories, directly or through
  passwordless sudo (`write-access`); `HTTPS_PROXY` set in the
  installing shell (`proxy-env`); a token source — `GH_TOKEN`
  or `GITHUB_TOKEN` set, or `gh auth token` succeeding
  (`github-token`); the gatekeeper state directory present
  (`gk-dir`); the publisher source present in the checkout
  (`publisher-source`).
- **FR-003**: The env file MUST be root-owned mode 0600 in the
  install's config directory, captured from the installing
  shell, and the installer MUST NOT print any captured value
  (variable names only).
- **FR-004**: The installed publisher copy MUST be root-owned,
  and the installer MUST assert its sha256 equals the
  checkout's `fleet_status.py` sha256; a mismatch is a failure,
  never a silent copy.
- **FR-005**: The installer MUST enable the timer and MUST NOT
  report success until a first publish is proven from records:
  the service result is success with exit status 0 AND the
  publisher's state record in the gatekeeper directory shows a
  `last_success` at or after the install began. A failed proof
  exits non-zero with the service's journal evidence and the
  verdict NOT VERIFIED.
- **FR-006**: The installer MUST be idempotent: a second run
  with unchanged inputs leaves the env file, the installed
  copy, and the units byte-identical, and reports each artifact
  UNCHANGED.
- **FR-007**: The publisher MUST publish one built snapshot
  byte-identically to every configured channel path in a run;
  `--path` MUST be repeatable on its command line; with no
  `--path` it MUST publish only the historical default
  (`channels/syncyard-dev/fleet-status.json`). The installer
  MUST configure the paths from `--channels` (default
  `syncyard-dev`).
- **FR-008**: The install battery MUST prove, in a sandbox
  that cannot touch a live service: (a) a fresh packaged
  install completes and the snapshot lands at every configured
  channel path in the stub API; (b) a second run is idempotent
  per FR-006; (c) with `proxy-env` missing and separately with
  `github-token` missing, the installer refuses by name and
  installs nothing; (d) the publisher's unit battery still
  passes unchanged in behavior for single-path publishing.
- **FR-009**: `gatekeeper/README.md` MUST carry the full
  operator-side dependency table (gatekeeper + publisher):
  each dependency, the component that needs it, and the
  installer or document that declares it. A dependency no
  installer preflights MUST be marked as such, not omitted.

**Falsifier**: Run the packaged installer in the sandbox of
FR-008. If it reports success while (a) the stub API holds no
snapshot at any configured channel path, or (b) any installed
artifact's bytes came from outside the checkout, or (c)
removing any single FR-002 dependency still lets it install,
this spec has failed. Equally: if the sandbox battery passes
but a rendered unit still contains an unsubstituted template
token, the install it proves is not the install that ships.

## Tasks

- T001: This spec.
- T002: Publisher multi-path publish + `FLEET_STATUS_API_BASE`
  override, with battery coverage in test_fleet_status.py.
- T003: Repo units (service template + timer).
- T004: `install-publisher.sh` (preflight, install, gate,
  idempotency, sandbox overrides).
- T005: Sandbox install battery (fresh, two-channel,
  idempotent, two refusal cases) + CI wiring.
- T006: README publisher section + dependency table;
  backchannel/INSTALL.md section 5 pointer; CHANGELOG entry.
- T007 (field, Desktop Access chat, not this change): swap the
  live loose install for the packaged one on the operator VM
  (an idempotent re-run over the working manual install), then
  add the laptop channel once its backchannel agent is live.

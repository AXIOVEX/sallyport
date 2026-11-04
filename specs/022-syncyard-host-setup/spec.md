# Feature Specification: Syncyard Host Setup (the formal installer)

**Feature Branch**: `spec/022-syncyard-host-setup`

**Created**: 2026-11-04

**Status**: **Specified** (this change carries the spec only so
far). Implementation follows under this spec.

**Direction (owner, 2026-11-04 ~17:20 ET)**: "make a setup for
syncyard that we can package and add to releases. formal
installer please. or whatever makes most sense"

## The gap

A Syncyard machine is assembled by hand, piece by piece, and
every piece has its own ad hoc install path: the tray by a
hash-pinned gatekeeper job, the backchannel agent by manual WSL
steps, the runners by manual registration, the tray settings by
a job-authored JSON file. Nothing enumerates "a complete
Syncyard host", so completeness is whatever the operator
remembered that day.

Field case, 2026-11-04: TRISTEN-LAPTOP was enrolled as the
fleet's second host (runners and tray installed and verified)
and the enrollment silently omitted the backchannel agent,
because no artifact anywhere said a host includes one. The gap
surfaced a day later as a dark tray fleet panel and no failsafe
channel for that machine. The owner's ruling: the setup should
have done that.

## The design

One formal installer per machine, shipped on every release:
`Syncyard-Setup-<version>.exe` (NSIS, the toolchain the tray
installer already uses), attached to the same GitHub release as
the tray build and its update manifest. An operator runs it
once per machine (elevated); it is idempotent, so re-running it
is also the repair path. It installs the machine-side Syncyard
as one coherent set and proves each piece from records before
it calls itself done:

1. **The tray** (bundled build, the release's own): installed
   or upgraded in place, with per-machine settings authored
   from the install's answers (runner root, enabled lanes,
   fleet-status pointer at this machine's backchannel channel
   copy when the agent is installed).
2. **The backchannel agent** in the machine's WSL, following
   `backchannel/INSTALL.md` exactly. The backchannel is not an
   optional extra or a satellite of Syncyard; it is bundled
   with the product as one piece and is its most critical
   component (owner ruling, 2026-11-04): a host without its
   agent is an incomplete install by definition, and the
   setup's summary must say so in those terms when the agent
   could not be installed or verified. The agent ships in this
   repo, versioned with the product, and its install is the
   setup's second step, not an appendix: the machine's own channel
   (`syncyard-<hostname>`, lowercased; `syncyard-dev` remains
   TRISTEN-DESKTOP's and is refused for any other host), a
   repo-scoped deploy key generated on the machine (the public
   half is printed for the operator to hand to the Desktop
   Access chat, which registers it; the setup pauses at this
   step with the exact text to send), the agent as a systemd
   user service with the channel set, linger enabled or the
   exact one-line owner command printed when it cannot be.
3. **Runner directories** prepared under the machine's
   Development root (one per lane the machine will host), with
   the exact `config.cmd` commands printed for the operator's
   registration-token step. The setup never mints, stores, or
   transmits a registration token; that step stays the owner's
   at github.com, by design.
4. **A machine manifest** at
   `Development\Syncyard\setup-manifest.json`: hostname,
   Syncyard version, channel name, what was installed or
   skipped and why, versions and hashes of every artifact
   placed, and the verification results. The manifest is the
   machine's enrollment record; a later setup run diffs against
   it and repairs drift it finds.
5. **A closing summary** printed and written beside the
   manifest: each component VERIFIED (with its evidence), NOT
   INSTALLED (and why), or OWNER STEP REMAINING (with the exact
   command or text). The setup exits non-zero if any component
   it attempted is unverified.

What the setup deliberately does not do: it does not run the
gatekeeper (that lives with the operator's Muse VM), it does
not create GitHub credentials of any kind, it does not touch
another machine's channel, and its uninstaller removes only
what the manifest lists, never runner job history, never the
channel repo, never another project's files.

Releases: the setup is built from the same tree as the tray,
in the same release step (until CI builds Windows artifacts,
the build runs on a fleet machine through the gatekeeper and
the release attach verifies hashes against the build record,
as tray-v0.4.0 did), and the release notes name it. The
version is the Syncyard version; the tray self-update manifest
stays tray-scoped and unchanged.

## Requirements

- **FR-001**: A release MUST attach `Syncyard-Setup-<version>.exe`
  beside the tray exe and manifest, built from the release's
  own tree, its sha256 recorded in the release notes and the
  build record.
- **FR-002**: The setup MUST install or upgrade the tray, the
  backchannel agent (this machine's own channel only), and the
  runner directory set as one operation, in that order, each
  step idempotent.
- **FR-003**: The setup MUST refuse to configure the
  `syncyard-dev` channel on any host whose hostname is not
  TRISTEN-DESKTOP, and MUST refuse a channel name already
  claimed in the backchannel repo by another host's heartbeat.
- **FR-004**: Credentials stay on the machine: the deploy key
  is generated locally and only its public half is displayed;
  runner registration is an owner step the setup prepares and
  prints but never performs.
- **FR-005**: Every run MUST write the machine manifest and
  print the closing summary with per-component verdicts; a run
  that leaves an attempted component unverified MUST exit
  non-zero and name the failing check.
- **FR-006**: Verification is from records: the tray by its
  installed hash and build stamp, the agent by a fresh
  heartbeat for this machine's channel read back through the
  repo plus a command round trip, the runners by their
  directory and config presence (registration state is the
  owner's step and is reported, not faked).
- **FR-007**: The uninstaller MUST remove only manifest-listed
  items and MUST leave the machine's channel, its deploy key
  registration, and all runner state untouched (those are
  retired by the Desktop Access chat and the owner,
  deliberately).

**Falsifier**: Run the setup on a machine missing the
backchannel agent (TRISTEN-LAPTOP's 2026-11-04 state). If the
run reports success while any of (a) the agent heartbeat for
the machine's channel, (b) the round-trip output, or (c) the
tray's installed build stamp cannot be read back from its
record, the setup has failed this spec. Equally: if the setup
completes on a fresh machine with no component left in an
OWNER STEP REMAINING state where one is genuinely required
(runner tokens, deploy key registration), it has hidden a step
it was required to surface.

## Tasks (sketch)

- T001: NSIS setup skeleton + bundled tray payload + version
  stamping from the release tree.
- T002: Backchannel bootstrap section (WSL detection, key
  generation, clone, unit, linger, service start, pause point
  for the deploy-key hand-off).
- T003: Runner directory preparation + printed registration
  commands.
- T004: Machine manifest writer + drift diff on re-run.
- T005: Verification section (per-component record reads) +
  closing summary + exit codes.
- T006: Source battery in the tray test style (pins for the
  refusal rules, the manifest shape, the summary states).
- T007: Uninstaller (manifest-scoped).
- T008: First field run: TRISTEN-LAPTOP (repairs the
  2026-11-04 enrollment gap through the artifact itself),
  then TRISTEN-DESKTOP (idempotent no-op expected), records
  filed with the enrollment records.
- T009: Release wiring: build + attach in the tray release
  flow; README and RUNBOOK pointers.

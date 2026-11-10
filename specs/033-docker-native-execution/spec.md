# Feature Specification: Docker-Native Execution (WSL retirement)

**Feature Branch**: `feat/033-docker-native`
**Created**: 2026-11-06
**Status**: **DRAFT, awaiting owner ratification.** Owner direction
2026-11-06 ~10:14 ET: "transition all projects that use WSL to use
docker instead (with Docker VMM) so no more WSL2 is needed. this
should be the syncyard expectation, tool that uses docker natively
whether it be Windows or Linux and perhaps macOS."
**Companions**: spec 004 (WSL2 management) is SUPERSEDED by this
spec for all execution purposes; the Arcturus solver stack (FR-009,
image sha256:1feb0fdd2853 referenced by ID in the VMM store) is the
proof case that the target state already works; the Docker round 2
window (2026-11-06) verified the VMM store carries the fleet's
keepers and ChronoHive's volumes.

## The direction

Syncyard's execution expectation becomes: **jobs run in Docker
containers on the Docker VMM engine, natively, on whatever host OS
the machine runs** (Windows today; the same job contract on Linux
and macOS hosts when they join). No project job may require WSL2.
WSL2 remains on the machines only as long as named legacy
exceptions need it, and each exception carries an owner and an exit
condition.

## What uses WSL today (measured inventory, 2026-11-06)

1. **wsl.yml workflow** (door repo): executes a base64 bash script
   inside the desktop's Ubuntu-24.04 distro. Its jobs are Linux
   shell work: probes, builds, harvests. This is the main lane to
   convert.
2. **The backchannel agents** (desktop and laptop): bash agents
   living in WSL, cycling commands and publishing heartbeats and
   the fleet snapshot into the backchannel repo clones. The laptop
   tray's fleetStatusPath reads through the laptop agent's clone.
3. **The Arcturus solver's old WSL2 Docker store**: already
   migrated to VMM in round 2; the store stays untouched until
   ChronoHive's destination-side reconciliation lands. Not an
   execution dependency anymore; a data custody item.
4. **Bench and hardware work that is NOT a Syncyard job**: the
   iSMART firmware toolchain lives in the laptop's own environment
   by the program's design (owner-directed, royalty class), and
   Tristen's WSL2 GPU desktop access is his own tooling. This spec
   governs Syncyard execution; it does not reach into programs'
   own build environments. Where a program's builds are submitted
   AS Syncyard jobs, they convert like everything else.

## The target architecture

### 1. The docker workflow

A new door-repo workflow, docker.yml, twin of run.yml and wsl.yml:
same inputs (cmd_b64, cwd), same wrapper discipline (payload exit
code is the verdict, wrapper success is transport only). The
payload runs inside a pinned Syncyard job image on the host's
Docker engine:

- The runner step starts the container with the job script mounted
  read-only, a per-job work directory mounted read-write, and the
  job's declared mounts (and only those) from the submission.
- Networking defaults to the job's declared profile (none, host
  loopback services, or full), recorded in the queue record.
- GPU jobs pass the device through on hosts whose capability probe
  reports a GPU (the per-machine lane rule still governs which
  lanes exist).
- The container is removed at job end; artifacts leave through the
  mounted work directory and the existing grab path, never through
  the container filesystem.

### 2. Job images

One Syncyard base job image (Linux userland matching today's
Ubuntu-24.04 WSL environment: bash, python3, git, gh, the fleet's
usual build tools), version-pinned by digest in the workflow, built
and published from this repo. Programs may name their own image in
the submission; the record carries the digest that actually ran.
An image change is a release, with the same battery as a tray
build.

### 3. The gatekeeper contract

- `gk submit --workflow docker.yml` becomes the default for Linux
  shell work; wsl.yml submissions warn from day one and refuse
  after the cutover date in the migration plan, except records
  carrying a named exception.
- Host capability probing gains the Docker facts (engine present,
  venue VMM on Windows, server version, GPU passthrough) beside
  the existing facts; routing (spec 016) prefers the docker venue
  for Linux work on every host that has it.
- The queue record gains additive fields: venue, image digest,
  mounts. Payload verdict discipline is unchanged.

### 4. The backchannel agents

The agents are Syncyard infrastructure, so they convert too: each
agent becomes a small native service (Windows side, windowless by
the standing hard constraint) that runs its command cycle through
the local Docker engine when a command is a Linux payload, and
natively when it is a Windows payload, with the same heartbeat and
publish contract as today. Until an agent converts, it is a named
exception below; the conversion must not interrupt the iSMART
royalty lane on the laptop, so the laptop agent converts LAST and
only in a window the iSMART chat approves.

### 5. Cross-platform

The job contract (script, cwd, mounts, network profile, image) is
OS-agnostic: the same submission runs on a Windows host's VMM
engine, a Linux host's native engine, or a macOS host's Docker
Desktop engine. Host paths in mounts are the only OS-specific
input, and they are resolved per host from the hosts registry,
never embedded in job scripts (the run.yml cwd defect of
2026-11-04 is the standing lesson).

## Named exceptions (each with an exit condition)

- **Laptop backchannel agent**: converts last, in an iSMART
  approved window. Exit: native agent live on the laptop with the
  heartbeat and publish contract verified over three cycles.
- **Desktop backchannel agent**: converts with the desktop's
  docker.yml proof. Exit: same verification.
- **WSL2 Docker store custody**: not an execution item; closes
  when ChronoHive's destination reconciliation lands and the owner
  rules on the store.
- **Programs' own build environments** (iSMART firmware toolchain
  and similar): out of scope by the boundary in the inventory;
  revisited only if the owning program asks.
- **iSMART follows the fleet (owner amendment, 2026-11-06
  10:18 ET)**: iSMART's own WSL-dependent work converts to Docker
  under this spec as well, but ONLY after all of today's iSMART
  work is complete. Nothing in the iSMART environment changes
  while any part of today's program (firmware builds, flashes,
  kiosk deploy, the Item G chain) is still open. The iSMART chat
  declares today's work done; only then does its conversion
  window open, sequenced with the laptop backchannel agent's
  conversion, which remains last and jointly approved.

## Migration order

1. docker.yml + base job image + capability probe facts; proof
   battery: the standing probe set (store probe, version probes)
   re-run as docker jobs with identical payloads and compared
   against their wsl.yml results.
2. New submissions default to docker.yml; wsl.yml warns.
3. Desktop backchannel agent conversion; laptop fleet file stops
   depending on the laptop agent's WSL clone (the tray points at a
   natively published path).
4. Laptop backchannel agent conversion in the iSMART window.
5. wsl.yml refuses new submissions except named exceptions;
   spec 004 marked superseded.

## Falsifiers

- Any converted job whose docker-venue payload verdict differs
  from its wsl-venue control run on the same script and inputs
  fails the conversion for that job class until the difference is
  explained and fixed.
- A docker job that can see a host path it did not declare in its
  mounts fails the spec outright (isolation is the point of the
  venue).
- The laptop conversion is a failure if any iSMART job is delayed
  or disturbed by it, by the royalty rule, regardless of the
  conversion's own success markers.

## What does not change

- The door stays the gatekeeper; the wrapper/payload verdict rule;
  the invisibility hard constraint (containers and their hosts
  never open a window); lane semantics and per-machine lane sets;
  the iSMART royalty priority.
- run.yml (native Windows jobs) is untouched: Windows work runs
  natively on Windows, Linux work runs in Docker, and WSL is the
  thing being retired, not a third venue.

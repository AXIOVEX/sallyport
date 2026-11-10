# Feature Specification: macOS Host (air-inference)

**Feature Branch**: `feat/039-macos-host`
**Created**: 2026-11-07
**Status**: **RATIFIED 2026-11-07 as a block with specs 034-039 (owner decision, Desktop Access chat). Implementation may proceed under the program sequence.**
**Direction**: Owner decision 2026-11-06 17:54 ET: **full Mac host, now.** This supersedes the earlier deferred stance (build/CI target only) and corrects spec-program Q2 accordingly. Source plan: MAC-SYNCYARD-HOST-PLAN-2026-11-06.md.
**Context correction (owner, 2026-11-06)**: the proving machine is a **shared household** MacBook Air, not one person's alone, despite its device name. The owner uses it for work (presentation machine, "Mac lite" test profile). Nothing is installed or changed on it without the owner's explicit go.
**Proving machine**: air-inference — MacBookAir10,1, Apple M1, 8 GB unified memory, 256 GB SSD (~27-31 GB free), macOS 27.0.1 build 26A434 at readiness check 2026-11-06 17:59 ET; Tailscale installed and running; Homebrew, xcodebuild, git present; docker, ollama, cargo absent (routine install-time items). Key-only SSH over the tailnet verified 2026-11-06 19:47 ET. Signing: the owner's own Terminal read (`security find-identity -v -p codesigning`, 2026-11-06 18:06 ET) reports **0 valid identities** — no Developer ID exists on the machine. First proof signs **ad-hoc** (accepted by the owner in advance); Apple Developer enrollment + Developer ID creation is a named owner act required before notarized distribution.
**Companions**: Spec 035 (presentation rules; the menu-bar app inherits them), spec 036 (compile-only CI discipline, FR-036-11), spec 038 (update discipline UPD-01..09 applies unchanged on macOS), spec 033 (Docker-native direction, carried here by FR-MAC-03).

## Requirements (FR-MAC-01..11 from the plan, restated with tests)

| FR | Requirement | Named test case(s) | Acceptance mode |
|---|---|---|---|
| FR-MAC-01 | BUILD/CI: the Rust core, agent, and control app MUST build and pass the full test suite for aarch64-apple-darwin and x86_64-apple-darwin, in the release matrix and in CI on a real macOS runner. No Linux-only (systemd, Unix-socket paths, /proc) or Windows-only assumptions outside the platform layer; violations MUST fail CI (also red-team L1). | T039-01 mac-ci-both-arches; T039-02 platform-assumption-lint | gate |
| FR-MAC-02 | AGENT SERVICE: the host agent MUST run as a launchd LaunchAgent in the owner's login session (jobs needing the GUI session, Keychain, or user files run as the user, not root), KeepAlive under launchd supervision, windowless by construction. Logs MUST go to ~/Library/Logs/Syncyard plus the gatekeeper harvest path. A system LaunchDaemon MUST be used only if a named job class needs pre-login availability, specced separately, never assumed. | T039-03 launchagent-session-and-keepalive; T039-04 agent-logs-fleet-standard | test |
| FR-MAC-03 | VENUES: (a) native venue — POSIX shell payloads (zsh/bash) executed directly, payload exit code recorded as the verdict; (b) docker venue — through the host's Docker engine (Docker Desktop or a detected alternative such as Colima/OrbStack). The agent MUST probe for a running engine and advertise the docker venue only when one answers; a Mac without Docker is a native-only host, recorded as such, never an error state (spec 033 carried to macOS). | T039-05 native-job-verdict-harvest; T039-06 docker-venue-probed-not-assumed; T039-07 engine-stopped-no-docker-routing | test |
| FR-MAC-04 | CAPABILITY PROBE: automatic probe (sysctl / system_profiler) MUST report chip and core counts, unified RAM, disk size/free, macOS version and build, Metal support, Docker engine presence/version, Tailscale identity. Registered under the fleet alias air-inference, refreshed on change and daily. Unknown MUST record as unknown, never 0. Unified memory MUST be reported as ONE pool (no fake VRAM figure). | T039-08 capability-matches-system-profiler | test + human-as-oracle |
| FR-MAC-05 | AVAILABILITY HONESTY: the agent MUST heartbeat while awake; on wake it MUST re-register before accepting work. The dispatcher MUST treat a sleeping Mac as unavailable (no wake-on-LAN promise). Payloads routed to a Mac MUST be checkpoint/resume-safe or short. A job interrupted by sleep MUST be reported as interrupted-by-sleep, never as failed or passed. | T039-09 sleep-wake-no-loss-no-double-run; T039-10 sleep-interruption-named-not-failed | test |
| FR-MAC-06 | RESOURCE GUARDS (sized for air-inference, probe-driven elsewhere): default concurrency 1 heavy / 2 light; per-job needs-ram caps MUST be enforced against the unified pool; the inference service's ~4 GB ceiling MUST count against the same pool when both run. Fanless thermal limits MUST be stated in the host record; sustained all-core batch campaigns MUST NOT route here when tower-compute or mini-server can take them. The Mac is classed an opportunistic, non-requireable node per spec 038 FR-038-16 (red-team M2). | T039-11 unified-pool-caps-enforced; T039-12 batch-campaigns-route-elsewhere | test |
| FR-MAC-07 | SIGNING: distributed macOS binaries MUST be code-signed and notarized (Developer ID; enrollment is the named owner act below), and the auto-update path MUST verify signature/pinned hash before applying anything (spec 038 UPD-07 discipline, unchanged on macOS). Acceptance MUST include a Gatekeeper pass on a clean user account. First proof on air-inference MAY sign ad-hoc under the settled tier rule (Developer ID when a certificate exists, else ad-hoc, always reporting which tier was produced) and MUST report the tier. | T039-13 clean-account-gatekeeper-pass; T039-14 signing-tier-reported | test + human-as-oracle |
| FR-MAC-08 | PERMISSIONS: the agent MUST request no broad TCC grants by default. Any grant a job class actually needs (e.g. Full Disk Access) MUST be named in that job class's record with its reason; first-run TCC prompts are an owner-at-keyboard step in the install checklist, never silently pre-granted. | T039-15 no-default-tcc-grants; T039-16 needed-grants-named-with-reason | test + gate |
| FR-MAC-09 | CONTROL APP: the Syncyard control app MUST run natively on macOS (menu-bar presence instead of a tray icon) under spec 035's rules, including the served-identity rule: status surfaces show the build stamp of the app actually running. | T039-17 menubar-app-identity-stamp | test |
| FR-MAC-10 | BACKCHANNEL: a macOS backchannel agent variant MUST operate under the same reserved-use rules as every other host (Syncyard development/recovery only, owned by the Syncyard chat). | T039-18 mac-backchannel-reserved-use | gate |
| FR-MAC-11 | INFERENCE COEXISTENCE: the Ollama inference service (MAC-INFERENCE-SERVICE-PLAN-2026-11-06.md) MUST remain a fleet service on this host, independent of the Syncyard agent; the agent's capability record MUST advertise the endpoint when it is live. The inference budgets stand and share the machine under FR-MAC-06. | T039-19 inference-endpoint-advertised-when-live; T039-20 inference-independent-of-agent | test |

## Acceptance tests (from the plan; verdicts from payload records)

1. Gatekeeper dispatches a native shell job to air-inference; the job's own payload exit code and output harvest back and match. (T039-05)
2. With a Docker engine running, a docker-venue job completes the same way; with the engine stopped, the host record shows the docker venue unavailable and no docker job is routed there. (T039-06, T039-07)
3. Sleep the Mac mid-heartbeat, wake it: the host goes unavailable, then re-registers, and no job is lost or double-run across the cycle. (T039-09)
4. Clean-account Gatekeeper test: the signed agent installs and starts with no security warning. (T039-13)
5. The capability record matches a hand read of system_profiler on the proving machine (chip, RAM, disk free within rounding). (T039-08)

## Owner acts and blockers (named, not hidden)

- **Apple Developer enrollment + Developer ID creation**: owner act, required before notarized distribution to other Macs; does NOT block the first ad-hoc proof on air-inference.
- **Install on air-inference**: the Mac must be awake; owner-at-keyboard only for any TCC prompts. The remote install path is key-only SSH over the tailnet (verified 2026-11-06 19:47 ET); the Muse pairing channel itself has no shell.
- Nothing is installed or changed on the shared household machine without the owner's explicit go.

## Falsifiers

- A job interrupted by sleep is reported as failed or passed: this spec has failed.
- The capability record shows a VRAM figure, or a 0 where the truth is unknown: this spec has failed.
- A docker job routes to the Mac with no engine answering: this spec has failed.
- The agent runs as root, or requests a broad TCC grant by default: this spec has failed.

## Tasks

- T001: This spec.
- T002 (after ratification + owner go): macOS CI legs for both arches.
- T003: LaunchAgent agent + native venue proof on air-inference (ad-hoc tier, reported).
- T004: Docker venue probe + proof; sleep/wake acceptance.
- T005: Menu-bar control app; backchannel variant; inference coexistence record.

## What this does NOT change

- macOS stays macOS: no format, no conversion, no device rename.
- Heavy compute still belongs on tower-compute / mini-server; a full host makes the Mac a real fleet citizen, not a workhorse.

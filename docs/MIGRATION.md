# Migration and cutover plan

How an existing, working setup moves into Syncyard without breaking
the door it walks through. This document records the founding fleet's
cutover; operators adopting Syncyard over a similar setup can follow
the same gates.

## Starting point

- Dispatch workflows and self-hosted runners live in two personal
  scratch repositories: one for the desktop, one for the laptops.
  They work, and active programs dispatch through them daily.
- A separate, older remote-administration system exists and has been
  **frozen**: it carries no job traffic and is kept only as an
  emergency path for the case where the Actions route itself is
  broken.
- The gatekeeper already runs on the control machine and tracks
  in-flight work in the old repositories.

## End state

- All dispatch workflows, the gatekeeper, runner setup, clients, the
  agent skill, and this standard live in **this repository**, which is
  the canonical published source; operator copies track its releases.
- Runners register to **this repository only**, in lanes (see
  [STANDARD.md](STANDARD.md) Part 2).
- The old runner repositories are deleted. The old administration
  repository is **archived** (read-only), not deleted — it preserves
  the frozen emergency path, and deleting it would need a separate
  owner decision against the freeze.

## The gates, in order

Cutover happens **inside** the already-planned runner setup session —
the session where the lanes are registered — not as a separate event.

1. **This repository stands complete.** Workflows, gatekeeper, setup
   scripts, clients, skill, standard, security policy, CI, and the
   first tagged release all exist *before* anything re-registers.
   Nothing about the live fleet changes yet.
2. **Desktop session: re-register the desktop runners here.** In one
   owner session on the desktop, register four runners to **this**
   repository with the lane labels (`lane-quick`, `lane-normal`,
   `lane-long`, `lane-gpu`), each in its own directory, each started
   as a user-window process under the owner's account. Registration
   tokens are fetched fresh in the session and never stored. The
   runner watchdog is installed in the same session and pointed at all
   four directories. The runbook is
   [runner/SETUP.md](https://github.com/electrohire/syncyard/blob/main/runner/SETUP.md).
3. **Identity proof per lane.** Before any real dispatch routes to a
   lane through this repository, run an identity job on it: a dispatch
   whose log prints the machine name, the Windows user, and (from the
   WSL side) the Linux user it ran as; the GPU lane's proof also prints
   both graphics cards from the Windows side. A lane with no identity
   proof on record carries no real work.
4. **Move the door.** Update the gatekeeper's repository constant to
   this repository (a release of this repo), repoint the convenience
   clients, and let each program roll over at its next natural
   dispatch — program by program, not in one flag day.
5. **Queue drain check.** Jobs already queued against the old
   repositories are allowed to finish there, or are cancelled by their
   own programs and re-submitted here. Nothing is silently carried
   over; every in-flight run is accounted for by its owner.
6. **Laptop session: same treatment, separately.** The laptop runners
   re-register to this repository in their own session, with
   machine-specific labels so a job can never land on the wrong
   laptop, and pass their own identity proof plus the laptop fleet's
   acceptance check. The laptop's existing access arrangements stay
   untouched until that acceptance passes — this gate is independent
   of the desktop cutover and never blocks it, and the desktop cutover
   never touches the laptop.
7. **Delete the old runner repositories — only now.** Both old runner
   repositories are deleted **only after** identity-job proof through
   this repository (gate 3 for the desktop, gate 6 for the laptops)
   and the queue drain check (gate 5). If any gate is unmet, the old
   repositories stay, untouched.
8. **Archive the old administration repository.** In the same pass,
   the old remote-administration repository is set to archived
   (read-only). It is **not** deleted. The emergency path it preserves
   stays frozen: no job traffic, no routine use, owner decision
   required to change its state.

## What does not change

- The gatekeeper's state (its queue files and ledger) lives on the
  control machine, not in any of these repositories; cutover does not
  touch it beyond the repository constant in gate 4.
- No runner is stopped, moved, or re-registered outside the two owner
  sessions. In particular, nothing is bootstrapped from inside an
  Actions job — job teardown kills child processes, so a job can never
  install the runner that would replace it.
- Programs keep their own records of their runs; the gatekeeper's
  ledger is the fleet-level record, and cutover does not rewrite it.

## Rollback

Until gate 7 completes, rollback is trivial: the old repositories and
their runners are untouched, the gatekeeper's constant points back,
and dispatch resumes the old route. After gate 7, rollback means
recreating a runner repository from this repository's `runner/`
contents and re-registering — the reason deletion waits for proof.

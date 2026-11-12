# Concepts

The ideas the whole tool is built from. Everything on this
page describes shipped behavior unless it sits under
[Specified, not yet shipped](#specified-not-yet-shipped) at
the bottom.

## The gatekeeper is the only door

Programs never dispatch GitHub Actions workflows directly and
never poll runs on their own. They submit job requests to the
gatekeeper, which is the single dispatcher and the single
watcher. That one decision is what makes the rest possible:
because every dispatch passes one point, the fleet's shared
API budget is spent once, centrally, with headroom checked
before every dispatch — and because every watch passes one
point, no program ever needs a poller of its own.

## Lanes

Every dispatch declares a lane; no lane declared means not
dispatched. Lanes split the runners by job shape so quick
checks never stand behind long renders:

| Lane | Expected duration | Carries |
|---|---|---|
| `quick` | under ~2 min | probes, status checks, small transfers |
| `normal` | ~2–30 min | builds, installs, packaging |
| `long` | over ~30 min, or unknown | batch stages, scoring runs, renders |
| `gpu` | any duration | any job that needs a GPU |

Routing rules: a GPU need overrides duration (a 30-second GPU
probe still goes to `gpu`); unknown duration goes to `long`;
a job that overruns its lane's norm twice is re-laned. Lane
names, counts, concurrency, and health watermarks are
configuration, not code — the table is the founding fleet's
set, and [Configuration](configuration.md) shows how to make
it yours.

Lanes give queue isolation, not more compute: the machine's
CPU, memory, and disk are still shared, and a job addressed
to a lane runs on that lane's host or is refused — never
silently rerouted.

## Marker verdicts

A job's verdict comes from the marker lines it prints in its
own log — never from the GitHub Actions success color alone.
Run status has been observed lagging far behind reality (a
run can sit at "queued" in the API long after its log holds
the complete result), so the gatekeeper harvests marker lines
(lines beginning with an `ALL_CAPS_` token, matched by a
configurable pattern) at completion and records them as the
verdict. The status board shows marker counts per job; a
completion with no markers is listed as an issue, not assumed
to be a success.

## Watermarks, and why nothing auto-cancels

Each lane carries watermarks for queue wait and execution
time (warn, then escalate). A breach triggers a recorded
check — starvation if the job is queued, stall-suspect if it
is executing — and an escalation with evidence. The
gatekeeper **never cancels anything**: cancellation authority
belongs to the job's own program coordinator after a recorded
check, or to the owner. A slow job and a wedged job look
identical from outside; the system escalates the question
instead of guessing with a kill.

## Pause and stop

Two fleet-wide holds, deliberately different:

- **Pause** holds dispatch of program jobs while the watch
  pass keeps running — verdicts, watermarks, and the board
  stay current, and jobs already dispatched are untouched.
  Operator maintenance jobs (`submit --maintenance`) still
  dispatch; that class exists for work on the fleet itself.
- **Stop** is the hard halt: a sweep while stopped returns
  immediately, spending no API budget and doing no watching.
  `start` clears both a stop and a pause.

## Hosts and venues

A lane points at a **host**; the host's type decides the
venue: `windows` (PowerShell), `wsl2` (a distro reached
through its Windows host), `linux` and `macos` (native bash —
one shared transport body, kept identical by a CI contract),
and `docker` (a named image; the caller's script runs inside
the container, with files surviving only under declared
mounts or via a grab). Each venue proves itself with identity
markers (`LID_HOST`, `LID_OS`, and a per-type user or image
marker) before anyone trusts its results.

## Liveness and the going-down record

The gatekeeper reads each host's heartbeat on its sweep
cadence and shows hosts UP or DOWN on the board (DOWN means
only that reporting stopped). When a host signals it is going
down, the gatekeeper records the event, annotates the
affected jobs, notifies their programs, and holds dispatch
to that host until the host is back and the hold is cleared
(`host-resume`). A controlled reboot (`reboot --host`) writes
that going-down record *before* it signals anything, refuses
while jobs are active unless forced, and never cancels or
re-dispatches interrupted work. The protection level is
delay + drain + checkpoint + notify — never prevent.

## The failsafe backchannel

Outside the dispatch flow sits a deliberately narrow
emergency channel (git-based, no API quota) used only for
Syncyard's own development and recovery when the Actions
route itself is broken. It carries **no job traffic, ever**.
Its client ships in the standalone package as
`syncyard.backchannel.bc`; the channel's ownership and rules
are in the repository's `backchannel/README.md`.

## Specified, not yet shipped

These exist as numbered specifications in the repository
(`specs/`) and are being built on their own branches. They
are **not** part of the currently released behavior, and the
rest of this site does not describe them as if they were:

- **Priorities and focus mode**: per-job priority
  values with an automatic optimizer under human override,
  and a focus mode that admits only a chosen program's,
  key's, or chat's jobs while the rest wait.
- **Lane locks**: an agent holding a priority can
  lock a lane so other programs' jobs cannot take it, with
  graceful release, suspension alongside the fleet pause,
  and expiry.
- **Adaptive lanes**: customizable lane profiles
  and an automatic mode that balances jobs across CPU, GPU,
  RAM, and storage, with Docker and WSL as venues, from
  cross-platform host capability probes.

When each merges and releases, this page moves it up into
the shipped sections in the same change — that is the
project's standing skill-currency rule.

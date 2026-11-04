# Follow-on note: `gk profile suggest` (2026-11-04)

Spec 016 shipped profiles (FR-001), capability snapshots
(FR-005), and auto routing (FR-006), but left an adopter with
a blank page when they wanted a profile sized to a machine
that is not the fleet desktop. This follow-on closes that
gap with a drafting aid, deliberately short of a runtime
verb: FR-001's rule stands, profiles change only by an
operator's config edit, and there is still no `gk profile
--set`.

## The command

```
gk profile suggest [--host HOST] [--name PROFILE_NAME] [--out PATH]
```

(Also reachable as `gk profile --suggest`.) It reads one
host's capability snapshot from `hosts.json`, the record
`gk probe-host` stores, and prints a YAML fragment of the
profiles shape (`lanes: profiles: <name>: ...`) preceded by
the reasoning as YAML comments, so the draft is
self-explaining wherever it is pasted. When `--host` is
omitted, the draft is for the active profile's host, and a
multi-host active profile refuses with the hosts named.

Fail-closed, like the config loader: no snapshot recorded
for the host, or a snapshot older than
`lanes.capability_max_age_min`, is a refusal (exit 2) that
names `gk probe-host` as the refresh route. A suggestion is
never drafted from evidence routing would call degraded,
and never from no evidence.

## The suggestion rules (deterministic)

The same snapshot always drafts the same profile.

- **quick**: always suggested; short work needs a home on
  every host, independent of measured values.
- **normal**: suggested only when the snapshot measures
  `cpu_cores >= 8` and `ram_total_mb >= 16384` (16 GiB).
  Both values must be measured: if either is unknown, the
  lane is not suggested and the reasoning says the field is
  unknown. Unknown never counts as meeting the bar.
- **long**: always suggested, with expectation-mode
  watermarks.
- **gpu**: suggested only when a GPU is actually measured
  present in the snapshot: at least one GPU entry carrying a
  measured name or a measured `vram_mb`. The reasoning names
  the largest GPU and its VRAM; a unified-memory GPU is
  called out as a shared pool, not a discrete budget. When
  the GPU fields are unknown (no GPU information recorded,
  or entries with neither name nor VRAM), no gpu lane is
  drafted and the reasoning says unknown is never treated
  as a GPU.
- **concurrency**: 1 on every suggested lane. A suggestion
  is a starting draft; raising concurrency is a human edit,
  and the reasoning says so.
- **watermarks and duration bands**: copied from the
  fleet-default profile's same-named lanes, so a suggested
  profile starts from the numbers the fleet already runs.

## Never self-applied

The command writes nothing except, when `--out` is given,
the fragment to that exact path. It refuses (exit 2) when
the path already exists and when the path is the live
config file this process loaded. It never edits the config,
never writes `hosts.json`, and never switches the active
profile. Using a draft is the operator's act: merge the
fragment into `syncyard.yaml` and set
`lanes.active_profile`, exactly the FR-001 flow.

## Acceptance

Battery: `gatekeeper/test_profile_suggest.py`, run in CI
with the other gatekeeper batteries. It pins the four
fixture shapes (large GPU host, small CPU-only host, stale
snapshot refusal, unknown GPU fields), the `--out`
discipline, determinism, the fragment loading as a valid
config through the real loader, and `hosts.json` left
byte-identical.

This does not establish that the suggested profile is the
right profile for a host: the thresholds are a stated,
deterministic starting policy, and the operator who merges
the draft owns the numbers from that moment, like every
profile in the table.

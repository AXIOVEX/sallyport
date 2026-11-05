# Plan: Spec 016 — Adaptive Lanes

Implementation plan for the change on `spec/016-adaptive-lanes`,
stacked on `spec/015-lane-priority-lock` (ef666f8). One repo
change; no fleet deploy (owner-gated, see tasks.md T018).

## Design decisions

1. **Profiles resolve at load; nothing downstream learns a
   new shape.** `config.py`'s `lanes` section becomes
   `{active_profile, auto_default, capability_max_age_min,
   profiles}`. `validate()` checks the whole table (every
   profile, active or not), then `load_config()` resolves:
   `cfg["lanes"]` is replaced by the active profile's
   *enabled* lanes — the same flat mapping gk.py, the golden
   battery, and the config battery have always read — and the
   table rides along as `cfg["lane_profiles"]` +
   `cfg["active_lane_profile"]` + the two knobs. Every
   consumer (dispatch, watermarks, locks, status) is
   untouched in shape; only new code reads the new keys.
   Consequence: the golden battery's LEGACY anchor
   (LANES == quick/normal/long/gpu, the watermark tables,
   LANE_HOST, …) passes unchanged *iff* `fleet-default`
   reproduces today's lanes exactly — which is therefore a
   hard design constraint, pinned in test_config and
   test_spec016 both.

2. **The pre-016 flat shape fails closed, with the migration
   named.** Deep merge would otherwise smuggle flat lane
   names in as siblings of `profiles` and produce a config
   that silently means something else. `validate()` rejects
   any `lanes` key outside the four named ones, with an error
   that names the profile shape. A fleet `syncyard.yaml` in
   the old shape therefore stops a *future* deploy loudly at
   load rather than misrouting quietly — conversion is a
   deploy-procedure step, not a loader heuristic.

3. **New per-lane fields are optional and default to the old
   world.** `resource_class` (default `any`), `duration_band`
   (default open), `enabled` (default true), `workflow` pin
   (default none). The resolved lane dicts carry them through
   so gk.py can read class/band/pin from the same dicts it
   already indexes; a lane written in the pre-016 per-lane
   shape inside a profile is fully valid.

4. **Auto resolution is a dispatch-pass head step that
   writes an ordinary job.** In `dispatch_pass`, after lock
   expiry (spec 015 FR-007) and alongside the host-hold set
   computation: each submitted job with `lane is None` is
   evaluated once per pass until placed. Placement writes
   `lane` + the `auto_route` snapshot through `save_job_safe`
   and ledgers `auto_routed` (+ `auto_degraded` when the
   evaluation was degraded); non-placement records
   `auto_wait` and ledgers `auto_no_fit` only when the unmet
   reason set changes (the sweep runs every 5 minutes;
   verdict noise is a defect). The waiting loop skips
   still-lane-less jobs before any gate can touch them, and
   every downstream consumer (watermarks, status, reboot,
   going-down) already tolerates or never sees a lane-less
   record — it has no run, so nothing run-shaped can be
   computed from it. Resolution deliberately runs *before*
   the pause/focus gate in code order but binds nothing
   about admission: the gates re-evaluate the resolved job
   exactly as an explicit one, locks included — a lock taken
   after resolution still holds the job at dispatch.

5. **Scoring is one pure function; context is built
   separately.** `resolve_auto_lane(job, ctx) -> (lane,
   snapshot)` takes a plain-data context (per-lane load,
   bands, classes, pins; the locks map; per-host capability
   views; the job's program history) and returns data. The
   impure shell (`_resolve_auto_jobs`) builds the context
   from the job list + `hosts.json`, calls the pure function,
   persists. Determinism is therefore testable without any
   filesystem: the battery calls the function twice on one
   context and pins identical output, snapshot included
   (modulo the evaluation timestamp it carries).

6. **Capability lives in hosts.json, beside liveness.**
   `hosts.json` already is the gatekeeper's per-host record
   file (verified writes via `save_hosts_state`); the
   snapshot rides as a `capability` key on the host's
   record, preserved across liveness saves because the
   liveness pass round-trips the whole record. No new state
   file, no new write discipline. Freshness is computed at
   read: `capability_view(host, now)` returns
   `(snapshot, stale)` against `capability_max_age_min`.

7. **Probe parsing is gatekeeper-side; probe scripts only
   print.** The sectioned report protocol (`SYNCYARD_PROBE 1`,
   `## <section>` blocks, `PROBE_END`) means the host side
   needs no logic beyond "run the platform's own tools and
   echo them" — all interpretation (units: CIM kilobytes,
   /proc kilobytes, sysctl bytes, df kilobytes; GPU classes;
   the unified-memory rule) lives in three parser functions
   in gk.py, proven against fixtures in the battery. The
   script bodies are constants in gk.py (the `gk reboot`
   signal-script precedent), staged to `BASE/probes/` by
   `gk probe-host` through the verified write; the copies
   under `gatekeeper/probes/` in the repo are the same bytes
   for operators to run by hand, and a battery check pins
   file == constant so the two can never drift. Parsers and
   bodies live in gk.py rather than a new module because
   every gatekeeper battery copies `gk.py` (+ `config.py`)
   into its fixture dir — a third module would have to be
   added to every pinned harness.

8. **The probe-job route reuses the reboot maintenance
   pattern.** `gk probe-host` (transport available) writes
   the script, then submits a `syncyard`-program maintenance
   job through `cmd_submit` itself (keyed `gk-probe-<host>-
   <ts>`), and stamps `probe_host` on the record — a WSL2
   host's probe rides its `via` host's `wsl.yml` lane, so
   the lane's host is not the probed host. The watch pass, on
   that job's completion, parses `log_tail` (the report is
   sized to fit it: sections carry greps/excerpts, never full
   dumps) and stores the snapshot under `probe_host`.
   Ingest is idempotent per job (`capability_ingested`).

9. **Venue is eligibility + a bonus, not a ranking** (the
   spec's stated scope cut). Docker eligibility reuses the
   existing image transport (an auto job simply has no lane
   host to default an image from, so `--image` must be
   explicit); WSL eligibility consults config truth (the
   host serves `wsl.yml`, or is a WSL2 host) with a fresh
   probe able to veto on Windows; native excludes the two
   non-native host types. Nothing translates between venues.

10. **Config-battery fixture updates are part of the
    change, and are the only pinned-battery edits.** The
    schema this spec replaces is the lanes table itself, so
    test_config's section-4 merge fixture and its three
    lanes-shaped rejection fixtures move to the profile
    shape (their checks keep their names and meanings), and
    golden profile checks are added. test_golden's MAC_YAML
    fixture gains its lane under `profiles.fleet-default`
    (deep merge then yields fleet + maclane, exactly the
    routing matrix it pins). No other battery is touched.

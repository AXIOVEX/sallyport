# Token economy in AEE core

AEE cores the token-economy **policy** and depends on the **tools**. Nothing is
vendored: `rtk`, `headroom`, `token-router`, and `ollama` are optional tools
declared in `extension.yml`, and every workflow degrades gracefully when one
is absent. The policy helper is `scripts/python/aee_token_economy.py`.

Two rules are load-bearing:

1. **Tools compress or route; they never originate evidence.** A claim is
   grounded only in original files, line ranges, and test output.
2. **Savings are measured, never estimated.** Only `rtk gain` and
   `headroom perf` count, reported as separate channels.

## Where each tool sits

### rtk — shell-output compression (implement, verify)

`rtk` (https://github.com/rtk-ai/rtk) intercepts noisy developer-command
output — `git status/log/diff`, `pytest`, `cargo test`, `docker`, `kubectl`,
`gh`, linters — and compresses it before it reaches model context. Install it
and run `rtk init -g` so its hooks filter shell output during the
shell-heavy AEE phases (implement, verify). Agents may also run commands
through the telemetry wrapper:

```bash
python .specify/extensions/aee/scripts/python/aee_token_economy.py shell -- pytest -q
```

The wrapper runs the command unchanged and appends a telemetry record
(command, byte counts, exit code) to
`.specify/extensions/aee/telemetry/shell-calls.jsonl`. Filtering itself is
done by rtk's hooks; the wrapper records, it does not filter. Savings are
read back with `rtk gain`, never inferred from byte counts.

**Trust model for `shell`:** the wrapper is telemetry, *not* a sandbox.
It confines the working directory to the project root and bounds the
command's lifetime (`--timeout`, default 600 s, after which the command
is killed and the wrapper returns 2), and it redacts secret-looking
argv values (secret-named `key=value` pairs, values after secret-named
flags, known token shapes) from the telemetry record. It does **not**
restrict what the wrapped command itself may read, write, or execute —
a command run through `shell` has the operator's full privileges. Keep
secrets out of argv regardless; redaction is hygiene, not a guarantee.

### token-router — evidence gathering before assess() gates

The upstream `token-router` script (https://github.com/sleeplesshan/token-router)
plus a local Ollama model scans an oversized log or source file and returns
JSON line ranges; the helper extracts the exact raw slices. The local model is
a router, never a summarizer, and its output is never cited as evidence —
cite the original file and line ranges. See
`commands/speckit.aee.route-evidence.md`. Without token-router or Ollama, the
helper falls back to deterministic literal-match windows and says so.

The router is executed code, so discovery is a trust decision: only a
router inside the project root or its parent is run — a
`TOKEN_ROUTER_HOME` pointing elsewhere is ignored (deterministic
fallback instead). Router ranges are strictly typed (JSON booleans are
not integers), and the route record names the router's path and
SHA-256 so a routing decision can be audited to an exact router build.

### headroom — long-horizon session compression

Headroom (https://github.com/headroomlabs-ai/headroom) is the context
optimization layer for long-horizon agent sessions. Integration points, in
order of increasing commitment:

1. **`headroom wrap` (lightest).** One-command integration around an existing
   coding-agent invocation. Use it for long implement/verify sessions where
   tool output accumulates faster than the task needs it.
2. **`headroom proxy` in `token` mode.** Sits between the agent loop and the
   model provider and compresses tool outputs, logs, and history by
   statistical analysis rather than blind truncation. This is the right layer
   for multi-phase Spec Kit runs that span condensations.
3. **`SharedContext` (multi-agent).** Use it as the substrate for cross-phase
   AEE state shared between agents, instead of pasting ledger excerpts into
   every handoff.

**Hard rule:** `speckit.aee.assess` and `speckit.aee.challenge` gates must see
raw evidence slices (via `speckit.aee.route-evidence`), never headroom-compressed
summaries. Compression is for transport between phases, not for evidence at a
gate. A compressed summary cited as evidence is `unsupported` by definition.

**Measurement caveat:** headroom's own repository once published token figures
that could not be traced to a benchmark and retracted them. Do not quote any
vendor figure. Verify headroom's effect with AEE's own benchmark before any
cost claim, and report only `headroom perf` measurements.

## Measurement discipline

- `scripts/python/aee_token_economy.py report --hours <N>` aggregates
  `rtk gain --format json` and `headroom perf --hours <N>`.
- Report RTK and Headroom separately; combine only when both are measured in
  the same report.
- `token-router`, Ollama, subagents, and state externalization are workflow
  tools, not savings channels. Never count them as savings.
- A missing or unparseable measurement is `n/a` with a reason — never a zero,
  never an estimate.

## Configuration

Reference settings live in `templates/aee-config.template.yml` under
`token_economy` (routing budgets, savings window, state paths). Like the rest
of that file they are reference only; the helper takes explicit flags.

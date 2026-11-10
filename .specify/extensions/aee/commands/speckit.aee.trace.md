---
description: "Render claim dependencies and inspect epistemic provenance"
---

# AEE Trace

Render the declared dependency/conflict structure for explicit claims. Manually inspect whether evidence and provenance references resolve; graph generation does not fetch or validate those references.

## User input

```text
$ARGUMENTS
```

Resolve `artifact=<path>`, apply the project-root and symlink guardrails from `__SPECKIT_COMMAND_AEE_ASSESS__`, then run:

```bash
python .specify/extensions/aee/scripts/python/run_aee.py graph --input "<artifact>"
```

Read the generated Mermaid file and report roots, leaves, missing references, cycles, contradictions, and claims capped by weak dependencies. The graph expresses declared epistemic structure; it does not establish causality.


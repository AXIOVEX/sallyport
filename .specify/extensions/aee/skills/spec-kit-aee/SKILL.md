---
name: spec-kit-aee
description: Set up and use the AEE Spec Kit extension from an ordinary-language request, selecting its installed assessment, evidence, challenge, and recovery skills without asking the user to learn CLI commands.
---

# AEE through Spec Kit

This is an onboarding/router skill. Product operations live in the eleven skill
documents rendered by Spec Kit from this repository's `commands/` files.

1. Resolve the requested project root and active agent integration. For an existing
   project, inspect `.specify/init-options.json` and the extension registry before
   changing anything. Preserve the user's constitution and custom artifacts.
2. If setup is requested and missing, initialize Spec Kit for the user's agent,
   then follow the [one-time publisher catalog setup](https://github.com/electrohire/spec-kit-aee#installation)
   and run `specify extension add aee`. Current Specify releases keep the default
   community catalog discovery-only; an upgrade alone does not enable installation
   by name. Inspect existing catalog configuration before registering a source.
   Do not silently change catalog trust or use `--force` over local work.
3. AEE provisions its Python engine (`applied-epistemic-engineering`, import/CLI
   `aee`), `jsonschema`, and evaluator companion (extension ID `evaluator`) on
   first use. Do not require users to install them manually before trying the
   runner. Relay a real provisioning error; respect `SPECKIT_AEE_AUTO_INSTALL=0`.
4. Read and follow the installed skill for the requested operation: draft-claims,
   assess, challenge, trace, verify, gate, review, gaps, route-evidence,
   report-savings, or message-security. Skills are installed in the active integration's skill
   directory, such as `.agents/skills/`, `.github/skills/`, or `.claude/skills/`.
   Do not assume a particular agent's slash-command spelling. If newly installed
   skills are not visible yet, tell the user to reload their agent session.
5. For “check my spec,” extract explicit claims as drafts if needed, run assess
   through the installed runner, inspect its ledger verdict, and compose other
   same-phase evaluator results through the runner's `compose` operation. Execute
   the mechanics yourself and explain the result in ordinary language.

Do not fabricate evidence or promote a draft to human acceptance. Report exact
output paths, recovery work, `not_understood`, and machine verification separately
from human acceptance. A pass is a configured-policy outcome, not truth or
independent certification. The standalone engine agent route, which does not
need Spec Kit, is documented in the
[engine guide](https://applied-epistemic-engineering.readthedocs.io/en/latest/agent/).

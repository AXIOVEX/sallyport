# Syncyard agent skills

Versioned skill packages that teach an agent to drive Syncyard:
submit jobs to the gatekeeper, watch by markers (never by run
color), read status, respect the shared API discipline, and use
the failsafe backchannel only for Syncyard recovery.

Every edition is generated from one shared source,
[`_source/contract.md`](_source/contract.md), so the content
cannot drift between hosts. Editions are listed in the support
ranking — Meta Muse is the owner's primary vehicle and the
first-class edition; the rest follow agent market share.

| Edition | Host | Install |
|---|---|---|
| [`meta-muse/`](meta-muse/SKILL.md) | Meta Muse | as a Muse skill (primary) |
| [`claude-code/`](claude-code/SKILL.md) | Claude Code | `~/.claude/skills/syncyard/SKILL.md` or `.claude/skills/` |
| [`github-copilot/`](github-copilot/SKILL.md) | GitHub Copilot | `.github/skills/syncyard/SKILL.md` |
| [`openai-codex/`](openai-codex/SKILL.md) | OpenAI Codex / ChatGPT | skill dir, or referenced from AGENTS.md |
| [`cursor/`](cursor/SKILL.md) | Cursor | `.cursor/rules/syncyard.mdc` |
| [`google-gemini/`](google-gemini/SKILL.md) | Google Gemini / Antigravity | rule/skill dir, or included from GEMINI.md |
| [`generic-agent/`](generic-agent/SKILL.md) | any agent host | wherever your host loads skill files |
| [`cli-only/`](cli-only/SKILL.md) | terminal only | no runtime assumed; `gh syncyard` + the gatekeeper CLI |

## Regenerating and checking

```bash
python3 skills/build_skills.py          # rewrite all editions from the source
python3 skills/build_skills.py --check  # fail if any edition has drifted
python3 skills/test_skills.py           # the currency battery
```

Edit the contract in `_source/contract.md` only, then regenerate.
CI runs the battery on every change: each edition's version must
equal the repo `VERSION`, the security invariant must
appear verbatim, and no edition may drift from the shared source.
A Syncyard behavior change updates the contract in the same
change (the standing SKILL.md currency rule).

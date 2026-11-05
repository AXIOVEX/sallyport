#!/usr/bin/env python3
"""Generate every Syncyard agent-skill edition from one shared source.

Spec 010 FR-006 (Amendment 1 ranking): editions are generated from
`skills/_source/contract.md` so their content cannot drift. Each
edition is the shared contract plus a small host-specific front
matter and install note. Run with no arguments to (re)write the
editions; run with --check to verify the committed editions match
a fresh render byte for byte (CI does this via test_skills.py).

Usage: python3 skills/build_skills.py [--check]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "skills" / "_source" / "contract.md"
VERSION_FILE = ROOT / "VERSION"

# Edition order is the FR-006 Amendment 1 support ranking:
# Meta Muse primary, then by agent market share.
EDITIONS = [
    {
        "slug": "meta-muse",
        "name": "syncyard",
        "description": (
            "Dispatch and watch jobs on the operator's own machines "
            "through the Syncyard gatekeeper - self-hosted GitHub "
            "Actions runner lanes with a shared API budget. Use "
            "whenever work must run on the operator's desktop or "
            "laptop: builds, probes, renders, hardware sessions, "
            "file returns."
        ),
        "edition_note": (
            "This is the primary, first-class edition of the Syncyard "
            "skill, written for Meta Muse. Install it as a Muse "
            "skill (this file as the skill's SKILL.md) in the "
            "workspace where you coordinate the operator's machines."
        ),
    },
    {
        "slug": "claude-code",
        "name": "syncyard",
        "description": (
            "Dispatch and watch jobs on the operator's own machines "
            "through the Syncyard gatekeeper (self-hosted GitHub "
            "Actions runner lanes, shared API budget). Use for any "
            "work that must run on the operator's machines instead "
            "of in this session."
        ),
        "edition_note": (
            "Claude Code edition. Install as a skill at "
            "`~/.claude/skills/syncyard/SKILL.md` (personal) or "
            "`.claude/skills/syncyard/SKILL.md` (project), or paste "
            "the contract into your CLAUDE.md if you do not use "
            "skills."
        ),
    },
    {
        "slug": "github-copilot",
        "name": "syncyard",
        "description": (
            "Dispatch and watch jobs on the operator's own machines "
            "through the Syncyard gatekeeper (self-hosted GitHub "
            "Actions runner lanes, shared API budget). Use for any "
            "work that must run on the operator's machines."
        ),
        "edition_note": (
            "GitHub Copilot edition. Install as an agent skill at "
            "`.github/skills/syncyard/SKILL.md`, or fold the "
            "contract into `.github/muse-instructions.md` for "
            "repositories whose work dispatches through Syncyard."
        ),
    },
    {
        "slug": "openai-codex",
        "name": "syncyard",
        "description": (
            "Dispatch and watch jobs on the operator's own machines "
            "through the Syncyard gatekeeper (self-hosted GitHub "
            "Actions runner lanes, shared API budget). Use for any "
            "work that must run on the operator's machines."
        ),
        "edition_note": (
            "OpenAI Codex / ChatGPT edition. Install as a skill "
            "where your Codex setup loads skills, or reference this "
            "file from your AGENTS.md so the contract is in context "
            "whenever machine work comes up."
        ),
    },
    {
        "slug": "cursor",
        "name": "syncyard",
        "description": (
            "Dispatch and watch jobs on the operator's own machines "
            "through the Syncyard gatekeeper (self-hosted GitHub "
            "Actions runner lanes, shared API budget)."
        ),
        "edition_note": (
            "Cursor edition. Install as a project rule at "
            "`.cursor/rules/syncyard.mdc` (the front matter below "
            "is Cursor-compatible), or as a user rule, so the "
            "contract applies whenever a task needs the operator's "
            "machines."
        ),
    },
    {
        "slug": "google-gemini",
        "name": "syncyard",
        "description": (
            "Dispatch and watch jobs on the operator's own machines "
            "through the Syncyard gatekeeper (self-hosted GitHub "
            "Actions runner lanes, shared API budget). Use for any "
            "work that must run on the operator's machines."
        ),
        "edition_note": (
            "Google Gemini / Antigravity edition. Install as a "
            "rule/skill where your Gemini setup loads them, or "
            "include this file from your GEMINI.md so the contract "
            "is in context whenever machine work comes up."
        ),
    },
    {
        "slug": "generic-agent",
        "name": "syncyard",
        "description": (
            "Dispatch and watch jobs on the operator's own machines "
            "through the Syncyard gatekeeper (self-hosted GitHub "
            "Actions runner lanes, shared API budget). Use for any "
            "work that must run on the operator's machines."
        ),
        "edition_note": (
            "Generic-agent edition: plain Markdown with minimal "
            "YAML front matter, for any agent host that loads "
            "skill or instruction files. If your host loads neither, "
            "keep this file in the working context for any task "
            "that touches the operator's machines."
        ),
    },
    {
        "slug": "cli-only",
        "name": "syncyard",
        "description": (
            "Drive Syncyard from a plain terminal: submit and watch "
            "jobs on the operator's own machines through the "
            "gatekeeper CLI / `gh syncyard`, with no agent runtime "
            "assumed."
        ),
        "edition_note": (
            "CLI-only fallback edition. It assumes nothing but a "
            "terminal and the gatekeeper: read the contract "
            "as operating instructions for a human or a script. "
            "Prefer the `gh syncyard ...` command form throughout "
            "(install once with `gh extension install "
            "AXIOVEX/gh-syncyard`), or the standalone `syncyard` "
            "command (install once with `pipx install syncyard`); "
            "every `gk.py` invocation below has the same arguments "
            "after either."
        ),
    },
]


def render(edition, contract, version):
    fm = [
        "---",
        f"name: {edition['name']}",
        f"description: {edition['description']}",
        f"version: {version}",
        "---",
        "",
    ]
    note = [
        f"<!-- Generated from skills/_source/contract.md by "
        f"skills/build_skills.py - do not edit this file directly. -->",
        "",
        f"**Edition: {edition['slug']}** (Syncyard {version}). "
        f"{edition['edition_note']}",
        "",
    ]
    body = contract.replace("{{VERSION}}", version)
    return "\n".join(fm + note) + body


def main():
    check = "--check" in sys.argv[1:]
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    contract = SOURCE.read_text(encoding="utf-8")
    drift = []
    for edition in EDITIONS:
        target = ROOT / "skills" / edition["slug"] / "SKILL.md"
        content = render(edition, contract, version)
        if check:
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                drift.append(str(target.relative_to(ROOT)))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            print(f"wrote {target.relative_to(ROOT)}")
    if check:
        if drift:
            print("DRIFT: editions out of sync with the shared source:")
            for path in drift:
                print(f"  {path}")
            print("Run: python3 skills/build_skills.py")
            return 1
        print(f"skills check: {len(EDITIONS)} editions in sync "
              f"(version {version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

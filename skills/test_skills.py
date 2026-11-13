#!/usr/bin/env python3
"""Skill currency battery.

Checks, for every generated skill edition:
  1. the edition exists at skills/<slug>/SKILL.md;
  2. its front-matter version equals the repo VERSION;
  3. the security invariant appears verbatim;
  4. the edition matches a fresh render from the shared source
     (no drift - enforced by build_skills.py --check);
  5. the edition set and order match the support
     ranking (Meta Muse primary, CLI-only fallback last).

Run: python3 skills/test_skills.py   (exit 0 = all pass)
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills"))
import build_skills  # noqa: E402

INVARIANT = (
    "Runners are reachable only via `workflow_dispatch` from the "
    "gatekeeper; no workflow may bind self-hosted labels to "
    "`pull_request`, `pull_request_target`, or issue events; the "
    "door repository is private; the backchannel repository is "
    "private."
)

EXPECTED_ORDER = [
    "meta-muse", "claude-code", "github-copilot", "openai-codex",
    "cursor", "google-gemini", "generic-agent", "cli-only",
]

fails = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


def main():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    slugs = [e["slug"] for e in build_skills.EDITIONS]
    check("edition order matches FR-006 Amendment 1 ranking",
          slugs == EXPECTED_ORDER, f"got {slugs}")

    for edition in build_skills.EDITIONS:
        slug = edition["slug"]
        path = ROOT / "skills" / slug / "SKILL.md"
        check(f"{slug}: SKILL.md exists", path.exists())
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        check(f"{slug}: version matches VERSION ({version})",
              f"\nversion: {version}\n" in text)
        check(f"{slug}: FR-009 invariant verbatim", INVARIANT in text)
        check(f"{slug}: teaches marker verdicts",
              "never from the GitHub Actions success color" in text)
        check(f"{slug}: teaches the FR-005 status duty",
              "Periodic status-check duty" in text)

    r = subprocess.run(
        [sys.executable, str(ROOT / "skills" / "build_skills.py"), "--check"],
        capture_output=True, text=True)
    check("generator drift check (editions == shared source)",
          r.returncode == 0, (r.stdout + r.stderr).strip()[:300])

    print()
    if fails:
        print(f"TEST_SKILLS: FAIL ({len(fails)} failing)")
        return 1
    print("TEST_SKILLS: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

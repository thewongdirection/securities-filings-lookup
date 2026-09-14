#!/usr/bin/env python3
"""
Mirror the skill at the repo root into the project-skill copy under
.claude/skills/securities-filings-lookup/.

The repo is the skill (clone it straight into ~/.claude/skills/), and it
*also* carries a copy under .claude/ so Claude Code on the web picks it
up as a project skill when a cloud session opens this repo. Two copies
means they can drift -- and they did: commit fa96deb changed only the
.claude copy, so for a while the version people installed from the repo
root was missing a documented default behavior.

So the root is canonical and the .claude copy is generated. Run this
after editing SKILL.md, scripts/ or references/:

    python scripts/sync_project_skill.py            # write the copy
    python scripts/sync_project_skill.py --check    # fail if it drifted

tests/test_skill_layout.py runs the --check path, so drift fails the
test run rather than reaching users.
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
PROJECT_COPY = SKILL_ROOT / ".claude" / "skills" / "securities-filings-lookup"

# Everything the skill needs at runtime. README.md and tests/ stay out:
# they are repo furniture, not part of what Claude loads.
MIRRORED_FILES = ["SKILL.md"]
MIRRORED_DIRS = ["scripts", "references"]

IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def _relevant(root: Path) -> set[Path]:
    out: set[Path] = set()
    for name in MIRRORED_FILES:
        if (root / name).is_file():
            out.add(Path(name))
    for name in MIRRORED_DIRS:
        for path in (root / name).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts \
                    and path.suffix != ".pyc":
                out.add(path.relative_to(root))
    return out


def differences() -> list[str]:
    """Paths that differ between the canonical skill and the copy."""
    problems = []
    canonical = _relevant(SKILL_ROOT)
    copied = _relevant(PROJECT_COPY)

    for rel in sorted(canonical - copied):
        problems.append(f"missing from project copy: {rel}")
    for rel in sorted(copied - canonical):
        problems.append(f"stale in project copy (not in the skill): {rel}")
    for rel in sorted(canonical & copied):
        if not filecmp.cmp(SKILL_ROOT / rel, PROJECT_COPY / rel, shallow=False):
            problems.append(f"differs: {rel}")
    return problems


def sync() -> list[str]:
    """Make the project copy match the canonical skill. Returns what changed."""
    changed = differences()
    if not changed:
        return []
    PROJECT_COPY.mkdir(parents=True, exist_ok=True)
    for name in MIRRORED_FILES:
        shutil.copy2(SKILL_ROOT / name, PROJECT_COPY / name)
    for name in MIRRORED_DIRS:
        target = PROJECT_COPY / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(SKILL_ROOT / name, target, ignore=IGNORED)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Report drift and exit 1 instead of writing")
    args = parser.parse_args(argv)

    problems = differences()
    if args.check:
        if problems:
            print("project skill copy is out of sync with the repo root:")
            for line in problems:
                print(f"  {line}")
            print("\nRun: python scripts/sync_project_skill.py")
            return 1
        print("project skill copy is in sync")
        return 0

    if not problems:
        print("project skill copy is already in sync")
        return 0
    sync()
    print(f"synced {len(problems)} path(s) into {PROJECT_COPY.relative_to(SKILL_ROOT)}:")
    for line in problems:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

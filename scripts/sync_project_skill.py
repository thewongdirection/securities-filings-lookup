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

# One rule for both halves: a file the copy step skips must not be a file
# the comparison demands, or --check reports drift that syncing can never
# clear (a stray .DS_Store did exactly that).
IGNORED_NAMES = {"__pycache__", ".DS_Store"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}

IGNORED = shutil.ignore_patterns(*IGNORED_NAMES, "*.pyc", "*.pyo")


def _ignored(path: Path) -> bool:
    return (any(part in IGNORED_NAMES for part in path.parts)
            or path.suffix in IGNORED_SUFFIXES)


def _relevant(root: Path) -> set[Path]:
    out: set[Path] = set()
    for name in MIRRORED_FILES:
        if (root / name).is_file():
            out.add(Path(name))
    for name in MIRRORED_DIRS:
        for path in (root / name).rglob("*"):
            if path.is_file() and not _ignored(path.relative_to(root)):
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


def sync(changed: list[str] | None = None) -> list[str]:
    """Make the project copy match the canonical skill. Returns what changed.

    Pass an already-computed differences() list to avoid walking and
    byte-comparing the whole mirrored tree a second time.
    """
    if changed is None:
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


def running_from_the_mirror() -> bool:
    """Is this the generated copy rather than the canonical script?

    Both copies are runnable and look identical, and running the mirrored
    one mirrors the mirror: .claude/skills/<skill>/.claude/skills/<skill>/...
    """
    return ".claude" in SKILL_ROOT.parts and "skills" in SKILL_ROOT.parts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="Report drift and exit 1 instead of writing")
    args = parser.parse_args(argv)

    if running_from_the_mirror():
        print(f"Refusing to run: {SKILL_ROOT} is the generated project-skill "
              "copy, not the canonical skill. Run scripts/sync_project_skill.py "
              "from the repository root instead.")
        return 1

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
    sync(problems)
    print(f"synced {len(problems)} path(s) into {PROJECT_COPY.relative_to(SKILL_ROOT)}:")
    for line in problems:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

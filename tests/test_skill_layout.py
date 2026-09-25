#!/usr/bin/env python3
"""
Structural regression tests for the skill itself.

The drift these guard against is real: commit fa96deb updated only the
.claude/skills/ copy of SKILL.md, so the version installed from the repo
root silently lost a documented default behavior until it was noticed by
hand.
"""
from __future__ import annotations

import contextlib
import io
import py_compile
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_project_skill  # noqa: E402

SKILL_MD = ROOT / "SKILL.md"


def step_zero(text: str) -> str:
    """Step 0's own prose: up to the NEXT top-level heading, whatever it is.

    Slicing to "## Step 1" swept in any section inserted between them, so
    assertions about Step 0 could be satisfied by unrelated text.
    """
    after = text.split("## Step 0", 1)[1]
    parts = re.split(r"\n## ", after, maxsplit=1)
    return parts[0]
SCRIPTS = sorted((ROOT / "scripts").glob("*.py"))

# Imported by the CLIs rather than run: no argparse, no network.
LIBRARY_MODULES = {"pdf_utils.py", "net_errors.py", "naming.py",
                   "sec_identity.py", "disk_cache.py"}
# CLIs that never touch the network, so they don't route through net_errors.
OFFLINE_CLIS = {"identify_venue.py", "update_skill.py", "sync_project_skill.py"}


class ProjectCopyTest(unittest.TestCase):
    def test_project_skill_copy_matches_the_repo_root(self):
        problems = sync_project_skill.differences()
        self.assertEqual(
            problems, [],
            "The .claude/skills copy has drifted from the canonical skill. "
            "Run: python scripts/sync_project_skill.py")

    def test_check_mode_exits_nonzero_when_it_drifts(self):
        with mock.patch.object(sync_project_skill, "differences",
                               return_value=["differs: SKILL.md"]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sync_project_skill.main(["--check"]), 1)
        self.assertIn("out of sync", out.getvalue())


class SyncScriptTest(unittest.TestCase):
    def test_ignored_files_are_ignored_by_both_halves(self):
        # A file the copy step skips but the comparison demands makes
        # --check fail forever: syncing can never clear it.
        for ignored in (Path("scripts/.DS_Store"), Path("scripts/__pycache__/x.pyc"),
                        Path("references/a.pyc")):
            with self.subTest(path=str(ignored)):
                self.assertTrue(sync_project_skill._ignored(ignored))
        self.assertFalse(sync_project_skill._ignored(Path("scripts/naming.py")))

    def test_a_stray_ds_store_does_not_wedge_the_check(self):
        stray = ROOT / "scripts" / ".DS_Store"
        self.assertFalse(stray.exists(), "test would clobber a real file")
        stray.write_bytes(b"junk")
        try:
            self.assertEqual(sync_project_skill.differences(), [])
        finally:
            stray.unlink()

    def test_running_the_mirrored_copy_is_refused(self):
        # Both copies are runnable; running the mirror mirrors the mirror.
        with mock.patch.object(sync_project_skill, "SKILL_ROOT",
                               ROOT / ".claude" / "skills" / "securities-filings-lookup"):
            self.assertTrue(sync_project_skill.running_from_the_mirror())
        self.assertFalse(sync_project_skill.running_from_the_mirror())


class SkillDefinitionTest(unittest.TestCase):
    def setUp(self):
        self.text = SKILL_MD.read_text(encoding="utf-8")

    def test_frontmatter_declares_a_name_and_description(self):
        self.assertTrue(self.text.startswith("---\n"))
        frontmatter = self.text.split("---", 2)[1]
        self.assertRegex(frontmatter, r"(?m)^name: securities-filings-lookup$")
        self.assertRegex(frontmatter, r"(?m)^description: \S")

    def test_every_script_it_tells_you_to_run_exists(self):
        referenced = set(re.findall(r"scripts/([A-Za-z0-9_]+\.py)", self.text))
        self.assertTrue(referenced)
        for name in sorted(referenced):
            with self.subTest(script=name):
                self.assertTrue((ROOT / "scripts" / name).is_file(),
                                f"SKILL.md references a missing script: {name}")

    def test_every_reference_doc_it_routes_to_exists(self):
        referenced = set(re.findall(r"references/([a-z0-9-]+\.md)", self.text))
        self.assertTrue(referenced)
        for name in sorted(referenced):
            with self.subTest(reference=name):
                self.assertTrue((ROOT / "references" / name).is_file())

    def test_script_paths_are_explained_as_skill_relative(self):
        # The shell's cwd is the user's project, not the skill, so a bare
        # "python scripts/..." silently does nothing -- or runs the
        # project's own scripts/ file.
        step0 = step_zero(self.text)
        self.assertIn("relative to the skill's own directory", step0)
        self.assertIn("<skill-dir>/scripts/update_skill.py", step0)

    def test_step_zero_tells_the_model_to_self_update_and_re_read(self):
        self.assertIn("scripts/update_skill.py", self.text)
        step0 = step_zero(self.text)
        for expected in ("updated", "up-to-date", "skipped", "Re-read `SKILL.md`"):
            with self.subTest(expected=expected):
                self.assertIn(expected, step0)


class SkipReasonDocumentationTest(unittest.TestCase):
    """Step 0 tells the model how to react to `reason:` -- so every reason
    the script can emit has to appear there. Six were undocumented."""

    def test_every_reason_the_script_emits_is_in_skill_md(self):
        source = (ROOT / "scripts" / "update_skill.py").read_text(encoding="utf-8")
        reasons = set(re.findall(r'skip\(\s*\n?\s*"([a-z-]+)"', source))
        reasons |= set(re.findall(r'report\.add\("reason", "([a-z-]+)"\)', source))
        self.assertTrue(reasons)
        step0 = step_zero(SKILL_MD.read_text(encoding="utf-8"))
        missing = sorted(r for r in reasons if r not in step0)
        self.assertEqual(missing, [],
                         f"update_skill.py can emit reasons SKILL.md never "
                         f"mentions: {missing}")


class SecContactDocumentationTest(unittest.TestCase):
    def test_the_contact_requirement_is_documented_before_retrieval(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        contact = text.index("SEC filings need a contact address")
        self.assertLess(text.index("## Step 1"), contact,
                        "venue identification is offline and needs no contact")
        self.assertLess(contact, text.index("## Step 2"),
                        "the contact is needed before the first SEC request")

    def test_it_tells_the_model_to_ask_rather_than_invent(self):
        section = SKILL_MD.read_text(encoding="utf-8").split(
            "SEC filings need a contact address", 1)[1].split("\n## ", 1)[0]
        for expected in ("Ask the user", "sec_user_agent.txt", "SEC_USER_AGENT",
                         "never reuse an address"):
            with self.subTest(expected=expected):
                self.assertIn(expected, section)


class ScriptHealthTest(unittest.TestCase):
    def test_the_library_and_cli_lists_cover_every_script(self):
        # A new script has to be classified deliberately, or the checks
        # below silently stop applying to it.
        classified = LIBRARY_MODULES | OFFLINE_CLIS
        network_clis = {s.name for s in SCRIPTS} - classified
        self.assertTrue(network_clis)
        for name in sorted(classified):
            with self.subTest(script=name):
                self.assertTrue((ROOT / "scripts" / name).is_file(),
                                f"{name} is classified but no longer exists")

    def test_all_scripts_compile(self):
        for script in SCRIPTS:
            with self.subTest(script=script.name):
                py_compile.compile(str(script), doraise=True)

    def test_all_scripts_expose_help_without_touching_the_network(self):
        # --help must work offline: it is how the model checks usage when
        # a venue's host is unreachable.
        for script in SCRIPTS:
            if script.name in LIBRARY_MODULES:
                continue
            with self.subTest(script=script.name):
                proc = subprocess.run([sys.executable, str(script), "--help"],
                                      capture_output=True, text=True, timeout=60)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("usage", proc.stdout.lower())

    def test_network_scripts_report_failures_through_net_errors(self):
        for script in SCRIPTS:
            if script.name in LIBRARY_MODULES | OFFLINE_CLIS:
                continue
            with self.subTest(script=script.name):
                text = script.read_text(encoding="utf-8")
                # Several scripts also import HostRefused; the import must
                # still be a single line in the module's import block.
                self.assertRegex(text, r"(?m)^from net_errors import .*\brun\b")
                self.assertIn("run(main)", text)

    def test_no_hardcoded_credentials_in_the_scripts(self):
        # The skill only ever talks to public, keyless sources; EDINET's
        # optional key must come from the environment, never from disk.
        suspicious = re.compile(
            r"(api[_-]?key|secret|token|password)\s*[:=]\s*[\"'][^\"']{8,}",
            re.IGNORECASE)
        for path in SCRIPTS + [SKILL_MD, ROOT / "README.md"]:
            with self.subTest(path=path.name):
                hits = [m.group(0) for m in suspicious.finditer(
                    path.read_text(encoding="utf-8"))]
                self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()

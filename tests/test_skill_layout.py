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


def section(text: str, heading: str) -> str:
    """One section's own prose: up to the NEXT top-level heading, whatever it is.

    Slicing to a fixed later heading swept in any section inserted between
    them, so assertions about one section could be satisfied by unrelated
    text.
    """
    after = text.split(f"## {heading}", 1)[1]
    parts = re.split(r"\n## ", after, maxsplit=1)
    return parts[0]


SCRIPTS = sorted((ROOT / "scripts").glob("*.py"))

# Imported by the CLIs rather than run: no argparse, no network.
LIBRARY_MODULES = {"pdf_utils.py", "net_errors.py", "naming.py",
                   "sec_identity.py", "disk_cache.py"}
# CLIs that never touch the network, so they don't route through net_errors.
OFFLINE_CLIS = {"identify_venue.py", "sync_project_skill.py"}


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
        guidance = section(self.text, "Running the scripts")
        self.assertIn("relative to the skill's own directory", guidance)
        self.assertIn("<skill-dir>/scripts/identify_venue.py", guidance)

    def test_the_path_guidance_comes_before_the_first_script_is_run(self):
        first_command = re.search(r"(?m)^python3? \S*scripts/", self.text)
        self.assertIsNotNone(first_command, "SKILL.md shows no script command")
        self.assertLess(self.text.index("## Running the scripts"),
                        first_command.start())


class NoSelfUpdateTest(unittest.TestCase):
    """The skill is distributed through a marketplace that owns updates, so
    it must never go back to pulling itself. Same shape as the guard for the
    removed Taiwan fetcher: the removal has to stay complete and explained.
    """

    def test_the_updater_script_and_its_tests_are_gone(self):
        self.assertFalse((ROOT / "scripts" / "update_skill.py").exists())
        self.assertFalse((ROOT / "tests" / "test_update_skill.py").exists())

    def test_nothing_runs_or_imports_an_updater(self):
        for path in SCRIPTS + [SKILL_MD, ROOT / "README.md"]:
            with self.subTest(path=path.name):
                self.assertNotIn("update_skill",
                                 path.read_text(encoding="utf-8"))

    def test_skill_md_has_no_update_step(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        # Steps 1-3 keep their numbers and every "see Step 3" cross-reference
        # points at them; a revived Step 0 would be an update step again.
        self.assertNotIn("## Step 0", text)
        self.assertNotRegex(text, r"(?i)\b(self-?updat\w*|updates itself)\b")
        self.assertNotRegex(text, r"(?i)\bfast-forward")
        self.assertNotRegex(text, r"(?i)\bauto-?updat")
        self.assertNotRegex(text, r"(?i)\bgit\s+(pull|fetch|merge)\b")


class CiWorkflowTest(unittest.TestCase):
    """The workflow is what keeps a failing suite from being merged, so the
    properties that make it both effective and safe are pinned here."""

    WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"

    def setUp(self):
        self.text = self.WORKFLOW.read_text(encoding="utf-8")

    def test_it_runs_the_suite_and_the_mirror_check(self):
        self.assertIn("python -m unittest discover -s tests", self.text)
        self.assertIn("python scripts/sync_project_skill.py --check", self.text)

    def test_it_runs_on_pull_requests_and_main(self):
        self.assertRegex(self.text, r"(?m)^  pull_request:")
        self.assertRegex(self.text, r"(?m)^    branches: \[main\]")

    def test_it_never_uses_the_privileged_fork_trigger(self):
        # pull_request_target runs with the base repo's secrets and write
        # token against code a fork controls.
        self.assertNotIn("pull_request_target", self.text)

    def test_it_is_read_only_and_does_not_persist_credentials(self):
        self.assertRegex(self.text, r"(?m)^permissions:\n  contents: read$")
        self.assertNotRegex(self.text, r"(?m)^\s+[\w-]+: write\b")
        self.assertIn("persist-credentials: false", self.text)

    def test_it_covers_the_oldest_python_the_readme_promises(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Python 3.10+", readme)
        self.assertIn('"3.10"', self.text)

    def test_it_never_enables_the_live_network_tests(self):
        self.assertNotIn("SKILL_LIVE_TESTS", self.text.split("jobs:", 1)[1])


class SecContactDocumentationTest(unittest.TestCase):
    def test_the_contact_requirement_is_documented_before_retrieval(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        contact = text.index("SEC filings need a contact address")
        self.assertLess(text.index("## Step 1"), contact,
                        "venue identification is offline and needs no contact")
        self.assertLess(contact, text.index("## Step 2"),
                        "the contact is needed before the first SEC request")

    def test_it_tells_the_model_to_ask_rather_than_invent(self):
        contact_section = SKILL_MD.read_text(encoding="utf-8").split(
            "SEC filings need a contact address", 1)[1].split("\n## ", 1)[0]
        for expected in ("Ask the user", "sec_user_agent.txt", "SEC_USER_AGENT",
                         "never reuse an address"):
            with self.subTest(expected=expected):
                self.assertIn(expected, contact_section)


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

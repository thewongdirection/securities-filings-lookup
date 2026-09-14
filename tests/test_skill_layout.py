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
SCRIPTS = sorted((ROOT / "scripts").glob("*.py"))


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

    def test_step_zero_tells_the_model_to_self_update_and_re_read(self):
        self.assertIn("scripts/update_skill.py", self.text)
        step0 = self.text.split("## Step 0", 1)[1].split("## Step 1", 1)[0]
        for expected in ("updated", "up-to-date", "skipped", "Re-read `SKILL.md`"):
            with self.subTest(expected=expected):
                self.assertIn(expected, step0)


class ScriptHealthTest(unittest.TestCase):
    def test_all_scripts_compile(self):
        for script in SCRIPTS:
            with self.subTest(script=script.name):
                py_compile.compile(str(script), doraise=True)

    def test_all_scripts_expose_help_without_touching_the_network(self):
        # --help must work offline: it is how the model checks usage when
        # a venue's host is unreachable.
        for script in SCRIPTS:
            if script.name in {"pdf_utils.py", "net_errors.py"}:
                continue  # library modules, no CLI
            with self.subTest(script=script.name):
                proc = subprocess.run([sys.executable, str(script), "--help"],
                                      capture_output=True, text=True, timeout=60)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("usage", proc.stdout.lower())

    def test_network_scripts_report_failures_through_net_errors(self):
        for script in SCRIPTS:
            if script.name in {"identify_venue.py", "pdf_utils.py",
                               "net_errors.py", "update_skill.py",
                               "sync_project_skill.py"}:
                continue
            with self.subTest(script=script.name):
                text = script.read_text(encoding="utf-8")
                self.assertIn("from net_errors import run", text)
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

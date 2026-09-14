#!/usr/bin/env python3
"""
Regression tests for scripts/update_skill.py.

Everything here runs against throwaway git repositories on local disk --
no network, no GitHub. The bare "origin" is named
securities-filings-lookup.git so the real foreign-remote guard applies
exactly as it would in a user's checkout.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import update_skill  # noqa: E402

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, env=GIT_ENV)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def report_dict(report) -> dict:
    return dict(report.lines)


class UpdateSkillTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

        # A bare origin whose name passes the upstream-repo check.
        self.origin = base / "securities-filings-lookup.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.origin)],
                       check=True, capture_output=True, env=GIT_ENV)

        # The "published" work clone used to push new versions.
        self.upstream = base / "upstream"
        git(base, "clone", str(self.origin), str(self.upstream))
        (self.upstream / "SKILL.md").write_text("version 1\n", encoding="utf-8")
        git(self.upstream, "add", "SKILL.md")
        git(self.upstream, "commit", "-m", "v1")
        git(self.upstream, "push", "origin", "main")

        # The user's skill checkout under test.
        self.skill = base / "skill"
        git(base, "clone", str(self.origin), str(self.skill))

    def publish(self, text: str = "version 2\n", filename: str = "SKILL.md") -> None:
        (self.upstream / filename).write_text(text, encoding="utf-8")
        git(self.upstream, "add", filename)
        git(self.upstream, "commit", "-m", "publish")
        git(self.upstream, "push", "origin", "main")

    def run_update(self, **kwargs) -> dict:
        return report_dict(update_skill.update(self.skill, **kwargs))

    # --- the happy paths -------------------------------------------------

    def test_up_to_date(self):
        out = self.run_update()
        self.assertEqual(out["status"], "up-to-date")
        self.assertIn("continue", out["action"])

    def test_fast_forwards_and_reports_changed_files(self):
        self.publish()
        out = self.run_update()
        self.assertEqual(out["status"], "updated")
        self.assertEqual((self.skill / "SKILL.md").read_text(encoding="utf-8"),
                         "version 2\n")
        self.assertIn("SKILL.md", out["files"])
        self.assertIn("re-read SKILL.md", out["action"])
        self.assertNotEqual(out["from"], out["to"])

    def test_check_only_reports_without_merging(self):
        self.publish()
        head_before = git(self.skill, "rev-parse", "HEAD")
        out = self.run_update(check_only=True)
        self.assertEqual(out["status"], "update-available")
        self.assertEqual(git(self.skill, "rev-parse", "HEAD"), head_before)
        self.assertEqual((self.skill / "SKILL.md").read_text(encoding="utf-8"),
                         "version 1\n")

    def test_untracked_files_do_not_block_the_update(self):
        # save_location.txt and filings/ are gitignored, so a real
        # checkout nearly always has untracked files present.
        (self.skill / "save_location.txt").write_text("/tmp/filings\n", encoding="utf-8")
        self.publish()
        self.assertEqual(self.run_update()["status"], "updated")

    def test_updates_when_the_skill_is_a_subdirectory_of_the_repo(self):
        # Mirrors .claude/skills/securities-filings-lookup/ inside this repo.
        nested = self.skill / ".claude" / "skills" / "securities-filings-lookup"
        nested.mkdir(parents=True)
        self.publish()
        out = report_dict(update_skill.update(nested))
        self.assertEqual(out["status"], "updated")
        self.assertEqual(out["repo"], str(self.skill))

    # --- the refusals ----------------------------------------------------

    def test_uncommitted_changes_block_the_update(self):
        (self.skill / "SKILL.md").write_text("local edit\n", encoding="utf-8")
        self.publish()
        out = self.run_update()
        self.assertEqual(out["status"], "skipped")
        self.assertEqual(out["reason"], "local-changes")
        self.assertIn("SKILL.md", out["detail"])
        self.assertEqual((self.skill / "SKILL.md").read_text(encoding="utf-8"),
                         "local edit\n")

    def test_local_commits_are_never_discarded(self):
        (self.skill / "SKILL.md").write_text("local work\n", encoding="utf-8")
        git(self.skill, "add", "SKILL.md")
        git(self.skill, "commit", "-m", "local")
        out = self.run_update()
        self.assertEqual(out["reason"], "local-ahead")
        self.assertEqual((self.skill / "SKILL.md").read_text(encoding="utf-8"),
                         "local work\n")

    def test_diverged_history_is_left_alone(self):
        (self.skill / "local.md").write_text("mine\n", encoding="utf-8")
        git(self.skill, "add", "local.md")
        git(self.skill, "commit", "-m", "local")
        self.publish()
        out = self.run_update()
        self.assertEqual(out["reason"], "diverged")
        self.assertEqual((self.skill / "SKILL.md").read_text(encoding="utf-8"),
                         "version 1\n")

    def test_foreign_remote_is_not_pulled(self):
        other = Path(self.tmp.name) / "someone-elses-project.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(other)],
                       check=True, capture_output=True, env=GIT_ENV)
        git(self.skill, "remote", "set-url", "origin", str(other))
        out = self.run_update()
        self.assertEqual(out["reason"], "foreign-remote")
        self.assertIn("someone-elses-project", out["detail"])

    def test_allow_any_remote_overrides_the_name_check(self):
        renamed = Path(self.tmp.name) / "my-fork.git"
        subprocess.run(["git", "clone", "--bare", str(self.origin), str(renamed)],
                       check=True, capture_output=True, env=GIT_ENV)
        git(self.skill, "remote", "set-url", "origin", str(renamed))
        # Publish through the renamed remote so it is genuinely ahead.
        git(self.upstream, "remote", "set-url", "origin", str(renamed))
        self.publish()
        self.assertEqual(self.run_update()["reason"], "foreign-remote")
        self.assertEqual(self.run_update(allow_any_remote=True)["status"], "updated")

    def test_detached_head_is_skipped(self):
        git(self.skill, "checkout", "--detach", "HEAD")
        self.assertEqual(self.run_update()["reason"], "detached-head")

    def test_branch_missing_on_origin_is_skipped(self):
        git(self.skill, "checkout", "-b", "some-local-experiment")
        self.assertEqual(self.run_update()["reason"], "no-remote-branch")

    def test_unreachable_origin_is_skipped_not_fatal(self):
        git(self.skill, "remote", "set-url", "origin",
            str(Path(self.tmp.name) / "gone" / "securities-filings-lookup.git"))
        out = self.run_update()
        self.assertEqual(out["reason"], "offline")
        self.assertIn("continue with the local copy", out["action"])

    def test_no_origin_remote_is_skipped(self):
        git(self.skill, "remote", "remove", "origin")
        self.assertEqual(self.run_update()["reason"], "no-origin-remote")

    def test_plain_directory_is_skipped(self):
        plain = Path(self.tmp.name) / "zip-install"
        plain.mkdir()
        out = report_dict(update_skill.update(plain))
        self.assertEqual(out["reason"], "not-a-git-checkout")

    # --- safety details --------------------------------------------------

    def test_credentials_in_the_remote_url_are_redacted(self):
        secret = "ghp_" + "fake-value-used-only-by-this-test"
        git(self.skill, "remote", "set-url", "origin",
            f"https://x-access-token:{secret}@example.invalid/o/securities-filings-lookup.git")
        out = self.run_update(timeout=10)
        self.assertNotIn(secret, "\n".join(f"{k}: {v}" for k, v in out.items()))
        self.assertIn("***@", out["remote"])

    def test_redact_handles_common_url_shapes(self):
        self.assertEqual(update_skill.redact("https://user:pw@host/o/r.git"),
                         "https://***@host/o/r.git")
        self.assertEqual(update_skill.redact("https://host/o/r.git"),
                         "https://host/o/r.git")
        self.assertEqual(update_skill.redact("git@github.com:o/r.git"),
                         "git@github.com:o/r.git")

    def test_repo_name_parsing(self):
        for url, expected in [
            ("https://github.com/o/securities-filings-lookup.git", "securities-filings-lookup"),
            ("https://github.com/o/securities-filings-lookup", "securities-filings-lookup"),
            ("git@github.com:o/securities-filings-lookup.git", "securities-filings-lookup"),
            ("ssh://git@github.com/o/securities-filings-lookup/", "securities-filings-lookup"),
            ("/local/path/securities-filings-lookup.git", "securities-filings-lookup"),
            ("", ""),
        ]:
            self.assertEqual(update_skill.repo_name_from_url(url), expected, url)

    def test_a_git_timeout_is_reported_as_a_skip_not_an_exception(self):
        # A slow network must not turn into a traceback out of main().
        with mock.patch.object(update_skill, "git",
                               side_effect=update_skill.GitUnavailable(
                                   "git fetch timed out after 20s")):
            out = self.run_update()
        self.assertEqual(out["status"], "skipped")
        self.assertEqual(out["reason"], "git-unavailable")
        self.assertIn("timed out", out["detail"])

    def test_cli_always_exits_zero_and_prints_a_status(self):
        plain = Path(self.tmp.name) / "zip-install-cli"
        plain.mkdir()
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "update_skill.py"), "--dir", str(plain)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("status: skipped", proc.stdout)
        self.assertIn("reason: not-a-git-checkout", proc.stdout)

    def test_cli_updates_a_real_checkout(self):
        self.publish()
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "update_skill.py"),
             "--dir", str(self.skill)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("status: updated", proc.stdout)
        self.assertEqual((self.skill / "SKILL.md").read_text(encoding="utf-8"),
                         "version 2\n")


if __name__ == "__main__":
    unittest.main()

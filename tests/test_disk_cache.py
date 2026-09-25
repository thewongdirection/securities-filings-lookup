#!/usr/bin/env python3
"""
Tests for the shared venue-directory cache.

The bug behind this module: four venues each wrote their downloaded
directory to a fixed name in the shared temp directory, so on a
multi-user machine another user could pre-create or replace
`/tmp/sec_company_tickers.json` and the skill would resolve a ticker
from it. Nothing cached is secret; the damage is answering from someone
else's file, which is the one thing this skill must not do.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import disk_cache  # noqa: E402

POSIX_ONLY = unittest.skipUnless(hasattr(os, "getuid"),
                                 "ownership and mode checks are POSIX-only")


class CacheDirectoryTest(unittest.TestCase):
    def setUp(self):
        disk_cache.cache_dir.cache_clear()
        self.addCleanup(disk_cache.cache_dir.cache_clear)
        self.tmp = tempfile.mkdtemp()

    def test_it_is_created_private_to_this_user(self):
        with mock.patch.object(tempfile, "gettempdir", return_value=self.tmp):
            path = disk_cache.cache_dir()
        self.assertTrue(path.startswith(self.tmp))
        self.assertTrue(os.path.isdir(path))
        if hasattr(os, "getuid"):
            self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o700)

    def test_it_is_resolved_once_per_process(self):
        # Without memoization the fallback below would make a new
        # directory per lookup, so nothing would ever hit the cache.
        with mock.patch.object(tempfile, "gettempdir", return_value=self.tmp):
            self.assertEqual(disk_cache.cache_dir(), disk_cache.cache_dir())

    def test_two_users_do_not_share_one_directory(self):
        if not hasattr(os, "getuid"):
            self.skipTest("POSIX only")
        with mock.patch.object(tempfile, "gettempdir", return_value=self.tmp):
            with mock.patch.object(os, "getuid", return_value=1000):
                disk_cache.cache_dir.cache_clear()
                first = disk_cache.cache_dir()
            with mock.patch.object(os, "getuid", return_value=1001):
                disk_cache.cache_dir.cache_clear()
                second = disk_cache.cache_dir()
        self.assertNotEqual(first, second)

    @POSIX_ONLY
    def test_a_symlink_in_place_of_the_directory_is_not_trusted(self):
        # The attack the lstat is for: a symlink to a directory someone
        # else controls passes every check that follows the link.
        victim = os.path.join(self.tmp, "elsewhere")
        os.makedirs(victim, mode=0o700)
        planted = os.path.join(self.tmp, f"{disk_cache.DIR_NAME}-{os.getuid()}")
        os.symlink(victim, planted)
        with mock.patch.object(tempfile, "gettempdir", return_value=self.tmp):
            path = disk_cache.cache_dir()
        self.assertNotEqual(os.path.realpath(path), os.path.realpath(victim))
        self.assertTrue(disk_cache.is_private_dir(path))

    @POSIX_ONLY
    def test_a_world_writable_directory_is_not_trusted(self):
        planted = os.path.join(self.tmp, f"{disk_cache.DIR_NAME}-{os.getuid()}")
        os.makedirs(planted, mode=0o777)
        os.chmod(planted, 0o777)          # defeat umask
        with mock.patch.object(tempfile, "gettempdir", return_value=self.tmp):
            path = disk_cache.cache_dir()
        self.assertNotEqual(path, planted)
        self.assertTrue(disk_cache.is_private_dir(path))

    @POSIX_ONLY
    def test_a_directory_owned_by_someone_else_is_not_trusted(self):
        # Creating a directory owned by another uid needs root, so this
        # moves our own uid instead: same comparison, either way round.
        planted = os.path.join(self.tmp, f"{disk_cache.DIR_NAME}-{os.getuid()}")
        os.makedirs(planted, mode=0o700)
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1):
            self.assertFalse(disk_cache.is_private_dir(planted))

    @POSIX_ONLY
    def test_a_plain_file_in_the_way_is_not_trusted(self):
        planted = os.path.join(self.tmp, f"{disk_cache.DIR_NAME}-{os.getuid()}")
        Path(planted).write_text("not a directory")
        with mock.patch.object(tempfile, "gettempdir", return_value=self.tmp):
            path = disk_cache.cache_dir()
        self.assertTrue(os.path.isdir(path))
        self.assertNotEqual(path, planted)

    def test_a_missing_path_is_not_a_private_directory(self):
        self.assertFalse(disk_cache.is_private_dir(
            os.path.join(self.tmp, "nothing-here")))


class CachePathTest(unittest.TestCase):
    def setUp(self):
        disk_cache.cache_dir.cache_clear()
        self.addCleanup(disk_cache.cache_dir.cache_clear)
        self.tmp = tempfile.mkdtemp()
        patcher = mock.patch.object(tempfile, "gettempdir", return_value=self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_name_lands_inside_the_cache_directory(self):
        path = disk_cache.cache_path("sec_company_tickers.json")
        self.assertEqual(os.path.dirname(path), disk_cache.cache_dir())

    def test_a_traversing_name_cannot_escape_the_cache_directory(self):
        path = disk_cache.cache_path("../../etc/passwd")
        self.assertEqual(os.path.dirname(path), disk_cache.cache_dir())
        self.assertEqual(os.path.basename(path), "passwd")

    def test_a_name_that_is_not_a_filename_is_refused(self):
        for name in ("", "/", ".", "..", "foo/"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    disk_cache.cache_path(name)


class FreshnessTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "cached.json")

    def test_a_missing_file_is_never_fresh(self):
        self.assertFalse(disk_cache.is_fresh(self.path))

    def test_a_new_file_is_fresh_and_an_old_one_is_not(self):
        Path(self.path).write_bytes(b"{}")
        self.assertTrue(disk_cache.is_fresh(self.path))
        self.assertFalse(disk_cache.is_fresh(self.path, ttl=0))

    def test_the_ttl_is_measured_from_the_modification_time(self):
        Path(self.path).write_bytes(b"{}")
        stale = time.time() - 90000
        os.utime(self.path, (stale, stale))
        self.assertFalse(disk_cache.is_fresh(self.path))
        self.assertTrue(disk_cache.is_fresh(self.path, ttl=100000))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "cached.json")

    def test_it_writes_the_bytes_verbatim(self):
        disk_cache.store(self.path, b'{"a": 1}')
        self.assertEqual(Path(self.path).read_bytes(), b'{"a": 1}')

    @POSIX_ONLY
    def test_the_file_is_readable_only_by_this_user(self):
        disk_cache.store(self.path, b"{}")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_an_unwritable_destination_does_not_raise(self):
        # A cache is an optimization: failing to write one must not fail
        # the lookup that just succeeded.
        try:
            disk_cache.store(os.path.join(self.dir, "missing-dir", "x.json"), b"{}")
        except OSError as exc:                                  # pragma: no cover
            self.fail(f"store() raised {exc}")

    def test_it_leaves_no_temporary_file_behind_on_failure(self):
        with mock.patch("builtins.open", side_effect=OSError("disk full")):
            disk_cache.store(self.path, b"{}")
        self.assertEqual(sorted(os.listdir(self.dir)), [])

    def test_a_replaced_cache_is_never_seen_half_written(self):
        # os.replace is atomic, so a concurrent reader sees the old file
        # or the new one, never a truncated one.
        disk_cache.store(self.path, b'{"old": true}')
        seen = []
        real_replace = os.replace

        def spy(src, dst):
            seen.append(Path(dst).read_bytes())
            return real_replace(src, dst)

        with mock.patch.object(os, "replace", side_effect=spy):
            disk_cache.store(self.path, b'{"new": true}')
        self.assertEqual(seen, [b'{"old": true}'])
        self.assertEqual(Path(self.path).read_bytes(), b'{"new": true}')


class VenueWiringTest(unittest.TestCase):
    """Every venue that caches a directory has to go through this module,
    or the fixed /tmp name comes back one script at a time."""

    def test_no_script_builds_its_own_temp_path(self):
        offenders = []
        for script in sorted((ROOT / "scripts").glob("*.py")):
            if script.name == "disk_cache.py":
                continue
            if "gettempdir" in script.read_text(encoding="utf-8"):
                offenders.append(script.name)
        self.assertEqual(offenders, [],
                         "these scripts bypass disk_cache: " + ", ".join(offenders))

    def test_the_caching_venues_use_it(self):
        for name in ("fetch_us_filings.py", "resolve_name.py",
                     "fetch_sg_filings.py"):
            with self.subTest(script=name):
                text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
                self.assertIn("import disk_cache", text)
                self.assertIn("disk_cache.cache_path(", text)


if __name__ == "__main__":
    unittest.main()

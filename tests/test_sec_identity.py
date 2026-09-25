#!/usr/bin/env python3
"""
Regression tests for the SEC contact requirement.

The skill used to hardcode `securities-filings-lookup-skill
contact@example.com` in two scripts and send a browser-spoofed
`Mozilla/5.0` to sec.gov from a third -- the exact string
references/us-edgar.md warns gets 403'd. Both are now configuration
errors that fail loudly instead of shipping.

Measured against the live endpoint, an address is not optional: a
User-Agent naming only a project URL returns 403 where the same string
plus an email returns 200. So "no contact configured" cannot degrade to
a softer request; it has to stop and say so.
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sec_identity  # noqa: E402

GOOD = "Securities Filings Lookup filings@realdomain.test"


class ValidationTest(unittest.TestCase):
    def test_a_name_and_address_is_accepted(self):
        self.assertIsNone(sec_identity.validate(GOOD))
        self.assertIsNone(sec_identity.validate("someone@company.io"))
        self.assertIsNone(
            sec_identity.validate("securities-filings-lookup/1.0 a.b@c.dev"))

    def test_an_empty_or_tiny_string_is_rejected(self):
        for bad in ("", "   ", "me@a"):
            with self.subTest(value=bad):
                self.assertIsNotNone(sec_identity.validate(bad))

    def test_a_browser_disguise_is_rejected_with_the_reason(self):
        # This is what got sent to sec.gov, and what SEC's edge blocks.
        problem = sec_identity.validate("Mozilla/5.0 (securities-filings-lookup-skill)")
        self.assertIn("browser", problem)

    def test_documentation_placeholders_are_rejected(self):
        for bad in ("securities-filings-lookup-skill contact@example.com",
                    "Me me@example.org", "Me me@sub.example.com",
                    "Contact your-email@domain.com"):
            with self.subTest(value=bad):
                problem = sec_identity.validate(bad)
                self.assertIsNotNone(problem, f"{bad} should be rejected")
                self.assertIn("placeholder", problem)

    def test_a_url_without_an_address_is_rejected(self):
        # Verified live: identical UA minus the address returns 403.
        problem = sec_identity.validate(
            "securities-filings-lookup (https://github.com/o/securities-filings-lookup)")
        self.assertIn("403", problem)

    def test_a_plus_tagged_address_is_accepted(self):
        # The recommended shape: it reaches the user, is filterable, and
        # keeps their main address out of the header. Verified against SEC
        # live (200), so the local-part '+' must survive validation.
        for tagged in ("someone someone+sec@gmail.com",
                       "Ops Team filings+edgar@company.co.uk"):
            with self.subTest(value=tagged):
                self.assertIsNone(sec_identity.validate(tagged))

    def test_a_real_domain_resembling_a_placeholder_is_still_fine(self):
        # "examples.com" is not "example.com".
        self.assertIsNone(sec_identity.validate("Team ops@examples.com"))


class ResolutionOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        env = mock.patch.dict("os.environ", {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        import os
        os.environ.pop(sec_identity.ENV_VAR, None)

    def test_explicit_beats_environment_and_file(self):
        import os
        os.environ[sec_identity.ENV_VAR] = "Env Person env@domain.test"
        sec_identity.save_user_agent("File Person file@domain.test", self.dir)
        self.assertEqual(sec_identity.resolve("Flag Person flag@domain.test", self.dir),
                         "Flag Person flag@domain.test")

    def test_environment_beats_the_file(self):
        import os
        os.environ[sec_identity.ENV_VAR] = "Env Person env@domain.test"
        sec_identity.save_user_agent("File Person file@domain.test", self.dir)
        self.assertEqual(sec_identity.resolve(None, self.dir),
                         "Env Person env@domain.test")

    def test_the_file_is_used_when_nothing_else_is_set(self):
        sec_identity.save_user_agent(GOOD, self.dir)
        self.assertEqual(sec_identity.resolve(None, self.dir), GOOD)

    def test_nothing_configured_raises_with_instructions(self):
        with self.assertRaises(sec_identity.MissingSecContact) as caught:
            sec_identity.resolve(None, self.dir)
        message = str(caught.exception)
        self.assertIn(sec_identity.ENV_VAR, message)
        self.assertIn(sec_identity.CONFIG_FILENAME, message)
        self.assertIn("Ask the user", message)

    def test_a_bad_value_names_the_source_rather_than_falling_back(self):
        import os
        os.environ[sec_identity.ENV_VAR] = "Mozilla/5.0 (pretending)"
        with self.assertRaises(sec_identity.MissingSecContact) as caught:
            sec_identity.resolve(None, self.dir)
        self.assertIn(sec_identity.ENV_VAR, str(caught.exception))
        self.assertIn("browser", str(caught.exception))

    def test_blank_sources_are_skipped_not_treated_as_configured(self):
        import os
        os.environ[sec_identity.ENV_VAR] = "   "
        sec_identity.save_user_agent(GOOD, self.dir)
        self.assertEqual(sec_identity.resolve("", self.dir), GOOD)

    def test_the_failure_is_a_setup_error_so_scripts_report_it_plainly(self):
        import net_errors
        self.assertTrue(issubclass(sec_identity.MissingSecContact,
                                   net_errors.SetupError))


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_round_trip(self):
        path = sec_identity.save_user_agent(GOOD, self.dir)
        self.assertEqual(path.name, sec_identity.CONFIG_FILENAME)
        self.assertEqual(sec_identity.stored_user_agent(self.dir), GOOD)

    def test_an_invalid_contact_is_never_stored(self):
        with self.assertRaises(ValueError):
            sec_identity.save_user_agent("contact@example.com", self.dir)
        self.assertIsNone(sec_identity.stored_user_agent(self.dir))

    def test_a_missing_or_empty_file_reads_as_unset(self):
        self.assertIsNone(sec_identity.stored_user_agent(self.dir))
        sec_identity.config_path(self.dir).write_text("\n\n", encoding="utf-8")
        self.assertIsNone(sec_identity.stored_user_agent(self.dir))

    def test_only_the_first_line_is_used(self):
        sec_identity.config_path(self.dir).write_text(
            GOOD + "\nstray second line\n", encoding="utf-8")
        self.assertEqual(sec_identity.stored_user_agent(self.dir), GOOD)

    def test_the_contact_file_is_gitignored(self):
        # It holds a personal address; it must never sync through the repo.
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
        self.assertIn(sec_identity.CONFIG_FILENAME, ignored)


@contextlib.contextmanager
def isolated_cache():
    """Point the venue cache at an empty directory, and yield it.

    The ticker map is cached for a day, so without this a warm cache on
    the developer's machine means _get is never called and these tests
    assert nothing. disk_cache memoizes its directory, hence the clears.
    """
    import disk_cache
    with tempfile.TemporaryDirectory() as base:
        disk_cache.cache_dir.cache_clear()
        try:
            with mock.patch.object(disk_cache.tempfile, "gettempdir",
                                   return_value=base):
                yield disk_cache.cache_dir()
        finally:
            disk_cache.cache_dir.cache_clear()


class CallerWiringTest(unittest.TestCase):
    """Every SEC-facing request has to go through the resolver."""

    def test_no_script_hardcodes_a_contact_it_would_send(self):
        # Targets values that actually go out as a User-Agent -- an example
        # address inside --user-agent help text is documentation, not a
        # contact being sent.
        import re
        placeholder = r"@(?:example\.(?:com|org|net)|domain\.com)"
        sending = re.compile(
            rf'(?:(?:UA|USER_AGENT|user_agent)\s*=\s*"[^"]*{placeholder}'
            rf'|"User-Agent"\s*:\s*"[^"]*{placeholder})')
        for script in sorted((ROOT / "scripts").glob("*.py")):
            with self.subTest(script=script.name):
                hits = sending.findall(script.read_text(encoding="utf-8"))
                self.assertEqual(hits, [])

    def test_the_us_fetcher_has_no_contact_of_its_own(self):
        # Not a module global main() fills in: an unset global sends an
        # empty UA, which disables pdf_utils' route interception and lets
        # Chromium hit SEC's edge directly.
        import fetch_us_filings
        self.assertFalse(hasattr(fetch_us_filings, "USER_AGENT"))
        fetch_us_filings._RESOLVED_USER_AGENT = None
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop(sec_identity.ENV_VAR, None)
            with mock.patch.object(sec_identity, "resolve",
                                   side_effect=sec_identity.MissingSecContact("x")):
                with self.assertRaises(sec_identity.MissingSecContact):
                    fetch_us_filings.user_agent()
        fetch_us_filings._RESOLVED_USER_AGENT = None

    def test_the_contact_is_resolved_once_per_process(self):
        import fetch_us_filings
        fetch_us_filings._RESOLVED_USER_AGENT = None
        with mock.patch.object(sec_identity, "resolve", return_value=GOOD) as res:
            self.assertEqual(fetch_us_filings.user_agent(), GOOD)
            self.assertEqual(fetch_us_filings.user_agent(), GOOD)
        self.assertEqual(res.call_count, 1)
        fetch_us_filings._RESOLVED_USER_AGENT = None

    def test_save_filing_only_demands_a_contact_for_sec_hosts(self):
        import save_filing
        for url in ("https://www.sec.gov/Archives/x.htm",
                    "https://data.sec.gov/submissions/CIK1.json",
                    "https://sec.gov/x"):
            with self.subTest(url=url):
                self.assertTrue(save_filing.is_sec(url))
        for url in ("http://static.cninfo.com.cn/x.PDF",
                    "https://doc.twse.com.tw/x.pdf",
                    "https://notsec.gov.example.test/x"):
            with self.subTest(url=url):
                self.assertFalse(save_filing.is_sec(url))
                self.assertEqual(save_filing.user_agent_for(url),
                                 save_filing.GENERIC_UA)

    def test_a_sec_url_resolves_the_configured_contact(self):
        with mock.patch.object(sec_identity, "resolve", return_value=GOOD):
            import save_filing
            self.assertEqual(
                save_filing.user_agent_for("https://www.sec.gov/Archives/x.htm"),
                GOOD)

    def test_the_sec_ticker_lookup_does_not_send_the_browser_string(self):
        import resolve_name
        captured = {}

        def fake_get(url, timeout=30, user_agent=None):
            captured["user_agent"] = user_agent
            return b"{}"

        # The ticker map is cached in the temp dir for a day; point that
        # somewhere empty or a warm cache means _get is never called.
        with isolated_cache(), \
                mock.patch.object(resolve_name, "_get", fake_get), \
                mock.patch.object(sec_identity, "resolve", return_value=GOOD):
            resolve_name.search_us("microsoft")
        self.assertEqual(captured["user_agent"], GOOD)
        self.assertFalse(captured["user_agent"].startswith("Mozilla"))

    def test_a_missing_contact_fails_only_the_us_venue(self):
        # A multi-venue name lookup must not die because SEC needs a contact.
        import resolve_name
        with isolated_cache(), \
                mock.patch.object(sec_identity, "resolve",
                                  side_effect=sec_identity.MissingSecContact("nope")):
            rows = resolve_name.search_us("microsoft")
        self.assertEqual([r[1] for r in rows], ["ERROR"])

    def test_a_warm_ticker_cache_needs_no_contact_at_all(self):
        # Reading yesterday's answer off disk makes no request, so it must
        # not demand a contact -- it used to, because resolve() was called
        # eagerly as an argument.
        import json
        import resolve_name
        with isolated_cache() as cache_dir:
            (Path(cache_dir) / "sec_company_tickers.json").write_text(
                json.dumps({"0": {"ticker": "MSFT", "cik_str": 789019,
                                  "title": "MICROSOFT CORP"}}),
                encoding="utf-8")
            with mock.patch.object(sec_identity, "resolve",
                                   side_effect=AssertionError(
                                       "resolve() must not be called on a cache hit")):
                rows = resolve_name.search_us("microsoft")
        self.assertEqual(rows, [("us", "MSFT", "MICROSOFT CORP")])

    def test_resolve_name_accepts_a_per_run_contact(self):
        # README and SKILL.md both promise --user-agent on the US scripts.
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "resolve_name.py"), "--help"],
            capture_output=True, text=True, timeout=60)
        self.assertIn("--user-agent", proc.stdout)


if __name__ == "__main__":
    unittest.main()

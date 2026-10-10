#!/usr/bin/env python3
"""
Offline regression tests for the parts of the fetch scripts that decide
what a filing is: form filtering, title matching, year conversion, and
the save-vs-render fork. Network calls are stubbed -- regulator sites
are not reachable from every environment and must never gate a test run.
"""
from __future__ import annotations

import argparse
import contextlib
import http.client
import io
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_cn_filings  # noqa: E402
import fetch_jp_filings  # noqa: E402
import fetch_us_filings  # noqa: E402
import net_errors  # noqa: E402
import pdf_utils  # noqa: E402
import save_filing  # noqa: E402


class FakeResponse(io.BytesIO):
    """Minimal stand-in for urlopen's context manager."""

    def __init__(self, payload: bytes, content_type: str = "application/json"):
        super().__init__(payload)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["10-K", "4", "10-Q", "8-K", "10-Q"],
            "filingDate": ["2026-07-29", "2026-07-20", "2026-04-29",
                           "2026-03-01", "2026-01-28"],
            "reportDate": ["2026-06-30", "", "2026-03-31", "", "2025-12-31"],
            "accessionNumber": ["0001193125-26-323660", "0000320193-26-000042",
                                "0000950170-26-000038", "0000950170-26-000012",
                                "0000950170-26-000001"],
            "primaryDocument": ["msft-20260630.htm", "xslF345X05/wk-form4.xml",
                                "msft-20260331.htm", "msft-8k.htm",
                                "msft-20251231.htm"],
            "primaryDocDescription": ["10-K", "FORM 4", "10-Q", "8-K", "10-Q"],
        }
    }
}


class UsFilingSelectionTest(unittest.TestCase):
    def _rows(self, forms, limit):
        with mock.patch.object(fetch_us_filings, "_get_json", return_value=SUBMISSIONS):
            return fetch_us_filings.fetch_filings(789019, forms, limit)[0]

    def test_form_filter_keeps_only_the_requested_type(self):
        rows = self._rows(["10-Q"], 10)
        self.assertEqual([r["form"] for r in rows], ["10-Q", "10-Q"])

    def test_limit_caps_the_result(self):
        self.assertEqual(len(self._rows(None, 3)), 3)

    def test_urls_point_at_the_primary_document_on_edgar(self):
        row = self._rows(["10-K"], 1)[0]
        self.assertEqual(
            row["url"],
            "https://www.sec.gov/Archives/edgar/data/789019/"
            "000119312526323660/msft-20260630.htm")
        self.assertEqual(row["period"], "2026-06-30")

    def test_unknown_form_yields_no_rows_rather_than_everything(self):
        self.assertEqual(self._rows(["20-F"], 10), [])


CN_RESPONSE = {
    "announcements": [
        {"announcementTitle": "2025年年度报告", "announcementTime": 1,
         "adjunctUrl": "finalpage/2026-03-25/1220000001.PDF"},
        {"announcementTitle": "2025年年度报告（英文版）", "announcementTime": 2,
         "adjunctUrl": "finalpage/2026-03-25/1220000002.PDF"},
        {"announcementTitle": "2025年半年度报告", "announcementTime": 3,
         "adjunctUrl": "finalpage/2025-08-25/1220000003.PDF"},
        {"announcementTitle": "2025年年度报告摘要", "announcementTime": 4,
         "adjunctUrl": "finalpage/2026-03-25/1220000004.PDF"},
    ]
}


class CnFilingSelectionTest(unittest.TestCase):
    def _fetch(self, kind, limit=10):
        payload = json.dumps(CN_RESPONSE).encode()
        with mock.patch.object(fetch_cn_filings, "resolve_org_id", return_value="gssh0600519"), \
             mock.patch("urllib.request.urlopen", return_value=FakeResponse(payload)):
            return fetch_cn_filings.fetch("600519", kind, limit)

    def test_interim_reports_do_not_leak_into_annual_results(self):
        # "年度报告" is a substring of "半年度报告" -- the guard against
        # that regression is the whole point of this test.
        titles = [r["title"] for r in self._fetch("annual")]
        self.assertTrue(titles)
        self.assertFalse([t for t in titles if "半年度报告" in t])

    def test_interim_kind_returns_the_interim_report(self):
        titles = [r["title"] for r in self._fetch("interim")]
        self.assertEqual(titles, ["2025年半年度报告"])

    def test_urls_are_built_against_the_static_host(self):
        row = self._fetch("annual")[0]
        self.assertTrue(row["url"].startswith("http://static.cninfo.com.cn/"))

    def test_exchange_classification(self):
        self.assertEqual(fetch_cn_filings.classify_exchange("600519"), ("sh", "sse"))
        self.assertEqual(fetch_cn_filings.classify_exchange("688981"), ("sh", "sse"))
        self.assertEqual(fetch_cn_filings.classify_exchange("300308"), ("sz", "szse"))
        self.assertEqual(fetch_cn_filings.classify_exchange("000001"), ("sz", "szse"))
        with self.assertRaises(SystemExit):
            fetch_cn_filings.classify_exchange("830799")  # Beijing, unsupported


TDNET_PAGE = """
<tr><td class="kjTime">09:00</td><td class="kjCode">72030</td>
<td class="kjName">トヨタ自動車</td><td class="kjTitle">
<a href="140120260501.pdf" target="_blank">2026年3月期 決算短信</a></td></tr>
"""


class JpParsingTest(unittest.TestCase):
    def test_tdnet_rows_parse_into_time_code_name_link_title(self):
        rows = fetch_jp_filings.ROW_RE.findall(TDNET_PAGE)
        self.assertEqual(len(rows), 1)
        time, code, name, link, title = rows[0]
        self.assertEqual(time, "09:00")
        self.assertEqual(code, "72030")      # TDnet uses the 5-digit form
        self.assertEqual(link, "140120260501.pdf")
        self.assertIn("決算短信", title)


class EdinetRequestTest(unittest.TestCase):
    """EDINET's API key rides in the query string, so anything that makes
    http.client echo the request path -- a malformed date did -- printed
    the key into the transcript."""

    KEY = "SECRETKEY123"

    def test_only_real_iso_dates_are_accepted(self):
        for good in ("2026-06-24", "2025-12-31"):
            with self.subTest(good=good):
                self.assertEqual(fetch_jp_filings.edinet_date(good), good)
        for bad in ("June 24", "2026-6-4", "20260624", "2026-13-01",
                    "2026-02-30", "2026-06-24&x=1", "", "2026-06-24 "):
            with self.subTest(bad=bad):
                with self.assertRaises(argparse.ArgumentTypeError):
                    fetch_jp_filings.edinet_date(bad)

    def test_every_query_value_is_encoded_the_key_included(self):
        url = fetch_jp_filings.edinet_url(
            "documents.json", "a&b=c d", date="2026-06-24", type=2)
        self.assertEqual(urllib.parse.urlsplit(url).path, "/api/v2/documents.json")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(query["Subscription-Key"], ["a&b=c d"])
        self.assertEqual(query["date"], ["2026-06-24"])
        self.assertNotIn(" ", url)

    def test_a_malformed_date_never_prints_the_key(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "fetch_jp_filings.py"),
             "7203", "--edinet-date", "June 24"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "EDINET_API_KEY": self.KEY})
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn(self.KEY, proc.stdout + proc.stderr)
        self.assertIn("YYYY-MM-DD", proc.stderr)

    def test_a_hostile_doc_id_stays_in_the_url_path_and_the_save_directory(self):
        listing = json.dumps({"results": [{
            "docID": "../evil", "secCode": "72030",
            "filerName": "Toyota", "docDescription": "annual"}]}).encode()
        calls = []

        def fake_get(url):
            calls.append(url)
            return listing if len(calls) == 1 else b"%PDF-1.4\nbody"

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"EDINET_API_KEY": self.KEY}), \
                mock.patch.object(fetch_jp_filings, "_get", fake_get), \
                contextlib.redirect_stdout(io.StringIO()):
            save_dir = os.path.join(tmp, "out")
            fetch_jp_filings.edinet("7203", "2026-06-24", save_dir)
            saved = os.listdir(save_dir)
            self.assertEqual(len(saved), 1)
            self.assertNotIn(os.sep, saved[0])
            self.assertEqual(os.listdir(tmp), ["out"])
        self.assertIn("documents/..%2Fevil?", calls[1])


class PdfUtilsTest(unittest.TestCase):
    def setUp(self):
        # SKILL_CHROMIUM_PATH is a knob the docs tell users to set, and it
        # short-circuits _launch_chromium -- these tests must not depend on
        # whether the developer's shell has it.
        patcher = mock.patch.dict("os.environ", {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(pdf_utils.CHROMIUM_PATH_ENV, None)

    def test_a_bad_override_explains_itself_instead_of_raising_playwright(self):
        playwright = mock.Mock()
        playwright.chromium.launch.side_effect = RuntimeError(
            "Executable doesn't exist at /nonexistent/chrome")
        with mock.patch.dict("os.environ",
                             {pdf_utils.CHROMIUM_PATH_ENV: "/nonexistent/chrome"}):
            with self.assertRaises(net_errors.SetupError) as caught:
                pdf_utils._launch_chromium(playwright)
        self.assertIn("/nonexistent/chrome", str(caught.exception))
        self.assertIn("playwright install chromium", str(caught.exception))

    def test_the_newest_build_wins_even_under_a_numbered_browsers_root(self):
        # A root like /opt/pw-9999 used to make every candidate score 9999.
        with tempfile.TemporaryDirectory(prefix="pw-9999-") as tmp:
            root = Path(tmp) / "pw-9999"
            for build in (999, 1194):
                target = root / f"chromium-{build}" / "chrome-linux"
                target.mkdir(parents=True)
                (target / "chrome").write_text("#!/bin/sh\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"PLAYWRIGHT_BROWSERS_PATH": str(root)}):
                self.assertIn("chromium-1194", pdf_utils._bundled_chromium())

    def test_pdf_sniffing(self):
        self.assertTrue(pdf_utils.is_pdf_bytes(b"%PDF-1.7\n..."))
        self.assertFalse(pdf_utils.is_pdf_bytes(b"<html><body>10-K</body>"))
        self.assertFalse(pdf_utils.is_pdf_bytes(b""))

    def test_native_pdfs_are_saved_byte_for_byte(self):
        payload = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nbody bytes\n%%EOF\n"
        with tempfile.TemporaryDirectory() as tmp:
            out = pdf_utils.save_pdf_bytes(payload, str(Path(tmp) / "filing"))
            self.assertTrue(out.endswith(".pdf"))  # extension added when missing
            self.assertEqual(Path(out).read_bytes(), payload)

    def test_save_filing_as_pdf_never_renders_an_existing_pdf(self):
        payload = b"%PDF-1.4\nalready a pdf\n"
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(pdf_utils, "render_url_to_pdf") as render:
            out = pdf_utils.save_filing_as_pdf(
                "https://example.invalid/a.pdf", payload, str(Path(tmp) / "a.pdf"))
            render.assert_not_called()
            self.assertEqual(Path(out).read_bytes(), payload)

    def test_html_goes_through_the_browser_render_path(self):
        with mock.patch.object(pdf_utils, "render_url_to_pdf",
                               return_value="/tmp/x.pdf") as render:
            pdf_utils.save_filing_as_pdf(
                "https://example.invalid/a.htm", b"<html>10-Q</html>",
                "/tmp/x.pdf", user_agent="ua")
            render.assert_called_once()

    def test_a_failing_fallback_launch_still_explains_the_fix(self):
        # Both launches failing used to escape as a raw Playwright error,
        # which net_errors.run does not catch.
        playwright = mock.Mock()
        playwright.chromium.launch.side_effect = [
            RuntimeError("pinned build missing"), RuntimeError("also broken")]
        with mock.patch.object(pdf_utils, "_bundled_chromium", return_value="/opt/chrome"):
            with self.assertRaises(net_errors.SetupError) as caught:
                pdf_utils._launch_chromium(playwright)
        self.assertIn("playwright install chromium", str(caught.exception))
        self.assertIn("also broken", str(caught.exception))

    def test_the_renderer_is_handed_bytes_the_caller_already_downloaded(self):
        # Re-fetching the same document doubles traffic to hosts that
        # rate-limit, which is what got this session blocked by SEC.
        with mock.patch.object(pdf_utils, "render_url_to_pdf") as render:
            pdf_utils.save_filing_as_pdf("https://example.invalid/a.htm",
                                         b"<html>10-K</html>", "/tmp/a.pdf",
                                         user_agent="ua")
        self.assertEqual(render.call_args.kwargs["prefetched"],
                         (b"<html>10-K</html>", "text/html"))

    def test_missing_chromium_explains_the_fix_instead_of_crashing(self):
        playwright = mock.Mock()
        playwright.chromium.launch.side_effect = RuntimeError("Executable doesn't exist")
        with mock.patch.dict("os.environ", {"PLAYWRIGHT_BROWSERS_PATH": ""}, clear=False), \
                mock.patch.object(pdf_utils, "_bundled_chromium", return_value=None):
            with self.assertRaises(net_errors.SetupError) as caught:
                pdf_utils._launch_chromium(playwright)
        self.assertIn("playwright install chromium", str(caught.exception))

    def test_a_bundled_chromium_is_used_when_the_pinned_build_is_absent(self):
        playwright = mock.Mock()
        playwright.chromium.launch.side_effect = [RuntimeError("no build"), "browser"]
        with mock.patch.object(pdf_utils, "_bundled_chromium", return_value="/opt/chrome"):
            self.assertEqual(pdf_utils._launch_chromium(playwright), "browser")
        playwright.chromium.launch.assert_called_with(executable_path="/opt/chrome")

    def test_bundled_chromium_picks_the_highest_build_numerically(self):
        with tempfile.TemporaryDirectory() as tmp:
            for build in (999, 1194):
                target = Path(tmp) / f"chromium-{build}" / "chrome-linux"
                target.mkdir(parents=True)
                (target / "chrome").write_text("#!/bin/sh\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"PLAYWRIGHT_BROWSERS_PATH": tmp}):
                found = pdf_utils._bundled_chromium()
            # "chromium-999" sorts after "chromium-1194" as text.
            self.assertIn("chromium-1194", found)

    def test_bundled_chromium_is_none_without_a_browsers_path(self):
        with mock.patch.dict("os.environ", {"PLAYWRIGHT_BROWSERS_PATH": "/nope/nowhere"}):
            self.assertIsNone(pdf_utils._bundled_chromium())

    def test_explicit_override_wins(self):
        playwright = mock.Mock()
        playwright.chromium.launch.return_value = "browser"
        with mock.patch.dict("os.environ", {pdf_utils.CHROMIUM_PATH_ENV: "/my/chrome"}):
            pdf_utils._launch_chromium(playwright)
        playwright.chromium.launch.assert_called_once_with(executable_path="/my/chrome")


class SubresourceUserAgentTest(unittest.TestCase):
    """The declared SEC contact is a person's address. A filing that
    hotlinks an image from a company web server -- possibly over plain
    http -- must not hand it to that host."""

    PAGE = "https://www.sec.gov/Archives/edgar/data/1/x.htm"
    CONTACT = "Some Person person@realdomain.com"

    def _ua(self, url):
        return pdf_utils.subresource_user_agent(url, self.PAGE, self.CONTACT)

    def test_the_regulators_own_hosts_get_the_contact(self):
        for url in ("https://www.sec.gov/images/a.gif",
                    "https://data.sec.gov/x.json",
                    "http://www.sec.gov/old/inline.gif"):
            with self.subTest(url=url):
                self.assertEqual(self._ua(url), self.CONTACT)

    def test_a_third_party_host_never_sees_the_contact(self):
        for url in ("http://cdn.company.example/logo.gif",
                    "https://tracker.example.net/pixel.png",
                    "https://sec.gov.evil.example/a.gif",
                    "https://notsec.gov.example/a.gif",
                    "data:image/gif;base64,R0lGOD"):
            with self.subTest(url=url):
                ua = self._ua(url)
                self.assertEqual(ua, pdf_utils.THIRD_PARTY_UA)
                self.assertNotIn("@", ua)

    def test_the_interceptor_actually_uses_it(self):
        # The helper is only worth having if the browser route calls it.
        source = (ROOT / "scripts" / "pdf_utils.py").read_text(encoding="utf-8")
        self.assertIn("subresource_user_agent(u, url, user_agent)", source)


class SaveFilingTest(unittest.TestCase):
    def test_output_name_is_derived_from_the_url(self):
        self.assertEqual(
            save_filing.default_out_path(
                "https://www.sec.gov/Archives/edgar/data/1/2/msft-20260630.htm"),
            "msft-20260630.pdf")
        self.assertEqual(
            save_filing.default_out_path(
                "http://static.cninfo.com.cn/finalpage/2026-03-25/1220000001.PDF"),
            "1220000001.pdf")
        # A URL with no document name still yields a usable .pdf path.
        self.assertTrue(
            save_filing.default_out_path("https://example.invalid/").endswith(".pdf"))


class NetworkErrorReportingTest(unittest.TestCase):
    """SKILL.md promises a plain explanation, not a stack trace."""

    def _http(self, code, reason, url="https://www.sec.gov/files/x.json"):
        return urllib.error.HTTPError(url, code, reason, {}, None)

    def test_rate_limiting_is_named_and_says_not_to_retry_in_a_loop(self):
        message = net_errors.explain(self._http(429, "Too Many Requests"))
        self.assertIn("Rate limited", message)
        self.assertIn("www.sec.gov", message)
        self.assertIn("do not retry in a loop", message)

    def test_403_mentions_both_bot_detection_and_a_sandboxed_proxy(self):
        message = net_errors.explain(
            self._http(403, "Forbidden", "http://www.cninfo.com.cn/new/x"))
        self.assertIn("bot detection", message)
        self.assertIn("proxy", message)
        self.assertIn("web search", message)

    def test_blocked_tunnel_is_reported_as_an_environment_limit(self):
        message = net_errors.explain(
            OSError("Tunnel connection failed: 403 Forbidden"))
        self.assertIn("network proxy", message)
        self.assertIn("not allowlisted", message)

    def test_a_blocked_tunnel_in_the_shape_urllib_actually_raises(self):
        # urllib wraps the tunnel OSError in URLError, so a check that only
        # looked at bare OSErrors never fired in a real cloud container.
        message = net_errors.explain(
            urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden")))
        self.assertIn("network proxy", message)
        self.assertIn("not allowlisted", message)
        self.assertIn("Network access", message)

    def test_filesystem_errors_are_not_dressed_up_as_network_errors(self):
        for exc in (PermissionError(13, "Permission denied", "/root/filings"),
                    FileNotFoundError(2, "No such file or directory", "/nope/x.pdf"),
                    OSError(28, "No space left on device")):
            with self.subTest(error=type(exc).__name__):
                message = net_errors.explain(exc)
                self.assertIn("not a network problem", message)
                self.assertNotIn("web search", message)

    def test_a_setup_error_prints_its_message_without_a_traceback(self):
        def boom():
            raise net_errors.SetupError("install chromium, then retry")

        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr), self.assertRaises(SystemExit):
            net_errors.run(boom)
        self.assertIn("install chromium", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_an_ordinary_runtime_error_keeps_its_traceback(self):
        # A parser break is a bug, not something the user can act on --
        # swallowing it into one line hid where it happened.
        def boom():
            raise RuntimeError("no temporary link in step-9 response")

        with self.assertRaises(RuntimeError):
            net_errors.run(boom)

    def test_tls_failure_points_at_certifi(self):
        message = net_errors.explain(
            urllib.error.URLError(ssl.SSLCertVerificationError("bad chain")))
        self.assertIn("certifi", message)

    def test_dns_failure_is_explained(self):
        message = net_errors.explain(
            urllib.error.URLError(socket.gaierror("Name or service not known")))
        self.assertIn("DNS", message)

    def test_run_exits_one_after_printing_the_explanation(self):
        def boom():
            raise urllib.error.HTTPError("https://data.sec.gov/x", 429,
                                         "Too Many Requests", {}, None)

        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr), self.assertRaises(SystemExit) as exit_:
            net_errors.run(boom)
        self.assertEqual(exit_.exception.code, 1)
        self.assertIn("Rate limited", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_a_reply_truncated_mid_read_is_not_a_traceback(self):
        # IncompleteRead and BadStatusLine are HTTPExceptions but NOT
        # OSErrors, so they escaped run() entirely: a CNINFO response cut
        # short mid-read reached the user as a stack trace, which is the
        # one thing SKILL.md promises these scripts never do.
        for exc in (http.client.IncompleteRead(b"12345"),
                    http.client.BadStatusLine("garbage")):
            with self.subTest(error=type(exc).__name__):
                def boom():
                    raise exc

                stderr = io.StringIO()
                with mock.patch("sys.stderr", stderr), \
                        self.assertRaises(SystemExit) as exit_:
                    net_errors.run(boom)
                self.assertEqual(exit_.exception.code, 1)
                self.assertNotIn("Traceback", stderr.getvalue())
                self.assertIn("could not finish reading", stderr.getvalue())

    def test_a_credential_echoed_in_an_error_is_masked(self):
        # http.client.InvalidURL quotes the whole request path, key and all.
        exc = http.client.InvalidURL(
            "URL can't contain control characters. "
            "'/api/v2/documents.json?date=June 24&type=2&Subscription-Key="
            "SECRETKEY123' (found at least ' ')")
        message = net_errors.explain(exc)
        self.assertNotIn("SECRETKEY123", message)
        self.assertIn("Subscription-Key=***", message)

    def test_redaction_covers_the_usual_credential_parameters(self):
        for param in ("Subscription-Key", "api_key", "apikey", "api-key",
                      "key", "token"):
            with self.subTest(param=param):
                masked = net_errors.redact_secrets(f"GET /x?a=1&{param}=hunter2xyz&b=2")
                self.assertNotIn("hunter2xyz", masked)
                self.assertIn("a=1", masked)
                self.assertIn("b=2", masked)

    def test_redaction_leaves_ordinary_text_alone(self):
        text = "monkey=banana turkey=sandwich Rate limited by sec.gov"
        self.assertEqual(net_errors.redact_secrets(text), text)

    def test_a_malformed_url_is_a_bad_argument_not_a_transient_cut(self):
        message = net_errors.explain(http.client.InvalidURL("bad path"))
        self.assertIn("malformed", message)
        self.assertNotIn("transient", message)
        self.assertNotIn("retry once", message)

    def test_a_truncated_reply_is_described_as_transient(self):
        message = net_errors.explain(http.client.IncompleteRead(b"1234"))
        self.assertIn("transient", message)
        self.assertIn("retry once", message)

    def test_successful_main_is_left_alone(self):
        calls = []
        net_errors.run(lambda: calls.append(1))
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()

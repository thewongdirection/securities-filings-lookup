#!/usr/bin/env python3
"""
Regression tests for the venue failures a seven-market sweep turned up.

Each of these was a wrong answer rather than a crash, which is why they
survived: TWSE served a refusal as HTTP 200 and the script called it "no
filings found"; a ticker pointing at a successor entity produced a bare
"No matching filings found"; the FCA's search index was retired and the
error read like a transient network problem; and a CNINFO 503 that a
single retry clears failed the lookup outright.
"""
from __future__ import annotations

import io
import json
import sys
import pathlib
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_cn_filings as cn  # noqa: E402
import fetch_tw_filings as tw  # noqa: E402
import fetch_uk_filings as uk  # noqa: E402
import fetch_us_filings as us  # noqa: E402
import net_errors  # noqa: E402

TWSE_BLOCKED = (ROOT / "tests" / "fixtures" / "twse_blocked.html").read_bytes()


def _json_response(payload: dict):
    class Response(io.BytesIO):
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return Response(json.dumps(payload).encode())


class TaiwanRefusalTest(unittest.TestCase):
    """TWSE answers a refused request with HTTP 200 and a block page."""

    def test_the_recorded_block_page_is_recognised(self):
        text = tw._decode(TWSE_BLOCKED, "text/html; charset=UTF-8")
        self.assertIn("CAN NOT BE ACCESSED", text.upper())
        with self.assertRaises(net_errors.HostRefused) as caught:
            tw._check_not_blocked(text)
        message = str(caught.exception)
        self.assertIn("mops.twse.com.tw", message)
        self.assertIn("not an absence of filings", message)

    def test_a_refusal_is_not_reported_as_no_filings(self):
        # The bug: the block page fell through to "No annual filings found
        # for 2330", sending the reader off to check a code that was fine.
        # Stubbed at the socket, not at _post -- the check lives inside it.
        class Response(io.BytesIO):
            headers = {"Content-Type": "text/html; charset=UTF-8"}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch("urllib.request.urlopen",
                        return_value=Response(TWSE_BLOCKED)):
            with self.assertRaises(net_errors.HostRefused):
                tw.list_files("2330", 2025, "annual")

    def test_decode_honours_the_declared_charset(self):
        big5 = "年報".encode("big5")
        utf8 = "年報".encode("utf-8")
        self.assertEqual(tw._decode(big5, "text/html; charset=big5"), "年報")
        self.assertEqual(tw._decode(utf8, "text/html; charset=UTF-8"), "年報")
        # Quoted forms parse too -- a hand-rolled split got this wrong.
        self.assertEqual(tw._decode(big5, 'text/html; charset="big5"'), "年報")
        # No header at all: Big5, the document server's historical default.
        self.assertEqual(tw._decode(big5, ""), "年報")
        # And what the parsing actually depends on is ASCII either way, so a
        # mis-declared charset cannot hide a refusal or a listing link.
        ascii_only = b'FOR SECURITY REASONS ... readfile2("F","2330","x.pdf")'
        for content_type in ("", "text/html; charset=UTF-8", "text/html; charset=big5"):
            with self.subTest(content_type=content_type):
                self.assertIn("readfile2", tw._decode(ascii_only, content_type))

    def test_a_refused_document_fetch_never_becomes_a_saved_filing(self):
        # The worst version of this bug: an 800-byte block page written to
        # <ticker>.pdf and reported as the annual report.
        class Response(io.BytesIO):
            headers = {"Content-Type": "text/html; charset=UTF-8"}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        step9 = "<a href='/pdf/202503_2330_AI1_ts.pdf'>download</a>"
        with mock.patch.object(tw, "_post", return_value=step9), \
                mock.patch("urllib.request.urlopen",
                           return_value=Response(TWSE_BLOCKED)):
            with self.assertRaises(net_errors.HostRefused):
                tw.download("F", "2330", "202503_2330_AI1.pdf")

    def test_a_chinese_only_refusal_is_recognised(self):
        # Markers taken from the recorded page, not invented: TWSE writes
        # 無法呈現. Guessing 無法瀏覽 would have been dead code.
        chinese = tw._decode(TWSE_BLOCKED, "text/html; charset=UTF-8")
        self.assertIn("無法呈現", chinese)
        english_removed = chinese.replace("CAN NOT BE ACCESSED", "")
        with self.assertRaises(net_errors.HostRefused):
            tw._check_not_blocked(english_removed)

    def test_a_real_listing_still_parses(self):
        listing = ('<a href="javascript:readfile2("F","2330",'
                   '"202503_2330_AI1.pdf")">')
        with mock.patch.object(tw, "_post", return_value=listing):
            self.assertEqual(
                [f[2] for f in tw.list_files("2330", 2025, "annual")],
                ["202503_2330_AI1.pdf"])


RECENT_ONLY_10Q = {
    "filings": {
        "recent": {
            "form": ["10-Q", "8-K"],
            "filingDate": ["2026-09-23", "2026-07-01"],
            "reportDate": ["2026-06-30", ""],
            "accessionNumber": ["0002115436-26-000009", "0002115436-26-000002"],
            "primaryDocument": ["xom-20260630.htm", "xom-8k.htm"],
            "primaryDocDescription": ["10-Q", "8-K"],
        },
        "files": [{"name": "CIK0002115436-submissions-001.json",
                   "filingCount": 500}],
    }
}

OLDER_PAGE = {
    "form": ["10-K"],
    "filingDate": ["2026-02-18"],
    "reportDate": ["2025-12-31"],
    "accessionNumber": ["0000034088-26-000010"],
    "primaryDocument": ["xom-20251231.htm"],
    "primaryDocDescription": ["10-K"],
}


class OlderFilingsTest(unittest.TestCase):
    """submissions.json holds a window; the rest is in filings.files."""

    def test_an_annual_report_outside_the_window_is_still_found(self):
        def fake(url):
            return OLDER_PAGE if "submissions-001" in url else RECENT_ONLY_10Q

        with mock.patch.object(us, "_get_json", side_effect=fake), \
                mock.patch.object(us.time, "sleep"):
            rows, _, _ = us.fetch_filings(2115436, ["10-K"], 1)
        self.assertEqual([r["form"] for r in rows], ["10-K"])
        self.assertEqual(rows[0]["filed"], "2026-02-18")

    def test_the_older_pages_are_left_alone_when_the_window_suffices(self):
        calls = []

        def fake(url):
            calls.append(url)
            return RECENT_ONLY_10Q

        with mock.patch.object(us, "_get_json", side_effect=fake), \
                mock.patch.object(us.time, "sleep"):
            rows, _, _ = us.fetch_filings(2115436, ["10-Q"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(calls), 1, "no extra request should be made")

    def test_a_rate_limit_on_an_older_page_is_not_hidden(self):
        # Swallowing it would report a partial answer as complete, and
        # main() would then blame a successor entity for a rate limit.
        def fake(url):
            if "submissions-001" in url:
                raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
            return RECENT_ONLY_10Q

        with mock.patch.object(us, "_get_json", side_effect=fake), \
                mock.patch.object(us.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                us.fetch_filings(2115436, ["10-K"], 1)
        self.assertEqual(caught.exception.code, 429)

    def test_another_failure_on_an_older_page_keeps_the_window(self):
        def fake(url):
            if "submissions-001" in url:
                raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)
            return RECENT_ONLY_10Q

        with mock.patch.object(us, "_get_json", side_effect=fake), \
                mock.patch.object(us.time, "sleep"):
            rows, _, note = us.fetch_filings(2115436, None, 5)
        self.assertEqual([r["form"] for r in rows], ["10-Q", "8-K"])
        # The caller has to know the scan did not finish, or a transient 500
        # gets reported to the user as "this company has no 10-K".
        self.assertIn("could not be read", note)
        self.assertIn("incomplete", note)

    def test_the_no_match_report_names_what_the_entity_does_hold(self):
        # No second GET: it takes the payload the caller already has.
        window_only = {"filings": {"recent": RECENT_ONLY_10Q["filings"]["recent"]}}
        summary = us.summarize_entity(window_only)
        self.assertIn("2 filings", summary)
        self.assertIn("2026-07-01", summary)
        self.assertIn("10-Q", summary)

    def test_a_window_is_not_described_as_everything_on_record(self):
        # With older pages present, the inline count is a window, and saying
        # "this CIK has 2 filings" about a heavy filer is simply false.
        summary = us.summarize_entity(RECENT_ONLY_10Q)
        self.assertIn("about 502", summary)
        self.assertIn("most recent 2", summary)

    def test_a_cik_with_nothing_on_record_says_so(self):
        empty = {"filings": {"recent": {"form": [], "filingDate": []}}}
        self.assertIn("no filings", us.summarize_entity(empty).lower())


class LondonEndpointTest(unittest.TestCase):
    def test_a_retired_index_is_reported_as_an_upstream_change(self):
        error = urllib.error.HTTPError(
            uk.SEARCH_URL, 400, "Bad Request", {},
            io.BytesIO(b"Invalid index"))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(net_errors.HostRefused) as caught:
                uk.search("AstraZeneca")
        message = str(caught.exception)
        self.assertIn("no longer accepts", message)
        self.assertIn("nationalstoragemechanism", message)
        self.assertIn("references/london.md", message)

    def test_a_400_partway_through_paging_keeps_the_hits_already_found(self):
        pages = []

        def urlopen(req, timeout=None, context=None):
            pages.append(1)
            if len(pages) == 1:
                return _json_response({"hits": {"hits": [
                    {"_source": {"download_link": "a.pdf", "headline": "Annual Report",
                                 "company": "SHELL PLC", "publication_date": "2026-03-01"}}
                ] * 100}})
            raise urllib.error.HTTPError(uk.SEARCH_URL, 400, "Bad Request", {},
                                         io.BytesIO(b"illegal_argument_exception"))

        with mock.patch("urllib.request.urlopen", side_effect=urlopen), \
                mock.patch("sys.stdout", io.StringIO()):
            rows = uk.search("Shell")
        self.assertEqual(len(rows), 100)

    def test_other_http_errors_are_left_to_net_errors(self):
        error = urllib.error.HTTPError(uk.SEARCH_URL, 429, "Too Many", {},
                                       io.BytesIO(b"slow down"))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                uk.search("AstraZeneca")


class ChinaRetryTest(unittest.TestCase):
    def _response(self, payload: dict):
        class Fake(io.BytesIO):
            headers = {"Content-Type": "application/json"}

            def __enter__(self): return self
            def __exit__(self, *exc): return False
        return Fake(json.dumps(payload).encode())

    def test_a_transient_503_is_retried_once(self):
        attempts = []

        def urlopen(req, timeout=None):
            attempts.append(1)
            if len(attempts) == 1:
                raise urllib.error.HTTPError(cn.QUERY_URL, 503,
                                             "Service Unavailable", {}, None)
            return self._response({"announcements": []})

        with mock.patch("urllib.request.urlopen", side_effect=urlopen), \
                mock.patch.object(cn.time, "sleep"):
            self.assertEqual(cn._post_json(cn.QUERY_URL, {}), {"announcements": []})
        self.assertEqual(len(attempts), 2)

    def test_a_second_failure_is_reported_not_retried_again(self):
        attempts = []

        def urlopen(req, timeout=None):
            attempts.append(1)
            raise urllib.error.HTTPError(cn.QUERY_URL, 503, "Service Unavailable",
                                         {}, None)

        with mock.patch("urllib.request.urlopen", side_effect=urlopen), \
                mock.patch.object(cn.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                cn._post_json(cn.QUERY_URL, {})
        self.assertEqual(len(attempts), 2)

    def test_a_rate_limit_is_never_retried(self):
        # SKILL.md is explicit: wait out a 429, do not press on.
        attempts = []

        def urlopen(req, timeout=None):
            attempts.append(1)
            raise urllib.error.HTTPError(cn.QUERY_URL, 429, "Too Many Requests",
                                         {}, None)

        with mock.patch("urllib.request.urlopen", side_effect=urlopen), \
                mock.patch.object(cn.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                cn._post_json(cn.QUERY_URL, {})
        self.assertEqual(len(attempts), 1)


class ChinaInterstitialTest(unittest.TestCase):
    def test_html_served_with_status_200_is_reported_not_traced_back(self):
        class Response(io.BytesIO):
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch("urllib.request.urlopen",
                        return_value=Response(b"<html>\xb7\xc3\xce\xca</html>")):
            with self.assertRaises(net_errors.HostRefused) as caught:
                cn._post_json(cn.QUERY_URL, {})
        self.assertIn("not \nJSON".replace("\n", ""), str(caught.exception))
        self.assertIn("cninfo.com.cn", str(caught.exception))


class HostRefusedReportingTest(unittest.TestCase):
    def test_a_refusal_prints_one_line_and_exits(self):
        def boom():
            raise net_errors.HostRefused("TWSE said no; use MOPS in a browser")

        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr), self.assertRaises(SystemExit) as exit_:
            net_errors.run(boom)
        self.assertEqual(exit_.exception.code, 1)
        self.assertIn("use MOPS", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


class TaiwanDirectoryRefusalTest(unittest.TestCase):
    """The company directory used for name resolution is served by
    openapi.twse.com.tw, which returns the same HTTP-200 refusal page as the
    document server -- and used to be reported as unreadable JSON."""

    BLOCK = (b"<html><body>\n FOR SECURITY REASONS, THIS PAGE CAN NOT BE "
             b"ACCESSED.<BR>\n</body></html>")

    def test_the_refusal_page_is_recognised_not_parsed(self):
        import resolve_name
        with self.assertRaises(net_errors.HostRefused) as caught:
            resolve_name._twse_not_blocked(self.BLOCK)
        message = str(caught.exception)
        self.assertIn("openapi.twse.com.tw", message)
        self.assertIn("mops.twse.com.tw", message)

    def test_real_json_passes_the_check(self):
        import resolve_name
        resolve_name._twse_not_blocked(b'[{"\u516c\u53f8\u4ee3\u865f": "2330"}]')

    def test_name_resolution_reports_the_refusal_rather_than_a_parse_error(self):
        import resolve_name
        with mock.patch.object(resolve_name, "_get", return_value=self.BLOCK), \
                mock.patch.object(resolve_name.disk_cache, "is_fresh",
                                  return_value=False):
            rows = resolve_name.search_tw("TSMC")
        self.assertEqual(len(rows), 1)
        venue, code, detail = rows[0]
        self.assertEqual((venue, code), ("tw", "ERROR"))
        self.assertIn("TWSE refused", detail)
        self.assertNotIn("JSON", detail)

    def test_a_non_json_body_from_any_venue_is_called_a_refusal(self):
        # The generic path: a directory that answers with HTML instead of
        # JSON is the host declining, not an empty result set.
        import resolve_name
        with mock.patch.object(resolve_name, "_get", return_value=b"<html>nope"), \
                mock.patch.object(resolve_name.disk_cache, "is_fresh",
                                  return_value=False), \
                self.assertRaises(net_errors.HostRefused) as caught:
            resolve_name._cached_json("probe.json", "https://example.invalid/x")
        self.assertIn("not JSON", str(caught.exception))

    def test_a_good_answer_is_cached_and_then_re_used(self):
        # The point of the cache: SEC's ticker map is 10,413 entries, and
        # re-downloading it per lookup is what trips its 429s. Nothing
        # asserted this, so a regression that stopped caching entirely
        # would have gone unnoticed until the rate limit hit.
        import resolve_name
        stored = {}
        with mock.patch.object(resolve_name, "_get",
                               return_value=b'{"a": 1}') as fetch, \
                mock.patch.object(resolve_name.disk_cache, "is_fresh",
                                  return_value=False), \
                mock.patch.object(resolve_name.disk_cache, "store",
                                  side_effect=lambda p, d: stored.update({p: d})):
            data = resolve_name._cached_json("probe.json", "https://example.invalid/x")
        self.assertEqual(data, {"a": 1})
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(list(stored.values()), [b'{"a": 1}'])

    def test_a_fresh_cache_is_read_instead_of_refetching(self):
        import json as json_mod

        import resolve_name
        with tempfile.TemporaryDirectory() as base:
            path = pathlib.Path(base) / "probe.json"
            path.write_text(json_mod.dumps({"cached": True}), encoding="utf-8")
            with mock.patch.object(resolve_name.disk_cache, "cache_path",
                                   return_value=str(path)), \
                    mock.patch.object(resolve_name, "_get",
                                      side_effect=AssertionError(
                                          "a fresh cache must not refetch")):
                data = resolve_name._cached_json("probe.json",
                                                 "https://example.invalid/x")
        self.assertEqual(data, {"cached": True})

    def test_a_refusal_is_never_cached(self):
        # Caching one would re-serve it for a day without another request.
        import resolve_name
        stored = []
        with mock.patch.object(resolve_name, "_get", return_value=b"<html>nope"), \
                mock.patch.object(resolve_name.disk_cache, "is_fresh",
                                  return_value=False), \
                mock.patch.object(resolve_name.disk_cache, "store",
                                  side_effect=lambda p, d: stored.append(p)):
            with self.assertRaises(net_errors.HostRefused):
                resolve_name._cached_json("probe.json", "https://example.invalid/x")
        self.assertEqual(stored, [])


class TaiwanUnsupportedNetworkTest(unittest.TestCase):
    """Taiwan's code is kept and works from a residential connection, but
    TWSE refuses datacentre IPs on every host, so the docs have to say that
    plainly -- otherwise a cloud user reads "403" as an egress gap and keeps
    adding allowlist entries that cannot help."""

    def test_the_reference_records_that_every_twse_host_refuses(self):
        text = (ROOT / "references" / "taiwan.md").read_text(encoding="utf-8")
        # Each host has to appear as a row of the measured-hosts table, not
        # merely somewhere in the file: several are also named in the prose
        # about routes that were ruled out, so a plain substring check
        # passed even with a row deleted.
        rows = [ln for ln in text.splitlines()
                if ln.startswith("| `") and "twse.com.tw`" in ln]
        listed = {ln.split("`")[1] for ln in rows}
        for host in ("doc.twse.com.tw", "mops.twse.com.tw",
                     "openapi.twse.com.tw", "mopsov.twse.com.tw",
                     "emops.twse.com.tw", "mopsfin.twse.com.tw"):
            with self.subTest(host=host):
                self.assertIn(host, listed,
                              "not a row of the measured-hosts table")
        self.assertIn("IP-wide", text)

    def test_the_reference_says_the_code_is_not_the_problem(self):
        text = (ROOT / "references" / "taiwan.md").read_text(encoding="utf-8")
        self.assertIn("It is the network that is", text)
        self.assertIn("residential", text)

    def test_the_reference_gives_the_manual_route(self):
        text = (ROOT / "references" / "taiwan.md").read_text(encoding="utf-8")
        for expected in ("mops.twse.com.tw", "investor-relations", "20-F"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_skill_md_marks_the_venue_unreachable_from_the_cloud(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        row = [ln for ln in text.splitlines()
               if ln.startswith("| Taiwan |")]
        self.assertTrue(row, "SKILL.md has no Taiwan row in the venue table")
        self.assertIn("unreachable from cloud sessions", row[0])

    def test_the_readme_says_allowlisting_the_hosts_is_not_enough(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Taiwan does not work from a cloud container", text)
        self.assertIn("no egress change\nfixes it", text)

    def test_the_twse_hosts_stay_documented_for_networks_that_work(self):
        # Removing them would make the venue undiscoverable on the networks
        # where it does work, and test_network_hosts would fail anyway.
        import re
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        block = re.search(r"<!--\s*egress-hosts:start\s*-->(.*?)"
                          r"<!--\s*egress-hosts:end\s*-->", text, re.S)
        self.assertIsNotNone(block, "README has no egress-hosts block")
        hosts = [ln.strip() for ln in block.group(1).splitlines()]
        self.assertIn("doc.twse.com.tw", hosts)


class JapanDirectoryTest(unittest.TestCase):
    """JPX moved the list to .xlsx, which xlrd 2.x cannot read at all; the
    old .xls URL 404s, and that was hidden behind a "pip install xlrd"
    message on any machine without xlrd."""

    def test_the_directory_url_is_the_xlsx_one(self):
        import resolve_name
        self.assertTrue(resolve_name.JPX_DIRECTORY_URL.endswith(".xlsx"))

    def test_nothing_still_reaches_for_xlrd(self):
        # The word itself stays, in the comment explaining why it went; what
        # must not come back is the import or the install instruction.
        text = (ROOT / "scripts" / "resolve_name.py").read_text(encoding="utf-8")
        self.assertNotIn("import xlrd", text)
        self.assertNotIn("pip install xlrd", text)

    def test_a_failed_cache_write_does_not_fail_the_lookup(self):
        # store() swallows write failures by design, so reading the cache
        # back would turn a successful download into an ERROR row.
        import importlib.util

        import resolve_name
        if importlib.util.find_spec("openpyxl") is None:
            self.skipTest("openpyxl not installed")
        with mock.patch.object(resolve_name.disk_cache, "is_fresh",
                               return_value=False), \
                mock.patch.object(resolve_name.disk_cache, "store",
                                  lambda path, data: None), \
                mock.patch.object(resolve_name, "_get",
                                  return_value=_minimal_xlsx()):
            rows = resolve_name.search_jp("Widget")
        self.assertEqual(rows, [("jp", "1234.T", "Widget Corporation")])


def _minimal_xlsx() -> bytes:
    """A one-row workbook in JPX's column layout, as bytes."""
    import io

    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Effective Date", "Local Code", "Name (English)",
                  "Section/Products"])
    sheet.append([20260831, 1234, "Widget Corporation", "Prime Market"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()

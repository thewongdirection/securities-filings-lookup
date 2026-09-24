#!/usr/bin/env python3
"""
Regression tests for SEC exhibit handling and saved-file naming.

Both behaviours here were bugs found in live use: saving only
`primaryDocument` handed back IBM's 31-page 10-K wrapper without the
EX-13 that carries the MD&A and the consolidated statements, and the
saved files were named `{date}_{form}.pdf`, so a folder holding several
companies was unsortable and two filers of one form on one day
collided.

The index fixture is a real EDGAR filing index (IBM's FY2025 10-K),
recorded so the parser is tested against what EDGAR actually serves --
including the `/ix?doc=` viewer links that inline-XBRL documents carry.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_us_filings as us  # noqa: E402
import pdf_utils  # noqa: E402

# Every SEC request now resolves a declared contact; these tests are about
# the save loop, not the configuration, so they stub one in.
TEST_CONTACT = "Test Runner tests@realdomain.test"

FIXTURE = (ROOT / "tests" / "fixtures" / "edgar_index_ibm_10k.html").read_text(
    encoding="utf-8")

ROW = {
    "form": "10-K",
    "filed": "2026-02-24",
    "period": "2025-12-31",
    "description": "10-K",
    "cik": 51143,
    "accession": "000005114326000010",
    "accession_dashed": "0000051143-26-000010",
    "url": ("https://www.sec.gov/Archives/edgar/data/51143/"
            "000005114326000010/ibm-20251231.htm"),
}


class IndexParsingTest(unittest.TestCase):
    def setUp(self):
        self.docs = us.parse_index_documents(FIXTURE)

    def test_every_document_row_is_parsed(self):
        self.assertGreaterEqual(len(self.docs), 13)
        types = [d["type"] for d in self.docs]
        for expected in ("10-K", "EX-13", "EX-21", "EX-23.1", "EX-31.1"):
            self.assertIn(expected, types)

    def test_inline_xbrl_viewer_links_resolve_to_the_raw_document(self):
        # EDGAR links iXBRL documents as /ix?doc=/Archives/... -- that URL
        # is a JavaScript viewer app, and rendering it yields a blank PDF.
        ex13 = [d for d in self.docs if d["type"] == "EX-13"][0]
        self.assertEqual(ex13["document"], "ibm-20251231_d2.htm")
        self.assertEqual(
            ex13["url"],
            "https://www.sec.gov/Archives/edgar/data/51143/"
            "000005114326000010/ibm-20251231_d2.htm")
        for doc in self.docs:
            with self.subTest(document=doc["document"]):
                self.assertNotIn("/ix?", doc["url"])
                self.assertIn("/Archives/", doc["url"])

    def test_header_row_is_not_mistaken_for_a_document(self):
        self.assertNotIn("Seq", [d["seq"] for d in self.docs])

    def test_a_page_without_a_document_table_yields_nothing(self):
        self.assertEqual(us.parse_index_documents("<html><body>no table</body></html>"), [])


class ExhibitSelectionTest(unittest.TestCase):
    def _docs(self, *types):
        return [{"type": t, "document": f"{t}.htm", "description": "", "seq": "1",
                 "size": "1", "url": f"https://example.invalid/{t}.htm"} for t in types]

    def test_ex13_and_its_numbered_variants_are_selected(self):
        picked = us.select_exhibits(self._docs("EX-13", "EX-13.1", "ex-13.2"), ["EX-13"])
        self.assertEqual([d["type"] for d in picked], ["EX-13", "EX-13.1", "ex-13.2"])

    def test_unrelated_exhibits_are_left_alone(self):
        docs = self._docs("10-K", "EX-10.1", "EX-21", "EX-23.1", "EX-99.1", "EX-131")
        self.assertEqual(us.select_exhibits(docs, ["EX-13"]), [])

    def test_extra_types_can_be_requested(self):
        docs = self._docs("EX-13", "EX-21", "EX-99.1")
        picked = us.select_exhibits(docs, ["EX-13", "EX-21"])
        self.assertEqual([d["type"] for d in picked], ["EX-13", "EX-21"])

    def test_no_wanted_types_selects_nothing(self):
        self.assertEqual(us.select_exhibits(self._docs("EX-13"), []), [])


class OutputNamingTest(unittest.TestCase):
    def test_name_carries_ticker_form_and_date(self):
        self.assertEqual(us.out_name("IBM", "10-K", "2026-02-24"),
                         "IBM_10-K_2026-02-24.pdf")

    def test_ticker_is_upper_cased(self):
        self.assertEqual(us.out_name("dell", "10-Q", "2026-09-08"),
                         "DELL_10-Q_2026-09-08.pdf")

    def test_amended_forms_do_not_create_directories(self):
        self.assertEqual(us.out_name("WDC", "10-K/A", "2026-08-14"),
                         "WDC_10-K-A_2026-08-14.pdf")

    def test_exhibits_are_distinguishable_from_the_primary_document(self):
        self.assertEqual(us.out_name("IBM", "10-K", "2026-02-24", "EX-13"),
                         "IBM_10-K-EX-13_2026-02-24.pdf")

    def test_two_filers_of_one_form_on_one_day_do_not_collide(self):
        self.assertNotEqual(us.out_name("WDC", "10-K", "2026-08-14"),
                            us.out_name("STX", "10-K", "2026-08-14"))


class SaveRowsTest(unittest.TestCase):
    """The save loop, with the network and the browser stubbed out."""

    def setUp(self):
        ua = mock.patch.object(us, "user_agent", return_value=TEST_CONTACT)
        ua.start()
        self.addCleanup(ua.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.fetched = []

    def _fake_get(self, url):
        self.fetched.append(url)
        if url.endswith("-index.htm"):
            return FIXTURE.encode("utf-8")
        return b"<html><body>filing</body></html>"

    def _fake_render(self, url, out_path, **kwargs):
        Path(out_path).write_bytes(b"%PDF-1.4\nrendered\n")
        return out_path

    def _run(self, exhibits, get=None):
        with mock.patch.object(us, "_get", get or self._fake_get), \
                mock.patch.object(us, "save_filing_as_pdf",
                                  side_effect=lambda u, d, o, **k: self._fake_render(u, o)), \
                mock.patch.object(us.time, "sleep"), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            saved = us.save_rows([dict(ROW)], "IBM", self.dir, exhibits)
        return saved, out.getvalue()

    def test_the_incorporated_annual_report_is_saved_alongside_the_10k(self):
        saved, printed = self._run(["EX-13"])
        names = sorted(Path(p).name for p in saved)
        self.assertEqual(names, ["IBM_10-K-EX-13_2026-02-24.pdf",
                                 "IBM_10-K_2026-02-24.pdf"])
        self.assertIn("EX-13", printed)
        for path in saved:
            self.assertTrue(Path(path).exists())

    def test_no_exhibits_means_primary_document_only(self):
        saved, _ = self._run([])
        self.assertEqual([Path(p).name for p in saved], ["IBM_10-K_2026-02-24.pdf"])
        # The index page is not even requested when nothing is wanted.
        self.assertEqual([u for u in self.fetched if u.endswith("-index.htm")], [])

    def test_a_rate_limited_index_still_yields_the_filing(self):
        def rate_limited(url):
            if url.endswith("-index.htm"):
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
            return self._fake_get(url)

        saved, printed = self._run(["EX-13"], get=rate_limited)
        self.assertEqual([Path(p).name for p in saved], ["IBM_10-K_2026-02-24.pdf"])
        self.assertIn("could not check", printed)
        self.assertIn("429", printed)

    def test_a_failing_exhibit_does_not_abandon_the_remaining_filings(self):
        rows = [dict(ROW), dict(ROW, filed="2025-02-25",
                                accession="000005114325000010",
                                accession_dashed="0000051143-25-000010",
                                url="https://www.sec.gov/Archives/x/prior.htm")]

        def get(url):
            self.fetched.append(url)
            if url.endswith("-index.htm"):
                return FIXTURE.encode("utf-8")
            if url.endswith("_d2.htm"):  # the EX-13
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            return b"<html>filing</html>"

        with mock.patch.object(us, "_get", get), \
                mock.patch.object(us, "save_filing_as_pdf",
                                  side_effect=lambda u, d, o, **k: self._fake_render(u, o)), \
                mock.patch.object(us.time, "sleep"), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            saved = us.save_rows(rows, "IBM", self.dir, ["EX-13"])

        names = sorted(Path(p).name for p in saved)
        self.assertEqual(names, ["IBM_10-K_2025-02-25.pdf", "IBM_10-K_2026-02-24.pdf"])
        self.assertIn("could not be saved", out.getvalue())

    def test_one_unreachable_filing_does_not_abandon_the_rest(self):
        rows = [dict(ROW, url="https://www.sec.gov/Archives/x/gone.htm"),
                dict(ROW, filed="2025-02-25", accession="000005114325000010",
                     accession_dashed="0000051143-25-000010",
                     url="https://www.sec.gov/Archives/x/fine.htm")]

        def get(url):
            if url.endswith("gone.htm"):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return self._fake_get(url)

        with mock.patch.object(us, "_get", get), \
                mock.patch.object(us, "save_filing_as_pdf",
                                  side_effect=lambda u, d, o, **k: self._fake_render(u, o)), \
                mock.patch.object(us.time, "sleep"), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            saved = us.save_rows(rows, "IBM", self.dir, [])

        self.assertEqual([Path(p).name for p in saved], ["IBM_10-K_2025-02-25.pdf"])
        self.assertIn("could not be saved", out.getvalue())

    def test_a_rate_limit_stops_the_run_instead_of_deepening_it(self):
        # 429 is the whole IP being limited: pressing on with nine more
        # documents only makes it worse, and SKILL.md says to wait.
        rows = [dict(ROW), dict(ROW, filed="2025-02-25")]

        def get(url):
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

        with mock.patch.object(us, "_get", get), \
                mock.patch.object(us.time, "sleep"), \
                mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                us.save_rows(rows, "IBM", self.dir, [])
        self.assertEqual(caught.exception.code, 429)

    def test_an_unwritable_save_directory_stops_the_run(self):
        rows = [dict(ROW)]

        def boom(url, data, out_path, **kwargs):
            raise PermissionError(13, "Permission denied", out_path)

        with mock.patch.object(us, "_get", self._fake_get), \
                mock.patch.object(us, "save_filing_as_pdf", side_effect=boom), \
                mock.patch.object(us.time, "sleep"), \
                mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(PermissionError):
                us.save_rows(rows, "IBM", self.dir, [])

    def test_a_native_pdf_filing_is_saved_verbatim(self):
        # The real save_filing_as_pdf runs here: a PDF must be written
        # byte-for-byte and never routed through the browser.
        def pdf_bytes(url):
            self.fetched.append(url)
            if url.endswith("-index.htm"):
                return FIXTURE.encode("utf-8")
            return b"%PDF-1.4\noriginal bytes\n"

        with mock.patch.object(us, "_get", pdf_bytes), \
                mock.patch.object(pdf_utils, "render_url_to_pdf") as render, \
                mock.patch.object(us.time, "sleep"), \
                mock.patch("sys.stdout", io.StringIO()):
            saved = us.save_rows([dict(ROW)], "IBM", self.dir, [])
        render.assert_not_called()
        self.assertEqual(Path(saved[0]).read_bytes(), b"%PDF-1.4\noriginal bytes\n")

    def test_the_declared_contact_reaches_the_renderer(self):
        with mock.patch.object(us, "_get", self._fake_get), \
                mock.patch.object(pdf_utils, "render_url_to_pdf",
                                  side_effect=self._fake_render) as render, \
                mock.patch.object(us.time, "sleep"), \
                mock.patch("sys.stdout", io.StringIO()):
            us.save_rows([dict(ROW)], "IBM", self.dir, [])
        sent = render.call_args.kwargs["user_agent"]
        self.assertEqual(sent, TEST_CONTACT)
        self.assertTrue(sent, "an empty UA disables route interception")

    def test_an_html_filing_is_downloaded_once_not_twice(self):
        # save_document used to fetch the document and then let the
        # renderer fetch the identical URL again -- double traffic to a
        # host that rate-limits.
        with mock.patch.object(us, "_get", self._fake_get), \
                mock.patch.object(pdf_utils, "render_url_to_pdf",
                                  side_effect=self._fake_render) as render, \
                mock.patch.object(us.time, "sleep"), \
                mock.patch("sys.stdout", io.StringIO()):
            us.save_rows([dict(ROW)], "IBM", self.dir, [])
        self.assertEqual(self.fetched, [ROW["url"]])
        self.assertEqual(render.call_args.kwargs["prefetched"],
                         (b"<html><body>filing</body></html>", "text/html"))

    def test_the_index_url_is_built_from_the_dashed_accession(self):
        self._run(["EX-13"])
        index_calls = [u for u in self.fetched if u.endswith("-index.htm")]
        self.assertEqual(index_calls, [
            "https://www.sec.gov/Archives/edgar/data/51143/"
            "000005114326000010/0000051143-26-000010-index.htm"])


class SameDayCollisionTest(unittest.TestCase):
    """One filer can file two of the same form on one day (8-Ks do)."""

    def setUp(self):
        ua = mock.patch.object(us, "user_agent", return_value=TEST_CONTACT)
        ua.start()
        self.addCleanup(ua.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _row(self, accession_dashed, body):
        row = dict(ROW, form="8-K", filed="2026-03-01",
                   accession_dashed=accession_dashed,
                   accession=accession_dashed.replace("-", ""),
                   url=f"https://www.sec.gov/Archives/x/{body}.htm")
        return row

    def test_two_filings_of_one_form_on_one_day_are_both_kept(self):
        rows = [self._row("0000051143-26-000011", "first"),
                self._row("0000051143-26-000012", "second")]
        bodies = {}

        def fake_render(url, out_path, **kwargs):
            Path(out_path).write_text(url, encoding="utf-8")
            bodies[out_path] = url
            return out_path

        with mock.patch.object(us, "_get", return_value=b"<html></html>"), \
                mock.patch.object(us, "save_filing_as_pdf",
                                  side_effect=lambda u, d, o, **k: fake_render(u, o)), \
                mock.patch.object(us.time, "sleep"), \
                mock.patch("sys.stdout", io.StringIO()):
            saved = us.save_rows(rows, "IBM", self.tmp.name, [])

        self.assertEqual(len(saved), 2)
        self.assertEqual(len(set(saved)), 2, "the second filing overwrote the first")
        for path in saved:
            self.assertTrue(Path(path).exists())
        self.assertEqual(len({Path(p).read_text(encoding="utf-8") for p in saved}), 2)

    def test_the_disambiguator_is_the_accession(self):
        used = set()
        first = us._claim_name(used, "IBM_8-K_2026-03-01.pdf", "000005114326000011")
        second = us._claim_name(used, "IBM_8-K_2026-03-01.pdf", "000005114326000012")
        self.assertEqual(first, "IBM_8-K_2026-03-01.pdf")
        self.assertEqual(second, "IBM_8-K_2026-03-01_000012.pdf")
        self.assertNotEqual(first, second)


ARM_FIXTURE = (ROOT / "tests" / "fixtures" / "edgar_index_arm_20f.html").read_text(
    encoding="utf-8")


class ExhibitScopeTest(unittest.TestCase):
    """The index lookup is a round trip against a host that rate-limits."""

    def test_quarterly_and_current_reports_skip_the_index_entirely(self):
        for form in ("10-Q", "8-K", "4", "S-8"):
            with self.subTest(form=form):
                with mock.patch.object(us, "_get") as get:
                    self.assertEqual(us.fetch_exhibits(dict(ROW, form=form), ["EX-13"]), [])
                get.assert_not_called()

    def test_ten_k_and_its_amendments_do_check(self):
        for form in ("10-K", "10-K/A", "10-K405"):
            with self.subTest(form=form):
                with mock.patch.object(us, "_get",
                                       return_value=FIXTURE.encode("utf-8")), \
                        mock.patch.object(us.time, "sleep"):
                    found = us.fetch_exhibits(dict(ROW, form=form), ["EX-13"])
                self.assertEqual([d["type"] for d in found], ["EX-13"])

    def test_a_20f_is_self_contained_so_its_ex13_is_left_alone(self):
        # Found live: ARM Holdings' FY2026 20-F carries an EX-13.1 that is a
        # one-page Sarbanes-Oxley 906 certification, not an annual report --
        # 13.x means something different under 20-F exhibit numbering. The
        # 216-page report was the primary document all along.
        for form in ("20-F", "40-F"):
            with self.subTest(form=form):
                with mock.patch.object(us, "_get") as get:
                    self.assertEqual(
                        us.fetch_exhibits(dict(ROW, form=form), ["EX-13"]), [])
                get.assert_not_called()

    def test_a_certification_is_never_delivered_as_a_report(self):
        # Belt and braces for a 10-K filer numbering its exhibits oddly.
        with mock.patch.object(us, "_get",
                               return_value=ARM_FIXTURE.encode("utf-8")), \
                mock.patch.object(us.time, "sleep"):
            found = us.fetch_exhibits(dict(ROW, form="10-K"), ["EX-13"])
        self.assertEqual(found, [])

    def test_the_certification_detector(self):
        self.assertTrue(us._is_certification(
            {"description": "EX-13.1", "document": "ex131-ceocertfye26.htm"}))
        self.assertTrue(us._is_certification(
            {"description": "CERTIFICATION OF CEO", "document": "x.htm"}))
        self.assertFalse(us._is_certification(
            {"description": "EX-13", "document": "ibm-20251231_d2.htm"}))

    def test_base_form_strips_the_amendment_suffix(self):
        self.assertEqual(us.base_form("10-K/A"), "10-K")
        self.assertEqual(us.base_form("10-k"), "10-K")
        self.assertEqual(us.base_form("8-K"), "8-K")


class RowMetadataTest(unittest.TestCase):
    """fetch_filings has to carry what the index URL needs."""

    SUBMISSIONS = {
        "filings": {
            "recent": {
                "form": ["10-K"],
                "filingDate": ["2026-02-24"],
                "reportDate": ["2025-12-31"],
                "accessionNumber": ["0000051143-26-000010"],
                "primaryDocument": ["ibm-20251231.htm"],
                "primaryDocDescription": ["10-K"],
            }
        }
    }

    def test_rows_carry_cik_and_both_accession_forms(self):
        with mock.patch.object(us, "_get_json", return_value=self.SUBMISSIONS), \
                mock.patch.object(us.time, "sleep"):
            row = us.fetch_filings(51143, ["10-K"], 1)[0][0]
        self.assertEqual(row["cik"], 51143)
        self.assertEqual(row["accession"], "000005114326000010")
        self.assertEqual(row["accession_dashed"], "0000051143-26-000010")
        self.assertTrue(row["url"].endswith("000005114326000010/ibm-20251231.htm"))


if __name__ == "__main__":
    unittest.main()

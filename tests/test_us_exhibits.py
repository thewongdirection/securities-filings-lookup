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
                mock.patch.object(us, "render_url_to_pdf", self._fake_render), \
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

    def test_a_native_pdf_filing_is_saved_verbatim(self):
        def pdf_bytes(url):
            self.fetched.append(url)
            if url.endswith("-index.htm"):
                return FIXTURE.encode("utf-8")
            return b"%PDF-1.4\noriginal bytes\n"

        with mock.patch.object(us, "_get", pdf_bytes), \
                mock.patch.object(us, "render_url_to_pdf") as render, \
                mock.patch.object(us.time, "sleep"), \
                mock.patch("sys.stdout", io.StringIO()):
            saved = us.save_rows([dict(ROW)], "IBM", self.dir, [])
        render.assert_not_called()
        self.assertEqual(Path(saved[0]).read_bytes(), b"%PDF-1.4\noriginal bytes\n")

    def test_the_index_url_is_built_from_the_dashed_accession(self):
        self._run(["EX-13"])
        index_calls = [u for u in self.fetched if u.endswith("-index.htm")]
        self.assertEqual(index_calls, [
            "https://www.sec.gov/Archives/edgar/data/51143/"
            "000005114326000010/0000051143-26-000010-index.htm"])


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
            row = us.fetch_filings(51143, ["10-K"], 1)[0]
        self.assertEqual(row["cik"], 51143)
        self.assertEqual(row["accession"], "000005114326000010")
        self.assertEqual(row["accession_dashed"], "0000051143-26-000010")
        self.assertTrue(row["url"].endswith("000005114326000010/ibm-20251231.htm"))


if __name__ == "__main__":
    unittest.main()

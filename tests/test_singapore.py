#!/usr/bin/env python3
"""
Offline tests for the Singapore (SGX) venue.

Every assertion here came from a live measurement rather than a guess:
the fixtures are the real securities directory payload and two real
SGXNet announcement pages, one of which (SoftBank Group, debt-only on
SGX) carries no `Securities` field at all and caught a bug that filed
its annual report under the trading code that had been searched for.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_sg_filings as sg  # noqa: E402
import identify_venue  # noqa: E402
from net_errors import HostRefused, SetupError  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class VenueClassificationTest(unittest.TestCase):
    def test_explicit_suffixes_are_singapore(self):
        for ticker in ("D05.SI", "d05.si", "Z74.SGX", "C52.SI"):
            with self.subTest(ticker=ticker):
                self.assertEqual(identify_venue.identify(ticker)["venue"],
                                 "singapore")

    def test_mixed_letter_digit_codes_are_claimed_for_singapore(self):
        # A shape no other venue here uses: US tickers are pure letters,
        # HK codes pure digits, Taiwan/Japan four digits.
        for code in ("D05", "Z74", "C6L", "A17U", "5E2", "O39"):
            with self.subTest(code=code):
                self.assertEqual(identify_venue.identify(code)["venue"],
                                 "singapore")

    def test_ambiguous_shapes_keep_their_established_venue_but_say_so(self):
        # 177 SGX codes are pure letters and 27 pure digits. Reassigning
        # those would break every US ticker and HK code, so the prior
        # stands -- the note has to mention Singapore instead.
        letters = identify_venue.identify("LVR")
        self.assertEqual(letters["venue"], "united_states")
        self.assertIn("SGX", letters["note"])
        digits = identify_venue.identify("533")
        self.assertEqual(digits["venue"], "hong_kong")
        self.assertIn("SGX", digits["note"])


class DirectoryTest(unittest.TestCase):
    def setUp(self):
        self.rows = sg.parse_directory(json.loads(fixture("sgx_securities.json")))

    def test_it_reads_code_name_board_and_type(self):
        dbs = next(r for r in self.rows if r["code"] == "D05")
        self.assertEqual(dbs["name"], "DBS")
        self.assertEqual(dbs["board"], "MAINBOARD")
        self.assertEqual(dbs["type"], "stocks")

    def test_reits_and_business_trusts_count_as_issuers(self):
        # The reason the fetcher uses /securities/v1.1 rather than
        # /securities/v1.1/stocks, which omits both.
        for code in ("A17U",):
            with self.subTest(code=code):
                row = next(r for r in self.rows if r["code"] == code)
                self.assertTrue(sg.is_issuer(row))

    def test_leveraged_certificates_are_not_issuers(self):
        row = next(r for r in self.rows if r["type"] == "dlcertificates")
        self.assertFalse(sg.is_issuer(row))

    def test_rows_without_a_code_are_dropped(self):
        payload = {"data": {"prices": [{"nc": "", "n": "Nameless"},
                                       {"n": "No code at all"},
                                       {"nc": " d05 ", "n": "Padded"}]}}
        rows = sg.parse_directory(payload)
        self.assertEqual([r["code"] for r in rows], ["D05"])

    def test_a_malformed_payload_yields_nothing_rather_than_raising(self):
        for payload in ({}, {"data": None}, {"data": {"prices": None}}):
            with self.subTest(payload=payload):
                self.assertEqual(sg.parse_directory(payload), [])


class FindTest(unittest.TestCase):
    def setUp(self):
        self.rows = sg.parse_directory(json.loads(fixture("sgx_securities.json")))

    def test_an_exact_code_beats_a_name_search(self):
        # 533 is ABR on SGX as well as looking like a Hong Kong code; if
        # the name search ran first, a company called "533..." would win.
        self.assertEqual([r["code"] for r in sg.find("D05", self.rows)], ["D05"])

    def test_a_suffixed_code_resolves(self):
        self.assertEqual([r["code"] for r in sg.find("d05.si", self.rows)], ["D05"])

    def test_a_name_search_leads_with_the_reporting_issuer(self):
        # "DBS" also matches a shelf of DBS 5xLong/5xShort certificates.
        hits = sg.find("DBS", self.rows)
        self.assertEqual(hits[0]["code"], "D05")
        self.assertTrue(sg.is_issuer(hits[0]))

    def test_an_empty_query_matches_nothing(self):
        for query in ("", "   ", None):
            with self.subTest(query=query):
                self.assertEqual(sg.find(query, self.rows), [])

    def test_normalise_strips_both_suffixes(self):
        self.assertEqual(sg.normalise("z74.sgx"), "Z74")
        self.assertEqual(sg.normalise(" d05 "), "D05")
        self.assertEqual(sg.normalise(None), "")


class AnnouncementIdTest(unittest.TestCase):
    def test_a_bare_id_and_a_full_url_both_resolve(self):
        for ref in ("U6RBLH1JFNDV1QZT", "u6rblh1jfndv1qzt",
                    "https://links.sgx.com/1.0.0/corporate-announcements/"
                    "U6RBLH1JFNDV1QZT/",
                    "https://links.sgx.com/1.0.0/corporate-announcements/"
                    "U6RBLH1JFNDV1QZT/877753_DBS%20Annual%20Report%202025.pdf"):
            with self.subTest(ref=ref):
                self.assertEqual(sg.announcement_id(ref), "U6RBLH1JFNDV1QZT")

    def test_anything_that_is_not_an_id_is_refused_with_the_shape(self):
        for ref in ("", "D05", "not-an-id", "https://links.sgx.com/FileOpen/x.ashx",
                    "U6RBLH1JFNDV1QZ"):
            with self.subTest(ref=ref):
                with self.assertRaises(SetupError) as caught:
                    sg.announcement_id(ref)
                self.assertIn("16", str(caught.exception))


class AnnouncementParsingTest(unittest.TestCase):
    def setUp(self):
        self.dbs = sg.parse_announcement(fixture("sgx_announcement_dbs.html"))

    def test_it_reads_the_announcement_metadata(self):
        self.assertEqual(self.dbs["title"], "Annual Reports and Related Documents")
        self.assertEqual(self.dbs["issuer"], "DBS GROUP HOLDINGS LTD")
        self.assertEqual(self.dbs["code"], "D05")
        self.assertEqual(self.dbs["isin"], "SG1L01001701")
        self.assertEqual(self.dbs["report_type"], "Annual Report")
        self.assertEqual(self.dbs["broadcast"], "09-Mar-2026 07:33:28")
        self.assertEqual(self.dbs["period_ended"], "31/12/2025")
        self.assertEqual(self.dbs["reference"], "SG260309OTHROF8R")

    def test_it_reads_every_attachment_with_an_absolute_url_and_file_id(self):
        # Two attachments: saving only the first loses the Letter to
        # Shareholders that the announcement's own description lists.
        self.assertEqual([a["file_id"] for a in self.dbs["attachments"]],
                         ["877753", "877754"])
        self.assertEqual(self.dbs["attachments"][0]["name"],
                         "DBS Annual Report 2025.pdf")
        for item in self.dbs["attachments"]:
            with self.subTest(name=item["name"]):
                self.assertTrue(item["url"].startswith(
                    "https://links.sgx.com/1.0.0/corporate-announcements/"))
                self.assertTrue(sg.is_sgx_document(item["url"]))

    def test_a_debt_only_issuer_has_no_trading_code(self):
        # SoftBank Group lists only debt on SGX, so its announcement
        # carries no `Securities` field. Parsing has to cope, and naming
        # must not fall back to whatever code was searched for.
        info = sg.parse_announcement(fixture("sgx_announcement_no_code.html"))
        self.assertEqual(info["issuer"], "SOFTBANK GROUP CORP.")
        self.assertNotIn("code", info)
        self.assertEqual(len(info["attachments"]), 2)

    def test_an_unrelated_page_parses_to_nothing_rather_than_half_a_filing(self):
        info = sg.parse_announcement("<html><body><p>Access denied</p></body></html>")
        self.assertEqual(info["attachments"], [])
        self.assertNotIn("title", info)


class IssuerAttributionTest(unittest.TestCase):
    """The bug this class exists for: a pasted announcement id from
    another company saved that company's annual report under the code
    that had been searched for."""

    def setUp(self):
        self.softbank = sg.parse_announcement(
            fixture("sgx_announcement_no_code.html"))
        self.dbs = sg.parse_announcement(fixture("sgx_announcement_dbs.html"))
        self.o39 = {"code": "O39", "name": "OCBC Bank"}
        self.d05 = {"code": "D05", "name": "DBS"}

    def test_a_codeless_announcement_is_named_for_its_own_issuer(self):
        self.assertEqual(sg.announcement_identifier(self.softbank, "O39"),
                         "SOFTBANK GROUP CORP.")

    def test_an_announcements_own_code_wins_over_the_query(self):
        self.assertEqual(sg.announcement_identifier(self.dbs, "O39"), "D05")

    def test_the_fallback_applies_only_when_nothing_is_known(self):
        self.assertEqual(sg.announcement_identifier({}, "O39"), "O39")

    def test_a_mismatch_is_detected_by_code_and_by_name(self):
        self.assertTrue(sg.issuer_matches(self.dbs, self.d05))
        self.assertFalse(sg.issuer_matches(self.dbs, self.o39))
        self.assertFalse(sg.issuer_matches(self.softbank, self.o39))
        self.assertTrue(sg.issuer_matches(self.softbank,
                                          {"code": "", "name": "SoftBank Group"}))


class DocumentUrlTest(unittest.TestCase):
    FILEOPEN = ("https://links.sgx.com/FileOpen/DBS%20Annual%20Report%202025"
                ".ashx?App=Announcement&FileID=877753")
    PATHED = ("https://links.sgx.com/1.0.0/corporate-announcements/"
              "U6RBLH1JFNDV1QZT/877753_DBS%20Annual%20Report%202025.pdf")

    def test_the_file_id_is_read_from_both_url_shapes(self):
        self.assertEqual(sg.file_id(self.FILEOPEN), "877753")
        self.assertEqual(sg.file_id(self.PATHED), "877753")
        self.assertEqual(sg.file_id("https://links.sgx.com/FileOpen/x.ashx"), "")

    def test_the_label_drops_the_plumbing_not_the_title(self):
        self.assertEqual(sg.document_label(self.FILEOPEN), "DBS Annual Report 2025")
        self.assertEqual(sg.document_label(self.PATHED), "DBS Annual Report 2025")

    def test_only_sgxnet_hosts_count_as_documents(self):
        self.assertTrue(sg.is_sgx_document(self.FILEOPEN))
        for url in ("https://www.ocbc.com/ar2025.pdf",
                    "https://links.sgx.com.evil.test/FileOpen/x.ashx",
                    "https://api.sgx.com/securities/v1.1"):
            with self.subTest(url=url):
                self.assertFalse(sg.is_sgx_document(url))

    def test_a_non_sgx_url_is_refused_and_points_at_save_filing(self):
        # A bad argument, not a host refusing -- nothing was sent.
        with self.assertRaises(SetupError) as caught:
            sg.save_document("https://www.ocbc.com/ar2025.pdf", "O39", "/tmp")
        self.assertIn("save_filing.py", str(caught.exception))

    def test_an_error_page_is_never_saved_as_a_filing(self):
        # Without this the bytes go to save_filing_as_pdf, which renders
        # non-PDF input through Chromium -- turning SGX's error page into a
        # plausible-looking "annual report". TWSE taught this lesson once.
        with mock.patch.object(sg, "_get", return_value=b"<html>Access denied"):
            with self.assertRaises(HostRefused) as caught:
                sg.save_document(self.FILEOPEN, "D05", tempfile.mkdtemp())
        self.assertIn("not a PDF", str(caught.exception))

    def test_a_real_pdf_is_saved_untouched(self):
        pdf = b"%PDF-1.6\n% minimal\n"
        out = tempfile.mkdtemp()
        with mock.patch.object(sg, "_get", return_value=pdf):
            saved = sg.save_document(self.FILEOPEN, "D05", out, date="2026-03-09")
        self.assertEqual(pathlib.Path(saved).read_bytes(), pdf)
        self.assertEqual(pathlib.Path(saved).name,
                         "D05_DBS Annual Report 2025_2026-03-09_877753.pdf")


class BroadcastDateTest(unittest.TestCase):
    def test_broadcast_dates_become_sortable_iso_dates(self):
        self.assertEqual(sg.iso_date("09-Mar-2026 07:33:28"), "2026-03-09")
        self.assertEqual(sg.iso_date("26-Dec-2025"), "2025-12-26")

    def test_an_unparsable_date_is_dropped_not_smuggled_into_a_filename(self):
        for value in ("", None, "2026-03-09", "09-Xxx-2026", "9-Mar-2026"):
            with self.subTest(value=value):
                self.assertEqual(sg.iso_date(value), "")

if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""
Regression tests for the shared filing-name scheme.

Naming used to be per-venue: the US fetcher had the documented
`{TICKER}_{FORM}_{DATE}` scheme while London wrote `{date}_{headline}`
with no company in it at all, and three scripts each carried their own
copy of a filename sanitizer. Labels come from the source -- an EDGAR
table cell, a CNINFO announcement title -- so sanitizing is not
cosmetic: a colon or a backslash makes the file unopenable on Windows
or, worse, adds a path separator.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import naming  # noqa: E402


class SafeFilenameTest(unittest.TestCase):
    def test_characters_illegal_on_windows_are_removed(self):
        self.assertEqual(naming.safe_filename('EX-99:1 <draft>?'), "EX-99 1 draft")

    def test_path_separators_never_survive(self):
        for dangerous in ("a/b", "a\\b", "../../etc/passwd"):
            with self.subTest(text=dangerous):
                cleaned = naming.safe_filename(dangerous)
                self.assertNotIn("/", cleaned)
                self.assertNotIn("\\", cleaned)

    def test_control_characters_become_spaces(self):
        self.assertEqual(naming.safe_filename("annual\nreport\t2025"),
                         "annual report 2025")

    def test_length_is_capped(self):
        self.assertLessEqual(len(naming.safe_filename("x" * 500)), 70)

    def test_non_latin_titles_are_kept(self):
        self.assertEqual(naming.safe_filename("2025年年度报告"), "2025年年度报告")

    def test_an_empty_label_still_yields_something_openable(self):
        self.assertEqual(naming.safe_filename(""), "document")
        self.assertEqual(naming.safe_filename("///"), "document")


class FilingNameTest(unittest.TestCase):
    def test_the_documented_scheme(self):
        self.assertEqual(naming.filing_name("MSFT", "10-K", "2026-07-29"),
                         "MSFT_10-K_2026-07-29.pdf")

    def test_identifier_is_upper_cased_and_extras_are_appended(self):
        self.assertEqual(naming.filing_name("ibm", "10-K", "2026-02-24", "EX-13"),
                         "IBM_10-K_2026-02-24_EX-13.pdf")

    def test_a_non_pdf_extension_is_kept(self):
        # UK annual reports are ESEF zip packages, not PDFs.
        self.assertEqual(
            naming.filing_name("AstraZeneca", "Annual Report", "2026-02-12",
                               ext=".zip"),
            "ASTRAZENECA_Annual Report_2026-02-12.zip")

    def test_an_empty_part_is_left_out_rather_than_filled_in(self):
        # safe_filename falls back to "document" so that it alone can name
        # a file; applied per fragment, that fallback landed in the date's
        # place -- SGXNet documents, which often carry no date, came out
        # as `C52_Annual Report 2025_document_880529.pdf`.
        self.assertEqual(naming.filing_name("C52", "Annual Report 2025", "",
                                            "880529"),
                         "C52_Annual Report 2025_880529.pdf")
        self.assertEqual(naming.filing_name("D05", "", "2026-03-09"),
                         "D05_2026-03-09.pdf")

    def test_a_name_with_nothing_in_it_is_still_openable(self):
        self.assertEqual(naming.filing_name("", "", ""), "document.pdf")
        self.assertEqual(naming.filing_name("   ", "  ", " "), "document.pdf")

    def test_a_dangerous_label_cannot_escape_the_directory(self):
        name = naming.filing_name("X", "../../etc/passwd", "2026-01-01")
        self.assertNotIn("/", name)


class ClaimNameTest(unittest.TestCase):
    def test_the_first_claim_is_unchanged(self):
        used = set()
        self.assertEqual(naming.claim_name(used, "A_8-K_2026-03-01.pdf", "0001"),
                         "A_8-K_2026-03-01.pdf")

    def test_a_second_document_never_overwrites_the_first(self):
        used = set()
        first = naming.claim_name(used, "A_8-K_2026-03-01.pdf", "000011")
        second = naming.claim_name(used, "A_8-K_2026-03-01.pdf", "000012")
        third = naming.claim_name(used, "A_8-K_2026-03-01.pdf", "000012")
        self.assertEqual(len({first, second, third}), 3)
        for name in (first, second, third):
            self.assertTrue(name.endswith(".pdf"))


if __name__ == "__main__":
    unittest.main()

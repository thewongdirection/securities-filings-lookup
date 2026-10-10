#!/usr/bin/env python3
"""Regression tests for the offline ticker -> venue classifier."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import identify_venue  # noqa: E402

CASES = [
    # (input, venue, code)
    ("MSFT", "united_states", "MSFT"),
    ("msft", "united_states", "MSFT"),
    ("BRK.B", "united_states", "BRK-B"),     # EDGAR uses the dash form
    ("BRK-B", "united_states", "BRK-B"),
    ("0700.HK", "hong_kong", "00700"),
    ("700", "hong_kong", "00700"),           # bare HK codes zero-pad to 5
    ("9988.HK", "hong_kong", "09988"),
    ("600519", "shanghai", "600519"),
    ("600519.SS", "shanghai", "600519"),
    ("600519.SH", "shanghai", "600519"),
    ("688981", "shanghai", "688981"),        # STAR market
    ("300308.SZ", "shenzhen", "300308"),
    ("000001", "shenzhen", "000001"),
    ("301308", "shenzhen", "301308"),        # ChiNext
    ("830799", "beijing", "830799"),
    ("900901", "shanghai", "900901"),        # Shanghai B-share, not Beijing
    ("920001", "beijing", "920001"),         # the BSE's 92 prefix
    ("285A", "tokyo", "285A"),               # Kioxia: TSE codes now mix letters
    ("186A", "tokyo", "186A"),               # Astroscale
    ("AZN.L", "london", "AZN"),
    ("HSBA.LON", "london", "HSBA"),
    ("7203.T", "tokyo", "7203"),
    ("SAP.DE", "frankfurt", "SAP"),
    ("BMW.F", "frankfurt", "BMW"),
]


class IdentifyVenueTest(unittest.TestCase):
    def test_known_ticker_shapes(self):
        for raw, venue, code in CASES:
            with self.subTest(ticker=raw):
                got = identify_venue.identify(raw)
                self.assertEqual(got["venue"], venue)
                self.assertEqual(got["code"], code)

    def test_a_tokyo_alphanumeric_code_names_the_singapore_alternative(self):
        got = identify_venue.identify("285A")
        self.assertIn("Kioxia", got["note"])
        self.assertIn(".SI", got["note"])
        self.assertIn(".T", got["note"])

    def test_sgx_shaped_codes_are_not_taken_by_the_tokyo_rule(self):
        # Digit-first SGX codes and the REIT-style trailing U stay Singapore.
        for code in ("D05", "Z74", "A17U", "5E2", "C38U"):
            with self.subTest(code=code):
                self.assertEqual(identify_venue.identify(code)["venue"], "singapore")

    def test_the_singapore_note_admits_a_london_epic_has_the_same_shape(self):
        note = identify_venue.identify("3IN")["note"]
        self.assertIn("London", note)
        self.assertIn(".L", note)

    def test_the_china_fetcher_can_map_what_the_classifier_calls_shanghai(self):
        # The classifier and the fetcher disagreed about 900xxx: one said
        # Shanghai, the other said it might be Beijing and gave up.
        import fetch_cn_filings
        self.assertEqual(fetch_cn_filings.classify_exchange("900901"), ("sh", "sse"))

    def test_bare_four_digit_codes_flag_their_ambiguity(self):
        # 2330 is TSMC in Taiwan and a real HK code; the classifier has
        # to pick one, but it must say so.
        got = identify_venue.identify("2330")
        self.assertEqual(got["venue"], "hong_kong")
        # Taiwan is not a covered venue, so the note has to name it
        # anyway -- otherwise a Taiwanese code is silently answered as HK.
        self.assertIn("Taiwan", got["note"])
        self.assertIn("Tokyo", got["note"])

    def test_us_tickers_carry_the_dual_listing_warning(self):
        self.assertIn("dual-listed", identify_venue.identify("BABA")["note"])

    def test_company_names_are_unknown_and_point_at_the_resolver(self):
        got = identify_venue.identify("Kweichow Moutai")
        self.assertEqual(got["venue"], "unknown")
        self.assertIn("resolve_name.py", got["note"])

    def test_whitespace_is_tolerated(self):
        self.assertEqual(identify_venue.identify("  0700.HK  ")["code"], "00700")


if __name__ == "__main__":
    unittest.main()

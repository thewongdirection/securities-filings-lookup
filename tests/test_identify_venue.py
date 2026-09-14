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
    ("2330.TW", "taiwan", "2330"),
    ("6488.TWO", "taiwan", "6488"),          # TPEx
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

    def test_bare_four_digit_codes_flag_their_ambiguity(self):
        # 2330 is TSMC in Taiwan and a real HK code; the classifier has
        # to pick one, but it must say so.
        got = identify_venue.identify("2330")
        self.assertEqual(got["venue"], "hong_kong")
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

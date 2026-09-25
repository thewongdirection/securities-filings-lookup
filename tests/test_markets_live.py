#!/usr/bin/env python3
"""
Every supported market, ten randomly chosen issuers each, against the live
regulators.

Opt-in: these tests talk to SEC, CNINFO, TWSE, TDnet, the FCA and HKEX, so
they are skipped unless SKILL_LIVE_TESTS=1. The rest of the suite stays
offline and deterministic.

    SKILL_LIVE_TESTS=1 python -m unittest tests.test_markets_live -v
    SKILL_LIVE_SEED=7 SKILL_LIVE_TESTS=1 python -m unittest tests.test_markets_live

Ten are sampled from a larger pool per market on each run, so repeated runs
cover different issuers; the chosen sample is printed, and SKILL_LIVE_SEED
makes a run reproducible.

What counts as a pass is per-venue, because "no rows" is a legitimate
answer in some markets and a bug in others:

  * a venue that answers with filings must return at least one row
  * TDnet keeps only ~1 month, so an empty window is a pass if the script
    says that is what happened
  * a venue that refuses this client (TWSE from a datacentre IP) or whose
    endpoint has been retired upstream (the FCA's NSM search index) must
    say so in those words -- the whole point of the venue-failure work is
    that these do not masquerade as "nothing found"

Nothing here may produce a traceback: every outcome has to arrive as one
readable line, which is what SKILL.md promises the model.
"""
from __future__ import annotations

import os
import random
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

LIVE = os.environ.get("SKILL_LIVE_TESTS") == "1"
SAMPLE_SIZE = 10
PER_CALL_TIMEOUT = 240

# Pools to sample from: real, currently-listed issuers per venue.
US_POOL = ["AAPL", "MSFT", "JNJ", "JPM", "PG", "KO", "DIS", "INTC", "CSCO",
           "PEP", "NVDA", "AMD", "AVGO", "WMT", "HD", "MRK", "ABBV", "CAT",
           "GE", "BA", "MMM", "UNH", "TXN", "QCOM", "ADBE"]
# Foreign private issuers: 20-F rather than 10-K.
US_FPI_POOL = ["TSM", "ARM", "SONY", "TM", "SAP", "SHEL", "BP", "AZN", "NVS",
               "HSBC"]
CN_POOL = ["600519", "601318", "000858", "600036", "000001", "601899",
           "300750", "688981", "601012", "000333", "600030", "601166",
           "002594", "600276", "601888", "000651", "600887", "601088"]
TW_POOL = ["2330", "2317", "2454", "2412", "2308", "2882", "1301", "2881",
           "3008", "2303", "2002", "2891", "3711", "2207", "1216"]
JP_POOL = ["7203", "6758", "9984", "8306", "6861", "7974", "6501", "4063",
           "9432", "8035", "6902", "4568", "8058", "6367", "7267", "9433"]
UK_POOL = ["AstraZeneca", "HSBC", "Shell", "Unilever", "BP", "GSK", "Diageo",
           "Rio Tinto", "Barclays", "Vodafone", "Tesco", "BT Group",
           "Lloyds", "National Grid", "Prudential"]
HK_POOL = ["Tencent", "HSBC", "China Mobile", "AIA", "Alibaba", "Meituan",
           "Hong Kong Exchanges", "ICBC", "Ping An", "CNOOC", "Xiaomi",
           "JD.com", "Galaxy Entertainment", "Sands China", "Bank of China"]
# Singapore samples from the live securities directory rather than a
# hardcoded list: the directory is the venue's own source of truth, so the
# sample is always current and never goes stale as issuers delist.
SG_ANNOUNCEMENT = "U6RBLH1JFNDV1QZT"  # DBS FY2025 annual report, 2 attachments

# Frankfurt has no script: the venue is a browse/IR workflow by design.
DE_POOL = ["SAP.DE", "SIE.DE", "ALV.DE", "BAS.DE", "BMW.DE", "MBG.DE",
           "DTE.DE", "BAYN.DE", "DBK.DE", "VOW3.DE", "MUV2.DE", "RWE.DE"]


def sample(pool: list[str], size: int = SAMPLE_SIZE) -> list[str]:
    seed = os.environ.get("SKILL_LIVE_SEED")
    rng = random.Random(int(seed)) if seed and seed.isdigit() else random.Random()
    return rng.sample(pool, min(size, len(pool)))


def run_script(script: str, target: str, *extra: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), target, *extra],
        capture_output=True, text=True, timeout=PER_CALL_TIMEOUT, cwd=ROOT)
    return proc.returncode, proc.stdout, proc.stderr


BLOCKED_BY_ENVIRONMENT = ("network proxy", "not allowlisted",
                          "DNS lookup failed", "Tunnel connection failed",
                          "Could not reach the host")


def environment_block(*streams: str) -> str:
    """The egress-policy reason in this output, if that is what failed.

    A host this container cannot reach is the environment's doing, not the
    skill's -- those skip, they do not fail.
    """
    combined = " ".join(streams)
    for marker in BLOCKED_BY_ENVIRONMENT:
        if marker in combined:
            return marker
    return ""


def assert_no_traceback(case: unittest.TestCase, target: str, out: str, err: str) -> None:
    combined = out + err
    case.assertNotIn("Traceback (most recent call last)", combined,
                     f"{target} produced a traceback:\n{combined[-800:]}")


@unittest.skipUnless(LIVE, "set SKILL_LIVE_TESTS=1 to exercise the live venues")
class UnitedStatesTest(unittest.TestCase):
    def test_ten_domestic_filers_return_an_annual_report(self):
        chosen = sample(US_POOL)
        print(f"\n  US sample: {', '.join(chosen)}")
        for ticker in chosen:
            with self.subTest(ticker=ticker):
                code, out, err = run_script("fetch_us_filings.py", ticker,
                                            "--forms", "10-K", "--limit", "1")
                assert_no_traceback(self, ticker, out, err)
                if code != 0:
                    self.skipTest(f"{ticker}: SEC unavailable -- {(err or out).strip()[:120]}")
                blocked = environment_block(out, err)
                if blocked:
                    self.skipTest(f"{ticker}: sec.gov unreachable here ({blocked})")
                rows = [ln for ln in out.splitlines() if "10-K" in ln and "http" in ln]
                if not rows:
                    # A successor entity legitimately has no 10-K yet, but the
                    # script has to explain that rather than shrug.
                    self.assertIn("No 10-K filings found", out)
                    self.assertIn("successor entity", out)
                    continue
                self.assertIn("sec.gov/Archives", rows[0])

    def test_ten_foreign_private_issuers_return_a_20f(self):
        chosen = sample(US_FPI_POOL)
        print(f"\n  US (20-F) sample: {', '.join(chosen)}")
        for ticker in chosen:
            with self.subTest(ticker=ticker):
                code, out, err = run_script("fetch_us_filings.py", ticker,
                                            "--forms", "20-F", "--limit", "1")
                assert_no_traceback(self, ticker, out, err)
                if code != 0:
                    self.skipTest(f"{ticker}: SEC unavailable")
                self.assertTrue("20-F" in out or "No 20-F filings found" in out,
                                f"{ticker}: unexpected output {out[:200]}")


@unittest.skipUnless(LIVE, "set SKILL_LIVE_TESTS=1 to exercise the live venues")
class ChinaTest(unittest.TestCase):
    def test_ten_a_share_issuers_return_an_annual_report(self):
        chosen = sample(CN_POOL)
        print(f"\n  China sample: {', '.join(chosen)}")
        for code_ in chosen:
            with self.subTest(code=code_):
                rc, out, err = run_script("fetch_cn_filings.py", code_,
                                          "--kind", "annual", "--limit", "1")
                assert_no_traceback(self, code_, out, err)
                if rc != 0:
                    # CNINFO's undocumented endpoints add friction; it must
                    # be reported plainly, never as a traceback or a shrug.
                    message = (err or out).strip()
                    self.assertTrue(message, f"{code_}: failed with no message")
                    self.skipTest(f"{code_}: CNINFO unavailable -- {message[:120]}")
                # The trap this used to fall into: the empty-result branch
                # prints a cninfo.com.cn *browse* URL, so asserting on the
                # host name passed while zero annual reports came back.
                self.assertNotIn("No matching announcements", out,
                                 f"{code_}: no annual report found")
                documents = [ln for ln in out.splitlines()
                             if "static.cninfo.com.cn" in ln]
                self.assertTrue(documents,
                                f"{code_}: no document URL in {out[:200]}")


@unittest.skipUnless(LIVE, "set SKILL_LIVE_TESTS=1 to exercise the live venues")
class TaiwanTest(unittest.TestCase):
    def test_ten_issuers_either_list_filings_or_report_the_refusal(self):
        chosen = sample(TW_POOL)
        print(f"\n  Taiwan sample: {', '.join(chosen)}")
        refused = 0
        for code_ in chosen:
            with self.subTest(code=code_):
                rc, out, err = run_script("fetch_tw_filings.py", code_)
                assert_no_traceback(self, code_, out, err)
                if rc != 0:
                    blocked = environment_block(out, err)
                    if blocked:
                        self.skipTest(f"{code_}: doc.twse.com.tw unreachable "
                                      f"from this environment ({blocked})")
                    # The regression this guards: a refusal reported as
                    # "no annual filings found" for a perfectly good code.
                    self.assertIn("refused this request", err + out)
                    self.assertIn("mops.twse.com.tw", err + out)
                    refused += 1
                    continue
                # A pass on exit 0 means filings were listed -- "No annual
                # filings found" is the wrong answer this work removed.
                self.assertNotIn("No annual filings found", out,
                                 f"{code_}: exited 0 with nothing listed")
                self.assertRegex(out, r"FY\d{4}\s+\S+",
                                 f"{code_}: no listing rows in {out[:200]}")
        if refused:
            print(f"  (TWSE refused {refused}/{len(chosen)} from this network "
                  "-- see references/taiwan.md)")


@unittest.skipUnless(LIVE, "set SKILL_LIVE_TESTS=1 to exercise the live venues")
class JapanTest(unittest.TestCase):
    def test_ten_issuers_list_disclosures_or_say_the_window_is_empty(self):
        chosen = sample(JP_POOL)
        print(f"\n  Japan sample: {', '.join(chosen)}")
        for code_ in chosen:
            with self.subTest(code=code_):
                rc, out, err = run_script("fetch_jp_filings.py", code_,
                                          "--days", "7", "--limit", "3")
                assert_no_traceback(self, code_, out, err)
                if rc != 0:
                    self.skipTest(f"{code_}: TDnet unavailable -- {(err or out).strip()[:120]}")
                # TDnet keeps ~1 month, so an empty 7-day window is normal --
                # but it must be stated, not implied by silence.
                self.assertTrue(
                    "tdnet.info" in out or "No TDnet disclosures" in out,
                    f"{code_}: unexpected output {out[:200]}")


@unittest.skipUnless(LIVE, "set SKILL_LIVE_TESTS=1 to exercise the live venues")
class LondonTest(unittest.TestCase):
    def test_ten_issuers_either_list_documents_or_report_the_retired_index(self):
        chosen = sample(UK_POOL)
        print(f"\n  London sample: {', '.join(chosen)}")
        for company in chosen:
            with self.subTest(company=company):
                rc, out, err = run_script("fetch_uk_filings.py", company,
                                          "--limit", "2")
                assert_no_traceback(self, company, out, err)
                if rc != 0:
                    blocked = environment_block(out, err)
                    if blocked:
                        self.skipTest(f"{company}: api.data.fca.org.uk "
                                      f"unreachable here ({blocked})")
                    # The FCA retired the search index this script queries;
                    # it must be named as an upstream change, not a blip.
                    self.assertIn("no longer accepts", err + out)
                    self.assertIn("nationalstoragemechanism", err + out)
                    continue
                self.assertNotIn("No matching NSM documents", out,
                                 f"{company}: exited 0 with nothing listed")
                self.assertIn("data.fca.org.uk/artefacts", out,
                              f"{company}: no document URL in {out[:200]}")


@unittest.skipUnless(LIVE, "set SKILL_LIVE_TESTS=1 to exercise the live venues")
class HongKongTest(unittest.TestCase):
    def test_ten_issuers_resolve_to_a_stock_code(self):
        # HKEX has no clean fetch API, so the programmatic step is the name
        # lookup; the plain equity should be the first row.
        chosen = sample(HK_POOL)
        print(f"\n  Hong Kong sample: {', '.join(chosen)}")
        for name in chosen:
            with self.subTest(company=name):
                rc, out, err = run_script("resolve_name.py", name,
                                          "--venues", "hk")
                assert_no_traceback(self, name, out, err)
                if rc != 0:
                    self.skipTest(f"{name}: HKEX unavailable")
                rows = [ln.split() for ln in out.splitlines() if ln.startswith("hk ")]
                if not rows or rows[0][1] == "ERROR":
                    self.skipTest(f"{name}: no HK candidates -- {out.strip()[:120]}")
                self.assertRegex(rows[0][1], r"^\d{5}\.HK$",
                                 f"{name}: first row is not a stock code")


@unittest.skipUnless(LIVE, "set SKILL_LIVE_TESTS=1 to exercise the live venues")
class SingaporeTest(unittest.TestCase):
    """SGX is scriptable either side of discovery, so both halves are tested:
    resolving an issuer, and reading a real announcement's documents."""

    @classmethod
    def setUpClass(cls):
        import fetch_sg_filings
        cls.sg = fetch_sg_filings
        try:
            directory = fetch_sg_filings.load_directory()
        except Exception as exc:                      # noqa: BLE001
            raise unittest.SkipTest(f"SGX directory unavailable: {exc}") from exc
        cls.issuers = [r for r in directory if fetch_sg_filings.is_issuer(r)]
        if len(cls.issuers) < SAMPLE_SIZE:
            raise unittest.SkipTest(
                f"only {len(cls.issuers)} reporting issuers in the directory")

    def test_the_directory_covers_every_kind_of_reporting_issuer(self):
        # /securities/v1.1/stocks would satisfy "stocks" alone while
        # silently dropping the REITs and trusts, which is the mistake
        # this venue's fetcher exists to avoid.
        kinds = {row["type"] for row in self.issuers}
        for kind in ("stocks", "reits", "businesstrusts"):
            with self.subTest(kind=kind):
                self.assertIn(kind, kinds)

    def test_ten_trading_codes_resolve_to_their_own_issuer(self):
        chosen = sample([r["code"] for r in self.issuers])
        print(f"\n  Singapore code sample: {', '.join(chosen)}")
        for code in chosen:
            with self.subTest(code=code):
                rc, out, err = run_script("fetch_sg_filings.py", code)
                assert_no_traceback(self, code, out, err)
                blocked = environment_block(out, err)
                if blocked:
                    self.skipTest(f"{code}: {blocked}")
                self.assertEqual(rc, 0, f"{code}: exited {rc}\n{err[-400:]}")
                first = out.splitlines()[0]
                self.assertTrue(first.startswith(code),
                                f"{code}: first row is {first!r}")
                self.assertNotIn("(not a reporting issuer)", first)
                # The manual step has to be stated, not glossed over.
                self.assertIn("company-announcements", out)

    def test_ten_suffixed_codes_resolve_the_same_way(self):
        chosen = sample([r["code"] for r in self.issuers])
        print(f"\n  Singapore .SI sample: {', '.join(c + '.SI' for c in chosen)}")
        for code in chosen:
            with self.subTest(code=f"{code}.SI"):
                rc, out, err = run_script("fetch_sg_filings.py", f"{code}.SI")
                assert_no_traceback(self, code, out, err)
                blocked = environment_block(out, err)
                if blocked:
                    self.skipTest(f"{code}.SI: {blocked}")
                self.assertEqual(rc, 0, f"{code}.SI: exited {rc}")
                self.assertTrue(out.splitlines()[0].startswith(code))

    def test_ten_issuer_names_resolve_to_a_code(self):
        chosen = sample(self.issuers)
        print(f"\n  Singapore name sample: "
              f"{', '.join(r['name'] for r in chosen)}")
        for row in chosen:
            with self.subTest(company=row["name"]):
                rc, out, err = run_script("fetch_sg_filings.py", row["name"])
                assert_no_traceback(self, row["name"], out, err)
                blocked = environment_block(out, err)
                if blocked:
                    self.skipTest(f"{row['name']}: {blocked}")
                self.assertEqual(rc, 0, f"{row['name']}: exited {rc}")
                self.assertNotIn("No SGX-listed security matches", out)
                codes = [ln.split()[0] for ln in out.splitlines()
                         if ln[:1].isalnum()]
                self.assertIn(row["code"], codes,
                              f"{row['name']} did not surface {row['code']}")

    def test_a_nonexistent_code_says_so_instead_of_guessing(self):
        rc, out, err = run_script("fetch_sg_filings.py", "ZZ99")
        assert_no_traceback(self, "ZZ99", out, err)
        if environment_block(out, err):
            self.skipTest("SGX unreachable")
        self.assertEqual(rc, 0)
        self.assertIn("No SGX-listed security matches", out)

    def test_a_real_announcement_yields_its_metadata_and_every_document(self):
        # The half that is scripted past discovery. Asserting the count is
        # the point: taking only the first attachment would drop DBS's
        # Letter to Shareholders and still look like a success.
        rc, out, err = run_script("fetch_sg_filings.py", "D05",
                                  "--announcement", SG_ANNOUNCEMENT)
        assert_no_traceback(self, SG_ANNOUNCEMENT, out, err)
        blocked = environment_block(out, err)
        if blocked:
            self.skipTest(f"SGXNet: {blocked}")
        self.assertEqual(rc, 0, f"exited {rc}\n{err[-400:]}")
        for expected in ("DBS GROUP HOLDINGS LTD", "Annual Report",
                         "31/12/2025", "877753"):
            with self.subTest(expected=expected):
                self.assertIn(expected, out)
        self.assertNotIn("WARNING", out, "issuer wrongly reported as a mismatch")
        listed = [ln for ln in out.splitlines()
                  if ln.strip().startswith(("877753", "877754"))]
        self.assertEqual(len(listed), 2, f"expected 2 attachments:\n{out}")

    def test_a_pasted_id_from_another_issuer_is_flagged(self):
        # SoftBank Group lists only debt on SGX, so its announcement has
        # no trading code at all -- the case that once filed its annual
        # report under the code on the command line.
        rc, out, err = run_script("fetch_sg_filings.py", "O39",
                                  "--announcement", "3EFY7OL9UR6RG9PA")
        assert_no_traceback(self, "3EFY7OL9UR6RG9PA", out, err)
        blocked = environment_block(out, err)
        if blocked:
            self.skipTest(f"SGXNet: {blocked}")
        self.assertEqual(rc, 0)
        self.assertIn("SOFTBANK GROUP CORP.", out)
        self.assertIn("WARNING", out)

    def test_a_malformed_announcement_id_is_refused_readably(self):
        rc, out, err = run_script("fetch_sg_filings.py", "D05",
                                  "--announcement", "not-an-id")
        assert_no_traceback(self, "not-an-id", out, err)
        self.assertIn("16", out + err)


class FrankfurtTest(unittest.TestCase):
    """Germany is a documented browse/IR workflow, not a script."""

    def test_every_german_ticker_classifies_to_frankfurt(self):
        # No sampling: this is pure offline classification, so the default
        # suite must check the same set every run.
        import identify_venue
        for ticker in DE_POOL:
            with self.subTest(ticker=ticker):
                self.assertEqual(identify_venue.identify(ticker)["venue"],
                                 "frankfurt")

    def test_the_manual_route_is_documented(self):
        reference = (ROOT / "references" / "frankfurt.md").read_text(encoding="utf-8")
        self.assertTrue(reference.strip(), "frankfurt.md is empty")
        for expected in ("unternehmensregister", "bundesanzeiger"):
            with self.subTest(expected=expected):
                self.assertIn(expected, reference.lower())

    def test_no_script_claims_to_cover_germany(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        row = [ln for ln in skill.splitlines()
               if "Frankfurt" in ln and "|" in ln]
        self.assertTrue(row, "SKILL.md has no Frankfurt row in the venue table")
        self.assertNotIn(".py", row[0],
                         "the venue table implies a German script that does not exist")


if __name__ == "__main__":
    unittest.main()

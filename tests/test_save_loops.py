#!/usr/bin/env python3
"""
Regression tests for the Japan, London and China save loops.

Each of these was a defect found in review and reproduced first: a refusal
page written to disk under a .pdf name, two same-day documents silently
overwriting one another, filenames that changed whenever a newer filing
appeared, and "today" taken from the wrong time zone.
"""
from __future__ import annotations

import contextlib
import datetime
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_cn_filings as cn  # noqa: E402
import fetch_jp_filings as jp  # noqa: E402
import fetch_uk_filings as uk  # noqa: E402
from net_errors import HostRefused  # noqa: E402

PDF_A = b"%PDF-1.4\nfirst document\n"
PDF_B = b"%PDF-1.4\nsecond document\n"


class FakeResponse(io.BytesIO):
    headers = {"Content-Type": "application/pdf"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


class TokyoDateTest(unittest.TestCase):
    def test_it_is_already_tomorrow_in_tokyo_on_a_new_york_evening(self):
        new_york = datetime.timezone(datetime.timedelta(hours=-4))
        evening = datetime.datetime(2026, 10, 9, 20, 0, tzinfo=new_york)
        self.assertEqual(evening.date(), datetime.date(2026, 10, 9))
        self.assertEqual(jp.tokyo_today(evening), datetime.date(2026, 10, 10))

    def test_the_utc_midnight_boundary_is_nine_hours_early_in_tokyo(self):
        utc = datetime.timezone.utc
        self.assertEqual(jp.tokyo_today(datetime.datetime(2026, 10, 9, 14, 59, tzinfo=utc)),
                         datetime.date(2026, 10, 9))
        self.assertEqual(jp.tokyo_today(datetime.datetime(2026, 10, 9, 15, 0, tzinfo=utc)),
                         datetime.date(2026, 10, 10))

    def test_main_asks_tdnet_for_the_tokyo_date(self):
        asked = []
        with mock.patch.object(jp, "tokyo_today", return_value=datetime.date(2026, 10, 9)), \
                mock.patch.object(jp, "day_rows",
                                  side_effect=lambda d: asked.append(d) or []), \
                mock.patch("sys.argv", ["fetch_jp_filings.py", "7203", "--days", "1"]), \
                quiet():
            jp.main()
        self.assertEqual(asked, [datetime.date(2026, 10, 9)])  # a Friday


class TdnetSaveTest(unittest.TestCase):
    ROWS = [("15:30", "72030", "Toyota", "140120261009000001.pdf", "Notice"),
            ("15:31", "72030", "Toyota", "140120261009000002.pdf", "Notice")]

    def _run(self, tmp, get):
        with mock.patch.object(jp, "tokyo_today", return_value=datetime.date(2026, 10, 9)), \
                mock.patch.object(jp, "day_rows", return_value=self.ROWS), \
                mock.patch.object(jp, "_get", get), \
                mock.patch("sys.argv", ["fetch_jp_filings.py", "7203", "--days", "1",
                                        "--save-dir", tmp]), \
                quiet():
            jp.main()
        return sorted(os.listdir(tmp))

    def test_two_same_day_disclosures_with_one_title_are_both_kept(self):
        bodies = {"140120261009000001.pdf": PDF_A, "140120261009000002.pdf": PDF_B}
        with tempfile.TemporaryDirectory() as tmp:
            names = self._run(tmp, lambda url: bodies[url.rsplit("/", 1)[1]])
            self.assertEqual(len(names), 2, names)
            contents = {(Path(tmp) / n).read_bytes() for n in names}
        self.assertEqual(contents, {PDF_A, PDF_B})

    def test_a_block_page_is_refused_not_saved_as_the_filing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(HostRefused) as caught:
                self._run(tmp, lambda url: b"<html>Access denied</html>")
            self.assertEqual(os.listdir(tmp), [])
        self.assertIn("not a PDF", str(caught.exception))

    def test_a_removed_document_is_reported_and_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, lambda url: None), [])


class EdinetAnswerTest(unittest.TestCase):
    def _edinet(self, answer):
        with mock.patch.dict(os.environ, {"EDINET_API_KEY": "k"}), \
                mock.patch.object(jp, "_get", return_value=answer), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            jp.edinet("7203", "2026-06-24", None)
        return out.getvalue()

    def test_a_404_is_a_plain_message_not_an_attribute_error(self):
        self.assertIn("no document list", self._edinet(None))

    def test_a_non_json_answer_is_a_refusal_not_a_json_traceback(self):
        with self.assertRaises(HostRefused) as caught:
            self._edinet(b"<html>blocked</html>")
        self.assertIn("EDINET_API_KEY", str(caught.exception))


class LondonSaveTest(unittest.TestCase):
    def test_same_day_notices_with_one_headline_are_both_kept(self):
        hits = [{"company": "AstraZeneca PLC", "headline": "Holding(s) in Company",
                 "publication_date": "2026-10-09T08:00:00Z",
                 "download_link": f"artefacts/{n}.pdf"} for n in ("aaa111", "bbb222")]
        bodies = iter([PDF_A, PDF_B])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(uk, "search", return_value=hits), \
                mock.patch("urllib.request.urlopen",
                           side_effect=lambda *a, **k: FakeResponse(next(bodies))), \
                mock.patch("sys.argv", ["fetch_uk_filings.py", "AstraZeneca",
                                        "--save-dir", tmp]), \
                quiet():
            uk.main()
            names = sorted(os.listdir(tmp))
            contents = {(Path(tmp) / n).read_bytes() for n in names}
        self.assertEqual(len(names), 2, names)
        self.assertEqual(contents, {PDF_A, PDF_B})


class ChinaSaveTest(unittest.TestCase):
    MILLIS = 1775000000000  # 2026-04-01 in China time

    def test_the_announcement_date_is_read_in_china_time(self):
        self.assertEqual(cn.announcement_date(self.MILLIS), "2026-04-01")
        self.assertEqual(cn.announcement_date(str(self.MILLIS)), "2026-04-01")

    def test_a_missing_or_unreadable_time_just_drops_the_date(self):
        for bad in (None, "", "soon", float("nan"), 10 ** 30):
            with self.subTest(value=bad):
                self.assertEqual(cn.announcement_date(bad), "")

    def _save(self, rows):
        bodies = iter([PDF_A, PDF_B, PDF_A])
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(cn, "fetch", return_value=rows), \
                mock.patch("urllib.request.urlopen",
                           side_effect=lambda *a, **k: FakeResponse(next(bodies))), \
                mock.patch("sys.argv", ["fetch_cn_filings.py", "600519",
                                        "--save-dir", tmp]), \
                quiet():
            cn.main()
            return sorted(os.listdir(tmp))

    def test_files_are_named_for_the_date_not_for_their_rank(self):
        row = {"title": "2025年年度报告", "time_ms": self.MILLIS,
               "url": "http://static.cninfo.com.cn/finalpage/2026-04-01/1.PDF"}
        self.assertEqual(self._save([row]), ["600519_2025年年度报告_2026-04-01.pdf"])
        # A newer filing arriving first must not rename this one.
        newer = dict(row, title="临时公告", time_ms=self.MILLIS + 86_400_000,
                     url="http://static.cninfo.com.cn/finalpage/2026-04-02/2.PDF")
        self.assertIn("600519_2025年年度报告_2026-04-01.pdf", self._save([newer, row]))

    def test_two_announcements_with_one_title_and_day_are_both_kept(self):
        rows = [{"title": "更正公告", "time_ms": self.MILLIS,
                 "url": f"http://static.cninfo.com.cn/finalpage/2026-04-01/{n}.PDF"}
                for n in (1, 2)]
        names = self._save(rows)
        self.assertEqual(len(names), 2, names)


if __name__ == "__main__":
    unittest.main()

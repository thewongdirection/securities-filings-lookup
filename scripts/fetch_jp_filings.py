#!/usr/bin/env python3
"""
Fetch recent disclosures for a Tokyo-listed company from TDnet
(www.release.tdnet.info), the TSE's timely-disclosure network. This is
the keyless path for Japan: it covers earnings reports (決算短信),
dividend/buyback notices, and other timely disclosures, all as native
PDFs.

Note what TDnet is NOT: the annual securities report (有価証券報告書)
is filed on EDINET, whose API requires a free subscription key -- see
references/japan.md. For most requests ("Toyota's latest results"),
TDnet has the document people actually want.

Requires real internet access to www.release.tdnet.info. Uses certifi
when installed.

Usage:
    python fetch_jp_filings.py 7203                    # Toyota, last 7 days
    python fetch_jp_filings.py 7203 --days 30
    python fetch_jp_filings.py 7203 --grep 決算        # earnings only
    python fetch_jp_filings.py 7203 --days 30 --save-dir ./filings

TDnet only keeps roughly the last month online; older documents need
EDINET or the company's IR site.
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

from pdf_utils import save_pdf_bytes

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://www.release.tdnet.info/inbs/"
USER_AGENT = "Mozilla/5.0 (securities-filings-lookup-skill)"

ROW_RE = re.compile(
    r"<td[^>]*kjTime[^>]*>([^<]+)</td>\s*<td[^>]*kjCode[^>]*>([^<]+)</td>\s*"
    r"<td[^>]*kjName[^>]*>([^<]+)</td>\s*<td[^>]*kjTitle[^>]*>\s*"
    r"<a href=\"([^\"]+)\"[^>]*>([^<]+)</a>")


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


CTX = _context()


def _get(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # no disclosures that day / no such page
        raise


def day_rows(date: datetime.date) -> list[tuple[str, str, str, str, str]]:
    rows = []
    for page in range(1, 30):
        body = _get(f"{BASE}I_list_{page:03d}_{date:%Y%m%d}.html")
        if body is None:
            break
        text = body.decode("utf-8", errors="replace")
        page_rows = ROW_RE.findall(text)
        rows.extend(page_rows)
        if not page_rows:
            break
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("code", help="4-digit Tokyo stock code, e.g. 7203")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--grep", help="Substring filter on the title")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--save-dir", help="If set, download matching PDFs here")
    args = parser.parse_args()

    hits = []
    today = datetime.date.today()
    for delta in range(args.days):
        date = today - datetime.timedelta(days=delta)
        if date.weekday() >= 5:
            continue
        for t, code, name, link, title in day_rows(date):
            if not code.strip().startswith(args.code):
                continue
            if args.grep and args.grep not in title:
                continue
            hits.append((date, t.strip(), code.strip(), name.strip(),
                         title.strip(), link.strip()))
            if len(hits) >= args.limit:
                break
        if len(hits) >= args.limit:
            break

    if not hits:
        print(f"No TDnet disclosures for code {args.code} in the last "
              f"{args.days} days. TDnet only keeps ~1 month; for annual "
              f"securities reports use EDINET (see references/japan.md), or "
              f"check the company's IR site.")
        return

    for date, t, code, name, title, link in hits:
        line = f"{date} {t}  {code}  {name}  {title}"
        if not args.save_dir:
            print(f"{line}\n          {BASE}{link}")
            continue
        os.makedirs(args.save_dir, exist_ok=True)
        data = _get(BASE + link)
        if data is None:
            print(f"{line}  -> GONE (removed from TDnet)")
            continue
        safe = "".join(c for c in title if c not in '/\\:*?"<>|')[:70]
        out = os.path.join(args.save_dir, f"{args.code}_{date:%Y-%m-%d}_{safe}.pdf")
        if data[:5] == b"%PDF-":
            saved = save_pdf_bytes(data, out)
        else:
            with open(out, "wb") as f:
                f.write(data)
            saved = out
        print(f"{line}  -> {saved}")


if __name__ == "__main__":
    main()

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

EDINET mode (statutory filings, e.g. the annual securities report
有価証券報告書) requires a free API key from https://api.edinet-fsa.go.jp
set as the EDINET_API_KEY environment variable:

    python fetch_jp_filings.py 7203 --edinet-date 2026-06-24
    python fetch_jp_filings.py 7203 --edinet-date 2026-06-24 --save-dir ./filings

It lists that date's EDINET filings for the company (annual securities
reports cluster ~3 months after fiscal year end, so late June for the
typical March year-end) and downloads the PDF rendition by docID.
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


def edinet(code: str, date: str, save_dir: str | None) -> None:
    """List (and optionally save) a date's EDINET filings for the company.
    Requires EDINET_API_KEY; keyless calls return 401 (verified live)."""
    import json
    key = os.environ.get("EDINET_API_KEY")
    if not key:
        print("EDINET requires a free API key: register at "
              "https://api.edinet-fsa.go.jp, then set EDINET_API_KEY. "
              "Without it, use the EDINET web UI: "
              "https://disclosure2.edinet-fsa.go.jp")
        return
    url = (f"https://api.edinet-fsa.go.jp/api/v2/documents.json"
           f"?date={date}&type=2&Subscription-Key={key}")
    data = _get(url)
    docs = json.loads(data.decode()).get("results") or []
    sec5 = code + "0"  # EDINET secCode is the 5-digit form
    hits = [d for d in docs if (d.get("secCode") or "").startswith(code)
            or (d.get("secCode") or "") == sec5]
    if not hits:
        print(f"No EDINET filings for {code} on {date}. Annual securities "
              f"reports cluster ~3 months after fiscal year end; try nearby "
              f"business days or the EDINET web UI.")
        return
    for d in hits:
        line = f"{date}  {d.get('docID')}  {d.get('filerName')}  {d.get('docDescription')}"
        if not save_dir:
            print(line)
            continue
        os.makedirs(save_dir, exist_ok=True)
        pdf = _get(f"https://api.edinet-fsa.go.jp/api/v2/documents/"
                   f"{d['docID']}?type=2&Subscription-Key={key}")
        out = os.path.join(save_dir, f"{code}_{date}_{d['docID']}.pdf")
        if pdf and pdf[:5] == b"%PDF-":
            save_pdf_bytes(pdf, out)
            print(f"{line}  -> {out}")
        else:
            print(f"{line}  -> unexpected content (not PDF); check the doc "
                  f"type on the EDINET web UI")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("code", help="4-digit Tokyo stock code, e.g. 7203")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--grep", help="Substring filter on the title")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--save-dir", help="If set, download matching PDFs here")
    parser.add_argument("--edinet-date",
                        help="Query EDINET (needs EDINET_API_KEY) for this "
                             "date's statutory filings instead of TDnet")
    args = parser.parse_args()

    if args.edinet_date:
        edinet(args.code, args.edinet_date, args.save_dir)
        return

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

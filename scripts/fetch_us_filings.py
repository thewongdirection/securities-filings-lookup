#!/usr/bin/env python3
"""
Fetch recent SEC EDGAR filings for a US ticker.

Requires real internet access to sec.gov / data.sec.gov -- works in
Claude Code / Claude Desktop. The claude.ai sandbox's bash tool does
NOT have these domains allowlisted; use the web_search + web_fetch
tools and references/us-edgar.md's environment note instead in that
case.

--save-dir renders each filing with a real headless browser (see
pdf_utils.py) rather than reconstructing its content, so it also
requires, one-time:
    pip install playwright
    playwright install chromium

Usage:
    python fetch_us_filings.py AAPL
    python fetch_us_filings.py AAOI --forms 10-K,10-Q --limit 10
    python fetch_us_filings.py AAOI --forms 10-Q --limit 1 --save-dir ./filings
"""
from __future__ import annotations

import argparse
import json
import os
import time
import gzip
import urllib.request

from pdf_utils import is_pdf_bytes, save_pdf_bytes, render_url_to_pdf

# SEC's fair-access policy wants a descriptive User-Agent identifying
# the requester. Customize this before heavy/repeated use.
USER_AGENT = "securities-filings-lookup-skill contact@example.com"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


def _get(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data


def _get_json(url: str) -> dict:
    return json.loads(_get(url).decode())


def resolve_cik(ticker: str) -> tuple[int, str]:
    # The full ticker->CIK mapping is a large file that changes rarely;
    # re-downloading it every invocation both wastes time and trips
    # SEC's rate limiting (observed live as HTTP 429) after repeated
    # runs. Cache it for a day.
    import tempfile
    cache = os.path.join(tempfile.gettempdir(), "sec_company_tickers.json")
    data = None
    try:
        if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 86400:
            with open(cache, encoding="utf-8") as f:
                data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = None
    if data is None:
        raw = _get(TICKERS_URL)
        data = json.loads(raw.decode())
        try:
            with open(cache, "wb") as f:
                f.write(raw)
        except OSError:
            pass
    ticker = ticker.upper()
    for entry in data.values():
        if entry["ticker"].upper() == ticker:
            return entry["cik_str"], entry["title"]
    raise SystemExit(
        f"No CIK found for ticker '{ticker}' in SEC's company_tickers.json. "
        "It may be very recently listed, a fund, or a SPAC -- try EDGAR full "
        "text search by company name instead: https://www.sec.gov/edgar/search/"
    )


def fetch_filings(cik: int, forms: list[str] | None, limit: int) -> list[dict]:
    time.sleep(0.15)  # stay well under the 10 req/sec fair-access limit
    data = _get_json(SUBMISSIONS_URL.format(cik=cik))
    recent = data["filings"]["recent"]
    n = len(recent["form"])
    periods = recent.get("reportDate", [None] * n)
    descriptions = recent.get("primaryDocDescription", [""] * n)

    rows = []
    for i in range(n):
        form = recent["form"][i]
        if forms and form not in forms:
            continue
        accession = recent["accessionNumber"][i].replace("-", "")
        doc = recent["primaryDocument"][i]
        rows.append({
            "form": form,
            "filed": recent["filingDate"][i],
            "period": periods[i],
            "description": descriptions[i],
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}",
        })
        if len(rows) >= limit:
            break
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker")
    parser.add_argument("--forms", help="Comma-separated form types, e.g. 10-K,10-Q")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--save-dir", help="If set, download each filing and save it as a PDF here")
    args = parser.parse_args()

    forms = [f.strip().upper() for f in args.forms.split(",")] if args.forms else None

    cik, name = resolve_cik(args.ticker)
    print(f"{name} (CIK {cik:010d})\n")

    rows = fetch_filings(cik, forms, args.limit)
    if not rows:
        print("No matching filings found.")
        return

    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)
        for r in rows:
            time.sleep(0.15)
            base = f"{r['filed']}_{r['form'].replace('/', '-')}"
            out_path = os.path.join(args.save_dir, base + ".pdf")
            data = _get(r["url"])
            if is_pdf_bytes(data):
                saved = save_pdf_bytes(data, out_path)
            else:
                saved = render_url_to_pdf(r["url"], out_path, user_agent=USER_AGENT)
            print(f"{r['filed']}  {r['form']:<10}  -> {saved}")
        return

    for r in rows:
        period = f" (period {r['period']})" if r["period"] else ""
        print(f"{r['filed']}  {r['form']:<10}{period}  {r['url']}")


if __name__ == "__main__":
    main()

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
    python fetch_us_filings.py IBM --forms 10-K --limit 1 --save-dir ./filings
        # also saves EX-13, where the annual report's substance lives
    python fetch_us_filings.py IBM --forms 10-K --save-dir ./filings --no-exhibits
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import gzip
import urllib.error
import urllib.request

from pdf_utils import is_pdf_bytes, save_pdf_bytes, render_url_to_pdf
from net_errors import run

# SEC's fair-access policy wants a descriptive User-Agent identifying
# the requester. Customize this before heavy/repeated use.
USER_AGENT = "securities-filings-lookup-skill contact@example.com"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
INDEX_URL = ("https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"
             "{accession_dashed}-index.htm")

# Filers may put the substance of an annual report in an exhibit and
# incorporate it into the 10-K by reference -- IBM's 10-K form is ~30
# pages of cross-references while EX-13 carries the MD&A, the
# consolidated statements and the audit report. Saving only
# primaryDocument silently hands back the wrapper, so EX-13 is fetched
# alongside it by default.
DEFAULT_EXHIBITS = "EX-13"


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

    def _load_cache() -> dict | None:
        try:
            if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 86400:
                with open(cache, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def _refresh() -> dict:
        raw = _get(TICKERS_URL)
        data = json.loads(raw.decode())
        try:
            with open(cache, "wb") as f:
                f.write(raw)
        except OSError:
            pass
        return data

    def _lookup(data: dict, ticker: str):
        for entry in data.values():
            if entry["ticker"].upper() == ticker:
                return entry["cik_str"], entry["title"]
        return None

    ticker = ticker.upper()
    data = _load_cache()
    from_cache = data is not None
    if data is None:
        data = _refresh()
    found = _lookup(data, ticker)
    if found is None and from_cache:
        # A stale or partial cache must not turn into a false "no such
        # ticker" -- re-download the real mapping before giving up.
        found = _lookup(_refresh(), ticker)
    if found:
        return found
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
        accession_dashed = recent["accessionNumber"][i]
        accession = accession_dashed.replace("-", "")
        doc = recent["primaryDocument"][i]
        rows.append({
            "form": form,
            "filed": recent["filingDate"][i],
            "period": periods[i],
            "description": descriptions[i],
            "cik": cik,
            "accession": accession,
            "accession_dashed": accession_dashed,
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}",
        })
        if len(rows) >= limit:
            break
    return rows


ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
# Inline-XBRL documents are linked through EDGAR's viewer
# (/ix?doc=/Archives/...), which is a JavaScript app, not the filing.
# Capture the /Archives path itself so the raw document gets rendered.
DOC_LINK_RE = re.compile(r'href="[^"]*?(/Archives/[^"?#]+)"', re.I)
TAG_RE = re.compile(r"<[^>]+>")


def _cell_text(cell: str) -> str:
    text = TAG_RE.sub(" ", cell).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_index_documents(html: str) -> list[dict]:
    """Documents in an accession, from EDGAR's filing-index page.

    The table is Seq | Description | Document | Type | Size. Description
    is free text (it is often but not always the form type), so the type
    column is what selects an exhibit, and the document's own link is
    what gives its URL -- the cell text carries "iXBRL" markers too.
    """
    docs = []
    for row in ROW_RE.findall(html):
        cells = CELL_RE.findall(row)
        if len(cells) < 5:
            continue
        link = DOC_LINK_RE.search(cells[2])
        if not link:
            continue
        href = link.group(1)
        docs.append({
            "seq": _cell_text(cells[0]),
            "description": _cell_text(cells[1]),
            "document": href.rsplit("/", 1)[-1],
            "type": _cell_text(cells[3]),
            "size": _cell_text(cells[4]),
            "url": href if href.startswith("http") else "https://www.sec.gov" + href,
        })
    return docs


def select_exhibits(docs: list[dict], wanted: list[str]) -> list[dict]:
    """Documents whose type matches one of the wanted exhibit types.

    Matching is case-insensitive and covers numbered variants, so
    "EX-13" selects EX-13, EX-13.1 and EX-13.2 but never EX-13A-style
    types from a different exhibit family.
    """
    patterns = [re.compile(rf"^{re.escape(w)}(\.\d+)?$", re.I)
                for w in wanted if w]
    return [d for d in docs
            if any(p.match(d["type"]) for p in patterns)]


def fetch_exhibits(row: dict, wanted: list[str]) -> list[dict]:
    """Exhibits to save alongside a filing's primary document.

    A failure here must not cost the filing itself, so an unreachable or
    unparseable index returns nothing and says so.
    """
    if not wanted:
        return []
    url = INDEX_URL.format(cik=row["cik"], accession=row["accession"],
                           accession_dashed=row["accession_dashed"])
    try:
        time.sleep(0.15)
        html = _get(url).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        print(f"    (could not check {row['form']} exhibits: {exc}; "
              f"primary document only)")
        return []
    return select_exhibits(parse_index_documents(html), wanted)


def out_name(ticker: str, form: str, filed: str, exhibit_type: str | None = None) -> str:
    """{TICKER}_{FORM}_{DATE}.pdf, per SKILL.md.

    Without the ticker a folder of filings from several companies is
    unsortable, and two filers of the same form on the same day collide.
    """
    safe_form = form.replace("/", "-")
    if exhibit_type:
        safe_form = f"{safe_form}-{exhibit_type.replace('/', '-')}"
    return f"{ticker.upper()}_{safe_form}_{filed}.pdf"


def save_document(url: str, out_path: str) -> str:
    data = _get(url)
    if is_pdf_bytes(data):
        return save_pdf_bytes(data, out_path)
    return render_url_to_pdf(url, out_path, user_agent=USER_AGENT)


def save_rows(rows: list[dict], ticker: str, save_dir: str,
              exhibits: list[str]) -> list[str]:
    os.makedirs(save_dir, exist_ok=True)
    saved_paths = []
    for r in rows:
        time.sleep(0.15)
        out_path = os.path.join(save_dir, out_name(ticker, r["form"], r["filed"]))
        saved = save_document(r["url"], out_path)
        saved_paths.append(saved)
        print(f"{r['filed']}  {r['form']:<10}  -> {saved}")

        for ex in fetch_exhibits(r, exhibits):
            time.sleep(0.15)
            ex_path = os.path.join(
                save_dir, out_name(ticker, r["form"], r["filed"], ex["type"]))
            saved_ex = save_document(ex["url"], ex_path)
            saved_paths.append(saved_ex)
            print(f"    + {ex['type']:<8} {ex['description'][:40]:<40} -> {saved_ex}")
    return saved_paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker")
    parser.add_argument("--forms", help="Comma-separated form types, e.g. 10-K,10-Q")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--save-dir", help="If set, download each filing and save it as a PDF here")
    parser.add_argument("--exhibits", default=DEFAULT_EXHIBITS,
                        help="Comma-separated exhibit types to save alongside each "
                             f"filing (default: {DEFAULT_EXHIBITS}; the annual report "
                             "some filers incorporate by reference)")
    parser.add_argument("--no-exhibits", action="store_true",
                        help="Save only each filing's primary document")
    args = parser.parse_args()

    forms = [f.strip().upper() for f in args.forms.split(",")] if args.forms else None

    cik, name = resolve_cik(args.ticker)
    print(f"{name} (CIK {cik:010d})\n")

    rows = fetch_filings(cik, forms, args.limit)
    if not rows:
        print("No matching filings found.")
        return

    if args.save_dir:
        exhibits = [] if args.no_exhibits else [
            e.strip() for e in args.exhibits.split(",") if e.strip()]
        save_rows(rows, args.ticker, args.save_dir, exhibits)
        return

    for r in rows:
        period = f" (period {r['period']})" if r["period"] else ""
        print(f"{r['filed']}  {r['form']:<10}{period}  {r['url']}")


if __name__ == "__main__":
    run(main)

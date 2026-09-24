#!/usr/bin/env python3
"""
Fetch recent disclosure announcements for a mainland China A-share
ticker from CNINFO (cninfo.com.cn), the CSRC-designated disclosure
platform for the Shanghai and Shenzhen exchanges.

Requires real internet access to cninfo.com.cn -- works in Claude Code
/ Claude Desktop. The claude.ai sandbox's bash tool does NOT have this
domain allowlisted; use web_search + web_fetch and the manual browse
URL in references/china-a-shares.md instead in that case.

This hits an undocumented endpoint that can change or add anti-bot
friction without notice. If it fails, fall back to the manual browse
URL printed in the "no results" message, or Option B in the reference
doc.

Usage:
    python fetch_cn_filings.py 600519             # Kweichow Moutai
    python fetch_cn_filings.py 000001 --kind annual
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Titles are Chinese; Windows consoles often default to a legacy code
# page that can't encode them.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from naming import filing_name
from net_errors import HostRefused, run
from pdf_utils import save_filing_as_pdf

# CNINFO returns a transient 503 often enough to be worth one retry --
# unlike a 429, where SKILL.md says to wait rather than press on.
RETRY_STATUSES = (502, 503, 504)
RETRY_DELAY_SECONDS = 2


def _retryable(exc: BaseException) -> bool:
    """Is this worth exactly one more attempt?

    A transient 5xx, a dropped connection or a read timeout -- all of which
    CNINFO produces on an undocumented endpoint. Never a 429: SKILL.md says
    wait one out rather than press on.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRY_STATUSES
    return isinstance(exc, (urllib.error.URLError, TimeoutError))


def _post_json(url: str, payload: dict, timeout: int = 15) -> dict:
    """POST a form and read JSON back, retrying once on a transient failure."""
    data = urllib.parse.urlencode(payload).encode()
    headers = {
        "User-Agent": "Mozilla/5.0 (securities-filings-lookup-skill)",
        "Referer": "http://www.cninfo.com.cn/",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    def attempt() -> dict:
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # CNINFO answers anti-bot friction with HTTP 200 and an HTML
            # interstitial, the same shape as TWSE's refusal. Without this
            # the JSON decode error reaches the user as a traceback.
            raise HostRefused(
                f"CNINFO answered {url} with {len(body)} bytes that are not "
                f"JSON (Content-Type: {content_type or 'unknown'}): "
                f"{body[:80]!r}. Its endpoints are undocumented and add "
                "anti-bot friction without notice -- browse "
                "http://www.cninfo.com.cn/new/disclosure/stock instead, or "
                "try again later.") from exc

    try:
        return attempt()
    except Exception as exc:
        if not _retryable(exc):
            raise
        time.sleep(RETRY_DELAY_SECONDS)
        return attempt()

QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
ORGID_URL = "http://www.cninfo.com.cn/new/information/topSearch/detailOfQuery"

KEYWORDS = {
    "annual": ["年度报告"],
    "interim": ["半年度报告"],
    "quarterly": ["季度报告"],
    "prospectus": ["招股说明书", "招股意向书"],
    "all": [],
}

# (code prefix, plate param, column param)
EXCHANGE_BY_PREFIX = [
    ("688", "sh", "sse"),
    ("6", "sh", "sse"),
    ("300", "sz", "szse"),
    ("301", "sz", "szse"),
    ("0", "sz", "szse"),
    ("2", "sz", "szse"),
    ("3", "sz", "szse"),
]


def classify_exchange(code: str) -> tuple[str, str]:
    for prefix, plate, column in EXCHANGE_BY_PREFIX:
        if code.startswith(prefix):
            return plate, column
    raise SystemExit(
        f"Could not map code '{code}' to Shanghai/Shenzhen by prefix -- it "
        "may be a Beijing Stock Exchange listing; check manually."
    )


def resolve_org_id(code: str) -> str | None:
    """The query endpoint silently returns zero results unless 'stock'
    is passed as 'code,orgId' -- a bare code used to work but no longer
    does. Resolve the orgId via CNINFO's search endpoint."""
    result = _post_json(ORGID_URL,
                        {"keyWord": code, "maxSecNum": "10", "maxListNum": "5"})
    for entry in result.get("keyBoardList") or []:
        if entry.get("code") == code:
            return entry.get("orgId")
    return None


def fetch(code: str, kind: str, limit: int) -> list[dict]:
    plate, column = classify_exchange(code)
    org_id = resolve_org_id(code)
    # Pass the kind's keyword to the endpoint's own title search rather
    # than only filtering the most recent page locally -- busy filers
    # push their annual report out of the recent-50 window within weeks.
    keywords = KEYWORDS.get(kind, [])
    payload = {
        "pageNum": "1",
        "pageSize": "50",
        "tabName": "fulltext",
        "column": column,
        "stock": f"{code},{org_id}" if org_id else code,
        "searchkey": keywords[0] if keywords else "",
        "secid": "",
        "plate": plate,
        "category": "",
        "trade": "",
        "seDate": "",
    }
    result = _post_json(QUERY_URL, payload)

    rows = []
    for a in result.get("announcements") or []:
        title = a.get("announcementTitle", "")
        if keywords and not any(k in title for k in keywords):
            continue
        # "年度报告" is a substring of "半年度报告", so interim reports
        # would otherwise leak into --kind annual results.
        if kind == "annual" and "半年度报告" in title:
            continue
        adjunct = a.get("adjunctUrl", "")
        rows.append({
            "title": title,
            "time_ms": a.get("announcementTime"),
            "url": f"http://static.cninfo.com.cn/{adjunct}" if adjunct else None,
        })
        if len(rows) >= limit:
            break
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("code", help="6-digit A-share code, e.g. 600519")
    parser.add_argument("--kind", choices=list(KEYWORDS), default="all")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--save-dir", help="If set, download each announcement's PDF here")
    args = parser.parse_args()

    rows = fetch(args.code, args.kind, args.limit)
    if not rows:
        plate, _ = classify_exchange(args.code)
        print(
            "No matching announcements found. Try --kind all, or confirm "
            f"manually at http://www.cninfo.com.cn/new/disclosure/stock"
            f"?plate={plate}&stockCode={args.code}"
        )
        return

    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)
        for idx, r in enumerate(rows):
            if not r["url"]:
                print(f"(no file URL) {r['title']}")
                continue
            user_agent = "Mozilla/5.0 (securities-filings-lookup-skill)"
            req = urllib.request.Request(r["url"], headers={
                "User-Agent": user_agent,
                "Referer": "http://www.cninfo.com.cn/",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            out_path = os.path.join(
                args.save_dir,
                filing_name(args.code, r["title"], f"{idx:02d}"))
            # CNINFO documents are already native PDFs, so this is a
            # direct byte save; the real-browser render is a defensive
            # fallback, and it needs the same UA and the bytes we hold.
            saved = save_filing_as_pdf(r["url"], data, out_path, user_agent=user_agent)
            print(f"{r['title']}  -> {saved}")
        return

    for r in rows:
        print(f"{r['title']}  {r['url']}")


if __name__ == "__main__":
    run(main)

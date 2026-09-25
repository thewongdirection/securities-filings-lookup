#!/usr/bin/env python3
"""
Fetch filings for a Taiwan-listed company (TWSE / TPEx) from TWSE's
document server (doc.twse.com.tw) -- the official repository behind
MOPS (Market Observation Post System), Taiwan's designated disclosure
platform.

Requires real internet access to doc.twse.com.tw (works in Claude Code
/ Claude Desktop). TWSE's TLS chain is issued by TWCA, which is missing
from some default trust stores -- if certifi is installed it is used
automatically (pip install certifi).

Usage:
    python fetch_tw_filings.py 2330                       # TSMC, latest annual report (年報)
    python fetch_tw_filings.py 2330 --year 2024           # a specific fiscal year
    python fetch_tw_filings.py 2330 --kind financial      # financial reports instead
    python fetch_tw_filings.py 2330 --save-dir ./filings  # download the PDFs

Notes:
- The server takes the ROC (Minguo) *publication* year; annual reports
  for fiscal year N are published in year N+1, and this script does the
  conversion -- pass the ordinary fiscal year (e.g. 2025).
- kind=annual lists the shareholder-meeting annual report (股東會年報,
  dtype F04); kind=financial lists the audited/reviewed financial
  reports (mtype A). Both are served as native PDFs.
"""
from __future__ import annotations

import argparse
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request

from pdf_utils import save_pdf_bytes
from net_errors import HostRefused, run

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URL = "https://doc.twse.com.tw/server-java/t57sb01"
USER_AGENT = "Mozilla/5.0 (securities-filings-lookup-skill)"


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


CTX = _context()


# TWSE serves a refusal as HTTP 200, so the status code says nothing. The
# English sentence and the Chinese line above it are separate markers: a
# Chinese-only variant would otherwise read as "no filings found" again.
# Taken from the recorded refusal (tests/fixtures/twse_blocked.html): the
# English sentence and the Chinese line, which reads 無法呈現 -- not the
# 無法瀏覽 a guess would use.
BLOCK_MARKERS = ("CAN NOT BE ACCESSED", "無法呈現", "為安全性考量")


def _decode(body: bytes, content_type: str = "") -> str:
    """Text of a TWSE response, honouring the charset it declares.

    Listings come back Big5 and the block page is UTF-8, so decoding
    everything as Big5 turned that message into mojibake -- which is how
    it went unnoticed as "no filings found". email.message does the
    header parsing (quoted forms included); Big5 is the server's default.
    """
    from email.message import Message

    message = Message()
    message["Content-Type"] = content_type or "text/html"
    charset = message.get_content_charset("big5")
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("big5", errors="replace")


def is_blocked(text: str) -> bool:
    """Is this TWSE's refusal page rather than a real response?"""
    upper = text.upper()
    return any(marker.upper() in upper for marker in BLOCK_MARKERS)


def refusal(host: str = "doc.twse.com.tw") -> HostRefused:
    """The same explanation whichever TWSE host served the block page.

    openapi.twse.com.tw serves it too, so the company directory used for
    name resolution fails the same way the document server does -- which
    it used to report as an unreadable-JSON error.
    """
    return HostRefused(
        f"TWSE refused this request: {host} answered HTTP 200 with its "
        "\"FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED\" page. That "
        "is TWSE declining the client -- typically a datacentre or cloud IP "
        "-- not an absence of filings, and headers, a referer and a session "
        "cookie make no difference. Use MOPS in a browser instead: "
        "https://mops.twse.com.tw (English: e-Search > annual reports), or "
        "run the skill from a network TWSE accepts.")


def _check_not_blocked(text: str) -> None:
    if is_blocked(text):
        raise refusal()


def _post(payload: dict) -> str:
    req = urllib.request.Request(
        URL,
        data=urllib.parse.urlencode(payload).encode(),
        headers={"User-Agent": USER_AGENT,
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30, context=CTX) as resp:
        text = _decode(resp.read(), resp.headers.get("Content-Type", ""))
    _check_not_blocked(text)
    return text


def list_files(co_id: str, fiscal_year: int, kind: str) -> list[tuple[str, str, str]]:
    """Return [(kind_char, co_id, filename)] for the fiscal year."""
    roc_publication_year = fiscal_year - 1911 + 1  # AR for FY N is published in N+1
    payload = {"id": "", "key": "", "step": "1", "co_id": co_id,
               "year": str(roc_publication_year), "seamon": "", "mtype": "F", "dtype": "F04"}
    if kind == "financial":
        payload.update({"mtype": "A", "dtype": "",
                        "year": str(fiscal_year - 1911)})  # financial reports carry their own year
    body = _post(payload)
    return re.findall(r'readfile2?\("(\w)","(\d+)","([^"]+)"\)', body)


def download(kind_char: str, co_id: str, filename: str) -> bytes:
    """The two-step flow: step=9 returns a page with a temporary link."""
    body = _post({"step": "9", "kind": kind_char, "co_id": co_id,
                  "filename": filename})
    m = re.search(r"href='(/pdf/[^']+)'", body)
    if not m:
        raise RuntimeError(f"no temporary link in step-9 response for {filename}")
    req = urllib.request.Request("https://doc.twse.com.tw" + m.group(1),
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120, context=CTX) as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type", "")
    if data[:5] != b"%PDF-":
        # A refusal here arrives as HTTP 200 HTML exactly as it does for the
        # listing; writing it to a .pdf would hand back an error page as the
        # annual report.
        _check_not_blocked(_decode(data, content_type))
        raise HostRefused(
            f"TWSE returned {len(data)} bytes that are not a PDF for "
            f"{filename} (Content-Type: {content_type or 'unknown'}). The "
            "temporary download link may have expired -- re-run the listing "
            "step -- or the server declined this client. Nothing was saved.")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("co_id", help="4-digit Taiwan stock code, e.g. 2330")
    parser.add_argument("--year", type=int, help="Fiscal year (default: last year)")
    parser.add_argument("--kind", choices=["annual", "financial"], default="annual")
    parser.add_argument("--save-dir", help="If set, download the PDFs here")
    args = parser.parse_args()

    import datetime
    fiscal = args.year or (datetime.date.today().year - 1)

    files = list_files(args.co_id, fiscal, args.kind)
    if not files and args.kind == "annual" and not args.year:
        fiscal -= 1  # this year's AR may not be published yet
        files = list_files(args.co_id, fiscal, args.kind)
    if not files:
        print(f"No {args.kind} filings found for {args.co_id} FY{fiscal}. "
              f"Confirm manually at https://mops.twse.com.tw (English: select "
              f"'e-Search' / annual reports).")
        return

    for kind_char, co_id, filename in files:
        if not args.save_dir:
            print(f"FY{fiscal}  {filename}")
            continue
        os.makedirs(args.save_dir, exist_ok=True)
        # download() raises unless the bytes really are a PDF, so there is
        # no non-PDF branch here to write an error page to a .pdf path.
        data = download(kind_char, co_id, filename)
        saved = save_pdf_bytes(data, os.path.join(args.save_dir, filename))
        print(f"FY{fiscal}  {filename}  -> {saved}")


if __name__ == "__main__":
    run(main)

#!/usr/bin/env python3
"""
Fetch regulated filings for a London-listed company from the FCA's
National Storage Mechanism (NSM) -- the UK's official repository for
regulated information (data.fca.org.uk/#/nsm/nationalstoragemechanism).

Requires real internet access to api.data.fca.org.uk. Uses certifi's CA
bundle when installed.

Usage:
    python fetch_uk_filings.py "AstraZeneca"                    # recent NSM documents
    python fetch_uk_filings.py "AstraZeneca" --grep annual      # filter titles
    python fetch_uk_filings.py "AstraZeneca" --include-rns      # also list RNS announcement pages
    python fetch_uk_filings.py "AstraZeneca" --limit 5 --save-dir ./filings

Notes:
- The search API is the undocumented Elasticsearch-style endpoint the
  NSM portal itself uses (POST api.data.fca.org.uk/search?index=
  fca-nsm-searchdata). Structured criteria filters silently return zero
  hits, so this script uses keyword search sorted by publication date
  and filters client-side. Headlines contain typos as filed (a real
  example: "2025 Annul Report") -- prefer loose --grep terms.
- Modern UK annual reports are filed as ESEF packages (.zip containing
  the xHTML/iXBRL report), not PDFs. The zip is the official document;
  the glossy PDF usually lives on the company's own IR site. Older
  documents and most circulars/prospectuses are PDFs.
- RNS announcements (the .html entries) are news-service pages, not
  documents; they're hidden unless --include-rns is passed.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.request

from pdf_utils import save_pdf_bytes

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SEARCH_URL = "https://api.data.fca.org.uk/search?index=fca-nsm-searchdata"
ARTEFACT_BASE = "https://data.fca.org.uk/artefacts/"
USER_AGENT = "Mozilla/5.0 (securities-filings-lookup-skill)"


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


CTX = _context()


def search(keyword: str, size: int = 100) -> list[dict]:
    body = {"from": 0, "size": size, "sort": "publication_date",
            "sortorder": "desc", "keyword": keyword,
            "criteriaObj": {"criteria": [], "dateCriteria": []}}
    req = urllib.request.Request(
        SEARCH_URL, data=json.dumps(body).encode(),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as resp:
        j = json.loads(resp.read().decode())
    return [h["_source"] for h in j.get("hits", {}).get("hits", [])]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("company", help="Company name to search, e.g. 'AstraZeneca'")
    parser.add_argument("--grep", help="Case-insensitive substring filter on the headline")
    parser.add_argument("--include-rns", action="store_true",
                        help="Also list RNS announcement pages (.html)")
    parser.add_argument("--limit", type=int, default=15)
    parser.add_argument("--save-dir", help="If set, download each matching document here")
    args = parser.parse_args()

    rows = []
    for s in search(args.company):
        link = s.get("download_link", "")
        if not link:
            continue
        if link.lower().endswith(".html") and not args.include_rns:
            continue
        if args.grep and args.grep.lower() not in s.get("headline", "").lower():
            continue
        rows.append(s)
        if len(rows) >= args.limit:
            break

    if not rows:
        print(f"No matching NSM documents found for '{args.company}'. Try a "
              f"different spelling, --include-rns, or browse "
              f"https://data.fca.org.uk/#/nsm/nationalstoragemechanism")
        return

    for s in rows:
        date = (s.get("publication_date") or "?")[:10]
        line = f"{date}  {s.get('company','?')[:30]:<30}  {s.get('headline','?')}"
        url = ARTEFACT_BASE + s["download_link"]
        if not args.save_dir:
            print(f"{line}\n          {url}")
            continue
        os.makedirs(args.save_dir, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120, context=CTX) as resp:
            data = resp.read()
        safe = "".join(c for c in s.get("headline", "document") if c not in '/\\:*?"<>|')[:70]
        ext = os.path.splitext(s["download_link"])[1] or ".bin"
        out = os.path.join(args.save_dir, f"{date}_{safe}{ext}")
        if data[:5] == b"%PDF-":
            saved = save_pdf_bytes(data, out)
        else:
            with open(out, "wb") as f:
                f.write(data)
            saved = out
        print(f"{line}  -> {saved}")


if __name__ == "__main__":
    main()

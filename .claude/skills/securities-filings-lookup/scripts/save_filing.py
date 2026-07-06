#!/usr/bin/env python3
"""
Download a single filing URL and save it as PDF -- the original
document, not a reconstruction.

If the URL is already a PDF (common for Hong Kong and mainland China
filings), the raw bytes are saved unmodified. If it's HTML (the normal
case for SEC EDGAR primary documents), a real headless browser
(Playwright + Chromium) renders the actual page and prints it to PDF,
matching what you'd get from Chrome's Print > Save as PDF.

Requires real internet access to the filing's host (works in Claude
Code / Claude Desktop). Also requires, one-time:
    pip install playwright
    playwright install chromium

In the claude.ai sandbox, none of this can reach sec.gov / hkexnews.hk
/ cninfo.com.cn -- not via this script, and not via a real browser
either (verified: even headless Chromium hits the same network
allowlist wall as everything else in that sandbox). There is currently
no way in that environment to retrieve a byte-faithful copy of an SEC
HTML filing -- web_fetch always extracts/transforms content, it never
returns raw markup. The honest move there is to hand the person the
direct URL rather than offer a reconstruction as if it were the same
thing. See SKILL.md's environment note.

Usage:
    python save_filing.py <url> [--out path/to/file.pdf]
"""
from __future__ import annotations

import argparse
import os
import urllib.request

from pdf_utils import is_pdf_bytes, render_url_to_pdf, save_pdf_bytes

DEFAULT_UA = "securities-filings-lookup-skill contact@example.com"


def peek(url: str, user_agent: str = DEFAULT_UA) -> bytes:
    """Fetch the URL's bytes -- just enough to tell if it's already a PDF."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def default_out_path(url: str) -> str:
    name = url.rstrip("/").split("/")[-1] or "filing"
    return os.path.splitext(name)[0] + ".pdf"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--out", help="Output path (default: derived from the URL)")
    args = parser.parse_args()

    out_path = args.out or default_out_path(args.url)
    data = peek(args.url)

    if is_pdf_bytes(data):
        saved = save_pdf_bytes(data, out_path)
        print(f"Already a PDF -- saved as-is: {saved}")
    else:
        saved = render_url_to_pdf(args.url, out_path, user_agent=DEFAULT_UA)
        print(f"Rendered the original page with a real browser: {saved}")


if __name__ == "__main__":
    main()

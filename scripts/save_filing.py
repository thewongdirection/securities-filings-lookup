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
import ssl
import urllib.parse
import urllib.request

import sec_identity
from pdf_utils import is_pdf_bytes, save_filing_as_pdf
from net_errors import run

# Only SEC demands a declared contact; the other venues are happier with
# an ordinary client string. Resolved per URL, because this script takes
# any filing URL from any venue.
GENERIC_UA = f"securities-filings-lookup (+{sec_identity.PROJECT_URL})"


def _context() -> ssl.SSLContext:
    # Some issuers' hosts (TWSE's TWCA chain, various IR-site CDNs) fail
    # verification against sparse default trust stores; certifi's Mozilla
    # bundle fixes this. Fall back to the default when not installed.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def is_sec(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == "sec.gov" or host.endswith(".sec.gov")


def user_agent_for(url: str, explicit: str | None = None) -> str:
    """The User-Agent for this URL's host.

    SEC needs a declared contact and raises if none is configured; the
    other venues are happy with an ordinary client string.
    """
    if is_sec(url):
        return sec_identity.resolve(explicit)
    return explicit or GENERIC_UA


def peek(url: str, user_agent: str | None = None) -> bytes:
    """Fetch the URL's bytes -- just enough to tell if it's already a PDF.

    Without an explicit User-Agent the host decides: SEC gets the declared
    contact it requires, everything else the generic client string. The
    old default sent an address-less string to SEC, which returns 403.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": user_agent or user_agent_for(url)})
    with urllib.request.urlopen(req, timeout=60, context=_context()) as resp:
        return resp.read()


def default_out_path(url: str) -> str:
    name = url.rstrip("/").split("/")[-1] or "filing"
    return os.path.splitext(name)[0] + ".pdf"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--out", help="Output path (default: derived from the URL)")
    parser.add_argument("--user-agent",
                        help="Contact for SEC's fair-access policy, e.g. "
                             "'Your Name you@domain.com'")
    args = parser.parse_args()

    out_path = args.out or default_out_path(args.url)
    user_agent = user_agent_for(args.url, args.user_agent)
    data = peek(args.url, user_agent)

    already_pdf = is_pdf_bytes(data)
    # The bytes are already in hand from peek(); handing them over means
    # the host is not asked for the same document a second time.
    saved = save_filing_as_pdf(args.url, data, out_path, user_agent=user_agent)
    if already_pdf:
        print(f"Already a PDF -- saved as-is: {saved}")
    else:
        print(f"Rendered the original page with a real browser: {saved}")


if __name__ == "__main__":
    run(main)

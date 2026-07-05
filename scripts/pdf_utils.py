#!/usr/bin/env python3
"""
Save a filing exactly as it is. No content reconstruction, ever.

Two cases:

  Already a PDF (true for essentially all Hong Kong and mainland China
  filings, and some SEC exhibits) -- the raw bytes are saved unmodified.
  This was always correct and is unchanged.

  HTML (the normal case for SEC EDGAR primary documents) -- a real
  headless browser (Playwright + Chromium) renders the actual page and
  prints it to PDF. This is the same output you'd get opening the page
  in Chrome and choosing Print > Save as PDF: the browser renders the
  real HTML/CSS as authored, so tables, fonts, and layout all come out
  matching the original document, because nothing about the content is
  being reinterpreted or rebuilt.

An earlier version of this module tried to parse the filing's content
(via BeautifulSoup or by parsing web_fetch's markdown-extracted text)
and rebuild a new PDF from scratch with reportlab. That produces a
readable document, but it is fundamentally a reconstruction, not the
original filing -- different layout, different table structure,
different everything except the underlying numbers. That approach has
been removed. If a real browser render isn't available in a given
environment, the honest fallback is to hand back the original URL, not
to silently substitute a reconstruction.

Setup (one-time):
    pip install playwright
    playwright install chromium
"""
from __future__ import annotations


def is_pdf_bytes(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def save_pdf_bytes(data: bytes, out_path: str) -> str:
    """Save already-downloaded PDF bytes verbatim."""
    if not out_path.lower().endswith(".pdf"):
        out_path += ".pdf"
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def render_url_to_pdf(url: str, out_path: str, wait_ms: int = 2000,
                      user_agent: str | None = None) -> str:
    """Render a URL with a real headless browser and print it to PDF.

    This is a faithful browser rendering of the original page, not a
    content reconstruction. Requires network access to the URL's host
    and Playwright + Chromium installed (see module docstring).

    user_agent: SEC's fair-access policy blocks undeclared automated
    tools. Setting the UA string alone is not enough -- SEC's edge
    (Akamai) fingerprints headless Chromium itself and serves a block
    page regardless of UA, while plain urllib requests with a declared
    UA go through fine. So when a user_agent is given, every request
    the browser makes (the page and all its subresources) is
    intercepted and fetched via urllib instead; Chromium only renders.
    """
    import urllib.request

    from playwright.sync_api import sync_playwright

    if not out_path.lower().endswith(".pdf"):
        out_path += ".pdf"

    def _fetch(u: str) -> tuple[bytes, str]:
        req = urllib.request.Request(u, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read(), resp.headers.get("Content-Type", "application/octet-stream")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(user_agent=user_agent) if user_agent else browser.new_page()
            if user_agent:
                def _route(route, request):
                    try:
                        body, ctype = _fetch(request.url)
                        route.fulfill(status=200, body=body, content_type=ctype)
                    except Exception:
                        route.abort()
                page.route("**/*", _route)
            page.goto(url, wait_until="networkidle", timeout=120000)
            page.wait_for_timeout(wait_ms)  # let any late layout/JS settle
            page.pdf(path=out_path, print_background=True)
        finally:
            browser.close()
    return out_path


def save_filing_as_pdf(url: str, data: bytes, out_path: str,
                       user_agent: str | None = None) -> str:
    """Given a URL and its already-downloaded bytes, save the original
    faithfully: raw save if it's already a PDF, real-browser render if
    it's HTML. Never reconstructs content."""
    if is_pdf_bytes(data):
        return save_pdf_bytes(data, out_path)
    return render_url_to_pdf(url, out_path, user_agent=user_agent)

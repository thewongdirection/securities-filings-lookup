#!/usr/bin/env python3
"""
Resolve a Singapore (SGX) issuer and retrieve its SGXNet filings.

Singapore is scriptable everywhere except discovery, and knowing exactly
where the line falls is what keeps this script honest. Measured against
the live service (2026-09):

  api.sgx.com/securities/v1.1                    200, ~1,330 securities
  links.sgx.com/1.0.0/corporate-announcements/<AnncID>/
                                                 200, server-rendered HTML:
                                                 full SGXNet metadata plus
                                                 every attachment and FileID
  links.sgx.com/FileOpen/<name>.ashx?FileID=<id>  200, application/pdf
  api.sgx.com/announcements/... (every route tried)  403/401, no listing
  www.sgx.com/securities/company-announcements   a 15 KB Angular shell

So: the securities directory resolves codes and names, an announcement
page yields its own metadata and documents, and any SGXNet document
downloads byte-for-byte -- but there is no public way to *list* an
issuer's announcements, so the AnncID or document URL has to come from
the browse UI or a web search. This script does every scriptable part
and says plainly which step is manual. It never guesses a FileID:
they are sequential across all issuers, so probing them would be both
rude and wrong.

Usage:
    python fetch_sg_filings.py D05                 # resolve, print the browse URL
    python fetch_sg_filings.py A17U                # REITs and trusts too
    python fetch_sg_filings.py "Singtel"           # by name
    python fetch_sg_filings.py D05 --announcement U6RBLH1JFNDV1QZT \\
        --save-dir ./filings                       # every attachment, with metadata
    python fetch_sg_filings.py C6L --document \\
        "https://links.sgx.com/FileOpen/AR.ashx?App=Announcement&FileID=751647" \\
        --save-dir ./filings
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request

from naming import claim_name, filing_name
from net_errors import HostRefused, SetupError, run
from pdf_utils import save_filing_as_pdf

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# The full directory, not /securities/v1.1/stocks: that one holds the 564
# equities and omits the 37 REITs and 14 business trusts, which in Singapore
# are a large share of the issuers people actually want (CapLand Ascendas,
# Mapletree, Frasers). This returns ~1,330 rows across 14 security types.
DIRECTORY_URL = "https://api.sgx.com/securities/v1.1"
ANNOUNCEMENTS_URL = ("https://www.sgx.com/securities/company-announcements"
                     "?value={code}")
DOCUMENT_HOST = "links.sgx.com"
ANNOUNCEMENT_BASE = f"https://{DOCUMENT_HOST}/1.0.0/corporate-announcements/"
USER_AGENT = "Mozilla/5.0 (securities-filings-lookup-skill)"

CACHE_NAME = "sgx_securities.json"
CACHE_TTL_SECONDS = 86400

# Types that file annual reports, in the order a name search should prefer
# them. Everything else in the directory -- structured warrants, daily
# leverage certificates, company warrants, bonds, preference shares -- is a
# tradable instrument rather than a reporting issuer, and 585 of the
# ~1,330 rows are exactly that noise.
ISSUER_TYPES = ("stocks", "reits", "businesstrusts", "adrs", "etfs")

# SGXNet announcement ids: 16 uppercase alphanumerics, e.g. U6RBLH1JFNDV1QZT.
ANNC_ID_RE = re.compile(r"^[A-Z0-9]{16}$")

# The announcement page's own <dt> labels, mapped to the keys used here.
# "Date &Time of Broadcast" really is spelled without the space.
ANNOUNCEMENT_FIELDS = {
    "Issuer/ Manager": "issuer",
    "Securities": "securities",
    "Announcement Title": "title",
    "Date &Time of Broadcast": "broadcast",
    "Report Type": "report_type",
    "Period Ended": "period_ended",
    "Announcement Reference": "reference",
}

MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}


def _get(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# --------------------------------------------------------------------------
# Issuer directory
# --------------------------------------------------------------------------

def load_directory(refresh: bool = False) -> list[dict]:
    """Every SGX-listed security, cached for a day.

    ~1,330 rows at the time of writing, so re-downloading it per lookup is
    both slow and rude; the SEC fetcher caches its ticker map the same way.
    """
    cache = os.path.join(tempfile.gettempdir(), CACHE_NAME)
    if not refresh:
        try:
            if time.time() - os.path.getmtime(cache) < CACHE_TTL_SECONDS:
                with open(cache, encoding="utf-8") as fh:
                    return parse_directory(json.load(fh))
        except (OSError, ValueError):
            pass
    raw = _get(DIRECTORY_URL)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostRefused(
            f"{DIRECTORY_URL} answered with {len(raw)} bytes that are not "
            f"JSON: {raw[:80]!r}. SGX's edge may be refusing this client -- "
            "browse https://www.sgx.com/securities/company-announcements "
            "instead.") from exc
    try:
        with open(cache, "wb") as fh:
            fh.write(raw)
    except OSError:
        pass
    return parse_directory(payload)


def parse_directory(payload: dict) -> list[dict]:
    """Issuers from the securities payload: code, name, board."""
    rows = ((payload or {}).get("data") or {}).get("prices") or []
    out = []
    for row in rows:
        code = (row.get("nc") or "").strip().upper()
        if not code:
            continue
        out.append({
            "code": code,
            "name": (row.get("n") or row.get("issuer-name") or "").strip(),
            "board": (row.get("m") or "").strip(),
            "type": (row.get("type") or "").strip().lower(),
        })
    return out


def is_issuer(row: dict) -> bool:
    """Does this row name a company that files reports?"""
    return row.get("type") in ISSUER_TYPES


def _rank(row: dict) -> int:
    try:
        return ISSUER_TYPES.index(row.get("type") or "")
    except ValueError:
        return len(ISSUER_TYPES)


def normalise(ticker: str) -> str:
    """'d05.si' -> 'D05'. SGX codes are three or four alphanumerics."""
    text = (ticker or "").strip().upper()
    for suffix in (".SI", ".SGX"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def find(query: str, directory: list[dict]) -> list[dict]:
    """Issuers matching a trading code exactly, or a name loosely.

    Code first: 533 is a real SGX code (ABR) as well as looking like a
    Hong Kong one, so an exact code match must win over any name search.
    """
    wanted = normalise(query)
    exact = [row for row in directory if row["code"] == wanted]
    if exact:
        return exact
    lowered = (query or "").strip().lower()
    if not lowered:
        return []
    # Reporting issuers first: a search for "DBS" should not lead with a
    # structured warrant over the bank itself.
    starts = sorted((r for r in directory if r["name"].lower().startswith(lowered)),
                    key=_rank)
    contains = sorted((r for r in directory if lowered in r["name"].lower()
                       and r not in starts), key=_rank)
    return (starts + contains)[:8]


def announcements_url(code: str) -> str:
    return ANNOUNCEMENTS_URL.format(code=urllib.parse.quote(code))


# --------------------------------------------------------------------------
# Announcements
# --------------------------------------------------------------------------

def announcement_id(reference: str) -> str:
    """An AnncID from either a bare id or a links.sgx.com announcement URL."""
    text = (reference or "").strip()
    if "/" in text:
        parts = [p for p in urllib.parse.urlsplit(text).path.split("/") if p]
        text = next((p for p in reversed(parts) if ANNC_ID_RE.match(p.upper())), "")
    text = text.upper()
    if not ANNC_ID_RE.match(text):
        raise SetupError(
            f"'{reference}' is not an SGXNet announcement id. Those are 16 "
            "characters, as in U6RBLH1JFNDV1QZT, and appear in the URL of an "
            f"announcement on sgx.com: {ANNOUNCEMENT_BASE}<AnncID>/")
    return text


def announcement_url(annc_id: str) -> str:
    return f"{ANNOUNCEMENT_BASE}{annc_id}/"


def _text(fragment: str) -> str:
    """Tag-stripped, whitespace-collapsed text from an HTML fragment."""
    fragment = re.sub(r"<br\s*/?>", " ", fragment, flags=re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ",
                                                    fragment))).strip()


def iso_date(broadcast: str) -> str:
    """'09-Mar-2026 07:33:28' -> '2026-03-09'; anything else -> ''.

    Used only for filenames, so an unparsed date drops the segment rather
    than smuggling a raw '09-Mar-2026 07:33:28' into one.
    """
    match = re.match(r"(\d{2})-([A-Za-z]{3})-(\d{4})", (broadcast or "").strip())
    if not match:
        return ""
    day, month, year = match.groups()
    number = MONTHS.get(month.title())
    return f"{year}-{number}-{day}" if number else ""


def parse_announcement(page: str) -> dict:
    """Metadata and attachments from an SGXNet announcement page.

    The page is plain server-rendered ASP.NET -- `<dl><dt>label</dt>
    <dd>value</dd></dl>` groups and an attachment list -- which is why
    Singapore is scriptable at all past the search box.
    """
    info: dict = {"attachments": []}
    for label, value in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", page,
                                  re.S | re.I):
        key = ANNOUNCEMENT_FIELDS.get(_text(label).rstrip(":").strip())
        if key and key not in info:
            info[key] = _text(value)
    if "title" not in info:
        heading = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S | re.I)
        if heading:
            info["title"] = _text(heading.group(1)).rstrip(":").strip()
    # The trading code is the last field of "NAME - ISIN - CODE".
    parts = [p.strip() for p in (info.get("securities") or "").split(" - ")]
    if len(parts) >= 3:
        info["isin"], info["code"] = parts[-2], parts[-1]
    for href, name in re.findall(
            r'<a\s[^>]*href="([^"]+)"[^>]*class="announcement-attachment"[^>]*>'
            r"(.*?)</a>", page, re.S | re.I):
        url = urllib.parse.urljoin(ANNOUNCEMENT_BASE, html.unescape(href))
        info["attachments"].append({
            "name": _text(name) or os.path.basename(urllib.parse.urlsplit(url).path),
            "url": url,
            "file_id": file_id(url),
        })
    return info


def fetch_announcement(reference: str) -> dict:
    """Fetch and parse one announcement, by id or URL."""
    annc_id = announcement_id(reference)
    url = announcement_url(annc_id)
    page = _get(url).decode("utf-8", errors="replace")
    info = parse_announcement(page)
    if not info.get("attachments") and not info.get("title"):
        raise HostRefused(
            f"{url} returned {len(page)} bytes with no announcement in them. "
            "Either the id is wrong or SGX has archived it -- open "
            f"{url} in a browser to check.")
    info["id"] = annc_id
    info["url"] = url
    return info


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------

def is_sgx_document(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == DOCUMENT_HOST or host.endswith("." + DOCUMENT_HOST)


def file_id(url: str) -> str:
    """The FileID of an SGXNet document, from either URL shape.

    FileOpen carries it in the query; the 1.0.0 path prefixes the
    filename with it, as in `877753_DBS Annual Report 2025.pdf`.
    """
    split = urllib.parse.urlsplit(url)
    from_query = urllib.parse.parse_qs(split.query).get("FileID", [""])[0].strip()
    if from_query:
        return from_query
    leaf = urllib.parse.unquote(split.path.rsplit("/", 1)[-1])
    match = re.match(r"(\d{4,})_", leaf)
    return match.group(1) if match else ""


def document_label(url: str) -> str:
    """A human label for a document URL: its filename, less the plumbing."""
    leaf = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    leaf = re.sub(r"\.(ashx|pdf)$", "", leaf, flags=re.I)
    return re.sub(r"^\d{4,}_", "", leaf).strip() or "document"


def save_document(url: str, code: str, save_dir: str, date: str = "",
                  label: str | None = None, used: set[str] | None = None) -> str:
    """Save one SGXNet document. They are native PDFs, so bytes are kept."""
    if not is_sgx_document(url):
        raise SetupError(
            f"{url} is not an SGXNet document link. Those look like "
            f"https://{DOCUMENT_HOST}/FileOpen/<name>.ashx?App=Announcement"
            f"&FileID=<id>, or https://{DOCUMENT_HOST}/1.0.0/"
            "corporate-announcements/<AnncID>/<FileID>_<name>.pdf -- copy one "
            "from the announcement on sgx.com. For a document on the issuer's "
            "own IR site, use save_filing.py instead.")
    os.makedirs(save_dir, exist_ok=True)
    data = _get(url, timeout=180)
    if not data.startswith(b"%PDF-"):
        # SGXNet documents are native PDFs without exception, so anything
        # else is an error page. Passing it on would render it through
        # Chromium and save the refusal as the filing -- which is exactly
        # what TWSE's HTTP-200 block page used to do here.
        raise HostRefused(
            f"{url} returned {len(data)} bytes that are not a PDF: "
            f"{data[:80]!r}. Every SGXNet document is a native PDF, so this "
            "is an error page -- re-check the FileID against the announcement "
            "on sgx.com.")
    ident = file_id(url)
    name = filing_name(code, label or document_label(url), date, ident or None)
    if used is not None:
        name = claim_name(used, name, ident or "2")
    return save_filing_as_pdf(url, data, os.path.join(save_dir, name),
                              user_agent=USER_AGENT)


def announcement_identifier(info: dict, fallback: str = "") -> str:
    """What to name this announcement's files after.

    Never the code that was searched for: SoftBank Group lists only debt
    on SGX, so its announcements carry no `Securities` field at all, and
    borrowing the queried issuer's code there filed SoftBank's annual
    report as `O39_...` -- another company's document under OCBC's name.
    """
    return ((info.get("code") or "").strip().upper()
            or (info.get("issuer") or "").strip() or fallback)


def issuer_matches(info: dict, row: dict) -> bool:
    """Does this announcement belong to the issuer that was resolved?

    Codes when the announcement has one, otherwise the issuer's leading
    word -- enough to catch a pasted id from the wrong company.
    """
    code = (info.get("code") or "").strip().upper()
    if code:
        return code == (row.get("code") or "").strip().upper()
    issuer = (info.get("issuer") or "").upper()
    first = re.split(r"[^A-Z0-9]+", (row.get("name") or "").upper())[0]
    return bool(first) and first in issuer


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def print_announcement(info: dict) -> None:
    print(f"\n{info.get('title') or 'Announcement'}  [{info['id']}]")
    for key, label in (("issuer", "issuer"), ("code", "code"),
                       ("report_type", "report type"),
                       ("broadcast", "broadcast"), ("period_ended", "period"),
                       ("reference", "reference")):
        if info.get(key):
            print(f"  {label:<12} {info[key]}")
    print(f"  {'attachments':<12} {len(info['attachments'])}")
    for item in info["attachments"]:
        print(f"    {item['file_id'] or '-':<8} {item['name']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="SGX trading code (D05, D05.SI) or issuer name")
    parser.add_argument("--announcement", action="append", default=[],
                        metavar="ANNC",
                        help="SGXNet announcement id or URL: print its "
                             "metadata, and with --save-dir save every "
                             "attachment (repeatable)")
    parser.add_argument("--document", action="append", default=[],
                        help="An SGXNet document URL to save (repeatable)")
    parser.add_argument("--save-dir", help="Where to save downloads")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-download the securities directory")
    args = parser.parse_args()

    matches = find(args.query, load_directory(refresh=args.refresh))
    if not matches:
        print(f"No SGX-listed security matches '{args.query}'. The directory "
              f"({DIRECTORY_URL}) covers mainboard and Catalist equities, "
              "REITs, business trusts, ADRs and ETFs. Confirm at "
              "https://www.sgx.com/securities/securities-prices")
        return

    for row in matches:
        kind = row["type"] or "?"
        note = "" if is_issuer(row) else "  (not a reporting issuer)"
        print(f"{row['code']:<6} {row['name']:<32} [{row['board']} {kind}]{note}")
    primary = matches[0]
    if len(matches) > 1:
        print(f"\n{len(matches)} matches -- the first is used below; "
              "pass the exact trading code to choose another.")

    if not args.announcement and not args.document:
        print("\nAnnouncements (annual report, half-year results, SGXNet filings):")
        print(f"  {announcements_url(primary['code'])}")
        print("  SGX publishes no listing API, and that page is a JavaScript "
              "app, so finding the announcement is the one manual step -- see "
              "references/singapore.md. Everything after it is scripted: pass "
              "an announcement's id or URL to --announcement to read its "
              "metadata and save every attachment, or a document URL to "
              "--document.")
        return

    used: set[str] = set()
    for reference in args.announcement:
        info = fetch_announcement(reference)
        print_announcement(info)
        if not issuer_matches(info, primary):
            print(f"  WARNING  this announcement is {info.get('issuer') or '?'}, "
                  f"not {primary['name']} ({primary['code']}). Files are named "
                  "for the announcement's own issuer; check the id if that is "
                  "not what you wanted.")
        if not args.save_dir:
            continue
        date = iso_date(info.get("broadcast", ""))
        identifier = announcement_identifier(info, primary["code"])
        for item in info["attachments"]:
            saved = save_document(item["url"], identifier,
                                  args.save_dir, date=date,
                                  label=re.sub(r"\.pdf$", "", item["name"],
                                               flags=re.I),
                                  used=used)
            print(f"  saved  {saved}")

    if args.document:
        if not args.save_dir:
            print("\n--document needs --save-dir")
            return
        print()
        for url in args.document:
            saved = save_document(url, primary["code"], args.save_dir, used=used)
            print(f"saved  {saved}")


if __name__ == "__main__":
    run(main)

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
        # also saves EX-13, where a 10-K filer may keep the annual report
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
import urllib.parse
import urllib.request

import disk_cache
import sec_identity
from naming import claim_name, filing_name
from pdf_utils import save_filing_as_pdf
from net_errors import HostRefused, run

# SEC's fair-access policy wants every request to declare a real
# contact, so the User-Agent is configuration, not a constant:
# --user-agent, then SEC_USER_AGENT, then sec_user_agent.txt. There is no
# fallback -- a User-Agent without an address is refused by SEC (403).
#
# Resolved through user_agent(), not a module global that main() fills
# in: an empty string would silently disable the route interception in
# pdf_utils and let headless Chromium hit SEC's edge directly, saving a
# block page as if it were the filing.
_EXPLICIT_USER_AGENT: str | None = None
_RESOLVED_USER_AGENT: str | None = None


def user_agent() -> str:
    """The declared contact, resolved once per process."""
    global _RESOLVED_USER_AGENT
    if _RESOLVED_USER_AGENT is None:
        _RESOLVED_USER_AGENT = sec_identity.resolve(_EXPLICIT_USER_AGENT)
    return _RESOLVED_USER_AGENT

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

# Forms where EX-13 means "Annual Report to Security Holders" -- the
# exhibit a filer can incorporate the substance of its 10-K into.
#
# 20-F and 40-F are deliberately absent: their exhibit numbering is
# different, and 13.x there is the Sarbanes-Oxley section 906
# certification. Fetching ARM Holdings' FY2026 20-F proved it -- the
# EX-13.1 that came back was a one-page CEO/CFO certification, while the
# 216-page annual report was the primary document all along. A foreign
# private issuer's 20-F is self-contained, so there is nothing to chase.
EX13_FORMS = {"10-K", "10-K405", "10-KSB"}

# Markers of a certification rather than a report, for a 10-K filer that
# numbers its exhibits unusually. EDGAR's Description column is often just
# the exhibit number ("EX-13.1"), so the filename carries the signal:
# ARM's was ex131-ceocertfye26.htm.
CERT_DESCRIPTION_MARKERS = ("certif", "906", "302", "sarbanes")
CERT_FILENAME_MARKER = "cert"


def _get(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent(),
                 "Accept-Encoding": "gzip, deflate"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data


def _get_json(url: str) -> dict:
    body = _get(url)
    try:
        return json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # SEC's edge can answer with an HTML block page and status 200; a
        # JSON decode error reaching the user as a traceback is the thing
        # net_errors exists to prevent.
        raise HostRefused(
            f"{url} answered with {len(body)} bytes that are not JSON: "
            f"{body[:80]!r}. That is usually SEC's edge refusing an "
            "automated client -- check the User-Agent contact (see "
            "scripts/sec_identity.py) and wait before retrying.") from exc


def resolve_cik(ticker: str) -> tuple[int, str]:
    # The full ticker->CIK mapping is a large file that changes rarely;
    # re-downloading it every invocation both wastes time and trips
    # SEC's rate limiting (observed live as HTTP 429) after repeated
    # runs. Cache it for a day.
    cache = disk_cache.cache_path("sec_company_tickers.json")

    def _load_cache() -> dict | None:
        try:
            if disk_cache.is_fresh(cache):
                with open(cache, encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def _refresh() -> dict:
        raw = _get(TICKERS_URL)
        data = json.loads(raw.decode())
        disk_cache.store(cache, raw)
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


# submissions.json carries only a recent window inline; heavy filers push
# their annual report out of it, and the rest arrives in separately named
# JSON pages listed under filings.files.
OLDER_PAGES_URL = "https://data.sec.gov/submissions/{name}"
MAX_OLDER_PAGES = 3



def fetch_filings(cik: int, forms: list[str] | None,
                  limit: int) -> tuple[list[dict], dict, str]:
    """(rows, submissions payload, note).

    The payload comes back so a caller can describe a miss without asking
    SEC for the same JSON twice, and `note` carries anything that makes
    the answer incomplete -- an older page that could not be read -- so a
    partial scan is never reported as a finished one.
    """
    time.sleep(0.15)  # stay well under the 10 req/sec fair-access limit
    data = _get_json(SUBMISSIONS_URL.format(cik=cik))
    note = ""
    rows = _rows_from(data["filings"]["recent"], cik, forms, limit)

    # Only reach for the older pages when the window did not satisfy the
    # request: for most filers this costs nothing.
    pages = data["filings"].get("files") or []
    for page in pages[:MAX_OLDER_PAGES]:
        if len(rows) >= limit:
            break
        name = page.get("name")
        if not name:
            continue
        time.sleep(0.15)
        try:
            older = _get_json(OLDER_PAGES_URL.format(name=name))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # A rate limit is the whole IP, so pressing on only deepens it.
            # Anything else leaves the window as a usable partial answer --
            # but the caller has to know the scan did not finish, or a
            # transient 500 gets reported as "this company has no 10-K".
            _reraise_if_fatal(exc)
            note = (f"An older filing page ({name}) could not be read "
                    f"({exc}), so this list may be incomplete.")
            break
        rows.extend(_rows_from(older, cik, forms, limit - len(rows)))
    return rows[:limit], data, note


# Corporate suffixes carry no search value, and browse-edgar matches from
# the start of the company name.
NAME_SUFFIXES = ("inc", "inc.", "corp", "corp.", "corporation", "co", "co.",
                 "company", "holdings", "holding", "plc", "ltd", "ltd.",
                 "limited", "group", "lp", "llc", "sa", "nv", "ag", "the")


def search_name(name: str) -> str:
    """The part of a company name worth searching EDGAR with.

    Verified against browse-edgar: `company=ExxonMobil Holdings Corp` and
    `company=ExxonMobil` both return "No matching companies", while the
    shortened `company=Exxon` finds EXXON MOBIL CORP -- the match is from
    the start of the name, so the fewer words the better.
    """
    words = [w for w in re.split(r"[\s,]+", name or "") if w]
    kept = [w for w in words if w.lower().strip(".") not in NAME_SUFFIXES]
    first = (kept or words or [name])[0]
    # A successor often concatenates the old name: "ExxonMobil Holdings Corp"
    # for what EDGAR still lists as "EXXON MOBIL CORP". Cutting the
    # camel-case token at its second capital gives "Exxon", which matches.
    camel = re.match(r"^([A-Z][a-z]+)(?=[A-Z][a-z])", first)
    return camel.group(1) if camel else first


def predecessor_searches(name: str, form: str) -> list[tuple[str, str]]:
    """Places to look for the filings a successor CIK does not have."""
    stem = search_name(name)
    return [
        ("EDGAR full-text search",
         "https://www.sec.gov/edgar/search/#/q=" + urllib.parse.quote(f'"{name}"')
         + f"&forms={urllib.parse.quote(form)}"),
        (f"EDGAR company search for '{stem}'",
         "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
         f"&company={urllib.parse.quote(stem)}&type={urllib.parse.quote(form)}"),
    ]


def summarize_entity(data: dict) -> str:
    """One line on what this CIK holds, for a no-match report.

    Takes the submissions payload the caller already fetched -- a second
    identical GET on the path where a rate limit is likeliest is exactly
    what not to do -- and is careful to describe the inline window as a
    window when older pages exist.
    """
    filings = (data or {}).get("filings") or {}
    recent = filings.get("recent") or {}
    dates = recent.get("filingDate") or []
    if not dates:
        return "This CIK has no filings on record at all."
    kinds = sorted(set(recent.get("form") or []))
    forms = ", ".join(kinds[:12]) + (", ..." if len(kinds) > 12 else "")
    older = filings.get("files") or []
    if older:
        total = len(dates) + sum(int(p.get("filingCount") or 0) for p in older)
        earliest = min([p.get("filingFrom") or dates[-1] for p in older] + [dates[-1]])
        return (f"This CIK has about {total} filings on record from {earliest} "
                f"to {dates[0]}; the most recent {len(dates)} are of these "
                f"forms: {forms}.")
    return (f"This CIK has {len(dates)} filings on record from {dates[-1]} to "
            f"{dates[0]}, of these forms: {forms}.")


def _rows_from(block: dict, cik: int, forms: list[str] | None,
               limit: int) -> list[dict]:
    n = len(block.get("form") or [])
    periods = block.get("reportDate", [None] * n)
    descriptions = block.get("primaryDocDescription", [""] * n)

    rows = []
    for i in range(n):
        form = block["form"][i]
        if forms and form not in forms:
            continue
        accession_dashed = block["accessionNumber"][i]
        accession = accession_dashed.replace("-", "")
        doc = block["primaryDocument"][i]
        rows.append({
            "form": form,
            "filed": block["filingDate"][i],
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
    if base_form(row["form"]) not in EX13_FORMS:
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
    return [d for d in select_exhibits(parse_index_documents(html), wanted)
            if not _is_certification(d)]


def _is_certification(doc: dict) -> bool:
    """Is this exhibit a certification rather than a report?

    A second line of defence behind EX13_FORMS: cheap, and it keeps a
    one-page signature block from being delivered as an annual report.
    """
    description = (doc.get("description") or "").lower()
    if any(marker in description for marker in CERT_DESCRIPTION_MARKERS):
        return True
    return CERT_FILENAME_MARKER in (doc.get("document") or "").lower()


def base_form(form: str) -> str:
    """"10-K/A" -> "10-K": the form without its amendment suffix."""
    return form.split("/")[0].strip().upper()


def out_name(ticker: str, form: str, filed: str, exhibit_type: str | None = None,
             suffix: str | None = None) -> str:
    """{TICKER}_{FORM}_{DATE}.pdf, per SKILL.md.

    The form and the EDGAR-supplied exhibit type are sanitized rather
    than having "/" replaced by hand: the type is free text scraped from
    a table cell, and a colon or backslash in it is an unopenable
    filename on Windows.
    """
    label = form.replace("/", "-")
    if exhibit_type:
        label = f"{label}-{exhibit_type.replace('/', '-')}"
    return filing_name(ticker, label, filed, extra=suffix)


def _claim_name(used: set[str], name: str, accession: str) -> str:
    """Kept as a thin alias so the accession tail stays the disambiguator."""
    return claim_name(used, name, accession[-6:])


def save_document(url: str, out_path: str) -> str:
    """Save one document: raw bytes if it is already a PDF, otherwise a
    real browser render of the page -- handing the renderer the bytes we
    already have so the host is not asked for the same document twice."""
    return save_filing_as_pdf(url, _get(url), out_path, user_agent=user_agent())


def _reraise_if_fatal(exc: BaseException) -> None:
    """Stop the run on failures that will hit every remaining document.

    A 429 is the whole IP being rate limited, so continuing just deepens
    the block; SKILL.md says to wait rather than retry. A filesystem
    error means the save directory itself is unusable. Anything else is
    about one document and the run carries on without it.
    """
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        raise exc
    if isinstance(exc, OSError) and not isinstance(exc, urllib.error.URLError) \
            and getattr(exc, "filename", None) is not None:
        raise exc


def save_rows(rows: list[dict], ticker: str, save_dir: str,
              exhibits: list[str]) -> list[str]:
    os.makedirs(save_dir, exist_ok=True)
    saved_paths = []
    used_names: set[str] = set()
    for r in rows:
        time.sleep(0.15)
        name = _claim_name(used_names, out_name(ticker, r["form"], r["filed"]),
                           r["accession"])
        try:
            saved = save_document(r["url"], os.path.join(save_dir, name))
        except (urllib.error.URLError, OSError) as exc:
            _reraise_if_fatal(exc)
            # One unreachable document is not worth abandoning the rest of
            # the run; --limit 10 must not quietly mean "until one fails".
            print(f"{r['filed']}  {r['form']:<10}  could not be saved ({exc})")
            continue
        saved_paths.append(saved)
        print(f"{r['filed']}  {r['form']:<10}  -> {saved}")

        for ex in fetch_exhibits(r, exhibits):
            time.sleep(0.15)
            ex_name = _claim_name(
                used_names,
                out_name(ticker, r["form"], r["filed"], ex["type"]),
                r["accession"])
            try:
                saved_ex = save_document(ex["url"], os.path.join(save_dir, ex_name))
            except (urllib.error.URLError, OSError) as exc:
                _reraise_if_fatal(exc)
                # The filing itself is already saved, and the filings after
                # this one still need fetching -- one bad exhibit is not
                # worth ending the run.
                print(f"    + {ex['type']:<8} could not be saved ({exc})")
                continue
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
    parser.add_argument("--user-agent",
                        help="Contact SEC's fair-access policy asks for, e.g. "
                             "'Your Name you@domain.com'. Otherwise "
                             f"{sec_identity.ENV_VAR} or "
                             f"{sec_identity.CONFIG_FILENAME} is used.")
    args = parser.parse_args()

    global _EXPLICIT_USER_AGENT
    _EXPLICIT_USER_AGENT = args.user_agent
    user_agent()  # fail here, before any request, if no contact is configured

    forms = [f.strip().upper() for f in args.forms.split(",")] if args.forms else None

    cik, name = resolve_cik(args.ticker)
    print(f"{name} (CIK {cik:010d})\n")

    rows, submissions, note = fetch_filings(cik, forms, args.limit)
    if note:
        print(f"note: {note}\n")
    if not rows:
        wanted = "/".join(forms) if forms else "any form"
        print(f"No {wanted} filings found for {args.ticker} under CIK "
              f"{cik:010d}.")
        print(f"  {summarize_entity(submissions)}")
        pages = ((submissions.get("filings") or {}).get("files") or [])
        if len(pages) > MAX_OLDER_PAGES:
            print(f"  Only the most recent {MAX_OLDER_PAGES} of {len(pages)} "
                  "older filing pages were searched, so an older match may "
                  "exist -- narrow with --forms or search EDGAR directly.")
        form = forms[0] if forms else "10-K"
        print("  A ticker can also point at a successor entity -- after a "
              "holding-company reorganisation the new CIK carries the ticker "
              "while the operating company's annual reports stay under the "
              "old one (XOM did exactly this in 2026).")
        for label, url in predecessor_searches(name, form):
            print(f"  {label}: {url}")
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

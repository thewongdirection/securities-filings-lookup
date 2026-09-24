# United States — SEC EDGAR

Primary source: **SEC EDGAR** (`sec.gov`, `data.sec.gov`). Free, authoritative, no login.

## Option A — script (needs real network access to sec.gov)

```
python scripts/fetch_us_filings.py AAPL
python scripts/fetch_us_filings.py AAOI --forms 10-K,10-Q --limit 10
python scripts/fetch_us_filings.py AAOI --forms 10-Q --limit 1 --save-dir ./filings
```

`--save-dir` saves each matching filing as a PDF. Since SEC's primary documents are HTML, this launches a real headless browser (Playwright + Chromium) to render and print the actual page — see the main SKILL.md's Step 3 for why that matters and what it replaced. One-time setup: `pip install playwright && playwright install chromium`. Without `--save-dir`, the script just prints the filing list with direct links.

**To *show* a US annual report in an interactive PDF viewer** (which typically can't open local files outside its allowed roots): the 10-K itself is HTML, but many large companies also file their glossy annual report as form **ARS** (Annual Report to Shareholders), which IS a native PDF hosted on sec.gov — `--forms ARS --limit 1` finds it, and the resulting `https://www.sec.gov/Archives/...pdf` URL streams into the viewer fine (tested with JPMorgan's 2025 ARS; the viewer's fetch is not caught by SEC's headless-browser bot detection). Not every company files an ARS (Apple and Tesla don't reliably), so fall back to handing over the 10-K link if the form isn't there.

What it does under the hood:
1. Downloads `https://www.sec.gov/files/company_tickers.json` (ticker → CIK mapping) and resolves the ticker.
2. Queries `https://data.sec.gov/submissions/CIK{10-digit-zero-padded-cik}.json`, which returns the company's recent filings (form type, filing date, period, accession number, primary document).
3. Builds the direct document URL: `https://www.sec.gov/Archives/edgar/data/{cik}/{accession-no-dashes}/{primaryDocument}`.

Requirements the script already handles, but worth knowing:
- **The `User-Agent` must carry an email address.** SEC's "fair access" policy asks clients to declare themselves, and the edge enforces it rather than merely requesting it. Tested live (2026-09) against `.../ibm-20251231x10kex311.htm`: `securities-filings-lookup-skill contact@example.com` → 200, `Securities Filings Lookup admin@<domain>` → 200, `securities-filings-lookup/1.0 contact@<domain>` → 200, but `securities-filings-lookup (https://github.com/...)` — identical minus the address — → **403**. A browser-spoofed `Mozilla/...` is also blocked. So there is no working anonymous form: the scripts resolve a real contact through `scripts/sec_identity.py` (`--user-agent` → `$SEC_USER_AGENT` → `sec_user_agent.txt`) and refuse to send without one. Do not hardcode an address, and do not reuse one found elsewhere in the conversation — ask the user, because SEC receives it.
- Rate limit is 10 requests/second; the script already paces itself, no need to add more delay for single lookups.
- **Heavy repeated use can still get you rate limited beyond the per-second pacing.** Observed live (2026-07): after many script runs in one day, `www.sec.gov` started returning HTTP 429 (Too Many Requests) on `company_tickers.json` — a sustained block lasting several minutes or more, not a momentary throttle — while `data.sec.gov` kept working. The script now caches the ticker→CIK mapping for a day, which removes the main repeat offender, but if you're looking up many tickers or saving many filings in a session, expect SEC may temporarily block you anyway. If a 429 appears: stop retrying in a loop, wait several minutes, and batch remaining lookups. Tell the user their IP is temporarily rate limited by SEC rather than silently failing.
- If the ticker isn't in `company_tickers.json` (common for very recent IPOs, SPACs, or funds), fall back to searching by company name via EDGAR full text search: `https://www.sec.gov/edgar/search/#/q=<company name>`.

## Option B — search + fetch (claude.ai sandbox, or when the script's network call fails)

1. `web_search "<ticker> SEC EDGAR filings"` or `"<company name> 10-K SEC"`.
2. `web_fetch` the resulting sec.gov page (usually a `cgi-bin/browse-edgar` company page or an EDGAR full text search results page).
3. For keyword-based digging inside filings (e.g. "which 10-Ks mention a specific customer"), search `https://www.sec.gov/edgar/search/` directly via `web_search` — it indexes full text of filings since 2001.
4. To save a single already-found filing URL as a genuine PDF (real browser render, not a reconstruction) without going through the ticker/CIK flow: `python scripts/save_filing.py <url> --out <path>`. Requires real network access — see Step 3 of SKILL.md for why this specifically doesn't work in claude.ai's sandbox.

**A dead end worth knowing about, so it isn't re-tried:** some companies' investor-relations pages (often hosted on a third-party distribution platform, e.g. `*.gcs-web.com`) show a "Download PDF" link next to each filing, which looks promising as a possible source of an already-rendered, legitimate PDF. Tested against Microsoft's: `web_fetch` returned a bot-detection block on the page itself, before even getting to the download link. Not a viable path from claude.ai's sandbox.

## Form type cheatsheet

| Form | Meaning |
|---|---|
| 10-K | Annual report (domestic filer) |
| 10-Q | Quarterly report (domestic filer) |
| 8-K | Current report — material events, between periodic filings |
| 20-F | Annual report (foreign private issuer — e.g. most non-US-incorporated companies, including Chinese ADRs) |
| 6-K | Current/interim report (foreign private issuer) — filed as needed, no fixed quarterly cadence |
| DEF 14A | Definitive proxy statement |
| S-1 / S-3 | Registration statement (IPO / follow-on offering) |
| SC 13D / 13G | Beneficial ownership disclosure |

Foreign private issuers (20-F filers) don't have a mandatory quarterly filing requirement the way domestic 10-Q filers do — 6-Ks are furnished whenever the company has something to disclose (often includes unaudited interim results, but check the specific 6-K, since many just cover press releases or officer changes).

## Incorporation by reference: the annual report can be an exhibit

A 10-K may be a thin form whose substance is incorporated by reference
from an exhibit in the same accession. IBM is the standard example: for
FY2025, `ibm-20251231.htm` (the primary document) renders to 31 pages
and carries Item 8 only as a cross-reference, while `EX-13`
(`ibm-20251231_d2.htm`, 4.5 MB) holds the MD&A, the consolidated
statements and the audit report -- 117 rendered pages.

The submissions API only names `primaryDocument`, so the exhibit has to
come from the accession's filing index:

```
https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{accession-dashed}-index.htm
```

Its table is `Seq | Description | Document | Type | Size`. Select on the
**Type** column (`EX-13`, `EX-13.1`), not Description, which is free
text. `fetch_us_filings.py --save-dir` does this by default; widen it
with `--exhibits EX-13,EX-21` or suppress it with `--no-exhibits`.

**Inline-XBRL documents are linked through EDGAR's viewer**, as
`/ix?doc=/Archives/edgar/data/...`. That URL is a JavaScript
application: rendering it gives a blank or broken PDF. Always strip the
viewer prefix and fetch the `/Archives/...` path itself.

Tells that you are looking at a wrapper rather than the real annual
report: a 10-K that renders to ~30 pages, repeated "incorporated herein
by reference" in Items 7 and 8, or an accession whose largest document
is not the primary one.

**EX-13 means this only under 10-K numbering.** A 20-F or 40-F numbers
its exhibits differently: 13.x there is the Sarbanes-Oxley section 906
certification. Tested live (2026-09) on ARM Holdings' FY2026 20-F: the
EX-13.1 is `ex131-ceocertfye26.htm`, a one-page CEO/CFO certification,
while the 216-page annual report is the primary document. A foreign
private issuer's 20-F is self-contained, so `fetch_us_filings.py` reads
the index for 10-K, 10-K405 and 10-KSB only, and skips any exhibit whose
filename or description marks it a certification.

## A ticker can point at a successor entity with no annual report yet

`company_tickers.json` maps a ticker to whichever CIK currently carries
it, and after a holding-company reorganisation that is the new entity.
Live example (2026-09): **XOM** resolves to `ExxonMobil Holdings Corp`,
CIK 2115436, whose record starts 2026-07-01 and holds 10-Q, 8-K, 8-K12B,
424B3, FWP, POSASR and S-8 POS -- no 10-K, because it has not filed a
first annual report yet. The 10-Ks are under the predecessor,
`EXXON MOBIL CORP` CIK 34088 (latest 2026-02-18), which no longer carries
any ticker.

So "no 10-K found" can be true of the CIK and false of the company.
`fetch_us_filings.py` now reports the entity's filing window and form
types alongside the miss, and points at EDGAR's company search by name.
The tell is a CIK whose earliest filing is recent and whose forms include
`8-K12B` (a successor registering securities).

Separately, `submissions.json` inlines only a recent window of filings
and lists the rest under `filings.files`; the script follows up to three
of those pages when the window does not satisfy the request, so a heavy
filer's annual report is still found.


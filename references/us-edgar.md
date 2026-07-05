# United States — SEC EDGAR

Primary source: **SEC EDGAR** (`sec.gov`, `data.sec.gov`). Free, authoritative, no login.

## Option A — script (needs real network access to sec.gov)

```
python scripts/fetch_us_filings.py AAPL
python scripts/fetch_us_filings.py AAOI --forms 10-K,10-Q --limit 10
python scripts/fetch_us_filings.py AAOI --forms 10-Q --limit 1 --save-dir ./filings
```

`--save-dir` saves each matching filing as a PDF. Since SEC's primary documents are HTML, this launches a real headless browser (Playwright + Chromium) to render and print the actual page — see the main SKILL.md's Step 3 for why that matters and what it replaced. One-time setup: `pip install playwright && playwright install chromium`. Without `--save-dir`, the script just prints the filing list with direct links.

What it does under the hood:
1. Downloads `https://www.sec.gov/files/company_tickers.json` (ticker → CIK mapping) and resolves the ticker.
2. Queries `https://data.sec.gov/submissions/CIK{10-digit-zero-padded-cik}.json`, which returns the company's recent filings (form type, filing date, period, accession number, primary document).
3. Builds the direct document URL: `https://www.sec.gov/Archives/edgar/data/{cik}/{accession-no-dashes}/{primaryDocument}`.

Requirements the script already handles, but worth knowing:
- SEC's "fair access" policy requires a descriptive `User-Agent` header (e.g. `Company/Contact your-email@domain.com`) — anonymous/browser-spoofed UAs can get 403'd. Customize the `USER_AGENT` constant in the script if you hit this.
- Rate limit is 10 requests/second; the script already paces itself, no need to add more delay for single lookups.
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

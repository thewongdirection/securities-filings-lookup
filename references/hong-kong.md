# Hong Kong — HKEXnews

Primary source: **HKEXnews** (`www1.hkexnews.hk` / `www2.hkexnews.hk`), the official disclosure platform for the Hong Kong Stock Exchange (HKEX).

There is no *documented* public API, but the search UI's own JSON endpoints are scriptable with plain urllib and worked when tested (2026-07, Tencent 00700):

1. Resolve HKEXnews' internal stockId: `GET https://www1.hkexnews.hk/search/prefix.do?callback=cb&lang=EN&type=A&name=<5-digit code>&market=SEHK` — returns JSONP; the `stockInfo[0].stockId` field is what the search endpoint wants (00700 → 7609).
2. List filings: `GET https://www1.hkexnews.hk/search/titleSearchServlet.do?sortDir=0&sortByOptions=DateTime&category=0&market=SEHK&stockId=<stockId>&documentType=-1&fromDate=YYYYMMDD&toDate=YYYYMMDD&title=&searchType=1&t1code=-2&t2Gcode=-2&t2code=-2&rowRange=200&lang=E` — returns JSON whose `result` field is itself a JSON *string* (parse twice). `FILE_LINK` is relative; prefix `https://www1.hkexnews.hk`.

**What didn't work, so don't re-try it:** the `title` parameter (with `searchType` 0 or 1) and guessed `t1code`/`t2code` category values all silently return 0 results. To find a specific report type, query a *date range* instead and filter titles locally — e.g. annual reports land in March–April, interims in August; a two-month window plus a case-insensitive "report" match found Tencent's ANNUAL REPORT 2025 immediately. Without a date range the endpoint returns only the most recent filings (a few weeks' worth for a busy filer, mostly Next Day Disclosure Returns during buybacks).

These endpoints are undocumented and can change or add anti-bot friction without notice. If they break, fall back to search + fetch (or a browser tool, if available), as described below.

## Step 1 — get the stock code

HK stock codes are numeric, conventionally zero-padded to 5 digits (Tencent = 00700, Alibaba's HK listing = 09988, AIA = 01299). If the user gives a company name instead of a code, `web_search "<company> HKEX stock code"` to confirm — don't guess.

## Step 2 — pull filings

Try, in order:

1. **HKEXnews title search**: `https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=EN&market=SEHK&stockId=<5-digit code>&category=0`
   `web_search` for this pattern with the actual code filled in (e.g. `"HKEXnews titlesearch stockId 00700"`), then `web_fetch` the result. If the page doesn't render usefully as fetched text (it's a listing widget), search instead for the specific filing type: `"<company> HKEXnews annual report 2025"`.
2. **Company's own investor relations page** — often mirrors the same PDFs (annual report, interim report, results announcements) with much better navigation than the HKEXnews search UI. Frequently the faster path in practice: `web_search "<company> investor relations annual report"`.
3. If a browser automation tool is available in this session, navigating directly to the HKEXnews search page and reading the rendered table is more reliable than trying to parse it as fetched text.

## Filing cadence and types

- **Annual Report** — full year, audited.
- **Interim Report** — half-year, reviewed but not necessarily audited.
- No mandatory quarterly reporting for most main-board issuers (abolished 2019), except GEM-board and some financial-sector issuers — don't assume a Q1/Q3 filing exists; check what's actually there.
- **Announcements** — a much broader, continuous stream: results announcements, connected transactions, notifiable transactions, changes in directors, profit warnings, etc. Same search mechanism applies; just don't filter to only "reports" if the user wants "everything recent."

## Tip for dual-listed / ADR companies

If the company also has a US listing (e.g. Alibaba is dual-primary-listed as both 9988.HK and BABA on the NYSE), check SEC EDGAR first. Foreign private issuers are required to furnish home-market disclosures — including the Hong Kong annual report and major announcements — as exhibits to their Form 6-K filings. Searching EDGAR full text search for `"<company> Hong Kong Annual Report"` often surfaces the exact PDF faster than navigating HKEXnews's JavaScript search UI, and it's the same document. Use `references/us-edgar.md` for that path.

## Saving it as a PDF

Once you have a direct URL to the filing (HKEXnews and company IR pages both serve these as native PDFs almost always), saving it with real network access is simple: `python scripts/save_filing.py <url> --out ./filings/whatever.pdf` — it detects the content is already a PDF and saves the bytes as-is.

**In claude.ai's sandbox, this does not work — tested and confirmed, not assumed.** The natural guess is that `web_fetch` with binary/base64 mode could retrieve a native PDF's actual bytes without needing bash network access. That was tested directly against two real CNINFO PDFs (one short, one 143 pages) with `web_fetch_pdf_extract_text=false`, and both came back as extracted text regardless — there's no working raw-bytes path found so far. Don't present extracted text as if it were the original file in this environment; hand the person the direct URL instead.

## Language

Main-board HKEX issuers are required to publish in both English and Chinese, so — unlike mainland China filings — an English version should exist. If a search only surfaces the Chinese version, try appending "English" to the query or check the company's IR page directly.

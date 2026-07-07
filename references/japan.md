# Japan — TDnet + EDINET

Two official systems split Japan's disclosures:

- **TDnet** (`www.release.tdnet.info`, TSE's timely-disclosure network) — earnings reports (決算短信), dividend/buyback/guidance notices, corporate actions. **Keyless and scriptable** — this is the bundled script's source, and for "the latest results" it's the document people actually want.
- **EDINET** (`disclosure2.edinet-fsa.go.jp`, the FSA's statutory system) — annual securities reports (有価証券報告書), quarterly reports, large-shareholding filings. **The API (v2) requires a subscription key** — free registration at `api.edinet-fsa.go.jp`, then set it as the `EDINET_API_KEY` environment variable and use `fetch_jp_filings.py <code> --edinet-date YYYY-MM-DD` (annual reports cluster ~3 months after fiscal year end — late June for March year-ends). Verified live: keyless calls return 401 and the script says so; the with-key path follows the documented API (documents.json list + documents/{docID}?type=2 PDF) but has not been exercised with a live key yet. Guessed web-UI JSON endpoints 404'd, don't re-try them.

**Company names** resolve via JPX's official English directory (`resolve_name.py --venues jp`) — the full TSE company list with English names, cached daily. Needs `pip install xlrd` (the file is old-format .xls).

## Ticker format

4-digit codes (Toyota = 7203), suffix `.T` (also seen: `.JP`). **Bare 4-digit codes are three-way ambiguous** (HK / Taiwan / Japan) — the classifier assumes HK and says so; confirm.

## Script (TDnet, needs real network access)

```
python scripts/fetch_jp_filings.py 7203                    # Toyota, last 7 days
python scripts/fetch_jp_filings.py 7203 --days 30 --grep 決算
python scripts/fetch_jp_filings.py 7203 --days 30 --save-dir ./filings
```

Mechanics, verified live (2026-07): daily list pages `I_list_{page:03d}_{YYYYMMDD}.html` (UTF-8, ~100 rows/page, 404 when out of pages/no disclosures), rows carry time / 5-char code (4-digit code + suffix digit) / name / PDF link / title, PDFs are native and download raw from the same directory. **TDnet only keeps roughly one month online.** Company codes on TDnet are 5 characters — match by 4-digit prefix.

## Language and English versions

TDnet documents are Japanese; some companies file English versions alongside (title contains "英文" or the row appears twice). Apply SKILL.md's Non-English rules, with these Japan-specific sources for English:

1. **US-listed ADRs file with the SEC** — Toyota (TM), Sony (SONY), Honda, Mitsubishi UFJ etc. file 20-F annual reports in English on EDGAR: often the single best English document. Route via `references/us-edgar.md`.
2. **IR sites** — most large caps publish English integrated/annual reports and English earnings presentations on their IR pages (web search "<company> integrated report English").
3. Otherwise translate the summary pages of the 短信 (first 2-3 pages carry the consolidated figures table) with the usual unofficial-translation labelling.

## Fallback

EDINET web UI (`disclosure2.edinet-fsa.go.jp`) or `web_search "<company> 有価証券報告書"`. In claude.ai's sandbox neither TDnet nor EDINET is reachable — hand over URLs.

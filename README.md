# securities-filings-lookup

A [Claude Code](https://claude.com/claude-code) skill that looks up official financial and securities filings for any stock ticker — working out where the company is actually listed, fetching from the authoritative regulator source, and saving the original documents as PDFs.

**Covered venues:**

| Venue | Source | Documents |
|---|---|---|
| 🇺🇸 United States | SEC EDGAR | 10-K, 10-Q, 8-K, 20-F, 6-K, ARS, proxies |
| 🇭🇰 Hong Kong | HKEXnews | Annual/interim reports, announcements |
| 🇨🇳 Mainland China A-shares | CNINFO (SSE + SZSE) | 年度报告, 半年度报告, 季度报告, prospectuses |
| 🇹🇼 Taiwan | MOPS / doc.twse.com.tw | 年報 (annual reports), financial reports |
| 🇬🇧 London | FCA National Storage Mechanism | Annual reports (ESEF), circulars, prospectuses |
| 🇯🇵 Japan | TDnet (+ EDINET pointers) | 決算短信 (earnings), timely disclosures |
| 🇩🇪 Frankfurt / Germany | Unternehmensregister / IR sites | Annual reports (documented workflow, no scraper) |

For cross-listed companies the skill retrieves every covered venue's filings and says what it can't reach. Venue quirks are documented per market: modern UK annual reports are ESEF zip packages (xHTML/iXBRL), Japan's EDINET API needs a free subscription key (TDnet is keyless but keeps only ~1 month), and Germany's official repositories are browse-only so the IR-site annual report or a SEC 20-F (SAP) is the practical route.

## Install

Clone straight into your personal skills directory:

```bash
# Mac/Linux
git clone https://github.com/thewongdirection/securities-filings-lookup.git ~/.claude/skills/securities-filings-lookup

# Windows (PowerShell)
git clone https://github.com/thewongdirection/securities-filings-lookup.git $env:USERPROFILE\.claude\skills\securities-filings-lookup
```

On claude.ai instead: download this repo as a zip (Code → Download ZIP) and upload it under Settings → Capabilities → Skills.

## Prerequisites

- **Claude Code (or Claude Desktop)** with real network access — the scripts talk directly to `sec.gov` / `data.sec.gov`, `cninfo.com.cn`, and `hkexnews.hk`. In claude.ai's sandbox those hosts are unreachable, so there the skill degrades gracefully to venue identification and direct links (no PDF downloads).
- **Python 3.10+** on PATH. Lookups and Hong Kong / China PDF saves use only the standard library.
- **For saving US SEC filings as PDFs** (one-time setup, the skill will prompt/do it when first needed):
  ```
  pip install playwright
  playwright install chromium
  ```
  SEC's primary documents are HTML; the skill prints them to PDF with a real headless browser. HK and China filings are native PDFs and need nothing extra.
- Optional: `pip install pypdf` — used to verify saved PDFs and to extract text when translating Chinese filing summaries.
- **For Taiwan filings** (and some IR-site downloads): `pip install certifi` — several issuers' TLS chains are missing from default trust stores; the scripts pick up certifi automatically.
- **For Japan name lookup**: `pip install xlrd` (JPX's company directory is an old-format .xls). For Japanese statutory filings via EDINET, register a free API key at api.edinet-fsa.go.jp and set `EDINET_API_KEY`; TDnet needs nothing.

## How to use

Invoke with a ticker, in any common format:

```
/securities-filings-lookup MSFT          # US
/securities-filings-lookup BRK.B         # US class shares (BRK-B also works)
/securities-filings-lookup 0700.HK       # Hong Kong (also: 700, 9988.HK)
/securities-filings-lookup 600519:SS     # Shanghai (also: 600519, 600519.SS)
/securities-filings-lookup 300308.SZ     # Shenzhen / ChiNext
/securities-filings-lookup 2330.TW       # Taiwan (bare 2330 is assumed HK — use the suffix)
/securities-filings-lookup AZN.L         # London
/securities-filings-lookup 7203.T        # Tokyo
/securities-filings-lookup SAP.DE        # Frankfurt / XETRA
```

Or just ask in plain language — "pull up Tencent's annual report", "where are Moutai's filings?", "get me BitMine's latest 10-Q". You can also ask for specific form types, past years, a specific save folder, or a translated/summarized section of any retrieved filing.

**Company names work too** — the skill resolves them to tickers via each venue's own directory (`scripts/resolve_name.py`), asks you to confirm the match, and proceeds with the closest match if you don't answer within ~15 seconds. Beware lookalikes (Tencent vs Tencent Music): the confirmation step exists for a reason.

What you get back: the company resolved to its official identifier (CIK / stock code), a list of recent filings with direct regulator links, the requested documents saved as PDFs, and — where the document is a native PDF — the report opened in the interactive PDF viewer.

## Default behaviors

- **PDFs are delivered automatically** — for every document a request resolves to (annual/quarterly report, requested forms, and each version in dual-listed/non-English cases), the skill saves the PDF to your remembered folder and hands it back, rather than only listing links and waiting to be asked. It scopes to what you actually want, not the whole tail of routine housekeeping filings (Form 4s, disclosure returns), and skips anything already retrieved earlier in the conversation.
- **Dual-listed companies** (A+H shares, US-listed Chinese ADRs with HK listings, dual primaries): the skill asks which market's filings you want — but **if you don't answer within ~15 seconds** (or the session is non-interactive), **it downloads all versions** and lets you narrow afterwards.
- **Non-English filings** (mainland A-shares, mostly): you always get **the original plus an English version** — the company's official English translation if one exists, otherwise the dual-listed English filing (H-share report / 20-F), otherwise a clearly-labelled unofficial translation of the official summary (摘要).
- **A-share annual reports open automatically** in the PDF viewer, preferring the official English edition when published.
- **Original documents only, never reconstructions**: HK/China PDFs are saved byte-for-byte; SEC HTML is rendered by a real browser (same output as Chrome's Print → Save as PDF). If a faithful copy can't be produced, you get the direct URL instead.
- **Polite to regulators**: results already fetched in the conversation aren't re-fetched; the SEC ticker→CIK mapping is cached locally for a day; HTTP 429 rate limits are reported plainly rather than retried in a loop. Heavy use (many tickers in a day) can still get your IP temporarily rate limited by SEC — the skill will tell you if that happens.

## Where downloaded files are saved

The save folder is **remembered per machine** in `save_location.txt` next to `SKILL.md` (created on first use, gitignored — it never syncs through this repo). Resolution order:

1. A folder you name in the request ("save it to my Downloads") — which then **becomes the new remembered default**
2. The folder in `save_location.txt`
3. If neither exists yet, the skill **asks you once** and records the answer

Files are named identifiably — `{ticker}_{form}_{date}.pdf` (e.g. `MSFT_10-K_2025-07-30.pdf`, `600519_00_贵州茅台2025年年度报告.pdf`) — so the folder stays sortable.

## Run it from your phone

1. **Claude Code on the web (full functionality).** This repo also carries the skill as a project skill under `.claude/skills/`, so from the Claude mobile app (or claude.ai/code) start a cloud session on this repo and just ask, e.g. *"look up filings for 0700.HK"*. Cloud sessions have real network access, so fetching and PDF saving work.
2. **claude.ai Skills upload (links only).** Upload the repo zip under Settings → Capabilities → Skills; the skill then triggers in regular Claude chats, including mobile — venue identification and direct links, no downloads.

## What's inside

- `SKILL.md` — the skill definition, workflow, and default behaviors
- `scripts/identify_venue.py` — offline ticker→venue classifier (suffixes, bare codes, class shares)
- `scripts/fetch_us_filings.py` — SEC EDGAR listing + PDF save (handles SEC's gzip responses, bot detection, rate-limit caching)
- `scripts/fetch_cn_filings.py` — CNINFO listing + PDF save (handles the `code,orgId` query requirement and server-side title search)
- `scripts/save_filing.py` / `scripts/pdf_utils.py` — save any filing URL as the original PDF
- `references/` — per-venue mechanics, URL patterns, and documented dead ends so they don't get re-tried

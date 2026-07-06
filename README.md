# securities-filings-lookup

A [Claude Code](https://claude.com/claude-code) skill that looks up official financial and securities filings for any stock ticker — working out where the company is actually listed, fetching from the authoritative regulator source, and saving the original documents as PDFs.

**Covered venues:**

| Venue | Source | Documents |
|---|---|---|
| 🇺🇸 United States | SEC EDGAR | 10-K, 10-Q, 8-K, 20-F, 6-K, proxies |
| 🇭🇰 Hong Kong | HKEXnews | Annual/interim reports, announcements |
| 🇨🇳 Mainland China A-shares | CNINFO (SSE + SZSE) | 年度报告, 半年度报告, 季度报告, prospectuses |

It handles ambiguous ticker formats, dual listings and Chinese ADRs, saves filings as the *original* documents (raw PDF bytes, or a faithful headless-browser render of SEC HTML — never a reconstruction), and for A-shares automatically opens the latest annual report in the PDF viewer, preferring the company's official English translation when one exists.

## Install

Clone straight into your personal skills directory:

```bash
# Mac/Linux
git clone https://github.com/thewongdirection/securities-filings-lookup.git ~/.claude/skills/securities-filings-lookup

# Windows (PowerShell)
git clone https://github.com/thewongdirection/securities-filings-lookup.git $env:USERPROFILE\.claude\skills\securities-filings-lookup
```

Then in any Claude Code session:

```
/securities-filings-lookup MSFT
/securities-filings-lookup 600519:SS
/securities-filings-lookup 0700.HK
```

On claude.ai instead: download this repo as a zip (Code → Download ZIP) and upload it under Settings → Capabilities → Skills. Note that claude.ai's sandbox has no network access to the filing sources, so you get venue identification and direct links there rather than PDF downloads — the skill explains this itself.

## Run it from your phone

Two options:

1. **Claude Code on the web (full functionality).** This repo also carries the skill as a project skill under `.claude/skills/`, so from the Claude mobile app (or claude.ai/code) start a cloud session on this repo and just ask, e.g. *"look up filings for 0700.HK"*. Cloud sessions have real network access, so fetching and PDF saving work. If the session needs the US PDF-render path, it will run the one-time Playwright install itself.
2. **claude.ai Skills upload (links only).** Download this repo as a zip and upload it under Settings → Capabilities → Skills; the skill then triggers in regular Claude chats, including mobile. Note claude.ai's sandbox has no network access to sec.gov / CNINFO / HKEXnews, so in chats you get venue identification and direct filing links, not PDF downloads — the skill explains this itself when it happens.

## Requirements

- Python 3.10+ on PATH (standard library only for lookups and HK/China PDF saves)
- For saving US SEC filings as PDFs (one-time): `pip install playwright && playwright install chromium`

## What's inside

- `SKILL.md` — the skill definition and workflow
- `scripts/identify_venue.py` — offline ticker→venue classifier
- `scripts/fetch_us_filings.py` — SEC EDGAR listing + PDF save (handles SEC's gzip responses and bot detection)
- `scripts/fetch_cn_filings.py` — CNINFO listing + PDF save (handles the `code,orgId` query requirement)
- `scripts/save_filing.py` / `scripts/pdf_utils.py` — save any filing URL as the original PDF
- `references/` — per-venue mechanics, URL patterns, and documented dead ends so they don't get re-tried

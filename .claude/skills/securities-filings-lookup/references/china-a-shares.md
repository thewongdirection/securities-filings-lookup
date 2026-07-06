# Mainland China A-shares — CNINFO

Primary source: **CNINFO** (`cninfo.com.cn`, 巨潮资讯网 / "Juchao"), the CSRC-designated disclosure platform covering both the Shanghai Stock Exchange (SSE) and Shenzhen Stock Exchange (SZSE), plus the Beijing Stock Exchange (BSE).

## Step 1 — classify the code

6-digit code prefix tells you the exchange:

| Prefix | Exchange | Board |
|---|---|---|
| 600, 601, 603, 605 | Shanghai (SSE) | Main board |
| 688 | Shanghai (SSE) | STAR Market (科创板) |
| 000, 001 | Shenzhen (SZSE) | Main board |
| 002, 003 | Shenzhen (SZSE) | Main board (formerly SME board) |
| 300, 301 | Shenzhen (SZSE) | ChiNext (创业板) |
| 43, 83, 87, 92 | Beijing (BSE) | — CNINFO coverage exists but is less consistent; verify manually |

## Option A — script (needs real network access to cninfo.com.cn)

```
python scripts/fetch_cn_filings.py 600519                 # Kweichow Moutai — all recent announcements
python scripts/fetch_cn_filings.py 000001 --kind annual    # Ping An Bank — annual reports only
python scripts/fetch_cn_filings.py 600519 --kind annual --save-dir ./filings
```

CNINFO documents are already native PDFs, so `--save-dir` is a raw byte save with real network access — no rendering or reconstruction of any kind, the simplest and highest-fidelity path of the three venues.

**This does not extend to claude.ai's sandbox, despite it being the "easy" no-rendering case.** Tested directly: fetched two real CNINFO PDFs (Kweichow Moutai's 143-page annual report and a 4-page board resolution) with `web_fetch_pdf_extract_text=false`, expecting raw bytes back. Both came back as extracted text instead, regardless of document length. There's currently no confirmed way to get a China A-share filing's actual file bytes from within claude.ai's sandbox — hand the person the direct `static.cninfo.com.cn` URL rather than presenting extracted text as the filing itself.

It posts to CNINFO's disclosure query endpoint (`http://www.cninfo.com.cn/new/hisAnnouncement/query`) and filters results by keyword match against the (Chinese) announcement title, since CNINFO's category-code taxonomy has shifted over the years and isn't reliable to hardcode. `--kind` options: `annual` (年度报告), `interim` (半年度报告), `quarterly` (季度报告), `prospectus` (招股说明书/招股意向书), `all`.

This endpoint is undocumented and can change without notice or add anti-bot friction. If it 403s or returns something unexpected, fall back to Option B. One shift already hit and fixed (2026-07): the endpoint started silently returning zero results unless `stock` is passed as `code,orgId` instead of the bare code — the script now resolves the orgId first via the `topSearch/detailOfQuery` endpoint. The script only fetches the most recent 50 announcements; for older filings (e.g. last year's interim report), pass the title keyword through the endpoint's `searchkey` parameter instead of paging.

## Presenting the annual report

When the skill runs for an A-share ticker, open the latest annual report in the interactive PDF viewer (`mcp__pdf-viewer__display_pdf`) automatically — see SKILL.md's "present the annual report automatically" section. Prefer the official English translation (英文版) when the company publishes one (Kweichow Moutai does; many don't), and give the viewer the `https://static.cninfo.com.cn/...` URL — the viewer streams HTTPS URLs fine but rejects local paths outside its allowed roots.

## Option B — manual browse / search + fetch

- Direct browse URL (works in a browser, or via `web_fetch` if the rendered content comes through): `http://www.cninfo.com.cn/new/disclosure/stock?plate=sz&stockCode=<code>` (Shenzhen) or `plate=sh` (Shanghai).
- Or `web_search "<code> 巨潮资讯网 年报"` (annual report) / `"<code> cninfo announcements"` and fetch whatever surfaces — company IR pages and financial data aggregators often mirror the same filings.

## Language

Filings are almost always **Chinese-only**. Say this to the user up front rather than after retrieving something they can't read, then deliver an English version alongside the original automatically (see SKILL.md's "Non-English filings" section). Finding one, in order:

1. **Official 英文版 on CNINFO** — the annual-report search results include it when published (title like "2025年年度报告（英文版）"). Kweichow Moutai and Wuliangye publish one; most filers don't.
2. **The H-share English annual report on HKEXnews** for A+H dual-listed companies (Ping An 601318→2318.HK, CATL 300750→3750.HK, most big banks/insurers) — same company, English, but HKFRS rather than Chinese accounting standards; say so.
3. **Translate the 摘要 (summary) yourself** when neither exists — extract its text and produce a clearly-labelled unofficial English translation of the summary/key sections in the conversation. Never present a translation as the filing, and don't translate a full report wholesale unless asked.

## Filing cadence

- 年度报告 (annual report) and 半年度报告 (interim/semi-annual report) are the core recurring disclosures.
- Mandatory standalone quarterly reports were relaxed for many issuers under 2023+ rule changes — don't assume a fixed Q1/Q3 filing exists; check what the query actually returns.

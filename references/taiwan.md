# Taiwan — MOPS / TWSE document server

Primary source: **MOPS** (Market Observation Post System, `mops.twse.com.tw`), Taiwan's designated disclosure platform covering both TWSE-listed and TPEx (OTC) companies. The actual document repository behind it is **`doc.twse.com.tw`**, which is what the bundled script talks to.

## Ticker format

4-digit codes (TSMC = 2330, MediaTek = 2454). Suffixes: `.TW` (TWSE main board), `.TWO` (TPEx). **A bare 4-digit code is ambiguous with Hong Kong** — the classifier assumes HK and flags the ambiguity; confirm which market the user means.

## Script (needs real network access to doc.twse.com.tw)

```
python scripts/fetch_tw_filings.py 2330                       # latest annual report (年報)
python scripts/fetch_tw_filings.py 2330 --year 2024
python scripts/fetch_tw_filings.py 2330 --kind financial      # audited financial reports
python scripts/fetch_tw_filings.py 2330 --save-dir ./filings
```

Mechanics, verified live (2026-07, TSMC):

1. `POST https://doc.twse.com.tw/server-java/t57sb01` with `step=1, co_id, year=<ROC publication year>, mtype=F, dtype=F04` returns an HTML list (Big5-encoded) whose rows carry `readfile2("F","2330","2025_2330_20260604F04.pdf")` links. The annual report for fiscal year N is published in year N+1, and the `year` parameter is the ROC (Minguo, CE−1911) **publication** year — the script converts from ordinary fiscal years.
2. A second POST with `step=9, kind, co_id, filename` returns a page containing a **temporary link** (`/pdf/<filename>_<timestamp>.pdf`); GET that within the session window for the actual PDF bytes. Native PDFs — raw byte saves, no rendering.

**TLS gotcha:** TWSE's chain is issued by TWCA, missing from some default trust stores (`CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain` seen live on Windows). `pip install certifi` fixes it; the script uses certifi automatically when present.

## Language

The 年報 (shareholder-meeting annual report, dtype F04) is Chinese. Large exporters (TSMC, MediaTek, etc.) also publish official English annual reports — check the company's own IR site, or MOPS's English side (`mops.twse.com.tw`, switch to English, or the company's 20-F if it's also a US ADR like TSM). Apply the standard "Non-English filings" rules from SKILL.md.

## Fallback

If the endpoint changes or errors, browse MOPS manually: `https://mops.twse.com.tw` → 電子書 → 年報, or `web_search "<company> MOPS annual report"`. In claude.ai's sandbox neither host is reachable — hand over URLs.

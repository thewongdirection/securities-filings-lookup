# Taiwan — MOPS / TWSE document server

Primary source: **MOPS** (Market Observation Post System, `mops.twse.com.tw`), Taiwan's designated disclosure platform covering both TWSE-listed and TPEx (OTC) companies. The actual document repository behind it is **`doc.twse.com.tw`**, which is what the bundled script talks to.

## Status: unreachable from cloud sessions

**TWSE refuses datacentre IP addresses, on every host it runs.** Measured
2026-09 — each of these answers HTTP 200 with the same 800-byte page,
`FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED` / `為安全性考量`:

| Host | What it serves |
|---|---|
| `doc.twse.com.tw` | the document repository this script uses |
| `mops.twse.com.tw` | MOPS (the optimised site) |
| `openapi.twse.com.tw` | the company directory used for name resolution |
| `mopsov.twse.com.tw` | MOPS (the legacy deployment kept alongside it) |
| `emops.twse.com.tw` | the English MOPS |
| `mopsfin.twse.com.tw` | open data, including `t187ap03_L.csv` |

So the filter is **IP-wide at TWSE's edge, not configured per host**: there
is no other TWSE hostname left to try, and a 403 here is TWSE's decision
rather than a missing egress rule. Headers, a browser User-Agent, a
`Referer` and a session cookie were all tried and make no difference; do
not try to defeat the filter, since it is an access control TWSE put up
deliberately rather than a quirk to work around.

**None of this means the Taiwan code is broken.** It is the network that is
unsupported. From a connection TWSE accepts — a residential line, or Claude
Code / Claude Desktop on a normal machine — `fetch_tw_filings.py` works as
documented below, and the live test suite runs the full Taiwan sample there.
In a cloud container it probes once, skips, and says why.

### Routes that were checked and do not help

- **`data.gov.tw`** (Taiwan's government open-data portal) is reachable and
  carries the listed-company dataset (`18419`, from the FSC's Securities and
  Futures Bureau) — but only as a **catalogue**. Its resource URL points
  straight back at `mopsfin.twse.com.tw`, so the data itself is still behind
  TWSE's filter.
- **`quality.data.gov.tw`**, the portal's own cached copy
  (`dq_download_json.php?nid=18419&md5_url=<token>`), is served from
  government infrastructure and so should be outside TWSE's filter. It was
  never tested — it needs its own egress entry, and `data.gov.tw` does not
  cover the subdomain. If Taiwan name resolution is ever wanted from a cloud
  session, that is the one lead worth following; read the current `md5_url`
  off the `data.gov.tw` dataset page rather than hardcoding it, since the
  token is tied to the resource version. It would restore name resolution
  only — the annual reports themselves live on `doc.twse.com.tw`.
- **TPEx** (`www.tpex.org.tw`, `openapi.tpex.org.tw`) is a separate operator
  and might not be filtered, but it covers OTC (上櫃) issuers, not the
  TWSE main board that Taiwanese requests are usually about.

### What to do when TWSE refuses

Say so plainly and hand over the manual route — do not report "no filings
found", which is the wrong answer this venue's work exists to prevent:

1. Open **https://mops.twse.com.tw** in a browser (English: e-Search >
   annual reports) and search the 4-digit code.
2. Or go to the company's own investor-relations site. Large Taiwanese
   exporters (TSMC, MediaTek, Delta) publish an official **English** annual
   report there, which is often what the user actually wants.
3. For a dual-listed name, the US ADR's 20-F on EDGAR is in English and
   fully reachable — TSMC files one as `TSM`.

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

**Some Taiwan filing PDFs are AES-encrypted with an empty password** (Delta Electronics' 年報, seen live) — they open normally in any viewer, but programmatic reading with pypdf requires `pip install cryptography`. The raw-byte save is unaffected either way.

## Showing a Taiwan filing in the PDF viewer

The step-9 temporary links (`https://doc.twse.com.tw/pdf/<filename>_<timestamp>.pdf`) work in the interactive PDF viewer — generate a fresh one right before calling the viewer (they're minted per request; an old one may expire). Verified live: the viewer fetched a 7 MB TSMC annual report through such a link, TWCA certificate and all.

## Language

The 年報 (shareholder-meeting annual report, dtype F04) is Chinese. Large exporters (TSMC, MediaTek, etc.) also publish official English annual reports — check the company's own IR site, or MOPS's English side (`mops.twse.com.tw`, switch to English, or the company's 20-F if it's also a US ADR like TSM). Apply the standard "Non-English filings" rules from SKILL.md.

## Fallback

If the endpoint changes or errors, browse MOPS manually: `https://mops.twse.com.tw` → 電子書 → 年報, or `web_search "<company> MOPS annual report"`. In claude.ai's sandbox neither host is reachable — hand over URLs.

## TWSE refuses some clients with HTTP 200

`doc.twse.com.tw` answers a request it declines with **status 200** and a
page reading "FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED" (UTF-8,
even though listings come back Big5). Nothing in the status code says the
request failed, so the old code decoded it as Big5 into mojibake, found no
`readfile2(...)` links, and reported "No annual filings found for 2330" --
pointing the reader at a stock code that was never the problem.

Measured 2026-09 from a cloud container: every one of ten major issuers
(2330, 2317, 2454, 2412, 2308, 2882, 1301, 2881, 3008, 2303) came back
blocked, and adding a browser User-Agent, a `Referer`, or a session cookie
from the search form changed nothing. It is the client IP -- datacentre
ranges appear to be refused wholesale. `fetch_tw_filings.py` now detects
the page and says so; do not retry it or try to route around it. The
working routes are MOPS in a browser
(https://mops.twse.com.tw, English: e-Search > annual reports), the
issuer's own IR site, or running the skill from a network TWSE accepts.


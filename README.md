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

### Staying up to date

The skill updates itself. Every time it runs, its first step is:

```bash
python scripts/update_skill.py
```

which fast-forwards the clone to the latest published commit and, when anything changed, re-reads `SKILL.md` and the reference docs from disk before doing the lookup — so a session always uses the current version, not whatever was cloned months ago.

It is deliberately conservative, and skips the update (rather than doing anything surprising) when the checkout has uncommitted edits, has local or diverged commits, is on a detached HEAD, isn't a git clone at all, or can't reach the network. It never merges, never rebases, and never discards local work; the worst case is that it does nothing and the existing copy is used. Nothing about a failed update stops the filings lookup.

Two consequences worth knowing:

- **Zip / marketplace installs don't self-update** (there's no git remote to check). Re-download to upgrade, or install via `git clone` as above.
- **If you've edited your copy**, the skill stops updating and says so. Commit your changes on a branch, or keep them and accept that you're pinned to that version.

`python scripts/update_skill.py --check-only` reports whether an update exists without taking it.

## Prerequisites

- **Claude Code (or Claude Desktop)** with real network access — the scripts talk directly to `sec.gov` / `data.sec.gov`, `cninfo.com.cn`, and `hkexnews.hk`. In claude.ai's sandbox those hosts are unreachable, so there the skill degrades gracefully to venue identification and direct links (no PDF downloads).
- **Python 3.10+** on PATH. Lookups and Hong Kong / China PDF saves use only the standard library.
- **For saving US SEC filings as PDFs** (one-time setup, the skill will prompt/do it when first needed):
  ```
  pip install playwright
  playwright install chromium
  ```
  SEC's primary documents are HTML; the skill prints them to PDF with a real headless browser. HK and China filings are native PDFs and need nothing extra.
  Where a Chromium is already installed but Playwright pins a different build (Claude Code on the web), the scripts fall back to the browser under `PLAYWRIGHT_BROWSERS_PATH`; `SKILL_CHROMIUM_PATH` forces a specific binary.
- Optional: `pip install pypdf` — used to verify saved PDFs and to extract text when translating Chinese filing summaries.
- **For Taiwan filings** (and some IR-site downloads): `pip install certifi` — several issuers' TLS chains are missing from default trust stores; the scripts pick up certifi automatically.
- **For Japan name lookup**: `pip install xlrd` (JPX's company directory is an old-format .xls). For Japanese statutory filings via EDINET, register a free API key at api.edinet-fsa.go.jp and set `EDINET_API_KEY`; TDnet needs nothing.

## Network access (cloud containers, proxies, locked-down networks)

The skill talks directly to each regulator's own servers, so those hosts have to be reachable. In a sandboxed environment — Claude Code on the web, a self-hosted runner, a corporate proxy — anything not on the egress allowlist fails at the connection, usually as a `403 Forbidden` on `CONNECT` or `Tunnel connection failed`, which is a network policy rather than a fault in the skill. A blocked venue degrades to venue identification and direct links; the rest keep working.

Allow these hosts:

<!-- egress-hosts:start -->
```text
sec.gov
data.sec.gov
www.sec.gov
www.cninfo.com.cn
static.cninfo.com.cn
www1.hkexnews.hk
www2.hkexnews.hk
doc.twse.com.tw
mops.twse.com.tw
openapi.twse.com.tw
www.release.tdnet.info
api.edinet-fsa.go.jp
disclosure2.edinet-fsa.go.jp
www.jpx.co.jp
data.fca.org.uk
api.data.fca.org.uk
```
<!-- egress-hosts:end -->

| Venue | Hosts | What they serve |
|---|---|---|
| 🇺🇸 United States | `sec.gov`, `www.sec.gov`, `data.sec.gov` | ticker→CIK map, submissions API, filing documents and exhibit indexes |
| 🇨🇳 Mainland China | `www.cninfo.com.cn`, `static.cninfo.com.cn` | announcement search and the PDFs themselves |
| 🇭🇰 Hong Kong | `www1.hkexnews.hk`, `www2.hkexnews.hk` | HKEXnews search, name lookup, documents |
| 🇹🇼 Taiwan | `doc.twse.com.tw`, `mops.twse.com.tw`, `openapi.twse.com.tw` | filing server, MOPS, the company directory used for name resolution |
| 🇯🇵 Japan | `www.release.tdnet.info`, `api.edinet-fsa.go.jp`, `disclosure2.edinet-fsa.go.jp`, `www.jpx.co.jp` | TDnet disclosures, EDINET API and web UI, JPX company list |
| 🇬🇧 London | `data.fca.org.uk`, `api.data.fca.org.uk` | NSM document downloads and the search API |

### Configuring it in Claude Code on the web

claude.ai/code → the cloud icon showing the environment name (the row above the message box) → hover the environment → settings gear → **Network access** → **Custom** → paste the list into **Allowed domains**, one per line.

Keep **Also include default list of common package managers** ticked: the optional `playwright`, `certifi`, `pypdf` and `xlrd` installs come from PyPI. Wildcards work too (`*.cninfo.com.cn`, `*.twse.com.tw`, `*.hkexnews.hk`), though `api.data.fca.org.uk` is two levels deep so it is safer spelled out. **Full** network access covers everything without a list.

Three things that catch people out:

- **GitHub needs no entry.** Repository traffic — including this skill's own [self-update](#staying-up-to-date) — goes through a separate GitHub proxy, independent of the allowlist. The skill keeps updating itself even with network access set to **None**.
- **CNINFO is reached over plain HTTP.** The mainland China endpoints are `http://` URLs, and a proxy that only tunnels HTTPS (`CONNECT`) can still refuse them after the domain is allowed. If China filings fail with the domain allowlisted, that is why.
- **Frankfurt / Germany can't be pinned down.** That venue is a browse-and-IR-site workflow, so beyond `unternehmensregister.de` and `bundesanzeiger.de` it needs whichever host the issuer publishes on (`eqs-news.com` and company IR domains). Add them per company, or use **Full**.

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

- **The skill self-updates before each request** — it fast-forwards its own clone, then uses the freshly pulled instructions and scripts (see [Staying up to date](#staying-up-to-date)). Uncommitted local edits, a missing network, or a non-git install just skip the update.
- **PDFs are delivered automatically** — for every document a request resolves to (annual/quarterly report, requested forms, and each version in dual-listed/non-English cases), the skill saves the PDF to your remembered folder and hands it back, rather than only listing links and waiting to be asked. It scopes to what you actually want, not the whole tail of routine housekeeping filings (Form 4s, disclosure returns), and skips anything already retrieved earlier in the conversation.
- **Dual-listed companies** (A+H shares, US-listed Chinese ADRs with HK listings, dual primaries): the skill asks which market's filings you want — but **if you don't answer within ~15 seconds** (or the session is non-interactive), **it downloads all versions** and lets you narrow afterwards.
- **Non-English filings** (mainland A-shares, mostly): you always get **the original plus an English version** — the company's official English translation if one exists, otherwise the dual-listed English filing (H-share report / 20-F), otherwise a clearly-labelled unofficial translation of the official summary (摘要).
- **A-share annual reports open automatically** in the PDF viewer, preferring the official English edition when published.
- **Exhibits that carry the annual report come too** — some filers (IBM is the standard case) file a thin 10-K that incorporates the MD&A and financial statements from `EX-13` by reference, so saving only the primary document hands back a wrapper with no financials. The US fetcher reads the filing index and saves any EX-13 alongside it (`--exhibits` to widen, `--no-exhibits` to turn off).
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
- `scripts/update_skill.py` — the Step 0 self-update: fast-forward-only, never on a dirty tree
- `scripts/identify_venue.py` — offline ticker→venue classifier (suffixes, bare codes, class shares)
- `scripts/fetch_us_filings.py` — SEC EDGAR listing + PDF save (handles SEC's gzip responses, bot detection, rate-limit caching)
- `scripts/fetch_cn_filings.py` — CNINFO listing + PDF save (handles the `code,orgId` query requirement and server-side title search)
- `scripts/save_filing.py` / `scripts/pdf_utils.py` — save any filing URL as the original PDF
- `references/` — per-venue mechanics, URL patterns, and documented dead ends so they don't get re-tried
- `scripts/net_errors.py` — turns rate limits, blocked hosts and TLS failures into one plain line instead of a traceback
- `scripts/sync_project_skill.py` — mirrors the skill into `.claude/skills/` so the two copies can't drift
- `tests/` — offline regression tests (no network required)

## Development

```bash
python -m unittest discover -s tests      # run every regression test
python scripts/sync_project_skill.py      # after editing SKILL.md / scripts / references
```

The repo root is the canonical skill; `.claude/skills/securities-filings-lookup/` is a generated mirror that lets Claude Code on the web load it as a project skill. `tests/test_skill_layout.py` fails if the two drift, so run the sync script before committing.

The tests are pure standard library and never touch a regulator's server: network responses are stubbed, and the self-update tests run against throwaway local git repositories. Anything that needs live access to SEC/CNINFO/TWSE belongs in a manual check, not the suite.

---
name: securities-filings-lookup
description: Given a stock ticker, determine which exchange or regulator it's listed under (US markets via SEC EDGAR, Hong Kong via HKEX, or mainland China A-shares via CNINFO/SSE/SZSE) and retrieve its official financial and securities filings (10-K, 10-Q, 8-K, 20-F, 6-K, annual reports, interim reports, prospectuses). Use this whenever the user gives a ticker and asks for filings, annual/quarterly reports, regulatory disclosures, or "where can I find X's filings" — even if they don't name the exchange, don't say the word "filing," or just paste a ticker and ask to pull up or look up the company's reports. Also use for dual-listed companies or ADRs (e.g. a US-listed Chinese ADR that also trades in Hong Kong), where filings may exist in more than one jurisdiction.
---

# Securities Filings Lookup

## What this does

Given a ticker, this skill:

1. Works out where the company is actually listed today — ticker formats are ambiguous, dual listings exist, and companies change exchanges, so this isn't always as obvious as it looks.
2. Pulls filings from the correct authoritative regulator/exchange source — never a paywalled aggregator when a free, primary source exists.
3. Saves the actual filing document as a PDF — the original, not a reconstruction of its content. See Step 3 below for exactly what that means and where it doesn't work.

## Why the venue matters

"Financial filings" means something different depending on where a company is listed:

- **United States (SEC EDGAR)** — 10-K/10-Q/8-K for domestic filers, or 20-F/6-K for foreign private issuers (companies incorporated abroad that list in the US, including most Chinese ADRs).
- **Hong Kong (HKEX / HKEXnews)** — Annual Report + Interim Report, plus a continuous stream of announcements (results, connected transactions, notifiable transactions). No mandatory quarterly reporting for most main-board issuers since 2019.
- **Mainland China A-shares (CNINFO, covering SSE + SZSE)** — 年度报告 (annual report), 半年度报告 (interim report), and other disclosures, almost always Chinese-only.

Guessing the wrong venue wastes time searching for filings that were never going to be there. Confirm first, then fetch.

## Step 1 — Identify the ticker and its listing venue

Run the heuristic classifier (no network needed):

```
python scripts/identify_venue.py <ticker>
```

It parses common suffixes (`.HK`, `.SS`/`.SH`, `.SZ`) and bare numeric codes (HK codes are short, 1–5 digits; mainland A-share codes are 6 digits, with the prefix indicating Shanghai vs. Shenzhen vs. Beijing). Alphabetic tickers default to "US-listed."

**Don't stop at the heuristic when it matters.** Confirm with a quick web search whenever:
- The ticker doesn't cleanly match any pattern.
- The stakes are non-trivial (the user is about to make a decision based on the result).
- The company might be a **Chinese ADR** — BABA, JD, PDD, NIO, BIDU, and similar tickers are alphabetic and look "US-listed" by format, and *are* filed with the SEC (20-F/6-K) as foreign private issuers — but the underlying operating business is Chinese, and some of these also carry a secondary Hong Kong listing with its own separate HKEX filings. See "Dual-listed tickers" below: ask which market's filings are required.
- The company recently IPO'd, was acquired, delisted, or renamed — ticker-to-venue mappings can go stale.

## Step 2 — Retrieve filings from the correct source

Route to the matching reference file for the exact mechanics, URL patterns, and a bundled script where one exists:

| Venue | Reference | Script |
|---|---|---|
| United States | `references/us-edgar.md` | `scripts/fetch_us_filings.py` |
| Hong Kong | `references/hong-kong.md` | — (search/browse workflow; no clean public API) |
| Mainland China A-shares | `references/china-a-shares.md` | `scripts/fetch_cn_filings.py` |

**General principle:** prefer the primary regulator's own system over third-party aggregators. It's free, authoritative, and won't be paywalled or stale.

**Environment matters here.** The bundled scripts shell out to `data.sec.gov` / `cninfo.com.cn` directly, which only works where the shell actually has internet access to those domains (Claude Code, Claude Desktop). In claude.ai's sandboxed code execution, outbound network access is restricted to package registries and does **not** include these filing sources — in that environment, use the `web_search` and `web_fetch` tools instead and follow the same search-then-fetch steps described in each reference file. Try the script first if you're unsure which environment you're in; a network error tells you immediately to fall back to search+fetch.

## Step 3 — Save the original filing as a PDF

Once the person has pointed at (or you've identified) the specific filing they want, save the actual document — not a reconstruction of its content. All three venues route through `scripts/pdf_utils.py`, which does exactly one of two things:

- **Already a PDF** (true for essentially all Hong Kong and mainland China filings, and some SEC exhibits): the raw bytes are saved unmodified. Byte-for-byte identical to the source — this was tested by downloading a file and comparing it to the original with `cmp`.
- **HTML** (the normal case for SEC EDGAR primary documents): a real headless browser (Playwright + Chromium) renders the actual page and prints it to PDF — the same output as opening the page in Chrome and choosing Print > Save as PDF. Nothing about the content is parsed, reorganized, or rebuilt; the browser renders the real HTML/CSS as authored, so tables, fonts, and layout match the original.

**Do not try to reconstruct a filing's content from extracted text.** An earlier version of this skill parsed filing HTML (or web_fetch's markdown-extracted text) and rebuilt a new PDF from scratch. That produces something readable, but it is a different document — different layout, different table structure, everything rebuilt except the underlying numbers. If a real browser render isn't available, say so and hand back the original URL. Don't substitute a reconstruction and present it as the filing.

**With real network access (Claude Code, Claude Desktop):**

```
python scripts/fetch_us_filings.py AAOI --forms 10-Q --limit 1 --save-dir ./filings
python scripts/fetch_cn_filings.py 600519 --kind annual --save-dir ./filings
python scripts/save_filing.py <any filing URL> --out ./filings/whatever.pdf
```

One-time setup for the browser-render path:
```
pip install playwright
playwright install chromium
```

**In claude.ai's sandbox: none of this works for any venue, and that's worth stating plainly rather than working around.** Verified directly: `web_fetch` always extracts/transforms content — including for PDFs. Setting `web_fetch_pdf_extract_text=false` was expected to return raw bytes for an already-PDF filing (the CNINFO/HKEX case), but tested against two real CNINFO PDFs (one 143 pages, one 4 pages) and both came back as extracted text either way, not base64. An earlier version of this doc claimed the binary-mode path let claude.ai retrieve HK/China filings byte-faithfully — that claim was untested and turned out to be wrong; it's corrected here. Combined with the earlier finding that even headless Chromium hits the same network allowlist wall as `bash` (a live `403 Host not in allowlist: www.sec.gov`), there is no tool available in this environment, for any of the three venues, that returns a filing's original bytes. The honest response is to give the person the direct URL so they can open or download it themselves — not to hand over extracted text or a reconstruction and imply it's the same thing.

This is a real limitation of the current environment, not a solvable formatting problem. It works correctly in Claude Code / Claude Desktop, where the scripts download raw bytes directly and this was verified with `cmp` to be byte-for-byte identical to the source.

Name saved files something identifiable — `{ticker}_{form}_{date}.pdf` or similar — so a folder of them stays sortable.

One distinction worth keeping straight: saving the person a copy of the actual filing is fine without reservation — these are public regulatory records, and handing someone their own copy is exactly what they're asking for. The usual quote/paraphrase limits apply only if you go on to *summarize or discuss* the filing's contents in the conversation itself.

## Dual-listed tickers: ask which version is required

If venue identification (or a confirming search) reveals the company is listed in more than one market — A+H shares (Ping An = 601318 + 2318.HK, CATL = 300750 + 3750.HK), US-listed Chinese ADRs with secondary Hong Kong listings (BABA = 9988.HK), London/HK dual-primaries like HSBC, or any other cross-listing — **stop and ask which market's filings are required before fetching** (use AskUserQuestion where available, offering each venue plus "both"). Don't silently pick one, even when the ticker's suffix implies a venue — treat the suffix as the presumed default and confirm it, because the other market's documents are often what's actually wanted (e.g. the HK version of an A-share filer is in English). The versions genuinely differ: accounting standards (Chinese CAS vs HKFRS vs US GAAP/IFRS), language, disclosure cadence, and sometimes content.

Common tells that a ticker is dual-listed: a 6-digit A-share code for a household-name financial/tech/EV company (check for an H-share), an alphabetic ADR for a Chinese company (check for a `9xxx.HK` listing), or an HK code whose issuer is a global bank or a mainland company.

## Non-English filings: always deliver the original plus an English version

Whenever the retrieved filing is not in English (mainland A-share filings almost always; some HK announcements), obtain **both** in the same turn, without being asked:

1. **The original** — fetch/save as usual; it is the authoritative document.
2. **An English version**, found in this order:
   - **Official English translation from the same source** — on CNINFO search the annual results for 英文版 (e.g. "2025年年度报告（英文版）"); Kweichow Moutai and Wuliangye publish one, many filers don't.
   - **The other listing's English filing** for dual-listed companies — an A+H company's H-share annual report on HKEXnews is in English (note it follows HKFRS, not CAS); a US-listed ADR's 20-F/6-K on EDGAR is in English.
   - **Translate it yourself** if no official English version exists anywhere: extract the text of the summary document (摘要 for annual reports) or the section the user cares about, and produce an English translation in the conversation, clearly labelled as an unofficial machine translation — it is not the filing and must not be presented as one. Don't translate a full multi-hundred-page report wholesale; translate the summary/key sections and offer to go deeper.

When auto-presenting an A-share annual report in the viewer, this means: official English version in the viewer if it exists, otherwise the original in the viewer plus a translated summary in the reply.

## Mainland China A-shares: present the annual report automatically

When the ticker resolves to a mainland A-share, don't stop at listing filings — open the latest annual report in the interactive PDF viewer (`mcp__pdf-viewer__display_pdf`) in the same turn, without waiting to be asked. Mechanics that matter:

- Find it with `python scripts/fetch_cn_filings.py <code> --kind annual` — the top results are typically the full Chinese report (年度报告), an official English translation (英文版) if the company publishes one, and the short summary (摘要).
- **Prefer the English version (英文版) when it exists**; fall back to the full Chinese report otherwise — and then apply the "Non-English filings" rules above (H-share English report for A+H companies, or a translated summary). Mention the summary (摘要) as the quick-read alternative.
- **Pass the viewer the `https://static.cninfo.com.cn/...` source URL, not a local path.** The viewer only reads local files under its allowed root directories, and saved filings usually live elsewhere — tested: a local path was rejected, while the HTTPS CNINFO URL (same host as the `http://` links the fetch script prints — just switch the scheme) streamed fine.
- Still list the other recent filings alongside the viewer, and offer to save PDFs locally as usual. If the viewer tool isn't available in the environment, fall back to giving the direct URL.

## Edge cases worth handling explicitly

- **Nothing found on the first try** — don't guess silently. Say what was tried (venue, source, search terms) and ask the user to confirm the company or exchange, or try the company's full legal name instead of the ticker.
- **Delisted, renamed, or acquired companies** — filings usually persist under the old CIK (SEC) or stock code (CNINFO). EDGAR full text search and CNINFO both retain historical filers; don't assume "no longer listed" means "no filings exist."
- **Dual-listed companies** — always ask which market's filings are required (see the dedicated section above). The content genuinely differs: HKEX reports follow HKFRS, mainland reports follow Chinese accounting standards, US 20-F filings follow US GAAP or IFRS with a reconciliation.
- **Language** — mainland Chinese filings are almost always Chinese-only. Flag this up front, and follow the "Non-English filings" section above: deliver the original plus an official English version or, failing that, a clearly-labelled translated summary.

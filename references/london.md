# London — FCA National Storage Mechanism (NSM)

Primary source: the **FCA's National Storage Mechanism** (`data.fca.org.uk/#/nsm/nationalstoragemechanism`), the UK's official repository for regulated information filed by LSE-listed issuers. RNS is the news wire; the NSM holds the documents.

## Ticker format

London tickers are alphabetic, up to ~4 letters (AZN, HSBA, NXT, SHEL), suffixed `.L` on most data platforms. The NSM is searched by **company name**, not ticker — resolve the name first (a quick web search if unsure).

## Script (needs real network access to api.data.fca.org.uk)

```
python scripts/fetch_uk_filings.py "AstraZeneca"                 # recent NSM documents
python scripts/fetch_uk_filings.py "AstraZeneca" --grep annual   # filter titles
python scripts/fetch_uk_filings.py "AstraZeneca" --include-rns   # include RNS announcement pages
python scripts/fetch_uk_filings.py "Next" --limit 5 --save-dir ./filings
```

Mechanics, verified live (2026-07, AstraZeneca):

1. `POST https://api.data.fca.org.uk/search?index=fca-nsm-searchdata` with JSON `{"from":0,"size":100,"sort":"publication_date","sortorder":"desc","keyword":"<company>","criteriaObj":{"criteria":[],"dateCriteria":[]}}` — an Elasticsearch-style response; each hit's `_source` has `company`, `headline`, `publication_date`, `download_link`, `source`.
2. Documents download from `https://data.fca.org.uk/artefacts/<download_link>` (supports Range requests). Raw byte saves.

**What didn't work, so don't re-try it:** structured `criteriaObj.criteria` filters with guessed field names (`company`, `issuer_name`, `classifications`) silently return zero hits. Use `keyword` + client-side filtering. Also note headlines are typed by filers and contain real typos ("2025 **Annul** Report" — AstraZeneca's actual filing headline), so prefer loose `--grep` terms like `20-F` or `annual` and be ready to eyeball the list.

Two practical findings from live testing:

- **Generic company names collide** — keyword "Next" returned NextEnergy Solar Fund, not Next plc. Use the fuller legal name ("Next plc") and check the `company` field on each hit.
- **Many issuers file BOTH a PDF and the ESEF zip** — Next plc files "Annual Financial Report" (PDF, viewer-friendly, `--grep "Annual Financial Report"` finds it) alongside "Annual Financial Report (ESEF)" (zip). Prefer the PDF for display, the zip when the user wants the official ESEF filing. Artefact PDFs display fine in the interactive viewer straight from `data.fca.org.uk/artefacts/...`. Some filers also upload bare `.xhtml` artefacts.

## ESEF: modern UK annual reports are ZIPs, not PDFs

Since the UK ESEF mandate, the officially filed annual report is an **ESEF package** — a `.zip` containing the xHTML/iXBRL report (`reports/*.html`) plus taxonomy files. The zip is the authoritative filing; save it as-is. It won't open in a PDF viewer — to let the user read it, extract `reports/*.html` and open that, or fetch the glossy PDF from the company's own IR site (secondary source; say so). Older filings, circulars, prospectuses, and AGM documents are ordinary PDFs.

## Dual listings

Many FTSE issuers are cross-listed: US ADRs with SEC filings (AZN, HSBA=HSBC, BP, SHEL...), or dual primaries (RIO with ASX). Apply SKILL.md's dual-listing rule — ask, defaulting to all after ~15 seconds. A US-ADR 20-F on EDGAR is often the most convenient single English document and renders to PDF.

## Fallback

Browse the NSM portal manually or `web_search "<company> annual report NSM"`. In claude.ai's sandbox the API host isn't reachable — hand over the portal URL.

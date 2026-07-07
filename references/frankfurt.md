# Frankfurt / Germany — Unternehmensregister, Bundesanzeiger, IR sites

Germany has no NSM/EDGAR-style scriptable repository. Know what the official sources are, then take the pragmatic path.

## Official sources (browse, don't script)

- **Unternehmensregister** (`unternehmensregister.de`) — Germany's OAM: the statutory storage for regulated information and financial statements.
- **Bundesanzeiger** (`bundesanzeiger.de`) — publishes annual financial statements. Probed live (2026-07): it's a stateful Apache-Wicket application (session-bound URLs, no stable query endpoints); no captcha on landing but programmatic form interaction is fragile and was not pursued. **Treat both as browse-only** and hand the user URLs rather than scraping.
- **EQS News** (`eqs-news.com`) — the dominant wire for German regulated announcements (ad-hoc disclosures), searchable by company in a browser.

## Ticker format

Alphabetic XETRA symbols, suffix `.DE` (XETRA) or `.F` (Frankfurt floor): SAP.DE, SIE.DE (Siemens), VOW3.DE (VW pref shares — note the share-class digits).

## The pragmatic path, in order

1. **Company IR site** — every DAX/MDAX company publishes its annual report (usually in English AND German), half-year report, and quarterly statements as PDFs on its IR page. `web_search "<company> annual report <year> pdf investor relations"` and fetch/save the PDF with `scripts/save_filing.py <url>`. This is the normal route for German filings and is entirely legitimate — the IR copy is the same document the company files.
2. **SEC filings for NYSE-listed names** — SAP files a 20-F on EDGAR (route via `references/us-edgar.md`); most other German blue chips delisted from the NYSE years ago (Siemens, Deutsche Telekom, BASF...) and only trade OTC as unsponsored ADRs with no SEC reports — don't assume a 20-F exists, check.
3. **Unternehmensregister / Bundesanzeiger browse URLs** for statutory statements when the user specifically needs the filed-as-deposited version.

## Language

German blue chips near-universally publish official English annual reports — the language rules rarely need the translation fallback here. If only German exists (small caps), translate key sections with the usual unofficial labelling.

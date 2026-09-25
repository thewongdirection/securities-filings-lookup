# Singapore — SGX (SGXNet)

SGX is the disclosure venue for Singapore-listed companies, REITs and
business trusts. **Reporting cadence**: since 2020 SGX has not required
quarterly reporting for most issuers, so Singapore looks like Hong Kong —
a full **annual report**, a **half-year** results announcement, and a
continuous stream of SGXNet announcements, rather than anything resembling
a 10-Q.

## What is scriptable, and what is not

Measured against the live service (2026-09), which is the whole point of
this section — the split decides what the skill can promise:

| Endpoint | Result |
|---|---|
| `api.sgx.com/securities/v1.1` | **200**, ~1,330 securities across 14 types |
| `api.sgx.com/securities/v1.1/stocks` | 200, but only the 564 equities |
| `links.sgx.com/1.0.0/corporate-announcements/<AnncID>/` | **200**, server-rendered HTML: full SGXNet metadata and every attachment |
| `links.sgx.com/FileOpen/<name>.ashx?App=Announcement&FileID=<id>` | **200**, `application/pdf`, byte-for-byte |
| `api.sgx.com/announcements/v1.0/securitiescompanyannouncement` | 403 `{"message":"Missing Authentication Token"}` |
| `api.sgx.com/announcements/v1.1/` | 401 `{"message":"Unauthorized"}` |
| `api.sgx.com/annualreports/v1.0/annualreport` | 403 `{"message":"Forbidden"}` |
| `www.sgx.com/securities/company-announcements` | 200, but a 15 KB Angular shell — no data in the HTML |
| `links.sgx.com/1.0.0/corporate-announcements/` (no id) | 404 — no index to walk |

So the only thing that is not scriptable is **discovery**: finding *which*
announcement to fetch. Everything on either side of that step works —
resolving the issuer, and then reading an announcement's metadata and
pulling all of its documents.

Two details keep this from being re-litigated every time someone reads a
403 here:

- **`Missing Authentication Token` does not mean "needs a key."** It is
  what AWS API Gateway answers for a route that does not exist, so a 403
  carrying it says nothing about authentication. Named routes were tried,
  not just bare bases, and they answer the same.
- **The site's own JavaScript contains no endpoint to copy.** The main
  bundle is 2.7 MB and, with all three lazy chunks, contains zero
  occurrences of `api.sgx.com`, `securities/v1` or `v1.1`; the page HTML
  holds only Akamai's RUM beacon. Whatever builds the API base is not
  shipped to the browser in a form worth chasing.

Do not add a headless-browser scrape of the announcements page to work
around it: the skill's rule is primary sources with stable mechanics, and a
JS scrape of a bot-protected SPA is neither. And never guess a FileID —
they are sequential across all issuers, so a probe that "works" hands back
some other company's document.

## The announcement page is the scriptable core

`links.sgx.com/1.0.0/corporate-announcements/<AnncID>/` is plain
server-rendered ASP.NET — `<dl><dt>label</dt><dd>value</dd></dl>` groups
and an attachment list. `fetch_sg_filings.py --announcement` parses it and
saves every attachment:

```
python scripts/fetch_sg_filings.py D05 --announcement U6RBLH1JFNDV1QZT \
    --save-dir ./filings
```

```
Annual Reports and Related Documents  [U6RBLH1JFNDV1QZT]
  issuer       DBS GROUP HOLDINGS LTD
  code         D05
  report type  Annual Report
  broadcast    09-Mar-2026 07:33:28
  period       31/12/2025
  reference    SG260309OTHROF8R
  attachments  2
    877753   DBS Annual Report 2025.pdf
    877754   Letter to Shareholders dated 9 March 2026.pdf
```

Saving all of them matters: DBS's annual-report announcement carries the
Letter to Shareholders beside the report, and ComfortDelGro's FY2025
filing carries the AGM circular beside it. Taking only the first
attachment quietly drops half the filing.

**An announcement is not guaranteed to belong to the issuer you searched
for**, and the script checks rather than assuming. Two live cases shaped
this:

- `3EFY7OL9UR6RG9PA` is **SoftBank Group Corp.**, which lists only debt on
  SGX and so has no `Securities` field and no trading code at all. Naming
  its files after whatever code was on the command line filed SoftBank's
  annual report as `O39_...` — OCBC's code. Files are now named for the
  announcement's own issuer, and a mismatch prints a warning.
- `FIX9B41T0Q5HMZOV` is ComfortDelGro's **Sustainability** Report 2025,
  not its annual report, though both were broadcast on 26 Mar 2026. Read
  the `report type` line before believing a search result's title.

## Use the full directory, not `/stocks`

`api.sgx.com/securities/v1.1/stocks` omits REITs and business trusts —
which in Singapore are a large share of what people ask for (CapLand
Ascendas `A17U`, CapLand Integrated Commercial Trust `C38U`, Mapletree
Industrial `ME8U`, Frasers Centrepoint `J69U`). The parent path
`api.sgx.com/securities/v1.1` returns all 14 security types:

```
stocks 564   dlcertificates 374   structuredwarrants 148   etfs 94
adrs 39   reits 37   sgsbonds 21   companywarrants 15   retailbonds 15
businesstrusts 14   others 6   liprod 4   retailpreferenceshares 2
listedcertificates 1
```

Over 500 of those rows are structured warrants and daily-leverage
certificates, and 585 in all are not reporting issuers — tradable
instruments rather than companies, and they crowd a name search (searching
"DBS" surfaces a shelf of `DBS 5xLongSG...` certificates before the bank).
`fetch_sg_filings.py` keeps `stocks`, `reits`, `businesstrusts`, `adrs` and
`etfs` as issuers, ranks them first, and labels the rest
`(not a reporting issuer)`.

Useful fields: `nc` is the trading code, `n` the short name, `m` the board
(`MAINBOARD` / `CATALIST`), `type` the security type.

## Trading codes are genuinely ambiguous

SGX codes are three or four alphanumerics in every combination. Counted
from the live directory (1,333 securities; the total drifts as instruments
list and expire):

| Shape | Count | Of which reporting issuers | Collides with |
|---|---|---|---|
| Pure letters (`LVR`, `AZG`, `HLPD`) | 747 | 307 | a US ticker |
| Pure digits (`533` = ABR, `541`, `570`) | 27 | 27 | a Hong Kong code |
| Mixed (`D05`, `Z74`, `C6L`, `A17U`, `5E2`) | 559 | 414 | nothing else here |

So `identify_venue.py` claims the mixed shape for Singapore outright, and
leaves the other two with their existing venue — reassigning them would
break every US ticker and every Hong Kong code — while saying in the note
that SGX is a possibility. `.SI` (and `.SGX`) removes all doubt and is what
most sources paste.

## Retrieving a filing

1. **Resolve the issuer** — `python scripts/fetch_sg_filings.py D05` (or a
   name, or `D05.SI`). It prints the code, name, board, security type, and
   the SGXNet browse URL for that issuer.
2. **Find the announcement** — the one manual step. Open the browse URL in
   a browser and pick the filing (annual report, half-year results,
   whatever was asked for). A web search works too, and often better from
   a container: search for `links.sgx.com` together with the issuer and the
   document, which surfaces both URL shapes directly. Check the year and
   the report type before trusting a hit — a sustainability report and an
   annual report are usually broadcast within hours of each other.
3. **Pull it** — either shape works, and both keep the bytes unmodified,
   since SGXNet documents are native PDFs and no browser render is
   involved:

   ```
   # Preferred: the whole announcement, with metadata and every attachment
   python scripts/fetch_sg_filings.py D05 --announcement U6RBLH1JFNDV1QZT \
       --save-dir ./filings

   # A single document, when only its link is to hand
   python scripts/fetch_sg_filings.py C52 --document \
       "https://links.sgx.com/FileOpen/ComfortDelGro%20-%20Annual%20Report%202025.ashx?App=Announcement&FileID=880529" \
       --save-dir ./filings
   ```

   Prefer `--announcement`: it records what the filing *is* (issuer, report
   type, period ended, SGX reference) and cannot miss a second attachment.
   Verified byte-for-byte: StarHub's FY2022 annual report downloads as
   9,366,630 bytes of `%PDF-1.7`, identical to a direct fetch; DBS's FY2025
   report as 8,292,422 bytes of `%PDF-1.6`.

Both document URL shapes are accepted — `FileOpen/<name>.ashx?App=
Announcement&FileID=<id>` and
`1.0.0/corporate-announcements/<AnncID>/<FileID>_<name>.pdf` — and the
FileID is read out of either for the saved filename.

A document on the **issuer's own IR site** is not an SGXNet link and is
refused by `--document` on purpose; use `scripts/save_filing.py <url>` for
those. Note that IR hosts (`ocbc.com`, `dbs.com`, `comfortdelgro.com`) are
not on this skill's egress allowlist, so in a cloud container they need
adding before that route works at all.

## Fallbacks worth knowing

- **The issuer's own IR site** carries the same annual report, usually as a
  glossy PDF, and is often easier to reach than SGXNet's browse UI.
- **Dual listings are common.** Singapore-listed names with a second
  listing file elsewhere too: Jardine Matheson and Jardine Cycle & Carriage
  (also London), Hongkong Land (London), several Chinese and Indonesian
  groups with Hong Kong or Jakarta listings, and a handful of US ADRs. Ask
  which market's filings are wanted, per SKILL.md's dual-listing rule.
- **ACRA** (`acra.gov.sg`) holds statutory filings for every Singapore
  company, listed or not, but its documents are paid and per-request — not
  a route for a listed issuer's annual report.

## Egress

`api.sgx.com`, `www.sgx.com` and `links.sgx.com` must be on the network
allowlist; `api2.sgx.com` answers but serves none of these paths (404 for
every one tried). See the Network access section of README.md.

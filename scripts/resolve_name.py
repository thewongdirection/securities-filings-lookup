#!/usr/bin/env python3
"""
Resolve a company NAME to candidate tickers across all covered venues,
using each regulator's own lookup source. Prints one line per
candidate: venue, ticker/code, and the official name matched.

Usage:
    python resolve_name.py "Tencent"
    python resolve_name.py "Berkshire Hathaway"
    python resolve_name.py "TSMC" --venues us,tw

Sources (all free, official):
- US:      SEC's company_tickers.json (shares fetch_us_filings.py's
           one-day cache in the temp dir)
- HK:      HKEXnews prefix.do name lookup
- China:   CNINFO topSearch (matches Chinese names/pinyin; English
           names usually don't match -- expect misses for English input)
- Taiwan:  TWSE OpenAPI t187ap03_L company directory (Chinese names +
           English abbreviations; TWSE main board only)
- London:  FCA NSM search, distinct company values from recent hits

A name can legitimately resolve in several venues at once (TSMC ->
TSM ADR on EDGAR + 2330 on TWSE). Present the candidates and apply
SKILL.md's confirmation rule; don't silently pick one.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import tempfile
import time
import urllib.parse
import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

USER_AGENT = "Mozilla/5.0 (securities-filings-lookup-skill)"


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


CTX = _context()


def _get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
        return resp.read()


def _post(url: str, data: bytes, content_type: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT, "Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
        return resp.read()


def _cached_json(cache_name: str, url: str, ttl: int = 86400):
    cache = os.path.join(tempfile.gettempdir(), cache_name)
    try:
        if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < ttl:
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
    raw = _get(url)
    data = json.loads(raw.decode())
    try:
        with open(cache, "wb") as f:
            f.write(raw)
    except OSError:
        pass
    return data


def search_us(q: str) -> list[tuple[str, str, str]]:
    ql = q.lower()
    out = []
    try:
        data = _cached_json("sec_company_tickers.json",
                            "https://www.sec.gov/files/company_tickers.json")
    except Exception as e:
        return [("us", "ERROR", str(e))]
    for entry in data.values():
        if ql in entry["title"].lower():
            out.append(("us", entry["ticker"], entry["title"]))
    return out[:8]


def search_hk(q: str) -> list[tuple[str, str, str]]:
    url = ("https://www1.hkexnews.hk/search/prefix.do?"
           + urllib.parse.urlencode({"callback": "cb", "lang": "EN",
                                     "type": "A", "name": q, "market": "SEHK"}))
    try:
        text = _get(url).decode("utf-8", errors="replace")
        inner = text[text.index("(") + 1: text.rindex(")")]
        stocks = json.loads(inner).get("stockInfo") or []
    except Exception as e:
        return [("hk", "ERROR", str(e))]
    return [("hk", s["code"] + ".HK", s["name"]) for s in stocks[:8]]


def search_cn(q: str) -> list[tuple[str, str, str]]:
    try:
        raw = _post("http://www.cninfo.com.cn/new/information/topSearch/detailOfQuery",
                    urllib.parse.urlencode({"keyWord": q, "maxSecNum": "10",
                                            "maxListNum": "5"}).encode(),
                    "application/x-www-form-urlencoded")
        entries = json.loads(raw.decode()).get("keyBoardList") or []
    except Exception as e:
        return [("cn", "ERROR", str(e))]
    return [("cn", e["code"], e.get("zwjc", "?")) for e in entries[:8]
            if e.get("category") == "A股"]


def search_tw(q: str) -> list[tuple[str, str, str]]:
    ql = q.lower()
    try:
        data = _cached_json("twse_companies.json",
                            "https://openapi.twse.com.tw/v1/opendata/t187ap03_L")
    except Exception as e:
        return [("tw", "ERROR", str(e))]
    out = []
    for c in data:
        names = " ".join([c.get("公司名稱", ""), c.get("公司簡稱", ""),
                          c.get("英文簡稱", "")])
        if ql in names.lower():
            out.append(("tw", c["公司代號"] + ".TW",
                        f"{c.get('公司簡稱','?')} / {c.get('英文簡稱','?')}"))
    return out[:8]


def search_jp(q: str) -> list[tuple[str, str, str]]:
    """JPX's English listed-company directory (an old-format .xls;
    requires xlrd: pip install xlrd)."""
    try:
        import xlrd
    except ImportError:
        return [("jp", "SKIPPED", "pip install xlrd to enable Japan name lookup")]
    cache = os.path.join(tempfile.gettempdir(), "jpx_companies_e.xls")
    try:
        if not (os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 86400):
            data = _get("https://www.jpx.co.jp/english/markets/statistics-equities/"
                        "misc/tvdivq0000001vg2-att/data_e.xls", timeout=60)
            with open(cache, "wb") as f:
                f.write(data)
        sh = xlrd.open_workbook(cache).sheet_by_index(0)
    except Exception as e:
        return [("jp", "ERROR", str(e))]
    ql = q.lower()
    out = []
    for r in range(1, sh.nrows):
        name = str(sh.cell_value(r, 2))
        if ql in name.lower():
            code = str(sh.cell_value(r, 1)).split(".")[0]
            out.append(("jp", code + ".T", name))
    return out[:8]


def search_uk(q: str) -> list[tuple[str, str, str]]:
    body = {"from": 0, "size": 40, "sort": "publication_date", "sortorder": "desc",
            "keyword": q, "criteriaObj": {"criteria": [], "dateCriteria": []}}
    try:
        raw = _post("https://api.data.fca.org.uk/search?index=fca-nsm-searchdata",
                    json.dumps(body).encode(), "application/json")
        hits = json.loads(raw.decode()).get("hits", {}).get("hits", [])
    except Exception as e:
        return [("uk", "ERROR", str(e))]
    seen, out = set(), []
    ql = q.lower()
    for h in hits:
        comp = (h["_source"].get("company") or "").strip("; ")
        sym = h["_source"].get("symbol") or "?"
        if comp and comp not in seen and ql.split()[0] in comp.lower():
            seen.add(comp)
            out.append(("uk", sym, comp))
    return out[:8]


VENUES = {"us": search_us, "hk": search_hk, "cn": search_cn,
          "tw": search_tw, "uk": search_uk, "jp": search_jp}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="Company name (or fragment) to resolve")
    parser.add_argument("--venues", default="us,hk,cn,tw,uk,jp",
                        help="Comma-separated subset of us,hk,cn,tw,uk,jp "
                             "(no directory exists for Germany -- web search)")
    args = parser.parse_args()

    any_hit = False
    for v in args.venues.split(","):
        fn = VENUES.get(v.strip())
        if not fn:
            continue
        for venue, code, name in fn(args.name):
            print(f"{venue:<3} {code:<12} {name}")
            any_hit = True
    if not any_hit:
        print("No candidates found in any venue directory. Fall back to a web "
              "search for '<name> stock ticker' and confirm with the user.")


if __name__ == "__main__":
    main()

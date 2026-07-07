#!/usr/bin/env python3
"""
Heuristically classify a ticker by likely listing venue. No network
access required -- pure pattern matching. Always confirm ambiguous or
high-stakes cases with a live search (see SKILL.md, Step 1).

Usage:
    python identify_venue.py <ticker>
"""
from __future__ import annotations

import re
import sys

# Some notes contain Chinese board names; Windows consoles often
# default to a legacy code page that can't encode them.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


SUFFIX_MAP = {
    ".HK": "hong_kong",
    ".SS": "shanghai",
    ".SH": "shanghai",
    ".SZ": "shenzhen",
    ".SSE": "shanghai",
    ".SZSE": "shenzhen",
    ".TW": "taiwan",
    ".TWO": "taiwan",  # TPEx (Taiwan OTC board)
    ".L": "london",
    ".LON": "london",
    ".T": "tokyo",
    ".DE": "frankfurt",  # XETRA
    ".F": "frankfurt",
}


def identify(raw: str) -> dict:
    t = raw.strip().upper()

    for suffix, venue in SUFFIX_MAP.items():
        if t.endswith(suffix):
            code = t[: -len(suffix)]
            if venue == "hong_kong" and code.isdigit():
                code = code.zfill(5)
            return _result(raw, venue, code)

    # Bare numeric: HK codes are short (commonly 1-5 digits, zero-padded
    # to 5 in URLs); mainland A-share codes are exactly 6 digits. Taiwan
    # codes are 4 digits, so bare 4-digit input is genuinely ambiguous.
    if re.fullmatch(r"\d{1,5}", t):
        note = None
        if len(t) == 4:
            note = ("Assumed Hong Kong; 4-digit codes are also the Taiwan "
                    "(2330 = TSMC) and Tokyo (7203 = Toyota) formats. If the "
                    "company is Taiwanese or Japanese, use the .TW / .T "
                    "suffix or confirm with a search.")
        return _result(raw, "hong_kong", t.zfill(5), note=note)

    if re.fullmatch(r"\d{6}", t):
        if t.startswith("688"):
            return _result(raw, "shanghai", t, note="STAR Market (科创板)")
        if t[0] == "6":
            return _result(raw, "shanghai", t)
        if t.startswith(("300", "301")):
            return _result(raw, "shenzhen", t, note="ChiNext (创业板)")
        if t[0] in "023":
            return _result(raw, "shenzhen", t)
        if t[0] in "489":
            return _result(
                raw, "beijing", t,
                note="Possible Beijing Stock Exchange listing -- CNINFO "
                     "coverage is less consistent here, verify manually.",
            )

    # Plain US tickers, including class-share forms like BRK-B / BRK.B
    # (EDGAR's company_tickers.json uses the dash form).
    if re.fullmatch(r"[A-Z]{1,5}([.-][A-Z])?", t):
        return _result(
            raw, "united_states", t.replace(".", "-"),
            note="Confirm with a search if this could be a foreign private "
                 "issuer or dual-listed company (e.g. a Chinese ADR that "
                 "also trades in Hong Kong).",
        )

    return _result(
        raw, "unknown", t,
        note=f"Could not classify from format alone -- if this is a company "
             f"name, run scripts/resolve_name.py \"{raw}\" to find candidate "
             f"tickers; otherwise search \"{raw} stock exchange listing\".",
    )


def _result(raw: str, venue: str, code: str, note: str | None = None) -> dict:
    out = {"input": raw, "venue": venue, "code": code}
    if note:
        out["note"] = note
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python identify_venue.py <ticker>")
        sys.exit(1)
    for key, value in identify(sys.argv[1]).items():
        print(f"{key}: {value}")

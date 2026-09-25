#!/usr/bin/env python3
"""
One naming scheme for saved filings, shared by every venue.

SKILL.md and the README both promise `{identifier}_{label}_{date}` --
the identifier being whatever names the company at that venue (a US
ticker, an A-share code, a Tokyo code) -- so a folder holding filings
from several companies stays sortable and two documents never land on
one name. That promise was kept in the US fetcher only, while the UK,
Japan and China fetchers each invented their own scheme and repeated
their own filename sanitizer.

Sanitizing matters beyond tidiness: titles and exhibit types come from
the source (an EDGAR table cell, a CNINFO announcement title), and a
colon or a backslash in one of them is an unopenable file on Windows or
a stray path separator anywhere.
"""
from __future__ import annotations

import os
import re

# Illegal on Windows, and a path separator or a leading dash is trouble
# everywhere. Control characters come out too.
ILLEGAL = '/\\:*?"<>|'
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def safe_filename(text: str, limit: int = 70) -> str:
    """A filename fragment safe on every platform, trimmed to `limit`."""
    cleaned = CONTROL_RE.sub(" ", text or "")
    # A space, not deletion: dropping the colon in "EX-99:1" would glue
    # it into "EX-991", which reads as a different exhibit.
    cleaned = "".join(" " if c in ILLEGAL else c for c in cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return cleaned[:limit].strip() or "document"


def _fragment(text: str, limit: int) -> str:
    """A sanitized name fragment, or "" when there was nothing to sanitize.

    `safe_filename` falls back to "document" so that it alone can name a
    file. Applied per fragment that fallback is wrong: a venue with no
    date for a document -- SGXNet serves plenty -- got
    `C52_Annual Report 2025_document_880529.pdf`, with the fallback sat
    in the date's place as if it meant something.
    """
    return safe_filename(text, limit) if (text or "").strip() else ""


def filing_name(identifier: str, label: str, date: str,
                extra: str | None = None, ext: str = ".pdf") -> str:
    """`{IDENTIFIER}_{LABEL}_{DATE}[_{EXTRA}]{ext}`.

    identifier: ticker or exchange code. label: form type, document
    kind, or headline. extra: disambiguator, when two documents would
    otherwise share a name. Empty parts are left out rather than filled
    in, so a missing date shortens the name instead of inventing one.
    """
    parts = [_fragment((identifier or "").upper(), 20), _fragment(label, 70),
             _fragment(date, 12)]
    if extra:
        parts.append(_fragment(extra, 30))
    if not ext.startswith("."):
        ext = "." + ext
    return ("_".join(p for p in parts if p) or "document") + ext


def claim_name(used: set[str], name: str, disambiguator: str) -> str:
    """A name no earlier document in this run has taken.

    Overwriting a file from an earlier run is fine -- it is the same
    document -- but two documents in one run must never collide, which
    is what happens when a filer files two of the same form on one day.
    """
    if name not in used:
        used.add(name)
        return name
    stem, ext = os.path.splitext(name)
    tail = safe_filename(disambiguator, 12) or "2"
    candidate = f"{stem}_{tail}{ext}"
    n = 2
    while candidate in used:
        candidate = f"{stem}_{tail}-{n}{ext}"
        n += 1
    used.add(candidate)
    return candidate

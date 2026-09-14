#!/usr/bin/env python3
"""
Turn a network failure into one plain, actionable line instead of a
stack trace.

Every fetch script in this skill talks to a regulator's server, and
every one of them can fail for reasons that are not bugs: a fair-access
rate limit, bot detection, or a sandbox whose proxy simply doesn't
allowlist the host. A traceback buries that. SKILL.md promises the
opposite -- say plainly what happened and fall back to search+fetch --
so the scripts route their failures through here.

Usage in a script:

    from net_errors import run
    if __name__ == "__main__":
        run(main)
"""
from __future__ import annotations

import socket
import ssl
import sys
import urllib.error
from urllib.parse import urlsplit

FALLBACK = ("Fall back to web search + web fetch for this venue (see the "
            "environment note in SKILL.md), or hand the user the direct URL.")

HOST_NOTES = {
    "sec.gov": ("SEC enforces a fair-access limit and has been observed "
                "returning sustained 429s to shared cloud IPs."),
    "cninfo.com.cn": "CNINFO's endpoints are undocumented and add anti-bot friction without notice.",
    "doc.twse.com.tw": "TWSE's document server is reachable only from networks it accepts.",
    "release.tdnet.info": "TDnet serves only the last ~1 month and blocks unusual clients.",
    "api.edinet-fsa.go.jp": "EDINET needs a free API key in EDINET_API_KEY.",
}


def _host(url: str | None) -> str:
    if not url:
        return ""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _note_for(host: str) -> str:
    for suffix, note in HOST_NOTES.items():
        if host == suffix or host.endswith("." + suffix):
            return note
    return ""


def explain(exc: BaseException) -> str:
    """One-or-two-line explanation of a failed request."""
    if isinstance(exc, urllib.error.HTTPError):
        host = _host(getattr(exc, "url", None)) or "the server"
        note = _note_for(host)
        if exc.code == 429:
            head = (f"Rate limited by {host} (HTTP 429). Wait several minutes "
                    "before retrying -- do not retry in a loop.")
        elif exc.code in (401, 403):
            head = (f"{host} refused the request (HTTP {exc.code}). That is "
                    "either bot detection or a sandboxed environment whose "
                    "proxy does not allowlist this host.")
        elif exc.code == 404:
            head = f"{host} has no document at that URL (HTTP 404)."
        else:
            head = f"{host} returned HTTP {exc.code} ({exc.reason})."
        return " ".join(p for p in (head, note, FALLBACK) if p)

    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            return ("TLS certificate verification failed. Several issuers' "
                    "hosts need certifi's CA bundle: pip install certifi, "
                    "then retry.")
        if isinstance(reason, socket.gaierror):
            return (f"DNS lookup failed ({reason}). The host is unreachable "
                    f"from this environment. {FALLBACK}")
        return f"Could not reach the host ({reason}). {FALLBACK}"

    if isinstance(exc, (socket.timeout, TimeoutError)):
        return f"The request timed out. The host may be slow or blocked. {FALLBACK}"

    if isinstance(exc, OSError):
        text = str(exc)
        if "Tunnel connection failed" in text:
            return ("Blocked by this environment's network proxy "
                    f"({text}). The host is not allowlisted here. {FALLBACK}")
        return f"Network error: {text}. {FALLBACK}"

    return f"{type(exc).__name__}: {exc}"


def run(main) -> None:
    """Call main(), reporting failures plainly. Exits 1 on failure."""
    try:
        main()
    except (urllib.error.URLError, OSError) as exc:
        # URLError subclasses OSError, and HTTPError subclasses URLError;
        # one clause covers HTTP status errors, DNS, TLS and proxy refusals.
        print(explain(exc), file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        # Raised by pdf_utils when Chromium is missing -- the message is
        # already written for the reader, so print it as-is.
        print(str(exc), file=sys.stderr)
        sys.exit(1)

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

import errno
import socket
import ssl
import sys
import urllib.error
from urllib.parse import urlsplit

FALLBACK = ("Fall back to web search + web fetch for this venue (see the "
            "environment note in SKILL.md), or hand the user the direct URL.")


class SetupError(RuntimeError):
    """Something about the environment needs fixing, and the message says how.

    Distinct from an ordinary RuntimeError: those are bugs or source-format
    changes, and they keep their traceback so they can be debugged.
    """


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


PROXY_BLOCK = ("Blocked by this environment's network proxy ({detail}). The "
               "host is not allowlisted here -- see the Network access section "
               "of README.md for the hosts this skill needs. " + FALLBACK)

# Failures that are about this machine, not the network. Answering them
# with "fall back to web search" sends the model off in the wrong
# direction and hides the real problem from the user.
FILESYSTEM_ERRORS = (PermissionError, FileNotFoundError, IsADirectoryError,
                     NotADirectoryError, FileExistsError)
# Enumerating errnos misses the long tail (ENAMETOOLONG from a Chinese
# announcement title, ELOOP, EIO). An OSError raised by the filesystem
# carries the path it failed on; a socket error never does.
FILESYSTEM_ERRNOS = {errno.EACCES, errno.EPERM, errno.ENOSPC, errno.EROFS,
                     errno.ENOENT, errno.EDQUOT, errno.EMFILE,
                     errno.ENAMETOOLONG, errno.ELOOP, errno.EIO, errno.EISDIR}


def _is_filesystem_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.URLError):  # includes HTTPError
        return False
    if isinstance(exc, FILESYSTEM_ERRORS):
        return True
    return isinstance(exc, OSError) and (
        getattr(exc, "filename", None) is not None or exc.errno in FILESYSTEM_ERRNOS)


def _is_tunnel_block(text: str) -> bool:
    return "Tunnel connection failed" in text


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
        # urllib re-raises the proxy's refused CONNECT as URLError(OSError),
        # so this has to be checked here rather than in the OSError branch.
        if _is_tunnel_block(str(reason)):
            return PROXY_BLOCK.format(detail=reason)
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

    if _is_filesystem_error(exc):
        return (f"Filesystem error, not a network problem: {exc}. Fix the path "
                "or its permissions -- the save location is wrong or "
                "unwritable, or the disk is full.")

    if isinstance(exc, OSError):
        text = str(exc)
        if _is_tunnel_block(text):
            return PROXY_BLOCK.format(detail=text)
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
    except SetupError as exc:
        # The message is written for the reader and says how to fix it.
        # Any other RuntimeError is a bug or a source-format change and
        # keeps its traceback, which is what makes it debuggable.
        print(str(exc), file=sys.stderr)
        sys.exit(1)

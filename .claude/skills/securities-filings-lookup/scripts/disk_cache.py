#!/usr/bin/env python3
"""
Where the venue directories get cached, and who can read them.

Every venue downloads a directory it then reuses for a day -- SEC's
ticker-to-CIK map, HKEX's name lookup, JPX's company list, SGX's
securities list. Each one used to write it straight into the shared
temp directory as a fixed name: `/tmp/sec_company_tickers.json`.

On a multi-user machine that name is a file any other user can create
first, or replace afterwards. Nothing cached here is secret, so the risk
is not disclosure -- it is that the skill reads someone else's JSON and
answers from it, resolving a ticker to whatever CIK that file claims.
A ticker resolving to the wrong company is exactly the failure this
skill exists to prevent, so it is worth the few lines below.

Everything lands in one directory per user, created 0700 and verified to
be a real directory owned by this user before it is trusted. If that
cannot be guaranteed -- a symlink sits there, or it belongs to someone
else -- this falls back to a fresh private directory for the life of the
process, which costs one extra download rather than trusting the file.
"""
from __future__ import annotations

import functools
import getpass
import os
import re
import stat
import tempfile
import time

DIR_NAME = "securities-filings-lookup"
DEFAULT_TTL = 86400


def _user_tag() -> str:
    """Something stable and per-user to hang the directory name on."""
    if hasattr(os, "getuid"):
        return str(os.getuid())
    try:                                    # Windows has no uid
        return re.sub(r"\W+", "-", getpass.getuser()) or "user"
    except Exception:                       # noqa: BLE001 - getuser() can raise
        return "user"


def is_private_dir(path: str) -> bool:
    """A real directory, owned by us, that no one else can write to.

    `lstat`, not `stat`: a symlink pointed at someone else's directory
    would otherwise pass every check that follows it.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        return False
    return not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)


@functools.cache
def cache_dir() -> str:
    """The directory cached venue directories live in, created if needed.

    Memoized: resolved once per process, so the fallback path below is
    not re-created per lookup and a run keeps one cache either way.
    """
    path = os.path.join(tempfile.gettempdir(), f"{DIR_NAME}-{_user_tag()}")
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        # makedirs' mode applies only when it creates the directory, so an
        # existing one still has to be checked rather than assumed.
        if is_private_dir(path):
            return path
    except OSError:
        pass
    return tempfile.mkdtemp(prefix=f"{DIR_NAME}-")


def cache_path(name: str) -> str:
    """Path for one cached file. `name` is a bare filename, not a path."""
    leaf = os.path.basename(name)
    if not leaf or leaf in (".", ".."):
        raise ValueError(f"not a cache file name: {name!r}")
    return os.path.join(cache_dir(), leaf)


def is_fresh(path: str, ttl: int = DEFAULT_TTL) -> bool:
    """Does this cache file exist and predate its time-to-live?"""
    try:
        return time.time() - os.path.getmtime(path) < ttl
    except OSError:
        return False


def store(path: str, data: bytes) -> None:
    """Write a cache file atomically, and never raise.

    Never raises because a cache is an optimization: failing to write one
    must not fail the lookup that just succeeded. Atomically because two
    runs can refresh the same directory at once, and a half-written file
    read by the other one reads as corruption.
    """
    temp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(temp, "wb") as handle:
            handle.write(data)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass

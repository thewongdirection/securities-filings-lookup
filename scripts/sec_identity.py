#!/usr/bin/env python3
"""
The User-Agent SEC requires, resolved from configuration rather than
hardcoded.

SEC's fair-access policy asks every automated client to declare itself:
"Sample Company Name AdminContact@<sample company domain>.com". Two
things follow, and this module exists because the skill got both wrong.

A browser-spoofed UA is worse than none. `Mozilla/5.0 ...` is exactly
what SEC's edge blocks, and resolve_name.py was sending it to
sec.gov while references/us-edgar.md warned against it.

A fake contact is not a declaration. The scripts shipped
`contact@example.com` -- an address nobody reads, at a domain IANA
reserves for documentation. It satisfies the letter of "descriptive"
and none of the intent: SEC cannot reach whoever is generating the
traffic, which is the entire point of the header.

So the contact is configuration, resolved in this order:

1. `--user-agent` on the command line (this run only)
2. `SEC_USER_AGENT` in the environment
3. `sec_user_agent.txt` next to SKILL.md (remembered across sessions,
   gitignored -- a contact address never syncs through the repo)
4. Failing all three, the request is refused with instructions, because
   there is nothing honest left to send. Measured against the live
   endpoint (2026-09, a small Archives document):

       securities-filings-lookup-skill contact@example.com   200
       securities-filings-lookup (https://github.com/...)    403
       Securities Filings Lookup admin@<domain>              200
       securities-filings-lookup/1.0 contact@<domain>        200

   SEC's edge wants an address, not a project URL -- so a UA with no
   email is not a softer fallback, it simply does not work. Inventing
   one is the thing this module exists to stop, so the skill asks
   instead.

SKILL.md asks the user for a real contact the first time a US filing is
fetched and writes it to (3).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from net_errors import SetupError

CONFIG_FILENAME = "sec_user_agent.txt"
ENV_VAR = "SEC_USER_AGENT"

PROJECT_URL = "https://github.com/thewongdirection/securities-filings-lookup"

EMAIL_RE = re.compile(r"[^@\s]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Domains reserved for documentation, and the placeholders that have
# shipped in this skill or its docs. None of them reaches a human.
PLACEHOLDER_DOMAINS = ("example.com", "example.org", "example.net",
                       "example.edu", "example.invalid", "domain.com",
                       "yourcompany.com", "company.com",
                       # the forms this skill's own docs and messages show
                       "your-domain.com", "your-provider.com")

# What the docs and error messages show. Deliberately rejected by
# validate(), so pasting it verbatim fails with "substitute your own"
# rather than sending a stranger's mailbox to SEC.
EXAMPLE_CONTACT = "<Your Name> <you@your-provider.com>"

SETUP_MESSAGE = (
    "SEC's fair-access policy requires every request to name a contact, and "
    "SEC's edge refuses a User-Agent without an email address (a project URL "
    "alone returns 403). Ask the user for a name and an address they are happy "
    "to send to SEC -- substituting their own for both parts of "
    f"'{EXAMPLE_CONTACT}' -- then either `export {ENV_VAR}='...'` for this "
    f"session or write that one line to {CONFIG_FILENAME} in the skill "
    "directory to remember it. A single run can also pass --user-agent. "
    "Nothing is sent until then: shipping a made-up address is what this "
    "check exists to prevent."
)


def skill_root() -> Path:
    """The directory holding the SKILL.md this module belongs to."""
    return Path(__file__).resolve().parent.parent


def candidate_paths(skill_dir: Path | None = None) -> list[Path]:
    """Where to look for a remembered contact, nearest first.

    The repo carries the skill twice -- at the root, and mirrored under
    .claude/skills/<name>/ for project-skill loading -- so a contact
    written beside one SKILL.md is invisible to the other. Reading both
    means a user configures it once, whichever copy is running.
    """
    base = skill_dir or skill_root()
    paths = [base / CONFIG_FILENAME]
    parts = base.parts
    if len(parts) > 3 and parts[-3] == ".claude" and parts[-2] == "skills":
        paths.append(base.parents[2] / CONFIG_FILENAME)
    return paths


def config_path(skill_dir: Path | None = None) -> Path:
    """Where a new contact is written: next to this copy's SKILL.md."""
    return candidate_paths(skill_dir)[0]


def looks_spoofed(user_agent: str) -> bool:
    """Does this pretend to be a browser? SEC's edge blocks those."""
    lowered = user_agent.strip().lower()
    return lowered.startswith(("mozilla/", "opera/", "chrome/", "safari/"))


def placeholder_contact(user_agent: str) -> str | None:
    """The placeholder domain in this UA, if it carries one."""
    for match in EMAIL_RE.finditer(user_agent):
        domain = match.group(0).rsplit("@", 1)[-1].lower()
        for bad in PLACEHOLDER_DOMAINS:
            if domain == bad or domain.endswith("." + bad):
                return match.group(0)
    return None


def validate(user_agent: str) -> str | None:
    """None if this is a usable declaration, else why it is not."""
    candidate = (user_agent or "").strip()
    if not candidate:
        return "it is empty"
    if len(candidate) < 8:
        return "it is too short to identify anyone"
    if looks_spoofed(candidate):
        return ("it pretends to be a browser, which is what SEC's edge "
                "blocks -- name the tool and a contact instead")
    placeholder = placeholder_contact(candidate)
    if placeholder:
        return (f"'{placeholder}' is a documentation placeholder, not an "
                "address anyone reads -- substitute a real one")
    if not EMAIL_RE.search(candidate):
        return ("it carries no email address, and SEC's edge returns 403 for a "
                "User-Agent without one")
    return None


def stored_user_agent(skill_dir: Path | None = None) -> str | None:
    """The remembered contact, from the nearest file that has one."""
    for path in candidate_paths(skill_dir):
        try:
            # A name with an accent typed in a legacy shell lands here as
            # cp1252; a decode error must not become a traceback.
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except (OSError, ValueError):
            continue
        if text:
            first = text.splitlines()[0].strip()
            if first:
                return first
    return None


def save_user_agent(user_agent: str, skill_dir: Path | None = None) -> Path:
    """Remember a contact for later sessions. Raises ValueError if unusable."""
    problem = validate(user_agent)
    if problem:
        raise ValueError(f"refusing to store that User-Agent: {problem}")
    path = config_path(skill_dir)
    path.write_text(user_agent.strip() + "\n", encoding="utf-8")
    return path


class MissingSecContact(SetupError):
    """No usable contact is configured, so no SEC request is made."""


def resolve(explicit: str | None = None, skill_dir: Path | None = None) -> str:
    """The User-Agent to send to SEC.

    Raises MissingSecContact when nothing valid is configured, naming the
    source at fault rather than silently falling back -- a user who set
    SEC_USER_AGENT badly should not be told the variable is in use.
    """
    for candidate, source in ((explicit, "--user-agent"),
                              (os.environ.get(ENV_VAR), ENV_VAR),
                              (stored_user_agent(skill_dir), CONFIG_FILENAME)):
        if not candidate or not candidate.strip():
            continue
        problem = validate(candidate)
        if problem:
            raise MissingSecContact(
                f"The User-Agent from {source} is unusable: {problem}. "
                f"{SETUP_MESSAGE}")
        return candidate.strip()
    raise MissingSecContact(SETUP_MESSAGE)

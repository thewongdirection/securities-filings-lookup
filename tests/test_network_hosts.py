#!/usr/bin/env python3
"""
The documented egress allowlist has to keep matching the hosts the skill
actually contacts.

A host that a script reaches but the README doesn't list is invisible
until someone's cloud container refuses the connection and the venue
looks broken. This test reads the hosts out of the scripts and reference
docs and checks each one is in README's egress block, so adding a new
source without documenting it fails here instead of in a user's session.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
README = ROOT / "README.md"

import sec_identity  # noqa: E402

URL_RE = re.compile(r"https?://[^\s'\"`)>\]}|]+")
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

# Reference docs name some hosts without a scheme -- references/frankfurt.md
# says `unternehmensregister.de`, not https://unternehmensregister.de -- and
# a scheme-only scan walked straight past them, which is exactly the
# undocumented-host case this file exists to catch. Anchoring on real
# top-level domains keeps filenames like SKILL.md and pdf_utils.py out.
TLDS = "com|cn|hk|tw|uk|jp|de|info|org|net|gov"
BARE_HOST_RE = re.compile(
    rf"\b(?:[a-z0-9][a-z0-9-]*\.)+(?:{TLDS})(?:\.[a-z]{{2}})?\b")

BLOCK_RE = re.compile(r"<!--\s*egress-hosts:start\s*-->(.*?)<!--\s*egress-hosts:end\s*-->",
                      re.S)
OPTIONAL_BLOCK_RE = re.compile(
    r"<!--\s*egress-hosts-optional:start\s*-->(.*?)<!--\s*egress-hosts-optional:end\s*-->",
    re.S)

# Hosts that are not filing sources and so are not part of the allowlist:
# GitHub reaches sessions through its own proxy, and the rest are
# documentation links or placeholders in docstrings.
EXEMPT = {
    "github.com", "www.github.com", "raw.githubusercontent.com",
    "claude.com", "claude.ai", "code.claude.com",
    "example.com", "example.invalid", "www.example.com",
    "host",  # "https://host/owner/repo" in a docstring about URL shapes
    # references/us-edgar.md records this one as a dead end precisely so it
    # is never fetched, so it must not be on an allowlist either.
    "gcs-web.com",
}

# Domains sec_identity names in order to REJECT them as fake contacts.
# Scoped to that module: exempting them everywhere would hide a future
# venue script that really did fetch from company.com.
PER_FILE_EXEMPT = {
    "sec_identity.py": set(sec_identity.PLACEHOLDER_DOMAINS),
    # singapore.md names these to say they are NOT retrieval routes:
    # api2.sgx.com answers but serves none of SGXNet's paths, ACRA's
    # documents are paid and per-request, and the three IR hosts are
    # called out as deliberately off the allowlist. Scoped to that file,
    # so a script that really did fetch ocbc.com still fails this test.
    "singapore.md": {"api2.sgx.com", "acra.gov.sg",
                     "dbs.com", "www.dbs.com",
                     "ocbc.com", "www.ocbc.com",
                     "comfortdelgro.com", "www.comfortdelgro.com"},
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")

SOURCE_FILES = (
    sorted((ROOT / "scripts").glob("*.py"))
    + sorted((ROOT / "references").glob("*.md"))
    + [ROOT / "SKILL.md"]
)


def _hosts_in_block(pattern: re.Pattern, required: bool) -> set[str]:
    match = pattern.search(README.read_text(encoding="utf-8"))
    if not match:
        if required:
            raise AssertionError(
                "README.md has no <!-- egress-hosts:start --> ... "
                "<!-- egress-hosts:end --> block")
        return set()
    return {line.strip() for line in match.group(1).splitlines()
            if HOSTNAME_RE.match(line.strip())}


def documented_hosts() -> set[str]:
    """The paste-ready allowlist."""
    return _hosts_in_block(BLOCK_RE, required=True)


def optional_hosts() -> set[str]:
    """Hosts documented as needed only for the Frankfurt/Germany route."""
    return _hosts_in_block(OPTIONAL_BLOCK_RE, required=False)


def hosts_in(path: Path) -> set[str]:
    return hosts_in_text(path.read_text(encoding="utf-8"),
                         PER_FILE_EXEMPT.get(path.name, set()))


def hosts_in_text(text: str, extra_exempt: set[str] | None = None) -> set[str]:
    found = set()
    for match in URL_RE.finditer(text):
        # Prose runs URLs into punctuation: "...api.edinet-fsa.go.jp," and
        # "(https://example/x)." both have to resolve to the bare host.
        host = urlsplit(match.group(0).rstrip(".,;:)")).hostname
        # f-string URLs put the host in a placeholder -- f"https://
        # {DOCUMENT_HOST}/FileOpen/..." -- and "{document_host" is not a
        # host anyone can allowlist. The constant itself is scanned where
        # it is defined.
        if host and "{" not in host and "}" not in host:
            found.add(host)
    # An address like your-email@domain.com is not a host to allowlist.
    found |= set(BARE_HOST_RE.findall(EMAIL_RE.sub(" ", text)))
    exempt = EXEMPT | (extra_exempt or set())
    return {h for h in found if h not in exempt}


def covered_by(host: str, documented: set[str]) -> bool:
    """Is this host accounted for by the documented list?

    Prose names parent domains -- "requires access to cninfo.com.cn" --
    where the code only ever contacts www. and static. of it. A parent of
    a documented host is covered; a child never is, so a genuinely new
    subdomain still has to be documented.
    """
    return host in documented or any(d.endswith("." + host) for d in documented)


class EgressAllowlistTest(unittest.TestCase):
    def setUp(self):
        self.documented = documented_hosts()
        self.all_documented = self.documented | optional_hosts()

    def test_the_block_is_a_paste_ready_list(self):
        # It goes straight into an allowlist field, one domain per line,
        # so no comments, no bullets, no URLs. The count is only a sanity
        # floor -- coverage is what the other tests assert, so removing a
        # venue doesn't have to mean editing a magic number here.
        self.assertGreater(len(self.documented), 8)
        for host in self.documented:
            with self.subTest(host=host):
                self.assertNotIn("/", host)
                self.assertNotIn(" ", host)

    def test_every_host_the_skill_contacts_is_documented(self):
        undocumented = {}
        for path in SOURCE_FILES:
            for host in hosts_in(path):
                if covered_by(host, self.all_documented):
                    continue
                undocumented.setdefault(host, set()).add(path.name)
        self.assertEqual(
            undocumented, {},
            "These hosts are reached by the skill but missing from README's "
            "egress-hosts block: "
            + ", ".join(f"{h} ({', '.join(sorted(f))})"
                        for h, f in sorted(undocumented.items())))

    def test_each_covered_venue_has_at_least_one_host(self):
        for venue, marker in [("United States", "sec.gov"),
                              ("Mainland China", "cninfo.com.cn"),
                              ("Hong Kong", "hkexnews.hk"),
                              ("Taiwan", "twse.com.tw"),
                              ("Japan", "tdnet.info"),
                              ("Japan (EDINET)", "edinet-fsa.go.jp"),
                              ("London", "fca.org.uk"),
                              ("Singapore", "sgx.com")]:
            with self.subTest(venue=venue):
                self.assertTrue(any(h.endswith(marker) for h in self.documented),
                                f"no {venue} host documented")

    def test_the_readme_explains_where_to_configure_it(self):
        text = README.read_text(encoding="utf-8")
        for expected in ["Network access", "Allowed domains", "Custom"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_bare_hostnames_in_the_docs_are_seen(self):
        # references/frankfurt.md names its hosts without a scheme; a
        # scheme-only scan reported "all documented" while three hosts
        # were missing from the README entirely.
        frankfurt = hosts_in(ROOT / "references" / "frankfurt.md")
        self.assertIn("unternehmensregister.de", frankfurt)
        self.assertIn("bundesanzeiger.de", frankfurt)

    def test_a_parent_domain_is_covered_but_a_new_subdomain_is_not(self):
        documented = {"www.cninfo.com.cn", "static.cninfo.com.cn"}
        self.assertTrue(covered_by("cninfo.com.cn", documented))
        self.assertTrue(covered_by("www.cninfo.com.cn", documented))
        self.assertFalse(covered_by("api.cninfo.com.cn", documented))

    def test_the_placeholder_exemption_is_scoped_to_one_module(self):
        # company.com is exempt where sec_identity rejects it, and nowhere else.
        self.assertIn("company.com", PER_FILE_EXEMPT["sec_identity.py"])
        self.assertNotIn("company.com", EXEMPT)

    def test_email_addresses_are_not_hosts(self):
        self.assertEqual(
            BARE_HOST_RE.findall(EMAIL_RE.sub(" ", "Contact your-email@domain.com")), [])

    def test_fstring_placeholders_are_not_mistaken_for_hosts(self):
        # A hostname built from a constant reads as "{document_host}" in
        # the source, which was reported as an undocumented host.
        self.assertEqual(hosts_in_text('f"https://{DOCUMENT_HOST}/FileOpen/x"'),
                         set())
        self.assertEqual(hosts_in_text('"https://links.sgx.com/FileOpen/x"'),
                         {"links.sgx.com"})

    def test_filenames_are_not_mistaken_for_hosts(self):
        for not_a_host in ("SKILL.md", "pdf_utils.py", "us-edgar.md", "10-K.htm"):
            with self.subTest(text=not_a_host):
                self.assertEqual(BARE_HOST_RE.findall(not_a_host), [])

    def test_the_optional_block_covers_the_germany_route(self):
        optional = optional_hosts()
        self.assertIn("unternehmensregister.de", optional)
        self.assertIn("bundesanzeiger.de", optional)

    def test_skill_md_tells_the_model_what_a_blocked_host_means(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Tunnel connection failed", text)
        self.assertIn("Network access", text)


if __name__ == "__main__":
    unittest.main()

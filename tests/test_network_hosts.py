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
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

URL_RE = re.compile(r"https?://[^\s'\"`)>\]}|]+")
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

BLOCK_RE = re.compile(r"<!--\s*egress-hosts:start\s*-->(.*?)<!--\s*egress-hosts:end\s*-->",
                      re.S)

# Hosts that are not filing sources and so are not part of the allowlist:
# GitHub reaches sessions through its own proxy, and the rest are
# documentation links or placeholders in docstrings.
EXEMPT = {
    "github.com", "www.github.com", "raw.githubusercontent.com",
    "claude.com", "claude.ai", "code.claude.com",
    "example.com", "example.invalid", "www.example.com",
    "host",  # "https://host/owner/repo" in a docstring about URL shapes
}

SOURCE_FILES = (
    sorted((ROOT / "scripts").glob("*.py"))
    + sorted((ROOT / "references").glob("*.md"))
    + [ROOT / "SKILL.md"]
)


def documented_hosts() -> set[str]:
    match = BLOCK_RE.search(README.read_text(encoding="utf-8"))
    if not match:
        raise AssertionError(
            "README.md has no <!-- egress-hosts:start --> ... "
            "<!-- egress-hosts:end --> block")
    return {line.strip() for line in match.group(1).splitlines()
            if HOSTNAME_RE.match(line.strip())}


def hosts_in(path: Path) -> set[str]:
    found = set()
    for match in URL_RE.finditer(path.read_text(encoding="utf-8")):
        # Prose runs URLs into punctuation: "...api.edinet-fsa.go.jp," and
        # "(https://example/x)." both have to resolve to the bare host.
        host = urlsplit(match.group(0).rstrip(".,;:)")).hostname
        if host and host not in EXEMPT:
            found.add(host)
    return found


class EgressAllowlistTest(unittest.TestCase):
    def setUp(self):
        self.documented = documented_hosts()

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
            for host in hosts_in(path) - self.documented:
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
                              ("London", "fca.org.uk")]:
            with self.subTest(venue=venue):
                self.assertTrue(any(h.endswith(marker) for h in self.documented),
                                f"no {venue} host documented")

    def test_the_readme_explains_where_to_configure_it(self):
        text = README.read_text(encoding="utf-8")
        for expected in ["Network access", "Allowed domains", "Custom"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, text)

    def test_skill_md_tells_the_model_what_a_blocked_host_means(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Tunnel connection failed", text)
        self.assertIn("Network access", text)


if __name__ == "__main__":
    unittest.main()

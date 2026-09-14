#!/usr/bin/env python3
"""
Bring this skill's own checkout up to date before it is used, so every
invocation runs the latest published version of the workflow, the
reference docs, and the fetch scripts.

Run it once at the start of a request (SKILL.md, Step 0):

    python scripts/update_skill.py

It fast-forwards the checkout the skill lives in to origin's copy of
the current branch, and prints a short machine-readable status block.
The only status that changes anything is `updated` -- SKILL.md and the
scripts on disk are then newer than whatever was loaded into the
conversation, so they must be re-read before use.

It is deliberately conservative. It will not touch the working tree
when any of these are true:

  * the skill isn't a git checkout at all (zip/marketplace install)
  * `origin` isn't this skill's repository (the skill has been vendored
    into somebody else's project -- pulling there would drag in their
    code, not ours); pass --allow-any-remote for forks under a
    different name
  * HEAD is detached, the branch is missing on origin, local commits
    exist, or history has diverged
  * tracked files have uncommitted edits

Only a clean, strictly-behind checkout is advanced, and only with
`merge --ff-only` -- it can never create a merge commit or discard
local work. Network trouble is not an error: the update is skipped and
the existing local copy is used.

Usage:
    python update_skill.py                   # check and fast-forward
    python update_skill.py --check-only      # report only, never merge
    python update_skill.py --dir PATH        # update another checkout
    python update_skill.py --timeout 30      # per-git-command timeout
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# The skill's own repository name. `origin` has to point at a repo with
# this name before anything is pulled -- see the module docstring.
UPSTREAM_REPO_NAME = "securities-filings-lookup"

DEFAULT_TIMEOUT = 20
MAX_LISTED_FILES = 10

ALLOW_ANY_REMOTE_ENV = "SKILL_UPDATE_ALLOW_ANY_REMOTE"


class GitUnavailable(RuntimeError):
    """git isn't installed, or a git command timed out."""


def _git_env() -> dict:
    env = os.environ.copy()
    # Never block on an interactive credential or host-key prompt: an
    # update that hangs is worse than one that is skipped.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_PAGER"] = "cat"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    env["LC_ALL"] = "C"  # stable, parseable git messages
    return env


def git(repo: Path, *args: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    """Run a git command. Returns (returncode, stdout, stderr), stripped."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout, env=_git_env(),
        )
    except FileNotFoundError as exc:  # no git on PATH
        raise GitUnavailable("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitUnavailable(f"git {args[0]} timed out after {timeout}s") from exc
    except OSError as exc:
        # git present but unrunnable: a noexec mount, a permission bit.
        raise GitUnavailable(f"could not run git ({exc})") from exc
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def redact(url: str) -> str:
    """Strip any embedded credentials before a remote URL is printed.

    Clones made with a token in the URL (https://x-access-token:ghp_...@
    github.com/...) would otherwise leak it into the transcript.
    """
    # Greedy within the authority: userinfo runs to the LAST '@' before
    # the host, and a password may itself contain '@'.
    return re.sub(r"://[^/\s]*@", "://***@", url)


def repo_name_from_url(url: str) -> str:
    """Last path component of a remote URL, minus any .git suffix."""
    cleaned = url.strip().rstrip("/")
    cleaned = re.sub(r"\.git$", "", cleaned)
    # Handles https://host/owner/repo, ssh://host/owner/repo and the
    # scp-style git@host:owner/repo alike -- only the tail matters.
    return re.split(r"[/:]", cleaned)[-1] if cleaned else ""


def first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


class Report:
    """The status block printed to stdout."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def add(self, key: str, value: str) -> None:
        if value:
            self.lines.append((key, value))

    def emit(self) -> None:
        for key, value in self.lines:
            print(f"{key}: {value}")


CONTINUE = ("continue with the local copy -- it is the best available "
            "version, and the lookup itself is unaffected")

RE_READ = ("re-read SKILL.md (and any reference file you use) from disk "
           "before continuing -- the copy loaded earlier is now stale")


def update(skill_dir: Path, *, check_only: bool = False,
           allow_any_remote: bool = False,
           timeout: int = DEFAULT_TIMEOUT) -> Report:
    """Fast-forward the checkout at skill_dir. Never raises."""
    report = Report()
    try:
        return _update(report, skill_dir, check_only=check_only,
                       allow_any_remote=allow_any_remote, timeout=timeout)
    except GitUnavailable as exc:
        # A timeout or a missing git anywhere in the sequence lands here:
        # the update is skipped, and the lookup carries on regardless.
        report.add("status", "skipped")
        report.add("reason", "git-unavailable")
        report.add("detail", str(exc))
        report.add("action", CONTINUE)
        return report


def _update(report: Report, skill_dir: Path, *, check_only: bool,
            allow_any_remote: bool, timeout: int) -> Report:

    def skip(reason: str, detail: str = "", action: str = CONTINUE) -> Report:
        report.add("status", "skipped")
        report.add("reason", reason)
        report.add("detail", detail)
        report.add("action", action)
        return report

    rc, toplevel, _ = git(skill_dir, "rev-parse", "--show-toplevel", timeout=timeout)
    if rc != 0 or not toplevel:
        return skip(
            "not-a-git-checkout",
            f"{skill_dir} is not inside a git repository (zip or marketplace "
            "install), so there is nothing to pull",
        )

    repo = Path(toplevel)
    report.add("repo", str(repo))

    skill_md = (skill_dir / "SKILL.md").resolve()
    if not skill_md.is_file():
        return skip("not-a-skill-directory",
                    f"{skill_dir} has no SKILL.md, so it is not this skill's "
                    "checkout")
    try:
        relative = skill_md.relative_to(repo.resolve()).as_posix()
    except ValueError:
        return skip("unrelated-repo",
                    f"{skill_md} is not inside {repo}")
    if git(repo, "ls-files", "--error-unmatch", "--", relative,
           timeout=timeout)[0] != 0:
        return skip(
            "unrelated-repo",
            f"{repo} does not track {relative} -- the skill sits inside "
            "somebody else's repository (a dotfiles checkout, say), and "
            "pulling there would update their code, not this skill",
        )

    # ls-remote --get-url applies url.<base>.insteadOf; remote get-url does
    # not, and fetch does -- so this is the URL that actually gets contacted.
    rc, origin_url, _ = git(repo, "ls-remote", "--get-url", "origin", timeout=timeout)
    if rc != 0 or not origin_url or origin_url == "origin":
        rc, origin_url, _ = git(repo, "remote", "get-url", "origin", timeout=timeout)
    if rc != 0 or not origin_url:
        return skip("no-origin-remote", f"{repo} has no 'origin' remote configured")
    report.add("remote", redact(origin_url))

    name = repo_name_from_url(origin_url)
    if not allow_any_remote and name.lower() != UPSTREAM_REPO_NAME.lower():
        return skip(
            "foreign-remote",
            f"origin is '{name}', not '{UPSTREAM_REPO_NAME}' -- this looks "
            "like a vendored copy inside another project, so nothing is "
            "pulled; use --allow-any-remote for a renamed fork",
        )

    rc, branch, _ = git(repo, "rev-parse", "--abbrev-ref", "HEAD", timeout=timeout)
    if rc != 0 or not branch:
        return skip("no-branch", "could not determine the current branch")
    if branch.startswith("-"):
        return skip("unsafe-branch-name", f"refusing to fetch a ref named '{branch}'")
    if branch == "HEAD":
        return skip("detached-head", "HEAD is detached; check out a branch to track origin")
    report.add("branch", branch)

    rc, _, err = git(repo, "fetch", "--quiet", "origin", branch, timeout=timeout)
    if rc != 0:
        detail = first_line(err) or "git fetch failed"
        if "couldn't find remote ref" in err:
            return skip("no-remote-branch",
                        f"origin has no branch '{branch}' to update from")
        return skip("offline", f"could not reach origin ({redact(detail)})")

    rc, remote_sha, _ = git(repo, "rev-parse", "FETCH_HEAD", timeout=timeout)
    rc2, local_sha, _ = git(repo, "rev-parse", "HEAD", timeout=timeout)
    if rc != 0 or rc2 != 0:
        return skip("unreadable-refs", "could not resolve HEAD or FETCH_HEAD")

    if local_sha == remote_sha:
        report.add("status", "up-to-date")
        report.add("head", local_sha[:9])
        report.add("action", "continue -- this checkout already matches origin/" + branch)
        return report

    behind = git(repo, "merge-base", "--is-ancestor", local_sha, remote_sha,
                 timeout=timeout)[0] == 0
    ahead = git(repo, "merge-base", "--is-ancestor", remote_sha, local_sha,
                timeout=timeout)[0] == 0
    if ahead:
        return skip("local-ahead",
                    f"this checkout has commits origin/{branch} doesn't have; "
                    "push or reset them yourself")
    if not behind:
        return skip("diverged",
                    f"local and origin/{branch} have both moved on; resolve "
                    "the divergence manually (this script never merges)")

    changed = git(repo, "diff", "--name-only", f"{local_sha}..{remote_sha}",
                  timeout=timeout)[1].splitlines()
    report.add("behind-by", f"{len(changed)} changed file(s)")

    if check_only:
        report.add("status", "update-available")
        report.add("from", local_sha[:9])
        report.add("to", remote_sha[:9])
        report.add("files", _format_files(changed))
        report.add("action", "run without --check-only to fast-forward")
        return report

    dirty = git(repo, "status", "--porcelain", "--untracked-files=no",
                timeout=timeout)[1]
    if dirty:
        return skip(
            "local-changes",
            "tracked files have uncommitted edits, so nothing is pulled: "
            + ", ".join(_porcelain_path(line)
                        for line in dirty.splitlines()[:MAX_LISTED_FILES]),
            action=("continue with the local copy, and tell the user the skill "
                    "could not self-update because of local edits"),
        )

    rc, _, err = git(repo, "merge", "--ff-only", "--quiet", remote_sha, timeout=timeout)
    if rc != 0:
        return skip("merge-failed", first_line(err) or "git merge --ff-only failed")

    report.add("status", "updated")
    report.add("from", local_sha[:9])
    report.add("to", remote_sha[:9])
    report.add("files", _format_files(changed))
    report.add("action", RE_READ)
    return report


def _porcelain_path(line: str) -> str:
    """Path out of a `git status --porcelain` line.

    The two status columns are not a fixed-width prefix to slice: the
    leading column is a space for an unstaged edit, and stdout has
    already been stripped, so a fixed offset eats the first character of
    the filename.
    """
    parts = line.strip().split(None, 1)
    return parts[1] if len(parts) == 2 else line.strip()


def _format_files(paths: list[str]) -> str:
    if not paths:
        return ""
    shown = ", ".join(paths[:MAX_LISTED_FILES])
    extra = len(paths) - MAX_LISTED_FILES
    return shown + (f", +{extra} more" if extra > 0 else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fast-forward this skill's checkout to the latest published version.")
    parser.add_argument("--dir", default=str(Path(__file__).resolve().parent.parent),
                        help="Skill directory to update (default: this skill)")
    parser.add_argument("--check-only", action="store_true",
                        help="Report whether an update exists, without merging")
    parser.add_argument("--allow-any-remote", action="store_true",
                        default=os.environ.get(ALLOW_ANY_REMOTE_ENV) == "1",
                        help="Pull even when origin isn't named "
                             f"'{UPSTREAM_REPO_NAME}' (renamed forks)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Per-git-command timeout in seconds (default {DEFAULT_TIMEOUT})")
    args = parser.parse_args(argv)

    update(
        Path(args.dir),
        check_only=args.check_only,
        allow_any_remote=args.allow_any_remote,
        timeout=args.timeout,
    ).emit()
    # Always succeed: a failed self-update must never stop the lookup the
    # user actually asked for.
    return 0


if __name__ == "__main__":
    sys.exit(main())

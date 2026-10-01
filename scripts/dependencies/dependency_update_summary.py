#!/usr/bin/env python3
"""Render the commits that a dependency update brings in, as Markdown.

The `update_dependencies.yml` workflow in mathlib4 runs `lake update` and puts
the output of this script in the body of the pull request it opens. For each
dependency whose revision changes, the output lists the commits between the old
and the new revision, so that a reviewer sees them without leaving the PR.

The commits come from local git clones (the Lake package checkouts under
`packagesDir`, and any repository given with `--extra`). The script needs no
network access and no token.

Commit subjects are untrusted input. The output never interprets them:

* Each subject is rendered inside a code span. GitHub does not process
  Markdown, HTML, @-mentions or issue references inside a code span.
* Control and format characters (this includes bidirectional overrides) are
  replaced with spaces, and long subjects are truncated.
* Issue references such as `#123` or `owner/repo#123` are extracted and
  rendered as separate links through `redirect.github.com`, so they are
  clickable but GitHub does not add a backlink to the referenced issue or PR.

Every repository name, URL and revision that goes into a link is checked
against a strict pattern first.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# The maximum number of commits listed for one dependency.
MAX_COMMITS = 50
# The maximum number of characters kept from one commit subject.
MAX_SUBJECT = 200
# GitHub rejects a PR body longer than 65536 characters. The caller adds some
# text around this output, so the output stays below this smaller limit.
MAX_OUTPUT = 60000

SHA_RE = re.compile(r"[0-9a-f]{40}")
NAME_RE = re.compile(r"[A-Za-z0-9_.-]+")
GITHUB_URL_RE = re.compile(
    r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?"
)
OWNER_RE = re.compile(r"[A-Za-z0-9-]+")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+")
# `#123`, or `owner/repo#123`. A reference starts at the beginning of the text,
# after whitespace or after an opening bracket, so that `foo#1`, `a/b/c#1` or
# `https://example.com/page#1` do not match.
ISSUE_REF_RE = re.compile(
    r"(?<![^\s(\[{,;])(?:([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+))?#([0-9]{1,9})\b"
)


@dataclass(frozen=True)
class GitHubRepo:
    owner: str
    repo: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


def parse_github_url(url: object) -> GitHubRepo | None:
    """Return the repository for a `https://github.com/owner/repo` URL."""
    if not isinstance(url, str):
        return None
    m = GITHUB_URL_RE.fullmatch(url)
    if m is None or m.group(2) in (".", ".."):
        return None
    return GitHubRepo(m.group(1), m.group(2))


@dataclass(frozen=True)
class Change:
    """One dependency whose revision differs between the old and new state."""

    name: str
    github: GitHubRepo | None
    gitdir: Path | None
    old: str | None
    new: str | None


def is_sha(rev: object) -> bool:
    return isinstance(rev, str) and SHA_RE.fullmatch(rev) is not None


def manifest_changes(old: dict, new: dict, packages_dir: Path) -> list[Change]:
    """Compare two `lake-manifest.json` documents."""

    def git_packages(manifest: dict) -> dict[str, dict]:
        out = {}
        for pkg in manifest.get("packages", []):
            if not isinstance(pkg, dict) or pkg.get("type") != "git":
                continue
            name = pkg.get("name")
            if isinstance(name, str):
                out[name] = pkg
        return out

    old_pkgs = git_packages(old)
    new_pkgs = git_packages(new)
    changes = []
    for name in sorted(old_pkgs.keys() | new_pkgs.keys(), key=str.lower):
        old_pkg = old_pkgs.get(name)
        new_pkg = new_pkgs.get(name)
        old_rev = old_pkg.get("rev") if old_pkg else None
        new_rev = new_pkg.get("rev") if new_pkg else None
        if old_rev == new_rev:
            continue
        url = (new_pkg or old_pkg).get("url")
        gitdir = packages_dir / name if new_pkg and NAME_RE.fullmatch(name) else None
        changes.append(Change(name, parse_github_url(url), gitdir, old_rev, new_rev))
    return changes


# ---------------------------------------------------------------------------
# Git


def git(gitdir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(gitdir), "-c", "log.showSignature=false", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def has_commit(gitdir: Path, rev: str) -> bool:
    return git(gitdir, "cat-file", "-e", f"{rev}^{{commit}}").returncode == 0


def is_ancestor(gitdir: Path, old: str, new: str) -> bool:
    return git(gitdir, "merge-base", "--is-ancestor", old, new).returncode == 0


def commit_count(gitdir: Path, old: str, new: str) -> int:
    proc = git(gitdir, "rev-list", "--count", f"{old}..{new}")
    proc.check_returncode()
    return int(proc.stdout.strip())


def commit_log(gitdir: Path, old: str, new: str, limit: int) -> list[tuple[str, str]]:
    """Return `(sha, subject)` for the newest `limit` commits in `old..new`."""
    proc = git(
        gitdir,
        "log",
        "--no-decorate",
        "--no-color",
        f"--max-count={limit}",
        "--format=%H%x00%s%x00",
        f"{old}..{new}",
        "--",
    )
    proc.check_returncode()
    fields = proc.stdout.split("\x00")
    out = []
    for i in range(0, len(fields) - 1, 2):
        sha = fields[i].strip()
        if is_sha(sha):
            out.append((sha, fields[i + 1]))
    return out


# ---------------------------------------------------------------------------
# Rendering


def clean_text(text: str) -> str:
    """Replace control, format and separator characters, and truncate."""
    chars = [
        " " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") else c for c in text
    ]
    text = " ".join("".join(chars).split())
    if len(text) > MAX_SUBJECT:
        text = text[: MAX_SUBJECT - 1] + "…"
    return text


def code_span(text: str) -> str:
    """Render `text` literally, as a Markdown code span.

    The delimiter is one backtick longer than the longest backtick run in the
    text, so the text cannot close the span. CommonMark strips one space from
    each side of the content, so the padding does not show.
    """
    text = clean_text(text)
    if not text:
        return "*(no subject)*"
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    return f"{fence} {text} {fence}"


def issue_links(subject: str, default: GitHubRepo | None) -> list[str]:
    """Return a link for each issue reference in `subject`.

    The links go through `redirect.github.com`. A link to github.com in a PR
    body makes GitHub add a backlink to the referenced issue or PR; a link
    through `redirect.github.com` does not.
    """
    links = []
    seen = set()
    for m in ISSUE_REF_RE.finditer(clean_text(subject)):
        owner, repo, number = m.group(1), m.group(2), m.group(3)
        if owner is None:
            if default is None:
                continue
            target = default
            label = f"#{number}"
        else:
            if not (OWNER_RE.fullmatch(owner) and REPO_RE.fullmatch(repo)):
                continue
            if repo in (".", ".."):
                continue
            target = GitHubRepo(owner, repo)
            label = f"{owner}/{repo}#{number}"
        key = (target, number)
        if key in seen:
            continue
        seen.add(key)
        links.append(
            f"[{label}](https://redirect.github.com/"
            f"{target.owner}/{target.repo}/issues/{number})"
        )
    return links


def short(sha: str) -> str:
    return sha[:7]


def commit_ref(github: GitHubRepo | None, sha: str) -> str:
    if github is None:
        return f"`{short(sha)}`"
    return f"[`{short(sha)}`]({github.url}/commit/{sha})"


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def heading(change: Change) -> str:
    name = change.name if NAME_RE.fullmatch(change.name) else code_span(change.name)
    return f"### {name}"


def render_change(change: Change, with_commits: bool) -> list[str]:
    lines = [heading(change), ""]
    old, new, github, gitdir = change.old, change.new, change.github, change.gitdir

    if not is_sha(new) and new is not None:
        lines.append("The new revision is not a commit hash.")
        return lines
    if not is_sha(old) and old is not None:
        lines.append("The old revision is not a commit hash.")
        return lines
    if old is None:
        lines.append(f"This dependency is new, at {commit_ref(github, new)}.")
        return lines
    if new is None:
        lines.append(f"This dependency is removed. It was at {commit_ref(github, old)}.")
        return lines

    if github is None:
        range_ref = f"`{short(old)}...{short(new)}`"
    else:
        range_ref = (
            f"[`{short(old)}...{short(new)}`]({github.url}/compare/{old}...{new})"
        )

    if gitdir is None or not gitdir.is_dir():
        lines.append(f"{range_ref}: the local clone is not available.")
        return lines
    if not has_commit(gitdir, new) or not has_commit(gitdir, old):
        lines.append(f"{range_ref}: a revision is missing from the local clone.")
        return lines

    count = commit_count(gitdir, old, new)
    if is_ancestor(gitdir, new, old):
        lines.append(
            f"{range_ref}: **the new revision is older than the old revision.**"
        )
        return lines
    note = ""
    if not is_ancestor(gitdir, old, new):
        note = (
            " **The old revision is not an ancestor of the new revision.** "
            "The list shows the commits in the new revision that are not in the old one."
        )
    lines.append(f"{range_ref}: {plural(count, 'commit')}.{note}")
    if not with_commits:
        return lines

    lines.append("")
    log = commit_log(gitdir, old, new, MAX_COMMITS)
    for sha, subject in log:
        line = f"- {commit_ref(github, sha)} {code_span(subject)}"
        links = issue_links(subject, github)
        if links:
            line += " (" + ", ".join(links) + ")"
        lines.append(line)
    if count > len(log):
        lines.append(
            f"- {plural(count - len(log), 'older commit')} not shown, "
            "see the comparison."
        )
    return lines


def render(changes: list[Change]) -> str:
    if not changes:
        return "No dependency revision changes.\n"

    def build(with_commits: bool) -> str:
        blocks = ["\n".join(render_change(c, with_commits)) for c in changes]
        return "\n\n".join(blocks) + "\n"

    out = build(with_commits=True)
    if len(out) > MAX_OUTPUT:
        out = build(with_commits=False)
        out += "\nThe commit lists are too long for the PR description. See the comparisons.\n"
    return out


# ---------------------------------------------------------------------------
# CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--old-manifest", type=Path, required=True, help="the old lake-manifest.json"
    )
    parser.add_argument(
        "--new-manifest",
        type=Path,
        required=True,
        help="the new lake-manifest.json; its packagesDir is resolved relative to it",
    )
    parser.add_argument(
        "--extra",
        nargs=5,
        action="append",
        default=[],
        metavar=("NAME", "URL", "GITDIR", "OLD", "NEW"),
        help="an additional repository to compare, with a local clone at GITDIR",
    )
    args = parser.parse_args(argv)

    old = json.loads(args.old_manifest.read_text(encoding="utf-8"))
    new = json.loads(args.new_manifest.read_text(encoding="utf-8"))
    packages_dir = args.new_manifest.parent / new.get("packagesDir", ".lake/packages")

    changes = manifest_changes(old, new, packages_dir)
    for name, url, gitdir, old_rev, new_rev in args.extra:
        if old_rev != new_rev:
            changes.append(
                Change(name, parse_github_url(url), Path(gitdir), old_rev, new_rev)
            )

    sys.stdout.write(render(changes))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Find open PRs that may already adapt Mathlib to the dependency update PR's changes.

Upstream maintainers often open a mathlib4 PR adapting Mathlib to their breaking change, and
nobody finds it before the update-dependencies bot's PR fails and someone redoes the fixes on
it. Run hourly by mathlib4's `update_dependencies.yml` while the bot's PR is open, this script
compares every recently updated open PR with the packages the bot's PR bumps and sorts the
related ones into two tiers:

* **likely adaptations** -- the PR makes the same bump as the bot's PR, or part of it (every
  package it changes in `lake-manifest.json` goes to the bot's new revision), or it names an
  upstream PR that the bump includes (through a `<pkg>-pr-testing-N` branch,
  `<repo>#N` or `.../<repo>/pull/N` in its title, or a link from that upstream PR when it also
  changes the package or has an adaptation-style title). Posted to Zulip once per bot PR.
* **also changing** -- the PR changes the package in `lake-manifest.json`, or its title says it
  bumps or adapts to it, and it was pushed within the last 14 days. Listed in the comment only.

PRs that change `lean-toolchain` are skipped: toolchain bumps pick up the same revisions.

The script only reads. It writes the comment body and the Zulip message to `--out-dir`; the
workflow posts them with its own credentials (Zulip first, then the comment, so a failed Zulip
post is retried on the next run). The comment carries the list of PRs already posted to Zulip.

PR heads are fetched into the mathlib4 checkout in the current directory (`--remote`), and only
`lake-manifest.json` is read from them, as data (`git cat-file`). Nothing from a PR is run.

Inputs:
    GH_TOKEN          read-only token for the `gh` CLI
    --repo            `owner/repo` of mathlib4
    --pr              number of the dependency update PR
    --head            use this commit instead of the PR's head (to test against an old update)
    --remote          git remote of the mathlib4 checkout in the current directory
    --base            base branch of the update PR
    --comment-author  login of the account that writes the comment
    --out-dir         where to write `comment.md` and `zulip.md`

Outputs: `comment.md` (empty when there is nothing to create or change) and `zulip.md` (empty
when there is nothing new to post). When `GITHUB_OUTPUT` is set, also the step outputs
`comment_id` (the existing comment, if any), `comment` (`true` when `comment.md` is non-empty)
and `zulip` (the Zulip message itself, for the `send-message` action; empty when none).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

SCAN_DAYS = 30  # open PRs updated within this window are considered at all
PUSH_DAYS = 14  # the "also changing" tier also needs a commit within this window

MARKER = "<!-- related-dependency-prs -->"
POSTED_RE = re.compile(r"<!-- related-dependency-prs-posted: (\[[0-9, ]*\]) -->")


# ── pure helpers ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Bump:
    """A package whose revision the update PR changes."""

    package: str
    url: str
    old: Optional[str]  # None when the package is new
    new: str

    @property
    def github(self) -> Optional[tuple[str, str]]:
        return github_repo(self.url)

    @property
    def names(self) -> list[str]:
        return package_names(self.package, self.url)


@dataclass
class Candidate:
    """An open PR, as far as the matching needs it."""

    number: int
    title: str
    branch: str
    paths: set[str]
    last_commit: datetime
    # package -> new revision, for the packages this PR changes in `lake-manifest.json`
    # relative to its merge base; None when the PR does not touch the manifest
    manifest_changes: Optional[dict[str, str]] = None


@dataclass
class Match:
    number: int
    title: str
    likely: bool
    reasons: list[str] = field(default_factory=list)


def github_repo(url: str) -> Optional[tuple[str, str]]:
    m = re.fullmatch(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?", url)
    return (m.group(1), m.group(2)) if m else None


def package_names(package: str, url: str) -> list[str]:
    """Lowercase names a PR title may use for a package: the package name and the repository
    name, with `-` also read as a space (`importGraph`, `import-graph`, `import graph`)."""
    names = {package.lower()}
    repo = github_repo(url)
    if repo:
        names.add(repo[1].lower())
        names.add(repo[1].lower().replace("-", " "))
    return sorted(names, key=lambda n: (-len(n), n))


def manifest_revisions(text: str) -> dict[str, tuple[str, str]]:
    """package name -> (url, rev) from a `lake-manifest.json`."""
    return {p["name"]: (p.get("url", ""), p.get("rev", "")) for p in json.loads(text)["packages"]}


def changed_revisions(base: dict[str, tuple[str, str]], head: dict[str, tuple[str, str]]) -> dict[str, str]:
    """package -> new revision, for the packages whose entry differs between the two manifests."""
    return {name: rev for name, (url, rev) in head.items() if base.get(name) != (url, rev)}


def bumps_between(base_text: str, head_text: str) -> list[Bump]:
    base, head = manifest_revisions(base_text), manifest_revisions(head_text)
    return [
        Bump(name, url, base[name][1] if name in base else None, rev)
        for name, (url, rev) in sorted(head.items())
        if base.get(name) != (url, rev)
    ]


def upstream_pr_numbers(messages: Iterable[str]) -> set[int]:
    """PR numbers from squash-merge subjects ending in `(#N)`."""
    out = set()
    for message in messages:
        m = re.search(r"\(#(\d+)\)\s*$", message.splitlines()[0] if message else "")
        if m:
            out.add(int(m.group(1)))
    return out


def _alternation(names: list[str]) -> str:
    return "|".join(re.escape(n) for n in names)


def referenced_upstream_prs(title: str, branch: str, names: list[str]) -> set[int]:
    """Upstream PR numbers a PR names in its branch or title."""
    alt = _alternation(names)
    out = set()
    m = re.fullmatch(rf"(?:{alt})-pr-testing-(\d+)|(?:{alt})-(\d+)-testing", branch.lower())
    if m:
        out.add(int(m.group(1) or m.group(2)))
    for a, b in re.findall(rf"(?<![\w-])(?:{alt})\s?#(\d+)|/(?:{alt})/pull/(\d+)", title.lower()):
        out.add(int(a or b))
    return out


def adaptation_style_title(title: str, branch: str, names: list[str]) -> bool:
    """Whether the title or branch says the PR bumps or adapts to the package: a reference to one
    of its PRs, or `bump`/`adapt...`/`update` followed later by its name. A bare mention is not
    enough ("replace `aesop` proof", "reduce some Batteries imports")."""
    if referenced_upstream_prs(title, branch, names):
        return True
    alt = _alternation(names)
    return bool(re.search(rf"\b(?:bump|adapt\w*|update)\b.*(?<![\w-])(?:{alt})(?![\w-])", title.lower()))


def classify(
    pr: Candidate,
    bumps: list[Bump],
    upstream_prs: dict[str, set[int]],
    links: dict[str, dict[int, set[int]]],
    now: datetime,
) -> Optional[Match]:
    """Sort an open PR into a tier, or None when it is not related.

    `upstream_prs[package]` holds the upstream PRs the bump includes; `links[package][n]` the
    upstream PRs among them that link to mathlib4 PR `n`."""
    if "lean-toolchain" in pr.paths:
        return None
    match = Match(pr.number, pr.title, likely=False)
    related = False
    recent = now - pr.last_commit <= timedelta(days=PUSH_DAYS)
    new_revs = {b.package: b.new for b in bumps}
    # A PR that also moves some other package elsewhere (e.g. a `batteries-pr-testing-N` PR that
    # ran `lake update`) only agrees with the bump by accident.
    same_bump = bool(pr.manifest_changes) and all(
        new_revs.get(package) == rev for package, rev in pr.manifest_changes.items())
    for bump in bumps:
        changes = (pr.manifest_changes or {}).get(bump.package)
        styled = adaptation_style_title(pr.title, pr.branch, bump.names)
        if same_bump and changes == bump.new:
            match.likely = True
            match.reasons.append(f"bumps `{bump.package}` to the same revision")
            continue
        named = referenced_upstream_prs(pr.title, pr.branch, bump.names)
        if changes is not None or styled:
            named |= links.get(bump.package, {}).get(pr.number, set())
        included = sorted(named & upstream_prs.get(bump.package, set()))
        if included:
            match.likely = True
            repo = bump.github[1] if bump.github else bump.package
            match.reasons.append(
                "adapts " + ", ".join(f"{repo}#{n}" for n in included) + ", which this update includes"
            )
            continue
        if recent and changes is not None:
            related = True
            match.reasons.append(f"also changes `{bump.package}` in `lake-manifest.json`")
        elif recent and styled:
            related = True
            match.reasons.append(f"its title mentions `{bump.package}`")
    return match if (match.likely or related) else None


def code_span(text: str) -> str:
    """Show untrusted text (a PR title) verbatim and inert: in a code span, `@`-mentions and
    links do nothing on GitHub or Zulip. Backticks would end the span, so they become `'`."""
    text = " ".join(text.split()).replace("`", "'")
    if len(text) > 120:
        text = text[:119] + "…"
    return f"`{text}`"


def posted_from_comment(body: Optional[str]) -> set[int]:
    m = POSTED_RE.search(body or "")
    return set(json.loads(m.group(1))) if m else set()


def render_comment(matches: list[Match], posted: set[int]) -> str:
    likely = [m for m in matches if m.likely]
    also = [m for m in matches if not m.likely]
    lines = [
        MARKER,
        "### Related open PRs",
        "",
        "Open PRs that may already adapt Mathlib to the dependency changes in this PR. "
        "Updated hourly by the update-dependencies workflow.",
        "",
    ]
    if not matches:
        lines.append("None at the moment.")
    if likely:
        lines += ["**Likely adaptations** (posted on Zulip):", ""]
        lines += [f"* #{m.number} {code_span(m.title)}: {'; '.join(m.reasons)}" for m in likely]
        lines.append("")
    if also:
        lines += [f"**Also changing these dependencies** (pushed in the last {PUSH_DAYS} days):", ""]
        lines += [f"* #{m.number} {code_span(m.title)}: {'; '.join(m.reasons)}" for m in also]
        lines.append("")
    lines.append(f"<!-- related-dependency-prs-posted: {json.dumps(sorted(posted))} -->")
    return "\n".join(lines).rstrip() + "\n"


def render_zulip(repo: str, pr: int, new: list[Match]) -> str:
    if not new:
        return ""
    base = f"https://github.com/{repo}/pull"
    lines = [f"Open PRs that may already adapt Mathlib to the dependency changes in [#{pr}]({base}/{pr}):"]
    lines += [f"* [#{m.number}]({base}/{m.number}) {code_span(m.title)}: {'; '.join(m.reasons)}" for m in new]
    return "\n".join(lines) + "\n"


def plan(repo: str, pr: int, matches: list[Match], old_body: Optional[str]) -> tuple[str, str]:
    """The new comment body and the Zulip message, each empty when there is nothing to do.

    Likely adaptations are posted once per update PR: the comment remembers them, and keeps
    remembering them after they drop off the list. No comment is created until there is
    something to list, and an unchanged comment is left alone."""
    posted = posted_from_comment(old_body)
    new = [m for m in matches if m.likely and m.number not in posted]
    zulip = render_zulip(repo, pr, new)
    body = render_comment(matches, posted | {m.number for m in new})
    if old_body is None and not matches:
        body = ""
    elif old_body is not None and old_body.replace("\r\n", "\n").strip() == body.strip():
        body = ""
    return body, zulip


# ── GitHub and git ───────────────────────────────────────────────────────────


# GitHub answers large GraphQL pages with the odd 502/504; retry those after a pause.
_TRANSIENT = re.compile(r"HTTP 50[234]|timed? ?out", re.IGNORECASE)
_BACKOFF_SECONDS = (5, 20, 60)


def gh(*args: str) -> str:
    for backoff in (*_BACKOFF_SECONDS, None):
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return proc.stdout
        if backoff is None or not _TRANSIENT.search(proc.stderr):
            break
        time.sleep(backoff)
    raise RuntimeError(f"gh {' '.join(args[:2])} failed: {proc.stderr.strip()}")


def graphql(query: str, **variables: object) -> dict:
    args = ["api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is not None:
            args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    payload = json.loads(gh(*args))
    if payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {payload['errors']}")
    return payload["data"]


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()}")
    return proc.stdout


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


OPEN_PRS = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 50, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title headRefName updatedAt
        commits(last: 1) { nodes { commit { committedDate } } }
        files(first: 100) { pageInfo { hasNextPage } nodes { path } }
      }
    }
  }
}"""

def _last_files_query(owner: str, name: str, numbers: list[int]) -> str:
    # Changed files come sorted by path, so the root-level files we look for
    # (`lake-manifest.json`, `lean-toolchain`) are among the last ones of a large PR.
    fields = "".join(f"p{n}: pullRequest(number: {n}) {{ files(last: 100) {{ nodes {{ path }} }} }} " for n in numbers)
    return f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields}}} }}'


def open_prs(repo: str, since: datetime) -> list[Candidate]:
    owner, name = repo.split("/")
    out, large, cursor = [], [], None
    while cursor is not False:
        page = graphql(OPEN_PRS, owner=owner, name=name, cursor=cursor)["repository"]["pullRequests"]
        cursor = page["pageInfo"]["endCursor"] if page["pageInfo"]["hasNextPage"] else False
        for node in page["nodes"]:
            if ts(node["updatedAt"]) < since:
                cursor = False
                break
            commits = node["commits"]["nodes"]
            out.append(Candidate(
                number=node["number"],
                title=node["title"],
                branch=node["headRefName"],
                paths={f["path"] for f in node["files"]["nodes"]},
                last_commit=ts(commits[0]["commit"]["committedDate"]) if commits else ts(node["updatedAt"]),
            ))
            if node["files"]["pageInfo"]["hasNextPage"]:
                large.append(out[-1])
    for i in range(0, len(large), 20):
        chunk = large[i:i + 20]
        data = graphql(_last_files_query(owner, name, [c.number for c in chunk]))["repository"]
        for c in chunk:
            c.paths |= {f["path"] for f in data[f"p{c.number}"]["files"]["nodes"]}
    return out


def fetch_heads(remote: str, numbers: Iterable[int]) -> None:
    refspecs = [f"+refs/pull/{n}/head:refs/related-prs/{n}" for n in numbers]
    if refspecs:
        git("fetch", "--quiet", "--no-tags", remote, *refspecs)


def manifest_at(rev: str) -> Optional[str]:
    try:
        return git("cat-file", "blob", f"{rev}:lake-manifest.json")
    except RuntimeError:
        return None


def manifest_changes(head: str, base_ref: str) -> Optional[dict[str, str]]:
    """package -> new revision for what `head` changes in the manifest relative to its merge base."""
    try:
        merge_base = git("merge-base", head, base_ref).strip()
    except RuntimeError:
        return None
    a, b = manifest_at(merge_base), manifest_at(head)
    if a is None or b is None:
        return None
    try:
        return changed_revisions(manifest_revisions(a), manifest_revisions(b))
    except (ValueError, KeyError, TypeError):
        return None  # e.g. conflict markers in the manifest


def included_upstream_prs(bump: Bump) -> set[int]:
    if not bump.old or not bump.github:
        return set()
    owner, name = bump.github
    try:
        out = gh("api", f"repos/{owner}/{name}/compare/{bump.old}...{bump.new}",
                 "--jq", "[.commits[].commit.message]")
    except RuntimeError as e:
        print(f"::warning::could not list the upstream PRs in the {bump.package} bump: {e}", file=sys.stderr)
        return set()
    return upstream_pr_numbers(json.loads(out))


def links_from_upstream(bump: Bump, numbers: set[int], repo: str) -> dict[int, set[int]]:
    """mathlib4 PR -> upstream PRs (among `numbers`) whose timeline shows a link from it."""
    if not numbers or not bump.github:
        return {}
    owner, name = bump.github
    out: dict[int, set[int]] = {}
    numbers_list = sorted(numbers)
    for i in range(0, len(numbers_list), 20):
        chunk = numbers_list[i:i + 20]
        fields = "".join(
            f"""p{n}: issueOrPullRequest(number: {n}) {{ ... on PullRequest {{
              timelineItems(first: 100, itemTypes: [CROSS_REFERENCED_EVENT]) {{ nodes {{
                ... on CrossReferencedEvent {{ source {{ ... on PullRequest {{
                  number repository {{ nameWithOwner }} }} }} }} }} }} }} }}"""
            for n in chunk
        )
        query = f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'
        try:
            data = graphql(query)["repository"]
        except RuntimeError as e:
            print(f"::warning::could not read links to {owner}/{name} PRs: {e}", file=sys.stderr)
            continue
        for n in chunk:
            for item in ((data.get(f"p{n}") or {}).get("timelineItems") or {}).get("nodes", []):
                source = item.get("source") or {}
                if (source.get("repository") or {}).get("nameWithOwner") == repo:
                    out.setdefault(source["number"], set()).add(n)
    return out


def state_comment(repo: str, pr: int, author: str) -> tuple[Optional[int], Optional[str]]:
    """The id and body of this script's comment on the update PR, if there is one."""
    out = gh("api", "--paginate", f"repos/{repo}/issues/{pr}/comments",
             "--jq", f'.[] | select(.user.login == "{author}" and (.body | contains("{MARKER}"))) | [.id, .body]')
    for line in out.splitlines():
        comment_id, body = json.loads(line)
        return int(comment_id), body
    return None, None


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        delimiter = f"EOF_{secrets.token_hex(8)}"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default="leanprover-community/mathlib4")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--base", default="master")
    parser.add_argument("--comment-author", default="mathlib-update-dependencies[bot]")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    base_ref = f"{args.remote}/{args.base}"

    if args.head:
        if subprocess.run(["git", "cat-file", "-e", f"{args.head}^{{commit}}"], capture_output=True).returncode:
            git("fetch", "--quiet", "--no-tags", args.remote, args.head)
        head = args.head
    else:
        fetch_heads(args.remote, [args.pr])
        head = f"refs/related-prs/{args.pr}"
    base_text = manifest_at(git("merge-base", head, base_ref).strip())
    head_text = manifest_at(head)
    if base_text is None or head_text is None:
        raise SystemExit(f"could not read lake-manifest.json for #{args.pr}")
    bumps = bumps_between(base_text, head_text)
    print("Bumps: " + (", ".join(f"{b.package} {(b.old or '-')[:7]} -> {b.new[:7]}" for b in bumps) or "none"))

    candidates = [c for c in open_prs(args.repo, now - timedelta(days=SCAN_DAYS)) if c.number != args.pr]
    with_manifest = [c for c in candidates if "lake-manifest.json" in c.paths and "lean-toolchain" not in c.paths]
    fetch_heads(args.remote, [c.number for c in with_manifest])
    for c in with_manifest:
        c.manifest_changes = manifest_changes(f"refs/related-prs/{c.number}", base_ref)

    upstream_prs = {b.package: included_upstream_prs(b) for b in bumps}
    links = {b.package: links_from_upstream(b, upstream_prs[b.package], args.repo) for b in bumps}
    matches = [m for c in candidates if (m := classify(c, bumps, upstream_prs, links, now))]
    matches.sort(key=lambda m: (not m.likely, m.number))
    print(f"Scanned {len(candidates)} open PRs; related: " + (", ".join(
        f"#{m.number} ({'likely' if m.likely else 'also'})" for m in matches) or "none"))

    comment_id, old_body = state_comment(args.repo, args.pr, args.comment_author)
    body, zulip = plan(args.repo, args.pr, matches, old_body)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "comment.md").write_text(body, encoding="utf-8")
    (args.out_dir / "zulip.md").write_text(zulip, encoding="utf-8")
    write_output("comment_id", str(comment_id or ""))
    write_output("comment", "true" if body else "false")
    write_output("zulip", zulip)
    return 0


if __name__ == "__main__":
    sys.exit(main())

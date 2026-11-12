#!/usr/bin/env python3
"""crossrefsDiff.py — compute the cross-reference tags a PR adds and print a Markdown summary table.

Diffs the two `crossrefs.json` exports (from `mathlib4/scripts/export_crossrefs.lean`), and displays
links to the loc with the new tag, the corresponding resolved database entry url, and the tag's
optional comment.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

# Path characters of `Database.url` and of every id the cross-ref attribute parsers accept; none of
# them can close a Markdown link.
_URL = re.compile(r"https://([^/]+)/[A-Za-z0-9._/]*")

# Dot-separated ASCII identifiers: no `/` and no empty component (so no `..`), nothing that closes a link.
_MODULE = re.compile(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*")

def trusted_hosts(base: dict) -> set[str]:
    """Hosts of the reference urls in the base export (built by master's CI)."""
    return {urlsplit(ref["url"]).netloc
            for entry in base.get("entries", []) for ref in entry.get("refs", [])
            if isinstance(ref.get("url"), str)}

def added_refs(base: dict, head: dict) -> list[dict]:
    """Return the flattened references present in `head` but not in `base`."""
    old = {
        (entry["decl"], ref["db"], ref["id"])
        for entry in base.get("entries", [])
        for ref in entry.get("refs", [])
    }
    return [
        {"decl": entry["decl"], "module": entry["module"], "line": entry["line"],
         "db": ref["db"], "id": ref["id"], "url": ref["url"], "comment": ref["comment"]}
        for entry in head.get("entries", [])
        for ref in entry.get("refs", [])
        if (entry["decl"], ref["db"], ref["id"]) not in old
    ]

#GitHub comments are capped at 65536 chars
COMMENT_MAX_SIZE = 60000

def render(rows: list[dict], repo: str, sha: str, hosts: set[str]) -> str:
    """Render the Markdown table, one row per added cross-reference. A reference is linked only if
    its url is on one of `hosts` and has a plain path, and a declaration only if its module and line
    are well-formed; otherwise they are shown unlinked."""
    if not rows:
        return ""
    lines = ["| Declaration | Reference | Comment |", "|---|---|---|"]
    size = [len(lines[0]), len("\n".join(lines))]
    for r in rows:
        decl = f"`{r['decl']}`"
        if (isinstance(r["module"], str) and _MODULE.fullmatch(r["module"])
                and type(r["line"]) is int and r["line"] > 0):
            path = r["module"].replace(".", "/") + ".lean"
            decl = f"[{decl}](https://github.com/{repo}/blob/{sha}/{path}#L{r['line']})"
        comment = " ".join(r["comment"].split()).replace("|", r"\|")
        ref = f"{r['db']} {r['id']}"
        if isinstance(r["url"], str) and (m := _URL.fullmatch(r["url"])) and m.group(1) in hosts:
            ref = f"[{ref}]({r['url']})"
        line = f"| {decl} | {ref} | {comment} |"
        lines.append(line)
        size.append(size[-1] + 1 + len(line))
    if size[-1] > COMMENT_MAX_SIZE:
        lines = [line for line, s in zip(lines, size) if s <= COMMENT_MAX_SIZE]
        lines.append(f"\n<!-- Truncated {len(rows) - len(lines) + 2} rows due to comment size limit -->")

    return "\n".join(lines) + "\n"

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="crossrefsDiff.py",
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--base-crossrefs", type=Path, required=True,
                   help="`crossrefs.json` of the reference commit")
    p.add_argument("--head-crossrefs", type=Path, required=True,
                   help="`crossrefs.json` of the new commit")
    p.add_argument("--repo", required=True,
                   help="`owner/name` used to build the permalinks")
    p.add_argument("--new-sha", required=True,
                   help="full SHA of the new commit, pinned in the permalinks")
    return p.parse_args(argv)

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    for path, flag in [(args.base_crossrefs, "--base-crossrefs"),
                       (args.head_crossrefs, "--head-crossrefs")]:
        if not path.is_file():
            print(f"crossrefsDiff.py: {flag} '{path}' does not exist.",
                  file=sys.stderr)
            return 1

    base = json.loads(args.base_crossrefs.read_text(encoding="utf-8"))
    head = json.loads(args.head_crossrefs.read_text(encoding="utf-8"))

    rows = added_refs(base, head)
    print(render(rows, args.repo, args.new_sha, trusted_hosts(base)), end="")
    return 0

if __name__ == "__main__":
    sys.exit(main())

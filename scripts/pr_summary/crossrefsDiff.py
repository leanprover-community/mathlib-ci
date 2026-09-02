#!/usr/bin/env python3
"""crossrefsDiff.py — compute the cross-references a PR adds by diffing two `crossrefs.json` exports 
(from mathlib4'/scripts/export_crossrefs.lean`) and prints to stdout an md summary table.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

def added_refs(base: dict, head: dict) -> list[dict]:
    """Return the flattened references present in `head` but not in `base`.
    """
    old = {
        (entry["decl"], ref["db"], ref["id"])
        for entry in base.get("entries", [])
        for ref in entry.get("refs", [])
    }
    return [
        {"decl": entry["decl"], "module": entry["module"], "line": entry["line"],
         "db": ref["db"], "id": ref["id"], "url": ref["url"],
         "comment": ref.get("comment", "")}
        for entry in head.get("entries", [])
        for ref in entry.get("refs", [])
        if (entry["decl"], ref["db"], ref["id"]) not in old
    ]

def render(rows: list[dict], repo: str, sha: str) -> str:
    """Render the Markdown table, one row per added cross-reference.
    """
    if not rows:
        return ""
    lines = ["| Declaration | Reference |", "|---|---|"]
    for r in rows:
        path = r["module"].replace(".", "/") + ".lean"
        permalink = f"https://github.com/{repo}/blob/{sha}/{path}#L{r['line']}"
        comment = f" ({r['comment']})" if r["comment"] else ""
        lines.append(
            f"| [`{r['decl']}`]({permalink}) "
            f"| [{r['db']} {r['id']}]({r['url']}){comment} |"
        )
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
    print(render(rows, args.repo, args.new_sha), end="")
    return 0

if __name__ == "__main__":
    sys.exit(main())

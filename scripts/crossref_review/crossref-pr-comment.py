#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
# Authors: Kim Morrison
"""Compose the body of a PR comment summarising the cross-reference tags
added by a pull request, and exit with a status code the workflow uses to
decide whether to fail CI.

Usage:
    crossref-pr-comment.py \\
        --pr-checkout <path-to-PR-checkout-of-mathlib4> \\
        --base-checkout <path-to-base-checkout-of-mathlib4> \\
        --base-ref <git-ref-of-base-branch-tip> \\
        --output <file>

The companion workflow in mathlib4 (`.github/workflows/crossref_review.yml`)
provides two checkouts:

* `--pr-checkout` points at the PR head — used as DATA only; the workflow
  never executes anything from this tree.
* `--base-checkout` points at the base branch's tip — its
  `scripts/crossref.lean` is the trusted version we actually run. The base's
  `lean-toolchain` is also what `lake env lean --run` picks up.

The orchestrator:

1. Runs `crossref.lean extract` in `--diff` mode against the PR checkout's
   working tree to find every `@[stacks ...]`, `@[kerodon ...]`, or
   `@[wikidata ...]` attribute added between `<base-ref>` and `HEAD`.
2. Groups the tags by database and calls `crossref.lean snippet` once per
   database to fetch labels / descriptions.
3. Writes a Markdown comment body to `--output`, one section per database,
   with one row per added tag. The first line is the title that
   `update_PR_comment.sh` uses to dedupe.
4. Exits 0 if no tags were added (output file is empty; workflow should
   delete any prior bot comment); 1 if tags were added and all resolved;
   2 if any tag was authoritatively missing upstream (CI should fail);
   3 if at least one tag had a transient network failure but none were
   missing (CI should not fail).

Pure stdlib only, so the CI job can run it without `pip install`.
"""
from __future__ import annotations

import argparse
import collections
import os
import subprocess
import sys
from pathlib import Path

# Sentinel string the workflow passes to `update_PR_comment.sh` so it can
# locate and replace the previous bot comment in place.
TITLE = "### Cross-reference tags added by this PR"

# Exit codes.
NO_TAGS = 0
TAGS_OK = 1
TAGS_MISSING = 2
TAGS_NETWORK = 3


def run_lean_script(base_checkout: Path, script: str, args: list[str],
                    cwd: Path) -> tuple[str, int]:
    """Invoke a Lean script from `base-checkout/scripts/<script>` under
    `lake env lean --run`, with the given `cwd` so that any `git diff` the
    script performs sees the PR repository. Stderr is forwarded so it appears
    in CI logs."""
    script_path = base_checkout / "scripts" / script
    proc = subprocess.run(
        ["lake", "env", "lean", "--run", str(script_path), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.stdout, proc.returncode


def md_escape(s: str) -> str:
    """Escape a cell for a Markdown table: pipe → entity, newlines → space."""
    return s.replace("|", "&#124;").replace("\n", " ").replace("\r", " ").strip()


def fetch_snippets(base_checkout: Path, database: str,
                   tags: list[str]) -> dict[str, tuple[str, str]]:
    """Return `{tag: (title, description)}` for every tag in `tags`.
    Missing or errored tags map to `("ERROR", reason)`."""
    if not tags:
        return {}
    out, _ = run_lean_script(
        base_checkout, "crossref.lean",
        ["snippet", database, *sorted(set(tags))],
        cwd=base_checkout,
    )
    table: dict[str, tuple[str, str]] = {}
    for line in out.splitlines():
        parts = line.split("\t", maxsplit=2)
        if len(parts) != 3:
            continue
        tag, title, description = parts
        table[tag] = (title, description)
    return table


def render_section(base_checkout: Path, database: str,
                   rows: list[dict]) -> tuple[str, dict[str, tuple[str, str]]]:
    """Render a Markdown table for one database's tags. Returns the
    rendered section text and the snippet map used (so the caller can
    inspect whether anything was missing/network-failed)."""
    pretty = {
        "stacks": ("Stacks", "https://stacks.math.columbia.edu/tag/"),
        "kerodon": ("Kerodon", "https://kerodon.net/tag/"),
        "wikidata": ("Wikidata", "https://www.wikidata.org/wiki/"),
    }
    label, base_url = pretty[database]
    snippets = fetch_snippets(base_checkout, database,
                              [row["tag"] for row in rows])
    out = [f"#### {label}\n",
           f"| Tag | Declaration | Title | Snippet | Author comment |",
           f"|---|---|---|---|---|"]
    for row in rows:
        tag = row["tag"]
        title, description = snippets.get(tag, ("ERROR", "no response"))
        link = f"[{tag}]({base_url}{tag})"
        decl = (f"`{row['sig_first_line']}`" if row["sig_first_line"] != "?"
                else f"_(declaration not found)_  `{row['decl_name']}`")
        if title == "ERROR":
            title_md = "⚠ **NOT FOUND**" if description == "missing" else f"⚠ {description}"
            description_md = ""
        else:
            title_md = md_escape(title)
            description_md = md_escape(description)
        out.append("| " + " | ".join(
            [link, md_escape(decl), title_md, description_md,
             md_escape(row["comment"])]) + " |")
    out.append("")
    return "\n".join(out), snippets


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pr-checkout", required=True, type=Path,
                   help="Path to the PR head checkout of mathlib4.")
    p.add_argument("--base-checkout", required=True, type=Path,
                   help="Path to the base-ref checkout of mathlib4 — its "
                        "scripts/*.lean are the trusted ones we actually run.")
    p.add_argument("--base-ref", required=True,
                   help="Git ref of the PR's base branch tip (e.g. "
                        "origin/master) — passed to crossref.lean extract "
                        "in --diff mode inside the PR checkout.")
    p.add_argument("--output", required=True, type=Path,
                   help="Where to write the Markdown comment body. Left "
                        "empty (0 bytes) when there are no tags to report.")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    script = args.base_checkout / "scripts" / "crossref.lean"
    if not script.exists():
        print(f"missing companion script in base checkout: {script}",
              file=sys.stderr)
        # Treat as a soft failure: write empty output and exit NO_TAGS so
        # the workflow doesn't post a confusing bot comment.
        args.output.write_text("")
        return NO_TAGS

    extract_out, extract_rc = run_lean_script(
        args.base_checkout, "crossref.lean",
        ["extract", "--diff", f"{args.base_ref}...HEAD"],
        cwd=args.pr_checkout,
    )
    if extract_rc != 0:
        print(f"extractor exited {extract_rc}", file=sys.stderr)
        args.output.write_text("")
        return NO_TAGS

    by_db: dict[str, list[dict]] = collections.defaultdict(list)
    for line in extract_out.splitlines():
        parts = line.split("\t")
        # database, file, line, tag, comment, decl_kind, decl_name, sig
        if len(parts) != 8:
            continue
        by_db[parts[0]].append({
            "file": parts[1], "line": parts[2], "tag": parts[3],
            "comment": parts[4], "decl_kind": parts[5],
            "decl_name": parts[6], "sig_first_line": parts[7],
        })

    if not by_db:
        args.output.write_text("")
        return NO_TAGS

    sections = []
    saw_missing = False
    saw_network = False
    for db in ("wikidata", "stacks", "kerodon"):
        if db not in by_db:
            continue
        section, snippets = render_section(args.base_checkout, db, by_db[db])
        sections.append(section)
        for title, description in snippets.values():
            if title == "ERROR":
                if description == "missing":
                    saw_missing = True
                else:
                    saw_network = True

    body = [TITLE, ""]
    body.append("\n".join(sections))
    if saw_missing:
        body.append("\n⚠ One or more tags above could not be found upstream. "
                    "Please verify the tag identifier or remove the attribute.")
    args.output.write_text("\n".join(body) + "\n")

    if saw_missing:
        return TAGS_MISSING
    if saw_network:
        return TAGS_NETWORK
    return TAGS_OK


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# Copyright (c) 2026 Lean FRO, LLC. All rights reserved.
# Released under Apache 2.0 license as described in the file LICENSE.
# Authors: Kim Morrison
"""Render the bot comment for the mathlib4 cross-reference review workflow.

This script runs in a privileged `workflow_run` context. It consumes a
machine-readable TSV produced by the unprivileged post-build lint step
in mathlib4's `build_template.yml` (which writes
`crossref-added.tsv` to an artifact). For each row, it fetches a
one-line `(title, description)` from the appropriate upstream database
(Wikidata, Stacks Project, Kerodon) and renders a Markdown comment.

We deliberately do not invoke any PR-derived code here: the TSV is
parsed defensively, all fields are markdown-escaped, and snippet
fetching is via Python's stdlib `urllib` against three fixed upstream
URLs. Nothing from the PR's tree is executed.

Usage:
    crossref-pr-comment.py --tsv <path-to-TSV> --output <markdown-out>

TSV format (one row per declaration):

    <database>\\t<tag>\\t<declName>\\t<file>\\t<comment>

Exit codes:

    0 — no rows in the TSV; the workflow should delete any prior comment.
    1 — rows present, all resolved upstream; post the comment.
    2 — at least one tag is missing upstream; post the comment AND fail CI.
    3 — only transient network errors; post the comment with warnings, do
        not fail CI.

Pure stdlib so the workflow can run it without `pip install`.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import html as html_module
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TITLE = "### Cross-reference tags added by this PR"

NO_TAGS = 0
TAGS_OK = 1
TAGS_MISSING = 2
TAGS_NETWORK = 3

USER_AGENT = (
    "mathlib-crossref-bot/1 "
    "(https://github.com/leanprover-community/mathlib4)"
)
TIMEOUT = 15

PRETTY = {
    "stacks":   ("Stacks",   "https://stacks.math.columbia.edu/tag/"),
    "kerodon":  ("Kerodon",  "https://kerodon.net/tag/"),
    "wikidata": ("Wikidata", "https://www.wikidata.org/wiki/"),
}


# --- snippet fetching -------------------------------------------------------


def _get(url: str) -> tuple[int, str]:
    """GET `url` with our user agent. Returns (status, body). Status 0 on
    network failure / timeout."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body
    except Exception:
        return 0, ""


def _collapse_ws(s: str) -> str:
    return " ".join(s.split())


_TAG_RE = re.compile(r"<[a-zA-Z/!][^>]*>")


def _strip_html(html: str) -> str:
    text = _TAG_RE.sub("", html)
    text = html_module.unescape(text)
    return _collapse_ws(text)


def fetch_wikidata(tags: list[str]) -> dict[str, tuple[str, str]]:
    """Return `{qid: (title, description)}` for each QID. Missing or
    errored ids map to `("ERROR", reason)`."""
    out: dict[str, tuple[str, str]] = {}
    todo = list(dict.fromkeys(tags))  # dedupe, preserve order
    while todo:
        batch = todo[:50]
        todo = todo[50:]
        url = ("https://www.wikidata.org/w/api.php?action=wbgetentities"
               f"&ids={'|'.join(batch)}"
               "&languages=en&props=labels%7Cdescriptions&format=json")
        status, body = _get(url)
        if status != 200:
            for q in batch:
                out[q] = ("ERROR", f"wikidata HTTP {status}")
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            for q in batch:
                out[q] = ("ERROR", "wikidata: invalid JSON")
            continue
        err = data.get("error")
        if err:
            code = err.get("code", "")
            info = err.get("info", code)
            if code == "no-such-entity":
                bad = err.get("id")
                if bad:
                    out[bad] = ("ERROR", "missing")
                    todo = [q for q in batch if q != bad] + todo
                else:
                    for q in batch:
                        out[q] = ("ERROR", "missing")
            else:
                for q in batch:
                    out[q] = ("ERROR", f"wikidata {code}: {info}")
            continue
        entities = data.get("entities", {})
        for q in batch:
            ent = entities.get(q)
            if not ent or "missing" in ent:
                out[q] = ("ERROR", "missing")
                continue
            label = ent.get("labels", {}).get("en", {}).get("value", "")
            desc  = ent.get("descriptions", {}).get("en", {}).get("value", "")
            out[q] = (_collapse_ws(label), _collapse_ws(desc))
    return out


def fetch_gerby(database: str, tag: str) -> tuple[str, str]:
    base = {"stacks": "https://stacks.math.columbia.edu",
            "kerodon": "https://kerodon.net"}[database]
    status, body = _get(f"{base}/data/tag/{tag}/content/statement")
    if status != 200:
        return ("ERROR", f"{database} HTTP {status}")
    if body.strip() == "This tag does not exist.":
        return ("ERROR", "missing")
    # Title: from `class="env-{type}" id="<tag>"` + the data-tag span content.
    env_m = re.search(r'class="env-([a-zA-Z]+)"', body)
    env_type = env_m.group(1).capitalize() if env_m else ""
    ref_m = re.search(r'data-tag="[^"]+">([^<]+)</span>', body)
    reference = ref_m.group(1).strip() if ref_m else ""
    title = f"{env_type} {reference}".strip()
    snippet = _strip_html(body)
    if not title and not snippet:
        return ("ERROR", f"{database}: could not parse statement")
    return (title, snippet)


def fetch_snippets(database: str, tags: list[str]) -> dict[str, tuple[str, str]]:
    if not tags:
        return {}
    if database == "wikidata":
        return fetch_wikidata(tags)
    # Gerby (Stacks, Kerodon): no batch endpoint — fan out concurrently.
    unique = list(dict.fromkeys(tags))
    out: dict[str, tuple[str, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        future_to_tag = {ex.submit(fetch_gerby, database, t): t for t in unique}
        for future in concurrent.futures.as_completed(future_to_tag):
            out[future_to_tag[future]] = future.result()
    return out


# --- TSV parsing -----------------------------------------------------------


def parse_tsv(path: Path) -> dict[str, list[dict]]:
    """Read the TSV emitted by `crossref.lean check`. Each row:
    `database\\ttag\\tdeclName\\tfile\\tcomment`.
    Unknown databases are silently dropped (defensive)."""
    by_db: dict[str, list[dict]] = collections.defaultdict(list)
    if not path.exists():
        return by_db
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        parts = raw.split("\t")
        if len(parts) < 4:
            continue
        # Pad to 5 in case `comment` is empty (Lean's intercalate could drop it).
        while len(parts) < 5:
            parts.append("")
        database, tag, decl_name, file, comment = parts[:5]
        if database not in PRETTY:
            continue
        by_db[database].append({
            "tag": tag,
            "decl_name": decl_name,
            "file": file,
            "comment": comment,
        })
    return by_db


# --- markdown rendering ----------------------------------------------------


def md_escape(s: str) -> str:
    """Escape a cell for a Markdown table: pipe → entity, newlines → space.
    We are paranoid about input that originated in PR code, even though by
    this point it has been through Lean's parser and our TSV parser.
    Backticks are preserved to keep `inline code` rendering."""
    return (s.replace("\r", " ")
             .replace("\n", " ")
             .replace("|", "&#124;")
             .strip())


def render_section(database: str, rows: list[dict],
                   snippets: dict[str, tuple[str, str]]) -> str:
    label, base_url = PRETTY[database]
    out = [f"#### {label}\n",
           "| Tag | Declaration | Title | Snippet | Author comment |",
           "|---|---|---|---|---|"]
    for row in rows:
        tag = row["tag"]
        decl_link = (f"[`{row['decl_name']}`]"
                     f"(https://leanprover-community.github.io/mathlib4_docs/"
                     f"{row['file'].replace('.lean', '.html')}"
                     f"#{row['decl_name']})")
        title, description = snippets.get(tag, ("ERROR", "no response"))
        if title == "ERROR":
            title_md = "⚠ **NOT FOUND**" if description == "missing" else f"⚠ {description}"
            desc_md = ""
        else:
            title_md = md_escape(title)
            desc_md = md_escape(description)
        link = f"[{tag}]({base_url}{tag})"
        out.append("| " + " | ".join([
            link, md_escape(decl_link), title_md, desc_md, md_escape(row["comment"])
        ]) + " |")
    out.append("")
    return "\n".join(out)


# --- driver ----------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tsv", required=True, type=Path,
                   help="TSV produced by `scripts/crossref.lean check`.")
    p.add_argument("--output", required=True, type=Path,
                   help="Where to write the Markdown comment body. Empty "
                        "(0 bytes) when there are no tags to report.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    by_db = parse_tsv(args.tsv)
    if not by_db:
        args.output.write_text("")
        return NO_TAGS

    saw_missing = False
    saw_network = False
    sections = []
    for db in ("wikidata", "stacks", "kerodon"):
        if db not in by_db:
            continue
        tags = [row["tag"] for row in by_db[db]]
        snippets = fetch_snippets(db, tags)
        sections.append(render_section(db, by_db[db], snippets))
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

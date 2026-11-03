#!/usr/bin/env python3
"""declsDiff.py — compute a `+NAME` / `-NAME` diff between two pre-computed
`decls.txt` dumps (produced by `dumpReasonableDecls.lean`) and render a
Markdown body for the `#### Declarations diff` section of mathlib4's PR
summary comment.

When both sides also supply a `meta_decls.txt` dump (`--meta-out`), the body
additionally lists declarations present on both sides whose `meta` marking
changed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


# Cap on how many `+NAME` / `-NAME` rows we splice into the rendered Markdown
# body. When the diff is longer, the body shows the first N rows and a
# "(showing first N of TOTAL lines)" note.
MAX_RENDERED_LINES = 200

# Newline-count threshold above which the `--with-heading` output wraps the
# section in `<details>`. Matches `PR_summary.yml`'s `wc -l > 15` heuristic for
# the declarations-diff part, so the post-build comment collapses the same way.
DETAILS_LINE_THRESHOLD = 15

# Hint shown when one of the input dumps is missing or empty — the most
# common cause is that the master-side artifact for the merge-base wasn't
# found in CI's artifact store.
MERGE_HINT = (
    "This usually means the master-side artifact for the merge-base wasn't "
    "found in CI; try merging master into your PR and pushing to refresh."
)

# Characters that split a Markdown line when they appear inside a `+NAME`
# / `-NAME` row. We replace each with a visible backslash escape rather
# than dropping the name silently.
_LINE_BREAK_ESCAPES = {
    "\r": r"\r",
    "\n": r"\n",
    " ": r" ",  # Unicode LINE SEPARATOR
    " ": r" ",  # Unicode PARAGRAPH SEPARATOR
}


# The HTML-comment closer. The comment patcher delimits this section with
# HTML-comment markers (`<!-- DECLS_DIFF_LEAN_BEGIN/END -->`); a declaration
# name carrying a literal `-->` could otherwise forge the END marker and
# truncate the region when the patcher re-parses the comment. Every marker ends
# in `-->`, so escaping the closer defeats every forgery. The escaped form
# contains no `-->`, so re-escaping is a no-op.
_COMMENT_CLOSER = "-->"
_COMMENT_CLOSER_ESCAPE = r"--\>"


def read_decls(path: Path) -> set[str]:
    """Read a `decls.txt` dump into a set of declaration names.

    Blank lines are dropped. Duplicates are collapsed by virtue of using a set.
    """
    return {line for line in path.read_text().splitlines() if line}


def compute_meta_moves(
    ref: set[str], new: set[str], ref_meta: set[str], new_meta: set[str]
) -> list[tuple[str, str]]:
    """Return declarations present on both sides whose `meta` marking changed.

    The result is a list of `(sign, name)` pairs sorted by `name`, where `sign`
    is `"+"` for a declaration that became `meta` and `"-"` for one that is no
    longer `meta`. Added and removed declarations are not reported here; they
    already appear in the main diff.
    """
    common = ref & new
    pairs: list[tuple[str, str]] = [("+", n) for n in (new_meta - ref_meta) & common]
    pairs += [("-", n) for n in (ref_meta - new_meta) & common]
    pairs.sort(key=lambda p: p[1])
    return pairs


def compute_diff(ref: set[str], new: set[str]) -> list[tuple[str, str]]:
    """Return the sorted-by-name diff between `ref` and `new`.

    The result is a list of `(sign, name)` pairs where `sign` is `"+"` for
    names only in `new` and `"-"` for names only in `ref`. The pairs are
    sorted by `name` alone; the sign does not influence ordering.
    """
    pairs: list[tuple[str, str]] = [("+", n) for n in (new - ref)]
    pairs += [("-", n) for n in (ref - new)]
    pairs.sort(key=lambda p: p[1])
    return pairs


def sanitize(name: str) -> str:
    """Strip line-splitting characters from a declaration name before splicing
    into Markdown.

    A Lean `Name` is in principle any string (e.g. via `Name.mkSimple`).
    Names that contain `\\n` or `\\r` would otherwise split the rendered diff
    across multiple physical lines, only the first of which gets a leading
    `+`/`-` — the rest would be attacker-controlled raw Markdown. Likewise a
    name containing `-->` could forge the region's closing HTML-comment marker.
    """
    for ch, esc in _LINE_BREAK_ESCAPES.items():
        name = name.replace(ch, esc)
    return name.replace(_COMMENT_CLOSER, _COMMENT_CLOSER_ESCAPE)


def render_override(
    plus: int,
    minus: int,
    diff: list[tuple[str, str]],
    head_sha: str | None,
    with_heading: bool = False,
    meta_moves: list[tuple[str, str]] | None = None,
    meta_unavailable: bool = False,
) -> str:
    """Render the Markdown body for the `#### Declarations diff` section.

    The body opens with a `> ✅` blockquote stamping the new-side SHA,
    follows with `**+N** new` / `**−M** removed` counts, and (when there
    are any differences) a ```diff fenced block containing the first
    `MAX_RENDERED_LINES` rows.

    With `with_heading=False` (the default) the heading and outer `<details>`
    wrap are the caller's responsibility. With `with_heading=True` the result
    is the complete section — `#### Declarations diff (Lean)` heading plus, when
    the body exceeds `DETAILS_LINE_THRESHOLD` newlines, a `<details>` wrap — so a
    consumer (the comment patcher) can splice it into the Lean region verbatim.

    `meta_moves` (from `compute_meta_moves`) adds a count line and, when
    non-empty, a second fenced block of `+meta NAME` / `-meta NAME` rows.
    `meta_unavailable` instead adds a note that `meta` changes could not be
    computed because one side lacks a `meta` dump.
    """
    short_sha = head_sha[:7] if head_sha else None
    stamp = (
        f"> ✅ **Lean-aware diff** — post-build, computed from the Lean environment "
        f"(commit `{short_sha}`)."
        if short_sha
        else "> ✅ **Lean-aware diff** — post-build, computed from the Lean environment."
    )
    lines: list[str] = [
        stamp,
        "",
        f"* **+{plus}** new declarations",
        f"* **−{minus}** removed declarations",
    ]
    if meta_moves is not None:
        into = sum(1 for sign, _ in meta_moves if sign == "+")
        out = sum(1 for sign, _ in meta_moves if sign == "-")
        lines.append(f"* **{into}** moved into `meta`, **{out}** moved out of `meta`")
    if plus == 0 and minus == 0:
        lines += ["", "_No declaration differences._"]
    else:
        total = len(diff)
        lines.append("")
        if total > MAX_RENDERED_LINES:
            lines += [
                f"_(showing first {MAX_RENDERED_LINES} of {total} lines)_",
                "",
            ]
        lines.append("```diff")
        for sign, name in diff[:MAX_RENDERED_LINES]:
            lines.append(f"{sign}{sanitize(name)}")
        lines.append("```")
    if meta_moves:
        total = len(meta_moves)
        lines += ["", "Declarations whose `meta` marking changed:", ""]
        if total > MAX_RENDERED_LINES:
            lines += [
                f"_(showing first {MAX_RENDERED_LINES} of {total} lines)_",
                "",
            ]
        lines.append("```diff")
        for sign, name in meta_moves[:MAX_RENDERED_LINES]:
            lines.append(f"{sign}meta {sanitize(name)}")
        lines.append("```")
    elif meta_unavailable:
        lines += [
            "",
            "_`meta` changes unavailable: the reference build has no `meta` dump._",
        ]
    body = "\n".join(lines)
    if not with_heading:
        return body + "\n"
    if body.count("\n") > DETAILS_LINE_THRESHOLD:
        wrapped = "\n".join([
            "<details><summary>",
            "",
            "#### Declarations diff (Lean)",
            "",
            "</summary>",
            "",
            body,
            "",
            "</details>",
        ])
    else:
        wrapped = "\n".join(["#### Declarations diff (Lean)", "", body])
    return wrapped + "\n"


def validate_input(path: Path, flag: str) -> str | None:
    """Return an error string if `path` is missing/empty, or `None` if OK."""
    if not path.is_file():
        return f"declsDiff.py: {flag} '{path}' does not exist. {MERGE_HINT}"
    if path.stat().st_size == 0:
        return f"declsDiff.py: {flag} '{path}' is empty. {MERGE_HINT}"
    return None


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="declsDiff.py",
        description=__doc__.strip().splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--ref-decls", type=Path, required=True,
                   help="declarations dump of the reference commit")
    p.add_argument("--new-decls", type=Path, required=True,
                   help="declarations dump of the new commit")
    p.add_argument("--new-sha", default="",
                   help="full SHA of the new commit (for the stamp)")
    p.add_argument("--decls-override", type=Path,
                   help="write the Markdown override snippet here")
    p.add_argument("--diff-out", type=Path,
                   help="write the raw `+NAME` / `-NAME` lines here")
    p.add_argument("--counts-file", type=Path,
                   help="write the `<plus> <minus>` counts here")
    p.add_argument("--ref-meta", type=Path,
                   help="`meta` declarations dump of the reference commit")
    p.add_argument("--new-meta", type=Path,
                   help="`meta` declarations dump of the new commit")
    p.add_argument("--meta-diff-out", type=Path,
                   help="write the raw `+meta NAME` / `-meta NAME` lines here")
    p.add_argument("--with-heading", action="store_true",
                   help="emit the `#### Declarations diff` heading and "
                        "`<details>` wrap in the override snippet")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    for path, flag in [(args.ref_decls, "--ref-decls"),
                       (args.new_decls, "--new-decls")]:
        err = validate_input(path, flag)
        if err is not None:
            print(err, file=sys.stderr)
            return 1

    ref = read_decls(args.ref_decls)
    new = read_decls(args.new_decls)
    diff = compute_diff(ref, new)
    plus = sum(1 for sign, _ in diff if sign == "+")
    minus = sum(1 for sign, _ in diff if sign == "-")

    # `meta` dumps are optional: an empty dump is valid (no `meta` declarations),
    # but a missing one means that side predates `meta` tracking.
    def usable(path: Path | None) -> bool:
        return path is not None and path.is_file()

    meta_moves: list[tuple[str, str]] | None = None
    meta_unavailable = False
    if usable(args.ref_meta) and usable(args.new_meta):
        meta_moves = compute_meta_moves(
            ref, new, read_decls(args.ref_meta), read_decls(args.new_meta))
    elif usable(args.new_meta):
        meta_unavailable = True

    if args.decls_override is not None:
        args.decls_override.write_text(
            render_override(plus, minus, diff, args.new_sha or None,
                            with_heading=args.with_heading,
                            meta_moves=meta_moves,
                            meta_unavailable=meta_unavailable))
    if args.meta_diff_out is not None:
        args.meta_diff_out.write_text(
            "".join(f"{sign}meta {name}\n" for sign, name in meta_moves or []))
    if args.diff_out is not None:
        args.diff_out.write_text(
            "".join(f"{sign}{name}\n" for sign, name in diff))
    if args.counts_file is not None:
        args.counts_file.write_text(f"{plus} {minus}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

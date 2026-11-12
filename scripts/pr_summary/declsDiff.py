#!/usr/bin/env python3
"""declsDiff.py — compute a `+NAME` / `-NAME` diff between two pre-computed
dumps produced by `dumpReasonableDecls.lean` and render a Markdown body for
the `#### Declarations diff` section of mathlib4's PR summary comment.

Depending on the arguments to `dumpReasonableDecls.lean`, each dump can be
either a directory (`--ref-dir`, `--new-dir`) or a bare `decls.txt`
(`--ref-decls`, `--new-decls`). Only names are compared so far. When both
sides have an API dump, `--api-status` reports which fields they share, but
the rendered section is the same either way. An API dump that cannot be read,
or that lists no declarations, is an error, as a missing or empty `decls.txt` is.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from apiDump import (
    API_FILE,
    DECLS_FILE,
    ApiDump,
    ApiDumpError,
    comparable_fields,
    read_api,
)

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


@dataclass
class Side:
    """One side of the comparison: its declaration names, and its API dump if it
    has one."""

    names: set[str]
    api: ApiDump | None


def load_side(flag: str, dump_dir: Path | None, decls: Path | None) -> Side | str:
    """Read one side, or return an error message. In a dump directory the names
    come from `api.jsonl`, or from `decls.txt` if the directory predates the API
    dump; otherwise from the bare `decls.txt`."""
    if dump_dir is not None:
        api_path = dump_dir / API_FILE
        if api_path.exists():
            try:
                api = read_api(api_path)
            except ApiDumpError as e:
                return f"declsDiff.py: {flag} '{api_path}' is unusable: {e}."
            return Side(set(api.decls), api)
        decls = dump_dir / DECLS_FILE
        if not decls.exists():
            return (f"declsDiff.py: {flag} '{dump_dir}' has neither {API_FILE} "
                    f"nor {DECLS_FILE}. {MERGE_HINT}")
    assert decls is not None  # parse_args requires one of the two
    err = validate_input(decls, flag)
    if err is not None:
        return err
    return Side(read_decls(decls), None)


def api_status(ref: Side, new: Side) -> tuple[str, str]:
    """Returns `used` and the fields the two API dumps can be compared on, or
    `absent` and which side is missing the API dump. The only text the message
    takes from the dumps is field names, and `read_api` accepts only short
    identifiers as field names (`_FIELD_NAME` in `apiDump.py`), so the message
    is safe to include in the step summary."""
    if ref.api is not None and new.api is not None:
        fields = ", ".join(comparable_fields(ref.api, new.api)) or "none"
        return "used", f"{API_FILE} on both sides; fields in common: {fields}"
    if ref.api is None and new.api is None:
        return "absent", f"names only: neither side has {API_FILE}"
    side = "reference" if ref.api is None else "new"
    return "absent", f"names only: the {side} side has no {API_FILE}"


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
    p.add_argument("--ref-dir", type=Path,
                   help=f"dump directory of the reference commit: its {API_FILE}, "
                        f"or {DECLS_FILE} if it predates the API dump")
    p.add_argument("--new-dir", type=Path,
                   help="dump directory of the new commit")
    p.add_argument("--ref-decls", type=Path,
                   help=f"{DECLS_FILE} of the reference commit, instead of --ref-dir")
    p.add_argument("--new-decls", type=Path,
                   help=f"{DECLS_FILE} of the new commit, instead of --new-dir")
    p.add_argument("--new-sha", default="",
                   help="full SHA of the new commit (for the stamp)")
    p.add_argument("--decls-override", type=Path,
                   help="write the Markdown override snippet here")
    p.add_argument("--diff-out", type=Path,
                   help="write the raw `+NAME` / `-NAME` lines here")
    p.add_argument("--counts-file", type=Path,
                   help="write the `<plus> <minus>` counts here")
    p.add_argument("--with-heading", action="store_true",
                   help="emit the `#### Declarations diff` heading and "
                        "`<details>` wrap in the override snippet")
    p.add_argument("--api-status", type=Path,
                   help="write `<state> <message>` about the API dumps here; "
                        "state is `used` (both sides have one) or `absent`")
    args = p.parse_args(argv)
    for side in ("ref", "new"):
        if (getattr(args, f"{side}_dir") is None) == (getattr(args, f"{side}_decls") is None):
            p.error(f"give exactly one of --{side}-dir and --{side}-decls")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    sides: list[Side] = []
    for name, dump_dir, decls in [("ref", args.ref_dir, args.ref_decls),
                                  ("new", args.new_dir, args.new_decls)]:
        flag = f"--{name}-dir" if dump_dir is not None else f"--{name}-decls"
        side = load_side(flag, dump_dir, decls)
        if isinstance(side, str):
            print(side, file=sys.stderr)
            return 1
        sides.append(side)
    ref, new = sides
    # Names need no version check: `read_api` accepts only dumps whose `n` is at the
    # version `decls.txt` names are (`KEYS` in `apiDump.py`).
    diff = compute_diff(ref.names, new.names)

    # The API dumps supply the names; their other fields do not change the output yet.
    state, message = api_status(ref, new)
    print(f"declsDiff.py: API dump: {state}: {message}", file=sys.stderr)

    plus = sum(1 for sign, _ in diff if sign == "+")
    minus = sum(1 for sign, _ in diff if sign == "-")

    if args.decls_override is not None:
        args.decls_override.write_text(
            render_override(plus, minus, diff, args.new_sha or None,
                            with_heading=args.with_heading))
    if args.diff_out is not None:
        args.diff_out.write_text(
            "".join(f"{sign}{name}\n" for sign, name in diff))
    if args.counts_file is not None:
        args.counts_file.write_text(f"{plus} {minus}\n")
    if args.api_status is not None:
        args.api_status.write_text(f"{state} {message}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

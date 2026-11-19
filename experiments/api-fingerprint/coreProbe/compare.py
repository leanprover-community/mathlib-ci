#!/usr/bin/env python3
"""PROBE: are the core's and the project script's files, merged, #99's dump?

    compare.py OLD_DIR NEW_DIR

OLD_DIR holds #99's `api.jsonl` and `imports.json`; NEW_DIR holds the core's `api.jsonl`, the
project script's `api.mathlib.jsonl` and its `imports.json`. The new files are merged by key, as
`ARCHITECTURE.md`'s reader would: `api.jsonl` has a row for every declaration and module, and the
project file only rows with something in them. `d` reads as empty where `dg` is true, which is
what #99's `d` means. Then every row must equal #99's, field for field, and the headers must list
the same fields at the same versions. Exits 1 on any difference.
"""

import json
import sys
from pathlib import Path


def read(path):
    with open(path) as f:
        header = json.loads(f.readline())
        rows = [json.loads(line) for line in f]
    return header, rows


def key(row):
    return ("n", row["n"]) if "n" in row else ("mod", row["mod"])


def main(old_dir, new_dir):
    old_dir, new_dir = Path(old_dir), Path(new_dir)
    old_h, old_rows = read(old_dir / "api.jsonl")
    core_h, core_rows = read(new_dir / "api.jsonl")
    proj_h, proj_rows = read(new_dir / "api.mathlib.jsonl")
    problems = []

    # Headers: the union of the new files' fields, less `dg`, is #99's.
    fields = dict(core_h["fields"])
    for k, v in proj_h["fields"].items():
        if k in fields and fields[k] != v:
            problems.append(f"header: {k} at {fields[k]} in the core, {v} in the project file")
        fields[k] = v
    fields.pop("dg", None)
    if fields != old_h["fields"] or core_h["schema"] != old_h["schema"]:
        problems.append(f"header: {old_h} against merged {fields}")

    # Rows: merge by key; every project row must name a core row.
    merged = {key(r): dict(r) for r in core_rows}
    order = [key(r) for r in core_rows]
    for r in proj_rows:
        k = key(r)
        if k not in merged:
            problems.append(f"{k}: in the project file, not in the core's")
            continue
        clash = set(r) & set(merged[k]) - {"n", "mod"}
        if clash:
            problems.append(f"{k}: both files write {sorted(clash)}")
        merged[k].update(r)
    gen_doc = 0
    for row in merged.values():
        if row.pop("dg", False):
            gen_doc += 1
            row.pop("d", None)

    old_order = [key(r) for r in old_rows]
    if old_order != order:
        problems.append(f"order or names differ: {len(old_order)} against {len(order)} rows")
    old_by_key = {key(r): r for r in old_rows}
    differ = 0
    for k in order:
        if merged.get(k) != old_by_key.get(k):
            differ += 1
            if differ <= 10:
                problems.append(f"{k}: {old_by_key.get(k)} against {merged.get(k)}")
    if differ:
        problems.append(f"{differ} rows differ")

    old_imp = (old_dir / "imports.json").read_bytes()
    new_imp = (new_dir / "imports.json").read_bytes()
    if old_imp != new_imp:
        problems.append("imports.json differs")

    print(f"{len(order)} rows ({len(core_rows)} core, {len(proj_rows)} project), "
          f"{gen_doc} with `dg`; headers {sorted(old_h['fields'])}")
    for p in problems:
        print("DIFF", p)
    print("same as #99's dump" if not problems else f"{len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))

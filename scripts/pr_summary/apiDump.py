"""apiDump.py — read the API dump (`api.jsonl`) that `dumpReasonableDecls.lean` writes,
and work out which of its fields two dumps can be compared on.

The format is documented in `dumpReasonableDecls.lean` (*The API dump*). In short: a header
line `{"schema": 1, "fields": {"n": 1, ...}}`, then one JSON object per declaration (keyed
by `n`) and one per module (keyed by `mod`). Two dumps are compared only on the fields both
headers list at the same version, and never on `l` or `src` (`comparable_fields`), except for
the keys `n` and `mod`, which every dump this reader accepts has at version 1 (`KEYS`).

The dump comes from the PR's own build, so it is untrusted. Anything malformed raises
`ApiDumpError`, and no error message quotes the file: the messages go to the workflow log.
Only the header and the two keys are checked here; whoever compares a field checks its values.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# The line format this reader understands: `apiSchema` in `dumpReasonableDecls.lean`. When
# it goes up, the reader must go on reading every older schema too: `master` artifacts
# written before the bump are compared for up to 90 days (*The API dump*).
SCHEMA = 1

# The keys that identify a row, at the only versions they have. `declsDiff.py` compares
# names whatever the other fields are at, so changing what these hold needs a new `SCHEMA`.
KEYS = {"n": 1, "mod": 1}

# The files in a dump directory, as named by `dumpReasonableDecls.lean --out-dir`.
# Directories written before the API dump hold `decls.txt` instead of `api.jsonl`.
API_FILE = "api.jsonl"
IMPORTS_FILE = "imports.json"
DECLS_FILE = "decls.txt"

# Default values assigned to omitted fields: the "empty" column of *The API dump*'s tables.
# `n`, `k`, `m` and `mod` are in every row of their kind.
EMPTY = {
    "d": None, "a": [], "p": False, "x": None, "g": False, "dep": None, "al": None, "rs": False,
    "l": None,
    "dm": None, "md": None, "imp": [], "src": None,
}

# The fields the dumper's tables say are never compared: where a declaration is (`l`), and
# whether its file is in the diff (`src`), not what it is.
NEVER_COMPARED = frozenset({"l", "src"})

# Field names are short identifiers, so that a list of them can go into a log line or the
# step summary as it is.
_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")


class ApiDumpError(ValueError):
    """The file is not an API dump this reader can use. The message never quotes the file."""


@dataclass
class ApiDump:
    """A parsed API dump, with at least one declaration. Rows are kept as parsed, so a field
    that *The API dump* gives an empty value for may be missing from a row: read it with
    `value`."""

    fields: dict[str, int]
    decls: dict[str, dict]
    modules: dict[str, dict]


def _read_header(row: dict) -> dict[str, int]:
    schema = row.get("schema")
    if type(schema) is not int:
        raise ApiDumpError("the first line is not a header with an integer `schema`")
    if schema != SCHEMA:
        newer = "newer than" if schema > SCHEMA else "different from"
        raise ApiDumpError(f"its schema is {newer} the one this reader knows ({SCHEMA})")
    fields = row.get("fields")
    if not isinstance(fields, dict):
        raise ApiDumpError("the header's `fields` is not an object")
    for name, version in fields.items():
        if not _FIELD_NAME.fullmatch(name) or type(version) is not int:
            raise ApiDumpError("the header's `fields` is not a map from names to versions")
    for key, version in KEYS.items():
        if fields.get(key) != version:
            raise ApiDumpError(f"the header does not list `{key}` at version {version}")
    return fields


def read_api(path: Path) -> ApiDump:
    """Parse an API dump, or raise `ApiDumpError` saying why it cannot be used."""
    try:
        with path.open("rb") as fh:
            return _parse(fh)
    except OSError:
        raise ApiDumpError("it could not be read") from None


def _parse(fh: BinaryIO) -> ApiDump:
    fields: dict[str, int] | None = None
    decls: dict[str, dict] = {}
    modules: dict[str, dict] = {}
    # A binary file splits on `\n` only: unlike `str.splitlines`, never inside a JSON string.
    # Streamed, since a full `Mathlib` dump is tens of megabytes.
    for lineno, raw in enumerate(fh, 1):
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ApiDumpError(f"line {lineno} is not UTF-8") from None
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            raise ApiDumpError(f"line {lineno} is not valid JSON") from None
        # The line is UTF-8, so only a `\u` escape can give an unpaired surrogate, which
        # would make writing the string back out fail.
        if "\\u" in line:
            try:
                json.dumps(row, ensure_ascii=False).encode("utf-8")
            except UnicodeEncodeError:
                raise ApiDumpError(f"line {lineno} escapes an unpaired surrogate") from None
        if not isinstance(row, dict):
            raise ApiDumpError(f"line {lineno} is not a JSON object")
        if fields is None:
            fields = _read_header(row)
            continue
        if ("n" in row) == ("mod" in row):
            raise ApiDumpError(f"line {lineno} has neither or both of `n` and `mod`")
        key, table = ("n", decls) if "n" in row else ("mod", modules)
        name = row[key]
        if not isinstance(name, str):
            raise ApiDumpError(f"line {lineno}: `{key}` is not a string")
        if name in table:
            raise ApiDumpError(f"line {lineno} repeats an earlier `{key}`")
        table[name] = row
    if fields is None:
        raise ApiDumpError("it is empty")
    # Like an empty `decls.txt`: read as it is, it would report every declaration removed.
    if not decls:
        raise ApiDumpError("it lists no declarations")
    return ApiDump(fields, decls, modules)


def value(row: dict, field: str):
    """A row's value for a field, or the field's empty value if the row leaves it out. A row
    is kept as parsed, to reduce duplication in a full `Mathlib` dump. An empty list is a fresh
    copy, so a caller that changes it does not change every later row's."""
    if field in row:
        return row[field]
    empty = EMPTY.get(field)
    return list(empty) if isinstance(empty, list) else empty


def comparable_fields(a: ApiDump, b: ApiDump) -> list[str]:
    """The fields that are at the same version in both dumps, less `NEVER_COMPARED`, sorted:
    the only ones to be compared."""
    return sorted(f for f, v in a.fields.items()
                  if b.fields.get(f) == v and f not in NEVER_COMPARED)

"""apiDump.py — read the API dump (`api.jsonl`) that `dumpReasonableDecls.lean` writes,
and work out which of its fields two dumps can be compared on.

The format is documented in `dumpReasonableDecls.lean` (*The API dump*). In short: a header
line `{"schema": 1, "fields": {"n": 1, ...}}`, then one JSON object per declaration (keyed
by `n`) and one per module (keyed by `mod`). Two dumps are compared only on the fields both
headers list at the same version (`comparable_fields`), so the dumper can gain or change a
field while the base side of a comparison is still a `master` artifact written by an older
dumper.

The dump comes from the PR's own build, so it is untrusted. Anything malformed raises
`ApiDumpError`, and no error message quotes the file: the messages go to the workflow log.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# The line format this reader understands: `apiSchema` in `dumpReasonableDecls.lean`.
SCHEMA = 1

# The files in a dump directory, as `dumpReasonableDecls.lean --out-dir` names them.
# Directories written before the API dump hold `decls.txt` instead of `api.jsonl`.
API_FILE = "api.jsonl"
IMPORTS_FILE = "imports.json"
DECLS_FILE = "decls.txt"

# Field names are short identifiers, so that a list of them can go into a log line or the
# step summary as it is.
_FIELD_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")


class ApiDumpError(ValueError):
    """The file is not an API dump this reader can use. The message never quotes the file."""


@dataclass
class ApiDump:
    """A parsed API dump. Rows are kept as parsed: a row may leave out a field the header
    lists, which then has its empty value."""

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
    for key in ("n", "mod"):
        if key not in fields:
            raise ApiDumpError(f"the header does not list `{key}`")
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
    return ApiDump(fields, decls, modules)


def comparable_fields(a: ApiDump, b: ApiDump) -> list[str]:
    """The fields both dumps list at the same version, sorted: the only ones to compare."""
    return sorted(f for f, v in a.fields.items() if b.fields.get(f) == v)

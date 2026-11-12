"""Tests for apiDump: reading an API dump, and which fields two dumps share."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import apiDump
from apiDump import ApiDump, ApiDumpError, comparable_fields, read_api

_REPO = Path(__file__).resolve().parents[2]
_DUMPER = _REPO / "scripts" / "pr_summary" / "dumpReasonableDecls.lean"

# `api.jsonl` from `dumpReasonableDecls.lean --out-dir` on `Mathlib.Order.Bounds.Basic`, at
# mathlib4 `079459b1486` (v4.36.0-rc2). Re-record it when the dumper's fields change.
_REAL = Path(__file__).resolve().parent / "data" / "order-bounds-basic"

# A schema-1 header. Keep it at schema 1 when `SCHEMA` goes up: `master` artifacts written
# at schema 1 are still compared for 90 days after the bump.
HEADER = {"schema": 1, "fields": {"n": 1, "k": 1, "m": 1, "mod": 1}}


def _write(p: Path, rows: list) -> Path:
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


class TestRealDump:
    def test_parses(self) -> None:
        """The dumper's own output reads cleanly, declarations sorted by name: Lean's
        `String.lt` and Python's `<` both compare code points."""
        api = read_api(_REAL / "api.jsonl")
        assert len(api.decls) == 311
        assert list(api.decls) == sorted(api.decls)
        assert list(api.modules) == ["Mathlib.Order.Bounds.Basic"]
        assert api.decls["upperBounds_union"] == {
            "n": "upperBounds_union", "k": "thm", "m": "Mathlib.Order.Bounds.Basic",
            "t": "16907828637081357657", "te": "857188824", "a": ["simp"], "x": "lowerBounds_union",
            "nu": 1, "l": 324}
        # Every field is in some row, so a field the dumper silently stopped writing shows.
        written = {k for r in (*api.decls.values(), *api.modules.values()) for k in r}
        assert written == set(api.fields) - {"dep", "rs", "uo", "dm", "imp"}

    def test_lean_and_python_agree(self) -> None:
        """The schema, the field list and the `--out-dir` file names are written in Lean
        and read in Python. Catch the two drifting apart."""
        src = _DUMPER.read_text()
        schema = re.search(r"def apiSchema : Nat := (\d+)", src)
        assert schema and int(schema.group(1)) == apiDump.SCHEMA
        fields = re.search(r"def apiFields : List \(String × Nat\) :=\s*\[(.*?)\]", src, re.S)
        assert fields
        lean_fields = {k: int(v) for k, v in re.findall(r'\("(\w+)", (\d+)\)', fields.group(1))}
        assert lean_fields == read_api(_REAL / "api.jsonl").fields, \
            "re-record tests/decls_diff/data/order-bounds-basic with the current dumper"
        for name in (apiDump.IMPORTS_FILE, apiDump.API_FILE):
            assert f'd / "{name}"' in src

    def test_empty_values_agree(self) -> None:
        """`EMPTY` is the "empty" column of the dumper's tables, which say what a row means by
        leaving a field out. A field with `—` is never left out."""
        src = _DUMPER.read_text()
        rows = re.findall(r"^\| `(\w+)` \| (\d+) \| (`[^`]*`|—) \|", src, re.M)
        assert {k for k, _, _ in rows} == set(read_api(_REAL / "api.jsonl").fields)
        assert {k: json.loads(e.strip("`")) for k, _, e in rows if e != "—"} == apiDump.EMPTY


class TestReadApi:
    def test_rows_and_header(self, tmp_path: Path) -> None:
        api = read_api(_write(tmp_path / "a.jsonl", [
            HEADER,
            {"n": "foo", "k": "thm", "m": "M"},
            {"mod": "M"},
        ]))
        assert api.fields == HEADER["fields"]
        assert api.decls == {"foo": {"n": "foo", "k": "thm", "m": "M"}}
        assert api.modules == {"M": {"mod": "M"}}

    def test_blank_lines_and_crlf(self, tmp_path: Path) -> None:
        """Blank lines are skipped; a trailing `\\r` is JSON whitespace."""
        p = tmp_path / "a.jsonl"
        p.write_bytes(b'\n' + json.dumps(HEADER).encode() + b'\r\n\n{"n": "foo"}\r\n')
        assert set(read_api(p).decls) == {"foo"}

    def test_undeclared_and_prototype_keys_are_kept(self, tmp_path: Path) -> None:
        """A prototype fingerprint row reads as is; keys the header does not list are
        simply never compared."""
        row = {"n": "foo", "k": "thm", "t": "123", "v": None, "d": None, "a": ["simp"],
               "p": False, "x": None, "g": False, "m": "M", "s": None,
               "dep": {"to": "bar", "since": "2026-01-01", "text": None}}
        api = read_api(_write(tmp_path / "a.jsonl", [HEADER, row]))
        assert api.decls["foo"] == row

    def test_name_with_line_separator(self, tmp_path: Path) -> None:
        """Lines split on `\\n` only, so a name `str.splitlines` would cut stays whole."""
        api = read_api(_write(tmp_path / "a.jsonl", [HEADER, {"n": "a\u2028b"}]))
        assert set(api.decls) == {"a\u2028b"}

    def test_escaped_surrogate_pair(self, tmp_path: Path) -> None:
        """A pair of `\\u` escapes is one character, and can be written out again."""
        p = tmp_path / "a.jsonl"
        p.write_text(json.dumps(HEADER) + '\n{"n": "a\\ud83d\\ude00", "k": "\\u0001"}\n')
        assert set(read_api(p).decls) == {"a\U0001f600"}

    @pytest.mark.parametrize("rows, reason", [
        ([], "it is empty"),
        ([{"n": "foo"}], "not a header"),
        ([{"schema": "1", "fields": {}}], "not a header"),
        ([{"schema": True, "fields": {}}], "not a header"),
        ([{"schema": 2, "fields": {"n": 1, "mod": 1}}], "newer than"),
        ([{"schema": 0, "fields": {"n": 1, "mod": 1}}], "different from"),
        ([{"schema": 1, "fields": ["n", "mod"]}], "not an object"),
        ([{"schema": 1, "fields": {"n": "1", "mod": 1}}], "map from names to versions"),
        ([{"schema": 1, "fields": {"n": True, "mod": 1}}], "map from names to versions"),
        ([{"schema": 1, "fields": {"n": 1, "mod": 1, "a\n::b": 1}}],
         "map from names to versions"),
        ([{"schema": 1, "fields": {"n": 1}}], "does not list `mod` at version 1"),
        ([{"schema": 1, "fields": {"mod": 1}}], "does not list `n` at version 1"),
        # Names are compared whatever the field versions, so their key cannot move.
        ([{"schema": 1, "fields": {"n": 2, "mod": 1}}], "does not list `n` at version 1"),
        ([{"schema": 1, "fields": {"n": 1, "mod": 2}}], "does not list `mod` at version 1"),
        # As an empty `decls.txt` is rejected: it would report every name removed.
        ([HEADER], "it lists no declarations"),
        ([HEADER, {"mod": "M"}], "it lists no declarations"),
        ([HEADER, {"n": "\ud800"}], "line 2 escapes an unpaired surrogate"),
        ([HEADER, {"n": "foo", "k": "\udc00"}], "line 2 escapes an unpaired surrogate"),
        ([HEADER, ["n", "foo"]], "line 2 is not a JSON object"),
        ([HEADER, {"k": "thm"}], "line 2 has neither or both"),
        ([HEADER, {"n": "foo", "mod": "M"}], "line 2 has neither or both"),
        ([HEADER, {"n": 3}], "line 2: `n` is not a string"),
        ([HEADER, {"n": "foo"}, {"n": "foo"}], "line 3 repeats an earlier `n`"),
        ([HEADER, {"mod": "M"}, {"mod": "M"}], "line 3 repeats an earlier `mod`"),
    ])
    def test_rejects(self, tmp_path: Path, rows: list, reason: str) -> None:
        with pytest.raises(ApiDumpError, match=re.escape(reason)):
            read_api(_write(tmp_path / "a.jsonl", rows))

    def test_rejects_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "a.jsonl"
        p.write_text(json.dumps(HEADER) + "\n{not json\n")
        with pytest.raises(ApiDumpError, match="line 2 is not valid JSON"):
            read_api(p)

    def test_rejects_deep_nesting(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Some Pythons' `json.loads` raise `RecursionError`, not `ValueError`, on deep
        nesting; which input triggers it depends on the version, so simulate it."""
        def deep(line: str) -> object:
            if line.startswith("[["):
                raise RecursionError("maximum recursion depth exceeded")
            return json.JSONDecoder().decode(line)
        monkeypatch.setattr(apiDump.json, "loads", deep)
        p = tmp_path / "a.jsonl"
        p.write_text(json.dumps(HEADER) + "\n[[[]]]\n")
        with pytest.raises(ApiDumpError, match="line 2 is not valid JSON"):
            read_api(p)

    def test_rejects_non_utf8(self, tmp_path: Path) -> None:
        p = tmp_path / "a.jsonl"
        p.write_bytes(json.dumps(HEADER).encode() + b'\n{"n": "\xff"}\n')
        with pytest.raises(ApiDumpError, match="line 2 is not UTF-8"):
            read_api(p)

    def test_rejects_unreadable(self, tmp_path: Path) -> None:
        """A directory where the file should be is reported, not raised as `OSError`."""
        with pytest.raises(ApiDumpError, match="could not be read"):
            read_api(tmp_path)

    def test_messages_do_not_quote_the_file(self, tmp_path: Path) -> None:
        """Errors go into workflow annotations, so they must not echo PR-controlled text."""
        evil = "\n::warning::forged"
        for rows in ([HEADER, {"n": evil}, {"n": evil}],
                     [{"schema": 1, "fields": {"n": 1, "mod": 1, evil: 1}}],
                     [HEADER, {"n": evil, "mod": evil}]):
            with pytest.raises(ApiDumpError) as e:
                read_api(_write(tmp_path / "a.jsonl", rows))
            assert "forged" not in str(e.value)


class TestValue:
    def test_a_field_left_out_reads_as_its_empty_value(self) -> None:
        row = {"n": "foo", "k": "thm", "m": "M", "t": "1", "te": "2", "a": ["simp"]}
        assert apiDump.value(row, "a") == ["simp"]
        assert apiDump.value(row, "p") is False and apiDump.value(row, "x") is None
        assert apiDump.value(row, "nu") == 0 and apiDump.value(row, "imp") == []
        # A field this reader does not know has no empty value.
        assert apiDump.value(row, "zz") is None


class TestComparableFields:
    def _dump(self, **fields: int) -> ApiDump:
        return ApiDump(fields=fields, decls={}, modules={})

    def test_same_fields(self) -> None:
        assert comparable_fields(self._dump(n=1, k=1, mod=1),
                                 self._dump(n=1, k=1, mod=1)) == ["k", "mod", "n"]

    def test_field_on_one_side_only_is_not_compared(self) -> None:
        """An older base dump without `t` leaves `t` uncompared, in either direction."""
        old, new = self._dump(n=1, mod=1), self._dump(n=1, mod=1, t=1)
        assert comparable_fields(old, new) == ["mod", "n"]
        assert comparable_fields(new, old) == ["mod", "n"]

    def test_field_at_another_version_is_not_compared(self) -> None:
        """A field whose meaning changed (a new hash, say) is skipped until both agree."""
        assert comparable_fields(self._dump(n=1, mod=1, t=1),
                                 self._dump(n=1, mod=1, t=2)) == ["mod", "n"]

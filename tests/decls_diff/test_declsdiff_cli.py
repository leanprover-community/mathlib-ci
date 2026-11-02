"""End-to-end CLI tests: invoke declsDiff.main and inspect its output files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import declsDiff


def _write(p: Path, lines: list[str]) -> Path:
    p.write_text("".join(line + "\n" for line in lines))
    return p


class TestCLI:
    def test_full_invocation_writes_all_three_files(self, tmp_path: Path) -> None:
        """All three output files are written and contain the expected content."""
        ref = _write(tmp_path / "ref.txt", ["A", "B", "C"])
        new = _write(tmp_path / "new.txt", ["A", "C", "D"])
        out = tmp_path / "out.md"
        diff = tmp_path / "diff.txt"
        counts = tmp_path / "counts.txt"

        rc = declsDiff.main([
            "--ref-decls", str(ref),
            "--new-decls", str(new),
            "--new-sha", "1234567890abcdef",
            "--decls-override", str(out),
            "--diff-out", str(diff),
            "--counts-file", str(counts),
        ])
        assert rc == 0
        assert diff.read_text() == "-B\n+D\n"
        assert counts.read_text() == "1 1\n"
        assert "(commit `1234567`)" in out.read_text()
        assert "**+1** new declarations" in out.read_text()

    def test_no_decls_override_no_file_written(self, tmp_path: Path) -> None:
        """Omitting `--decls-override` skips writing the Markdown body."""
        ref = _write(tmp_path / "ref.txt", ["A"])
        new = _write(tmp_path / "new.txt", ["A"])
        counts = tmp_path / "counts.txt"

        rc = declsDiff.main([
            "--ref-decls", str(ref),
            "--new-decls", str(new),
            "--counts-file", str(counts),
        ])
        assert rc == 0
        assert counts.read_text() == "0 0\n"
        assert not (tmp_path / "out.md").exists()

    def test_missing_ref_dump_returns_nonzero_with_merge_hint(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A missing reference dump exits non-zero and points the user at merging master."""
        new = _write(tmp_path / "new.txt", ["A"])

        rc = declsDiff.main([
            "--ref-decls", str(tmp_path / "missing.txt"),
            "--new-decls", str(new),
        ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "--ref-decls" in err
        assert "does not exist" in err
        assert "merging master" in err

    def test_empty_ref_dump_returns_nonzero_with_merge_hint(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An empty reference dump (file exists but zero-byte) is treated like missing."""
        ref = tmp_path / "ref.txt"
        ref.write_text("")
        new = _write(tmp_path / "new.txt", ["A"])

        rc = declsDiff.main([
            "--ref-decls", str(ref),
            "--new-decls", str(new),
        ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "is empty" in err
        assert "merging master" in err


def _dump_dir(d: Path, names: list[str], fields: dict[str, int] | None = None) -> Path:
    """A dump directory as `dumpReasonableDecls.lean --out-dir` writes it: an `api.jsonl`
    whose header lists `fields`. Without `fields`, one written before the API dump, with
    `decls.txt` instead."""
    d.mkdir(parents=True)
    (d / "imports.json").write_text("{}")
    if fields is None:
        _write(d / "decls.txt", names)
    else:
        rows = [{"schema": 1, "fields": fields}]
        rows += [{"n": n, "k": "thm", "m": "M"} for n in names]
        rows += [{"mod": "M"}]
        _write(d / "api.jsonl", [json.dumps(r) for r in rows])
    return d


V1 = {"n": 1, "k": 1, "m": 1, "mod": 1}


class TestDumpDirs:
    def _run(self, tmp_path: Path, ref: Path, new: Path) -> tuple[int, str, str, str]:
        out, status = tmp_path / "out.md", tmp_path / "status.txt"
        rc = declsDiff.main([
            "--ref-dir", str(ref), "--new-dir", str(new), "--new-sha", "1234567890abcdef",
            "--decls-override", str(out), "--diff-out", str(tmp_path / "diff.txt"),
            "--with-heading", "--api-status", str(status),
        ])
        return rc, out.read_text(), (tmp_path / "diff.txt").read_text(), status.read_text()

    def test_both_sides_have_an_api_dump(self, tmp_path: Path) -> None:
        ref = _dump_dir(tmp_path / "ref", ["A", "B"], fields=V1)
        new = _dump_dir(tmp_path / "new", ["A", "C"], fields=V1)
        rc, _, diff, status = self._run(tmp_path, ref, new)
        assert rc == 0
        assert diff == "-B\n+C\n"
        assert status == "used api.jsonl on both sides; fields in common: k, m, mod, n\n"

    def test_fields_in_common_follow_versions(self, tmp_path: Path) -> None:
        ref = _dump_dir(tmp_path / "ref", ["A"], fields=V1)
        new = _dump_dir(tmp_path / "new", ["A"], fields={**V1, "k": 2, "t": 1})
        _, _, _, status = self._run(tmp_path, ref, new)
        assert status.endswith("fields in common: m, mod, n\n")

    def test_output_is_the_same_from_either_file(self, tmp_path: Path) -> None:
        """No visible change: names from `api.jsonl`, from `decls.txt` in a directory,
        or from bare files render byte-identically. (Not for a name holding a line
        separator, which `decls.txt` splits and `api.jsonl` keeps.)"""
        ref_names, new_names = ["A", "B", "-->"], ["A", "C", "-->"]
        x, y = tmp_path / "x", tmp_path / "y"
        _, md_api, diff_api, _ = self._run(
            x, _dump_dir(x / "ref", ref_names, fields=V1),
            _dump_dir(x / "new", new_names, fields=V1))
        _, md_txt, diff_txt, _ = self._run(
            y, _dump_dir(y / "ref", ref_names), _dump_dir(y / "new", new_names))
        bare = tmp_path / "bare.md"
        declsDiff.main([
            "--ref-decls", str(y / "ref" / "decls.txt"),
            "--new-decls", str(y / "new" / "decls.txt"),
            "--new-sha", "1234567890abcdef", "--decls-override", str(bare), "--with-heading",
        ])
        assert md_api == md_txt == bare.read_text()
        assert diff_api == diff_txt

    def test_reference_side_predates_the_api_dump(self, tmp_path: Path) -> None:
        """A master artifact from before the API dump: names from its `decls.txt`."""
        ref = _dump_dir(tmp_path / "ref", ["A", "B"])
        new = _dump_dir(tmp_path / "new", ["A", "C"], fields=V1)
        rc, _, diff, status = self._run(tmp_path, ref, new)
        assert rc == 0
        assert diff == "-B\n+C\n"
        assert status == "absent names only: the reference side has no api.jsonl\n"

    def test_neither_side_has_one(self, tmp_path: Path) -> None:
        rc, _, _, status = self._run(tmp_path, _dump_dir(tmp_path / "ref", ["A"]),
                                     _dump_dir(tmp_path / "new", ["A"]))
        assert rc == 0
        assert status == "absent names only: neither side has api.jsonl\n"

    def test_api_dump_wins_over_a_stray_decls_txt(self, tmp_path: Path) -> None:
        ref = _dump_dir(tmp_path / "ref", ["A"], fields=V1)
        _write(ref / "decls.txt", ["A", "Z"])
        new = _dump_dir(tmp_path / "new", ["A"], fields=V1)
        _, _, diff, _ = self._run(tmp_path, ref, new)
        assert diff == ""

    def test_unreadable_api_dump_fails(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """As a missing or empty `decls.txt` does: there is nothing to fall back to."""
        ref = _dump_dir(tmp_path / "ref", ["A"], fields=V1)
        new = _dump_dir(tmp_path / "new", ["A"], fields=V1)
        (new / "api.jsonl").write_text('{"schema": 2, "fields": {}}\n::warning::forged\n')
        rc = declsDiff.main(["--ref-dir", str(ref), "--new-dir", str(new)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "--new-dir" in err and "is unusable: its schema is newer than" in err
        assert "forged" not in err

    def test_directory_with_neither_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "ref").mkdir()
        new = _dump_dir(tmp_path / "new", ["A"])
        rc = declsDiff.main(["--ref-dir", str(tmp_path / "ref"), "--new-dir", str(new)])
        assert rc == 1
        err = capsys.readouterr().err
        assert "has neither api.jsonl nor decls.txt" in err and "merging master" in err

    def test_empty_decls_txt_in_an_old_directory(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ref = _dump_dir(tmp_path / "ref", [])
        new = _dump_dir(tmp_path / "new", ["A"])
        rc = declsDiff.main(["--ref-dir", str(ref), "--new-dir", str(new)])
        assert rc == 1
        assert "is empty" in capsys.readouterr().err

    @pytest.mark.parametrize("args", [
        ["--new-dir", "n"],
        ["--ref-dir", "r", "--ref-decls", "r/decls.txt", "--new-dir", "n"],
        ["--ref-dir", "r", "--new-decls", "n/decls.txt", "--new-dir", "n"],
    ])
    def test_exactly_one_input_per_side(self, args: list[str]) -> None:
        with pytest.raises(SystemExit) as e:
            declsDiff.main(args)
        assert e.value.code == 2

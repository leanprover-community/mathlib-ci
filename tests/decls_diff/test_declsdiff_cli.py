"""End-to-end CLI tests: invoke declsDiff.main and inspect its output files."""

from __future__ import annotations

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


class TestMeta:
    def _run(self, tmp_path: Path, extra: list[str]) -> str:
        ref = _write(tmp_path / "ref.txt", ["A", "B", "C"])
        new = _write(tmp_path / "new.txt", ["A", "B", "D"])
        out = tmp_path / "out.md"
        rc = declsDiff.main([
            "--ref-decls", str(ref),
            "--new-decls", str(new),
            "--decls-override", str(out),
            *extra,
        ])
        assert rc == 0
        return out.read_text()

    def test_reports_moves_into_and_out_of_meta(self, tmp_path: Path) -> None:
        """Only declarations present on both sides are reported as meta moves."""
        ref_meta = _write(tmp_path / "ref_meta.txt", ["B", "C"])
        new_meta = _write(tmp_path / "new_meta.txt", ["A", "D"])
        meta_diff = tmp_path / "meta_diff.txt"
        body = self._run(tmp_path, [
            "--ref-meta", str(ref_meta),
            "--new-meta", str(new_meta),
            "--meta-diff-out", str(meta_diff),
        ])
        # A became meta, B stopped being meta; C (removed) and D (added) are not moves.
        assert meta_diff.read_text() == "+meta A\n-meta B\n"
        assert "**1** moved into `meta`, **1** moved out of `meta`" in body
        assert "+meta A\n-meta B" in body

    def test_empty_meta_dumps_are_valid(self, tmp_path: Path) -> None:
        """Empty meta dumps mean no meta declarations, not missing data."""
        ref_meta = tmp_path / "ref_meta.txt"
        ref_meta.write_text("")
        new_meta = tmp_path / "new_meta.txt"
        new_meta.write_text("")
        body = self._run(tmp_path, [
            "--ref-meta", str(ref_meta),
            "--new-meta", str(new_meta),
        ])
        assert "**0** moved into `meta`, **0** moved out of `meta`" in body
        assert "marking changed" not in body

    def test_missing_ref_meta_notes_unavailable(self, tmp_path: Path) -> None:
        """A reference build without a meta dump is noted, not treated as an error."""
        new_meta = _write(tmp_path / "new_meta.txt", ["A"])
        body = self._run(tmp_path, [
            "--ref-meta", str(tmp_path / "missing.txt"),
            "--new-meta", str(new_meta),
        ])
        assert "`meta` changes unavailable" in body
        assert "moved into `meta`" not in body

    def test_no_meta_args_leaves_body_unchanged(self, tmp_path: Path) -> None:
        """Without meta dumps the body has no meta lines at all."""
        body = self._run(tmp_path, [])
        assert "meta" not in body

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

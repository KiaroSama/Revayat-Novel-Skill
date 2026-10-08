"""Input format admission."""

from __future__ import annotations

from pathlib import Path
import pytest
from extract import ExtractError, detect_format

def test_detect_format_by_suffix(sample_pdf, sample_epub, tmp_path):
    assert detect_format(Path(sample_pdf)) == "pdf"
    assert detect_format(Path(sample_epub)) == "epub"


def test_detect_format_falls_back_to_magic_bytes(sample_pdf, tmp_path):
    renamed = tmp_path / "book.bin"
    renamed.write_bytes(Path(sample_pdf).read_bytes())
    assert detect_format(renamed) == "pdf"


def test_detect_format_rejects_the_unknown(tmp_path):
    odd = tmp_path / "notes.rtf"
    odd.write_bytes(b"{\\rtf1 hello}")
    with pytest.raises(ExtractError, match="supported inputs"):
        detect_format(odd)

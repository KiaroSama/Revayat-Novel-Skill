"""A converter's success is not evidence that an old PDF was produced now."""
import subprocess
from pathlib import Path

import pytest
import pymupdf
import wordrender


def test_success_without_fresh_output_preserves_previous_pdf(tmp_path, monkeypatch):
    source = tmp_path / "preview.docx"
    source.write_bytes(b"input document")
    out = tmp_path / "renders"
    out.mkdir()
    old = out / "preview.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(old)
    before = old.read_bytes()
    monkeypatch.setattr(wordrender, "backend", lambda: "word")
    monkeypatch.setattr(wordrender, "_run_bounded", lambda argv, timeout:
                        subprocess.CompletedProcess(argv, 0, b"", b""))
    with pytest.raises(wordrender.RenderError):
        wordrender.render(source, out)
    assert old.read_bytes() == before


def test_batch_never_promotes_a_previous_output_or_silent_duplicate_name(tmp_path, monkeypatch):
    sources = [tmp_path / "first.docx", tmp_path / "second.docx"]
    for source in sources:
        source.write_bytes(b"input document")
    out = tmp_path / "renders"
    out.mkdir()
    for source in sources:
        with pymupdf.open() as document:
            document.new_page()
            document.save(out / (source.stem + ".pdf"))
    previous = {path: path.read_bytes() for path in out.glob("*.pdf")}
    monkeypatch.setattr(wordrender, "backend", lambda: "word")
    monkeypatch.setattr(wordrender, "_run_bounded", lambda argv, timeout:
                        subprocess.CompletedProcess(argv, 0, b"", b""))
    produced, failed, _ = wordrender.render_many(sources, out)
    assert not produced and set(failed) == set(sources)
    assert all(path.read_bytes() == data for path, data in previous.items())
    with pytest.raises(wordrender.RenderError, match="duplicate"):
        wordrender.render_many([sources[0], tmp_path / "other" / sources[0].name], out)


def test_batch_success_token_without_readable_artifact_is_a_failure(tmp_path, monkeypatch):
    source = tmp_path / "first.docx"
    source.write_bytes(b"input document")
    out = tmp_path / "renders"
    out.mkdir()
    previous = out / "first.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(previous)
    before = previous.read_bytes()
    monkeypatch.setattr(wordrender, "backend", lambda: "word")
    monkeypatch.setattr(wordrender, "_run_bounded", lambda argv, timeout:
                        subprocess.CompletedProcess(argv, 0, b"OK\tfirst.docx\tfirst.pdf\n", b""))
    produced, failed, _ = wordrender.render_many([source], out)
    assert produced == {} and set(failed) == {source}
    assert "fresh readable PDF" in failed[source]
    assert previous.read_bytes() == before


def test_fresh_readable_result_replaces_previous_pdf_only_after_validation(tmp_path, monkeypatch):
    source = tmp_path / "preview.docx"
    source.write_bytes(b"input document")
    out = tmp_path / "renders"
    out.mkdir()
    previous = out / "preview.pdf"
    previous.write_bytes(b"old invalid PDF")
    monkeypatch.setattr(wordrender, "backend", lambda: "word")
    def convert(argv, timeout):
        with pymupdf.open() as document:
            document.new_page()
            document.save(str(Path(argv[-1]) / "preview.pdf"))
        return subprocess.CompletedProcess(argv, 0, b"", b"")
    monkeypatch.setattr(wordrender, "_run_bounded", convert)
    made, backend = wordrender.render(source, out)
    assert made == previous and backend == "word"
    assert wordrender.readable_pdf(made)
    assert not list(out.glob(".render-*"))

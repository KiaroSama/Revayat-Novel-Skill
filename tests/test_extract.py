"""Format detection, OCR routing, and the side-door importers.

The OCR *decisions* are what matter here and they are tested without the binary
installed: which mode a book gets is the difference between recognising a scan
and destroying a page that was already perfect. Whether OCRmyPDF itself works is
OCRmyPDF's problem; whether we ask it for the right thing is ours.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bookir as ir
from extract import (
    ExtractError,
    detect_format,
    find_ocrmypdf,
    from_markdown,
    from_mineru,
    ocr_command,
    probe_pdf,
    run_ocr,
)


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Scan probing
# --------------------------------------------------------------------------- #

def test_probe_classifies_born_digital(sample_pdf):
    probe = probe_pdf(Path(sample_pdf))
    assert probe["kind"] == "digital"
    assert probe["pages_without_text"] == []


def test_probe_classifies_a_full_scan(scanned_pdf):
    probe = probe_pdf(scanned_pdf)
    assert probe["kind"] == "scanned"
    assert probe["pages_with_text"] == 0
    assert probe["median_chars_per_page"] == 0


def test_probe_classifies_a_mixed_book_and_names_the_scanned_pages(mixed_pdf):
    """The case that matters: OCR must not touch the page that is already fine."""
    probe = probe_pdf(mixed_pdf)
    assert probe["kind"] == "mixed"
    assert probe["pages_with_text"] == 1
    assert probe["pages_without_text"] == [2]


# --------------------------------------------------------------------------- #
# OCR routing
# --------------------------------------------------------------------------- #

def test_a_full_scan_gets_force_ocr_and_deskew(tmp_path):
    command = ocr_command(["ocrmypdf"], tmp_path / "in.pdf", tmp_path / "out.pdf",
                          kind="scanned")
    assert "--force-ocr" in command
    assert "--deskew" in command
    assert "--skip-text" not in command


def test_a_mixed_book_gets_skip_text_and_no_deskew(tmp_path):
    """Deskew rewrites the raster, so it must not run over good pages."""
    command = ocr_command(["ocrmypdf"], tmp_path / "in.pdf", tmp_path / "out.pdf",
                          kind="mixed")
    assert "--skip-text" in command
    assert "--force-ocr" not in command
    assert "--deskew" not in command


def test_images_are_never_recompressed(tmp_path):
    """The whole point of the pipeline is that the book's pictures survive."""
    for kind in ("scanned", "mixed"):
        command = ocr_command(["ocrmypdf"], tmp_path / "in.pdf",
                              tmp_path / "out.pdf", kind=kind)
        assert command[command.index("--optimize") + 1] == "0"
        assert command[command.index("--output-type") + 1] == "pdf"


def test_deskew_can_be_forced_either_way(tmp_path):
    on = ocr_command(["ocrmypdf"], tmp_path / "i", tmp_path / "o",
                     kind="mixed", deskew=True)
    off = ocr_command(["ocrmypdf"], tmp_path / "i", tmp_path / "o",
                      kind="scanned", deskew=False)
    assert "--deskew" in on
    assert "--deskew" not in off


def test_launcher_and_language_reach_the_command(tmp_path):
    command = ocr_command(["python", "-m", "ocrmypdf"], tmp_path / "i", tmp_path / "o",
                          kind="scanned", language="fas+eng")
    assert command[:3] == ["python", "-m", "ocrmypdf"]
    assert command[command.index("--language") + 1] == "fas+eng"
    assert command[-2:] == [str(tmp_path / "i"), str(tmp_path / "o")]


def test_missing_ocrmypdf_explains_all_three_prerequisites(monkeypatch, tmp_path):
    monkeypatch.setattr("extract.find_ocrmypdf", lambda: None)
    with pytest.raises(ExtractError) as caught:
        run_ocr(tmp_path / "in.pdf", tmp_path / "out.pdf", kind="scanned")
    message = str(caught.value)
    # Ghostscript is not distributed through winget, so the message must not
    # send anyone there for it.
    assert "pip install ocrmypdf" in message
    assert "tesseract" in message.lower()
    assert "ghostscript.com" in message
    assert "--ocr off" in message


def test_find_ocrmypdf_prefers_path_then_falls_back_to_the_module(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ocrmypdf")
    assert find_ocrmypdf() == ["/usr/bin/ocrmypdf"]

    monkeypatch.setattr("shutil.which", lambda name: None)
    launcher = find_ocrmypdf()
    assert launcher is None or launcher[1:] == ["-m", "ocrmypdf"]


# --------------------------------------------------------------------------- #
# MinerU importer
# --------------------------------------------------------------------------- #

def _mineru_run(root: Path, png: bytes) -> Path:
    """A MinerU output directory, matching its documented content_list schema."""
    out = root / "mineru"
    (out / "images").mkdir(parents=True)
    (out / "images" / "fig.png").write_bytes(png)
    content = [
        {"type": "text", "text": "Chapter One", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "The morning came slowly.", "text_level": 0,
         "page_idx": 0},
        {"type": "image", "img_path": "images/fig.png",
         "image_caption": ["Figure 1: the ridge"], "page_idx": 0},
        {"type": "text", "text": "A second page of prose.", "text_level": 0,
         "page_idx": 1},
    ]
    (out / "book_content_list.json").write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8"
    )
    return out


def test_mineru_import_maps_levels_images_and_captions(tmp_path, sample_png):
    run = _mineru_run(tmp_path, sample_png.read_bytes())
    book = from_mineru(run, tmp_path / "assets", source_name="book",
                       lang_source="en", lang_target="fa-IR")

    assert ir.validate_book(book) == []
    kinds = [b["type"] for b in book["blocks"]]
    assert kinds.count("heading") == 1
    assert kinds.count("image") == 1
    assert kinds.count("caption") == 1
    # text_level 1 is a heading; 0 is body.
    heading = next(b for b in book["blocks"] if b["type"] == "heading")
    assert ir.plain_text(heading["text"]) == "Chapter One"
    image = next(b for b in book["blocks"] if b["type"] == "image")
    assert (tmp_path / "assets" / image["asset"]).exists()
    # A page change becomes a soft break, not a lost boundary.
    assert "pagebreak" in kinds


def test_mineru_import_without_a_content_list_says_how_to_produce_one(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ExtractError, match="mineru -p"):
        from_mineru(tmp_path / "empty", tmp_path / "assets", source_name="b",
                    lang_source="en", lang_target="fa-IR")


# --------------------------------------------------------------------------- #
# Markdown importer (Marker, Docling, or a hand-made file)
# --------------------------------------------------------------------------- #

def test_markdown_import_reads_structure_and_copies_images(tmp_path, sample_png):
    source = tmp_path / "md"
    source.mkdir()
    (source / "fig.png").write_bytes(sample_png.read_bytes())
    (source / "book.md").write_text(
        "# Chapter One\n"
        "\n"
        "He whispered, *do not look back*.\n"
        "Still on the same paragraph.\n"
        "\n"
        "![A red rectangle](fig.png)\n"
        "\n"
        "> A quoted line.\n"
        "\n"
        "- First item\n"
        "- Second item\n"
        "\n"
        "---\n"
        "\n"
        "## Chapter Two\n",
        encoding="utf-8",
    )

    book = from_markdown(source / "book.md", tmp_path / "assets",
                         lang_source="en", lang_target="fa-IR")
    assert ir.validate_book(book) == []

    kinds = [b["type"] for b in book["blocks"]]
    assert kinds.count("heading") == 2
    assert kinds.count("image") == 1
    assert kinds.count("listitem") == 2
    assert "separator" in kinds

    image = next(b for b in book["blocks"] if b["type"] == "image")
    assert image["alt"] == "A red rectangle"
    assert (tmp_path / "assets" / image["asset"]).exists()

    # Lines of one paragraph are joined, and emphasis survives the import.
    paragraph = next(b for b in book["blocks"] if b["type"] == "paragraph")
    assert "*do not look back*" in paragraph["text"]
    assert "Still on the same paragraph." in paragraph["text"]


def test_markdown_import_tolerates_a_missing_image(tmp_path):
    source = tmp_path / "md"
    source.mkdir()
    (source / "book.md").write_text("![gone](missing.png)\n\nProse.\n", encoding="utf-8")
    book = from_markdown(source / "book.md", tmp_path / "assets",
                         lang_source="en", lang_target="fa-IR")
    assert not any(b["type"] == "image" for b in book["blocks"])
    assert any(b["type"] == "paragraph" for b in book["blocks"])

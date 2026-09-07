"""Extraction: the parts a naive `book → markdown` pass throws away.

The regressions guarded here are the ones that were actually found while
building this: a PDF text block holding a paragraph tail *and* the next
subheading collapsing into one wrong heading, and a paragraph split by a page
break arriving at the translator as two half-sentences.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import bookir as ir


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def pdf_book(sample_pdf, tmp_path_factory):
    from read_pdf import read_pdf
    return read_pdf(str(sample_pdf), tmp_path_factory.mktemp("pdf_assets"))


def test_pdf_metadata_and_validity(pdf_book):
    assert pdf_book["meta"]["title"] == "Pride and Prejudice"
    assert pdf_book["meta"]["author"] == "Jane Austen"
    assert ir.validate_book(pdf_book) == []


def test_pdf_running_heads_and_page_numbers_are_dropped(pdf_book):
    body = " ".join(b.get("text", "") for b in pdf_book["blocks"])
    assert "PRIDE AND PREJUDICE" not in body
    # A bare page number normalises to "#" and repeats on every page.
    assert "#" in pdf_book["source"]["running_heads_dropped"]


def test_pdf_headings_detected_by_relative_font_size(pdf_book):
    headings = [b for b in pdf_book["blocks"] if b["type"] == "heading"]
    titles = [ir.plain_text(h["text"]) for h in headings]
    assert "Chapter One" in titles and "Chapter Two" in titles
    assert min(h["level"] for h in headings) == 1


def test_pdf_subheading_does_not_swallow_the_paragraph_before_it(pdf_book):
    """A mixed-size text block must split, not classify as one heading."""
    note = next(
        b for b in pdf_book["blocks"]
        if b["type"] == "heading" and "A Note on the Text" in ir.plain_text(b["text"])
    )
    assert "betrayed" not in ir.plain_text(note["text"])
    assert any(
        b["type"] == "paragraph" and "betrayed" in ir.plain_text(b.get("text", ""))
        for b in pdf_book["blocks"]
    )


def test_pdf_split_paragraph_is_rejoined(pdf_book):
    joined = [
        ir.plain_text(b["text"]) for b in pdf_book["blocks"] if b["type"] == "paragraph"
    ]
    assert any("morning came slowly" in t and "Elizabeth" in t for t in joined)


def test_pdf_italic_run_becomes_markup(pdf_book):
    assert any("*do not look back*" in (b.get("text") or "") for b in pdf_book["blocks"])


def test_pdf_images_keep_bytes_and_physical_geometry(pdf_book, tmp_path_factory):
    images = [b for b in pdf_book["blocks"] if b["type"] == "image"]
    assert len(images) == 2
    for image in images:
        assert image["sha256"]
        assert image["width_pt"] == pytest.approx(180.0, abs=1.0)
        assert image["height_pt"] == pytest.approx(120.0, abs=1.0)
        assert image["pixel_width"] == 120 and image["pixel_height"] == 80


def test_pdf_probe_classifies_a_born_digital_file(sample_pdf):
    from extract import probe_pdf
    probe = probe_pdf(Path(sample_pdf))
    assert probe["kind"] == "digital"
    assert probe["pages"] == 3
    assert probe["pages_without_text"] == []


# --------------------------------------------------------------------------- #
# EPUB
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def epub_book(sample_epub, tmp_path_factory):
    from read_epub import read_epub
    return read_epub(str(sample_epub), tmp_path_factory.mktemp("epub_assets"))


def test_epub_structure_follows_spine_order(epub_book):
    assert ir.validate_book(epub_book) == []
    assert epub_book["meta"]["title"] == "A Small Book"
    kinds = [b["type"] for b in epub_book["blocks"]]
    assert kinds.index("heading") < kinds.index("image")
    headings = [b for b in epub_book["blocks"] if b["type"] == "heading"]
    assert [h["level"] for h in headings] == [1, 2]


def test_epub_emphasis_and_verbatim_survive(epub_book):
    body = " ".join(b.get("text") or "" for b in epub_book["blocks"])
    assert "*do not look back*" in body
    assert "**gone**" in body
    assert "`literal_token`" in body


def test_epub_footnote_is_lifted_out_of_the_flow(epub_book):
    assert len(epub_book["footnotes"]) == 1
    note = epub_book["footnotes"][0]
    assert "Thanksgiving" in note["text"]
    # The leading "1." marker is stripped: Word numbers footnotes itself.
    assert not note["text"].startswith("1.")
    anchored = [
        b for b in epub_book["blocks"] if note["id"] in ir.footnote_refs(b.get("text") or "")
    ]
    assert len(anchored) == 1
    # The note body must not also appear as ordinary prose.
    assert sum(
        "Thanksgiving" in (b.get("text") or "") for b in epub_book["blocks"]
    ) == 0


def test_epub_image_alt_and_bytes(epub_book, tmp_path_factory):
    image = next(b for b in epub_book["blocks"] if b["type"] == "image")
    assert image["alt"] == "A red rectangle"
    assert image["pixel_width"] == 120 and image["pixel_height"] == 80


def test_epub_list_items_and_quotes_keep_their_types(epub_book):
    kinds = {b["type"] for b in epub_book["blocks"]}
    assert "listitem" in kinds and "blockquote" in kinds


# --------------------------------------------------------------------------- #
# DOCX
# --------------------------------------------------------------------------- #

def test_docx_reader_round_trips_a_document_we_built(translated_book, tmp_path):
    """Build a DOCX from the IR, read it back, and check nothing structural is lost."""
    import argparse

    from build_docx import Builder, add_arguments
    from read_docx import read_docx

    book, assets = translated_book
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(
        ["--book", "x", "--out", "y", "--font", "Tahoma", "--no-toc"]
    )
    destination = tmp_path / "round.docx"
    Builder(book, assets, options).build(destination)

    again = read_docx(str(destination), tmp_path / "again_assets")
    assert ir.validate_book(again) == []
    assert any(b["type"] == "heading" for b in again["blocks"])
    assert any(b["type"] == "image" for b in again["blocks"])
    assert len(again["footnotes"]) == len(book["footnotes"])
    assert "یادداشت مترجم" in again["footnotes"][0]["text"]


# --------------------------------------------------------------------------- #
# An archive is untrusted input, and a zip declares its own size
# --------------------------------------------------------------------------- #

def _bomb(path, *, members=1, size_each=64 * 1024 * 1024):
    """A tiny file that declares a huge unpacked size: zeros compress to nothing."""
    import zipfile
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for n in range(members):
            z.writestr(f"m{n:05d}.bin", b"\0" * size_each)
    return path


def test_an_archive_that_inflates_absurdly_is_refused_before_it_is_opened(tmp_path):
    """Sixty-four megabytes of zeros is a few kilobytes on disk and a 1000:1 member.

    The reader must refuse from the central directory alone. Nothing may be
    extracted to find out — that is the attack.
    """
    import bookir as ir

    bomb = _bomb(tmp_path / "bomb.docx")
    assert bomb.stat().st_size < 200 * 1024, "the fixture is not a bomb"
    with pytest.raises(ir.ArchiveTooLarge) as refused:
        ir.check_archive_limits(bomb)
    assert "inflates" in str(refused.value)


def test_too_many_members_is_refused_even_when_each_is_tiny(tmp_path):
    import bookir as ir

    many = _bomb(tmp_path / "many.epub", members=ir.ARCHIVE_MAX_MEMBERS + 1,
                 size_each=1)
    with pytest.raises(ir.ArchiveTooLarge) as refused:
        ir.check_archive_limits(many)
    assert "members" in str(refused.value)


def test_the_docx_and_epub_readers_both_refuse_a_bomb(tmp_path):
    """The guard has to sit in front of the readers, not beside them."""
    import bookir as ir
    from read_docx import read_docx
    from read_epub import read_epub

    bomb = _bomb(tmp_path / "bomb.docx")
    with pytest.raises(ir.ArchiveTooLarge):
        read_docx(str(bomb), tmp_path / "assets-docx")

    bomb2 = _bomb(tmp_path / "bomb.epub")
    with pytest.raises(ir.ArchiveTooLarge):
        read_epub(str(bomb2), tmp_path / "assets-epub")


def test_a_real_sized_book_archive_is_not_refused(tmp_path):
    """The ceilings must clear a novel by a wide margin, or the guard is an outage."""
    import zipfile
    import bookir as ir

    book = tmp_path / "novel.epub"
    with zipfile.ZipFile(book, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip")
        for n in range(60):                       # sixty chapters
            z.writestr(f"OEBPS/ch{n:02d}.xhtml", "<p>" + ("word " * 20_000) + "</p>")
        z.writestr("OEBPS/cover.jpg", bytes(range(256)) * 4000)  # incompressible
    measured = ir.check_archive_limits(book)
    assert measured["members"] == 62
    assert measured["unpacked_bytes"] > 5 * 1024 * 1024


def test_pillow_keeps_its_own_decode_ceiling():
    """A 66-byte PNG can declare 60000x60000 and cost ten gigabytes to decode.

    Pillow refuses that by default (`Image.MAX_IMAGE_PIXELS`). This pins the
    default so nobody sets it to None to make one large scan load — the fix for
    that is a higher ceiling, never no ceiling.
    """
    import io
    import struct
    import zlib

    from PIL import Image

    assert Image.MAX_IMAGE_PIXELS is not None, "Pillow's decode ceiling was disabled"

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    hostile = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", struct.pack(">IIBBBBB", 60000, 60000, 8, 0, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(b"\0"))
               + chunk(b"IEND", b""))
    assert len(hostile) < 100
    with pytest.raises(Image.DecompressionBombError):
        Image.open(io.BytesIO(hostile)).load()

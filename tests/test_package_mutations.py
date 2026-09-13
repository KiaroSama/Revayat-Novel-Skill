"""The finished `.docx`, mutated on purpose, part by part.

`qa check` reads `book.json`; `qa docx` reads the file that was actually written.
Only the second can catch a build that dropped a picture or renumbered a note —
and it could not, because it compared *counts and the intersection of shared
hashes* rather than bytes and placement. Measured on real synthetic packages
before this was fixed:

* replacing `word/media/image1.png` with a different image of the same length
  passed, because the forged bytes were in neither side's set and so dropped out
  of their own comparison;
* deleting the `<w:drawing>` while leaving the media part in place passed, because
  with no placements at all there was "nothing to compare";
* removing `[Content_Types].xml` raised `KeyError` out of the function whose whole
  job is to report what is wrong with a package.

Every row below is a real archive, repacked. The control matters as much as the
mutations: a valid package must still pass, including the legitimate variations a
real builder produces.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import qa  # noqa: E402
from build_docx import Builder, add_arguments  # noqa: E402
from tests_support import png_bytes  # noqa: E402

PERSIAN = "بندی فارسی که به اندازهٔ کافی بلند است تا نسبت طول را نشکند."


def _options() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    return parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma"])


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    """One real package, built by the real builder, with a picture and a note."""
    pytest.importorskip("docx")
    work = tmp_path_factory.mktemp("package")
    assets = work / "assets"
    assets.mkdir()
    picture = png_bytes(120, 80)
    (assets / "fig.png").write_bytes(picture)

    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="A Small Book", author="Test Author")
    book["blocks"].append(ir.make_block("heading", 1, level=1, text="Chapter One"))
    book["blocks"][0]["target"] = "فصل یکم"
    book["blocks"].append(
        ir.make_block("paragraph", 2, text="A paragraph.[[fn:fn0001]]"))
    book["blocks"][1]["target"] = f"{PERSIAN}[[fn:fn0001]]"
    book["blocks"].append(ir.make_block(
        "image", 3, asset="fig.png", sha256=ir.sha256_bytes(picture), bbox=None,
        width_pt=180.0, height_pt=120.0, pixel_width=120, pixel_height=80,
        alt="A red rectangle", target_alt="یک مستطیل سرخ"))
    book["footnotes"].append(
        ir.make_footnote(1, anchor_block="b00002", text="A note."))
    book["footnotes"][0]["target"] = "یادداشتی فارسی."

    destination = work / "book.fa.docx"
    Builder(book, assets, _options()).build(destination)
    return book, destination


def _repack(source: Path, destination: Path, transform) -> Path:
    with zipfile.ZipFile(source) as archive:
        items = {item.filename: archive.read(item.filename)
                 for item in archive.infolist()}
    items = transform(items)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in items.items():
            archive.writestr(name, data)
    return destination


def _codes(path: Path, book: dict) -> set[str]:
    summary = qa.check_docx(path, book).summary()
    return {finding["code"] for finding in summary["findings"]}


def test_a_valid_package_passes(built):
    """The control. A verifier that rejects good work is not a verifier."""
    book, path = built
    summary = qa.check_docx(path, book).summary()
    assert summary["ok"], summary["findings"]


def test_a_substituted_picture_of_the_same_size_is_caught(built, tmp_path):
    """Counting the media parts cannot see this, and neither could the order check."""
    book, path = built

    def forge(items: dict) -> dict:
        media = sorted(name for name in items if name.startswith("word/media/"))
        assert media, "the fixture has no picture"
        altered = bytearray(items[media[0]])
        altered[-6] ^= 0xFF          # same length, different bytes
        items[media[0]] = bytes(altered)
        return items

    broken = _repack(path, tmp_path / "forged.docx", forge)

    codes = _codes(broken, book)
    assert "image-unexpected" in codes, codes
    assert not qa.check_docx(broken, book).summary()["ok"]


def test_removing_the_drawing_but_keeping_the_media_part_is_caught(built, tmp_path):
    """The file count is unchanged; the reader sees no illustration."""
    book, path = built

    def undraw(items: dict) -> dict:
        document = items["word/document.xml"].decode("utf-8")
        start = document.find("<w:drawing>")
        end = document.find("</w:drawing>")
        assert start >= 0 and end > start, "the fixture has no drawing"
        items["word/document.xml"] = (
            document[:start] + document[end + len("</w:drawing>"):]).encode("utf-8")
        return items

    broken = _repack(path, tmp_path / "undrawn.docx", undraw)

    codes = _codes(broken, book)
    assert "image-missing-placement" in codes, codes
    assert sorted(zipfile.ZipFile(broken).namelist()) != [], "repack failed"
    assert any(name.startswith("word/media/")
               for name in zipfile.ZipFile(broken).namelist()), (
        "this test only means something while the media part is still there")


def test_dropping_every_footnote_part_is_caught(built, tmp_path):
    book, path = built
    broken = _repack(path, tmp_path / "noteless.docx",
                     lambda items: {name: data for name, data in items.items()
                                    if "footnote" not in name.lower()})
    assert "footnotes-part-missing" in _codes(broken, book)


@pytest.mark.parametrize("part", [
    "[Content_Types].xml",
    "word/_rels/document.xml.rels",
    "word/document.xml",
])
def test_a_missing_required_part_is_a_finding_not_an_exception(built, tmp_path, part):
    """`[Content_Types].xml` was read without asking whether it was there."""
    book, path = built
    broken = _repack(path, tmp_path / f"no-{part.replace('/', '-')}.docx",
                     lambda items: {name: data for name, data in items.items()
                                    if name != part})

    summary = qa.check_docx(broken, book).summary()   # must not raise

    assert summary["ok"] is False
    assert "docx-invalid" in {finding["code"] for finding in summary["findings"]}
    assert any(part in (finding["detail"] or "")
               for finding in summary["findings"]), summary["findings"]


def test_a_damaged_archive_is_reported_rather_than_raised(tmp_path):
    """Not a zip at all: the shape of a truncated download or a half-written file."""
    broken = tmp_path / "truncated.docx"
    broken.write_bytes(b"PK\x03\x04 and then nothing useful at all")

    summary = qa.check_docx(broken, None).summary()

    assert summary["ok"] is False
    assert "docx-unreadable" in {finding["code"] for finding in summary["findings"]}


def test_a_malformed_document_part_is_reported(built, tmp_path):
    book, path = built
    broken = _repack(path, tmp_path / "malformed.docx",
                     lambda items: {**items,
                                    "word/document.xml": b"<w:document><w:body><w:p>"})
    assert not qa.check_docx(broken, book).summary()["ok"]


def test_two_placements_of_one_picture_are_not_a_duplicate(tmp_path):
    """A legitimate variation the stricter check must not reject.

    python-docx stores one media part per *distinct* image, so a book that shows
    the same illustration twice has two placements and one part. A check that
    compared the media files to the expected list would call that a missing
    picture.
    """
    pytest.importorskip("docx")
    assets = tmp_path / "assets"
    assets.mkdir()
    picture = png_bytes(90, 60)
    (assets / "fig.png").write_bytes(picture)

    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="Twice", author="Test Author")
    book["blocks"].append(ir.make_block("paragraph", 1, text="A paragraph."))
    book["blocks"][0]["target"] = PERSIAN
    for index in (2, 3):
        book["blocks"].append(ir.make_block(
            "image", index, asset="fig.png", sha256=ir.sha256_bytes(picture),
            bbox=None, width_pt=120.0, height_pt=80.0,
            pixel_width=90, pixel_height=60, alt="A rectangle",
            target_alt="یک مستطیل"))

    destination = tmp_path / "twice.docx"
    Builder(book, assets, _options()).build(destination)

    summary = qa.check_docx(destination, book).summary()
    assert summary["ok"], summary["findings"]
    assert summary["counts"].get("pictures_placed") == 2

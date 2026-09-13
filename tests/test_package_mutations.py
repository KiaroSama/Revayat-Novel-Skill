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
import re
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


# --------------------------------------------------------------------------- #
# The notes, as a graph rather than as a count
# --------------------------------------------------------------------------- #
# Every mutation below passed. The note checks ran only `if refs:`, matched bodies
# by id and collected the references into a set — so an emptied document, a
# substituted note, a deleted relationship and a duplicated marker were each
# invisible, and a real render showed the footnote gone while the package reported
# zero errors.

def _document(items: dict) -> str:
    return items["word/document.xml"].decode("utf-8")


def test_stripping_every_footnote_reference_is_caught(built, tmp_path):
    """The document that shows no note at all, which used to be unexamined."""
    book, path = built

    def strip(items: dict) -> dict:
        document = _document(items)
        items["word/document.xml"] = re.sub(
            r"<w:footnoteReference[^>]*/>", "", document).encode("utf-8")
        return items

    broken = _repack(path, tmp_path / "unreferenced.docx", strip)

    assert "footnotes-lost" in _codes(broken, book)


def test_substituting_a_notes_text_is_caught(built, tmp_path):
    """Matching ids cannot see this: the note says what nobody wrote."""
    book, path = built

    def rewrite(items: dict) -> dict:
        notes = items["word/footnotes.xml"].decode("utf-8")
        items["word/footnotes.xml"] = notes.replace(
            "یادداشتی فارسی.", "چیز دیگری که مترجم ننوشته است.").encode("utf-8")
        return items

    broken = _repack(path, tmp_path / "substituted.docx", rewrite)

    codes = _codes(broken, book)
    assert "footnote-text-mismatch" in codes, codes


def test_removing_the_footnotes_relationship_is_caught(built, tmp_path):
    """Word reaches the notes through the relationship graph, not by filename."""
    book, path = built

    def unrelate(items: dict) -> dict:
        rels = items["word/_rels/document.xml.rels"].decode("utf-8")
        items["word/_rels/document.xml.rels"] = re.sub(
            r'<Relationship[^>]*Type="[^"]*/footnotes"[^>]*/>', "",
            rels).encode("utf-8")
        return items

    broken = _repack(path, tmp_path / "unrelated.docx", unrelate)

    codes = _codes(broken, book)
    assert "footnotes-relationship-missing" in codes, codes


def test_duplicating_a_reference_is_caught(built, tmp_path):
    """A set of ids loses exactly this: one note, two sentences."""
    book, path = built

    def twice(items: dict) -> dict:
        document = _document(items)
        found = re.search(r"<w:r\b[^>]*>(?:(?!</w:r>).)*?"
                          r"<w:footnoteReference[^>]*/>.*?</w:r>", document,
                          re.S)
        assert found, "the fixture has no footnote reference run"
        run = found.group(0)
        items["word/document.xml"] = document.replace(
            run, run + run, 1).encode("utf-8")
        return items

    broken = _repack(path, tmp_path / "twice.docx", twice)

    codes = _codes(broken, book)
    assert "footnote-reference-duplicated" in codes, codes


def test_a_note_body_nothing_refers_to_is_caught(built, tmp_path):
    book, path = built

    def orphan(items: dict) -> dict:
        notes = items["word/footnotes.xml"].decode("utf-8")
        found = re.search(r'<w:footnote w:id="(\d+)">.*?</w:footnote>', notes, re.S)
        assert found, "the fixture has no note body"
        spare = found.group(0).replace(f'w:id="{found.group(1)}"', 'w:id="99"')
        items["word/footnotes.xml"] = notes.replace(
            found.group(0), found.group(0) + spare, 1).encode("utf-8")
        return items

    broken = _repack(path, tmp_path / "orphan.docx", orphan)

    codes = _codes(broken, book)
    assert "footnote-body-orphaned" in codes, codes


# --------------------------------------------------------------------------- #
# The pictures, as geometry rather than as a count of tags
# --------------------------------------------------------------------------- #

def test_distorting_a_pictures_extent_is_caught(built, tmp_path):
    """The bytes are untouched and the count is unchanged; the picture is squashed."""
    book, path = built

    def squash(items: dict) -> dict:
        document = _document(items)
        found = re.search(r'<wp:extent cx="(\d+)" cy="(\d+)"/>', document)
        assert found, "the fixture has no sized picture"
        stretched = f'<wp:extent cx="{found.group(1)}" cy="{int(found.group(2)) * 2}"/>'
        items["word/document.xml"] = document.replace(
            found.group(0), stretched, 1).encode("utf-8")
        return items

    broken = _repack(path, tmp_path / "squashed.docx", squash)

    codes = _codes(broken, book)
    assert "picture-aspect" in codes, codes


def test_widening_a_picture_past_the_text_measure_is_caught(built, tmp_path):
    book, path = built

    def widen(items: dict) -> dict:
        document = _document(items)
        found = re.search(r'<wp:extent cx="(\d+)" cy="(\d+)"/>', document)
        assert found
        wide = (f'<wp:extent cx="{int(found.group(1)) * 4}" '
                f'cy="{int(found.group(2)) * 4}"/>')
        items["word/document.xml"] = document.replace(
            found.group(0), wide, 1).encode("utf-8")
        return items

    broken = _repack(path, tmp_path / "wide.docx", widen)

    codes = _codes(broken, book)
    assert "picture-too-wide" in codes, codes


def test_a_picture_the_builder_fitted_to_the_text_block_still_passes(tmp_path):
    """The control for the two above: legitimate fitting is not a distortion.

    A source picture wider than the text measure is scaled down to it, aspect
    preserved — `build_docx._image_size` — so the drawn width is the measure and
    the drawn shape is the source's. A width check without the fitting rule would
    reject every large illustration in every book.
    """
    pytest.importorskip("docx")
    assets = tmp_path / "assets"
    assets.mkdir()
    picture = png_bytes(200, 100)
    (assets / "wide.png").write_bytes(picture)

    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="Wide", author="Test Author")
    book["blocks"].append(ir.make_block("paragraph", 1, text="A paragraph."))
    book["blocks"][0]["target"] = PERSIAN
    book["blocks"].append(ir.make_block(
        "image", 2, asset="wide.png", sha256=ir.sha256_bytes(picture), bbox=None,
        # Far wider than any page's text measure, which is the case the builder's
        # fitting exists for.
        width_pt=2000.0, height_pt=1000.0, pixel_width=200, pixel_height=100,
        alt="A wide rectangle", target_alt="یک مستطیل پهن"))

    destination = tmp_path / "wide.docx"
    Builder(book, assets, _options()).build(destination)

    summary = qa.check_docx(destination, book).summary()
    assert summary["ok"], summary["findings"]


# --------------------------------------------------------------------------- #
# Malformed parts, hostile archives, and a serialisation that is merely different
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("part", [
    "word/document.xml",
    "word/footnotes.xml",
    "[Content_Types].xml",
    "word/_rels/document.xml.rels",
])
def test_a_truncated_required_part_is_a_finding_not_a_pass(built, tmp_path, part):
    """A regex over broken XML finds fewer matches and says nothing."""
    book, path = built
    broken = _repack(path, tmp_path / f"cut-{part.replace('/', '-')}.docx",
                     lambda items: {**items,
                                    part: items[part][:len(items[part]) // 3]})

    summary = qa.check_docx(broken, book).summary()

    assert summary["ok"] is False, summary
    codes = {finding["code"] for finding in summary["findings"]}
    assert codes & {"part-malformed", "part-root-wrong", "docx-invalid"}, codes


def test_a_part_whose_root_is_not_the_formats_root_is_caught(built, tmp_path):
    book, path = built
    broken = _repack(path, tmp_path / "rooted.docx",
                     lambda items: {**items,
                                    "[Content_Types].xml": b"<Nonsense/>"})

    codes = _codes(broken, book)
    assert codes & {"part-root-wrong", "content-types-invalid"}, codes


def test_relationships_written_with_another_prefix_still_pass(built, tmp_path):
    """Equivalent XML, different serialisation. This used to be rejected.

    The relationship elements are re-serialised with an explicit prefix for the
    same namespace — which is legal, and which a real toolchain produces. A
    lexical check looks for one spelling of the tag and finds nothing.
    """
    book, path = built

    def reprefix(items: dict) -> dict:
        rels = items["word/_rels/document.xml.rels"].decode("utf-8")
        rewritten = (rels
                     .replace('xmlns="http://schemas.openxmlformats.org/package/'
                              '2006/relationships"',
                              'xmlns:pr="http://schemas.openxmlformats.org/'
                              'package/2006/relationships"')
                     .replace("<Relationships", "<pr:Relationships")
                     .replace("</Relationships>", "</pr:Relationships>")
                     .replace("<Relationship ", "<pr:Relationship "))
        items["word/_rels/document.xml.rels"] = rewritten.encode("utf-8")
        return items

    other = _repack(path, tmp_path / "prefixed.docx", reprefix)

    summary = qa.check_docx(other, book).summary()
    assert summary["ok"], summary["findings"]


def test_an_archive_member_that_would_escape_the_package_is_refused(tmp_path):
    hostile = tmp_path / "escape.docx"
    with zipfile.ZipFile(hostile, "w") as archive:
        archive.writestr("../outside.txt", "no")
        archive.writestr("word/document.xml", "<w:document/>")

    summary = qa.check_docx(hostile, None).summary()

    assert summary["ok"] is False
    assert "member-escapes" in {finding["code"] for finding in summary["findings"]}


def test_an_implausibly_compressible_member_is_refused(tmp_path):
    """A verifier is exactly the code that is handed a hostile file."""
    bomb = tmp_path / "bomb.docx"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "0" * (4 * 1024 * 1024))

    summary = qa.check_docx(bomb, None).summary()

    assert summary["ok"] is False
    assert "member-ratio" in {finding["code"] for finding in summary["findings"]}

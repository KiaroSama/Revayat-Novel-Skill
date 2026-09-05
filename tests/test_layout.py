"""The Persian publication profile, checked in the saved package.

The point of this profile is that book layout lives in Word **styles** rather
than in direct formatting on each paragraph. That is what lets a designer
restyle the whole book by editing `Normal` once, and it is easy to regress
silently — direct formatting looks identical until someone tries to change it.
So these tests read `word/styles.xml`, not the builder's own report.
"""

from __future__ import annotations

import argparse
import zipfile

import pytest

import bookir as ir
import layout
from build_docx import Builder, add_arguments

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _options(**overrides) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    args = parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma"])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _build(translated_book, tmp_path, **overrides):
    book, assets = translated_book
    destination = tmp_path / "layout.docx"
    report = Builder(book, assets, _options(**overrides)).build(destination)
    return destination, report


def _xml(path, member):
    from lxml import etree
    with zipfile.ZipFile(path) as archive:
        return etree.fromstring(archive.read(member))


def _style_pPr(styles_root, style_id):
    for style in styles_root.findall(f"{{{W}}}style"):
        if style.get(f"{{{W}}}styleId") == style_id:
            return style.find(f"{{{W}}}pPr")
    return None


def test_twips_conversion_is_exact():
    assert layout.twips(1) == 20
    assert layout.twips(18) == 360      # an 18pt indent
    assert layout.twips(0) == 0


def test_body_layout_lands_in_the_style_not_on_paragraphs(translated_book, tmp_path):
    path, report = _build(translated_book, tmp_path)
    assert "Normal" in report["layout"]["styles"]

    properties = _style_pPr(_xml(path, "word/styles.xml"), "Normal")
    assert properties is not None, "Normal has no paragraph properties"

    spacing = properties.find(f"{{{W}}}spacing")
    assert spacing is not None
    assert spacing.get(f"{{{W}}}line") == str(layout.twips(1.5 * 12))

    indent = properties.find(f"{{{W}}}ind")
    assert indent is not None
    assert indent.get(f"{{{W}}}firstLine") == str(layout.twips(18.0))

    assert properties.find(f"{{{W}}}widowControl") is not None

    # And the same values must NOT be stamped onto every body paragraph.
    document = _xml(path, "word/document.xml")
    body_paragraphs = [
        p for p in document.find(f"{{{W}}}body").findall(f"{{{W}}}p")
        if (p.find(f"{{{W}}}pPr") is None
            or p.find(f"{{{W}}}pPr").find(f"{{{W}}}pStyle") is None)
    ]
    for paragraph in body_paragraphs:
        properties = paragraph.find(f"{{{W}}}pPr")
        if properties is None:
            continue
        assert properties.find(f"{{{W}}}ind") is None, (
            "the indent was written onto a paragraph; it belongs in the style"
        )


def test_headings_never_end_a_page_alone(translated_book, tmp_path):
    path, _ = _build(translated_book, tmp_path)
    styles = _xml(path, "word/styles.xml")
    for level in (1, 2, 3):
        properties = _style_pPr(styles, f"Heading{level}")
        if properties is None:
            continue
        assert properties.find(f"{{{W}}}keepNext") is not None, level
        assert properties.find(f"{{{W}}}keepLines") is not None, level
        indent = properties.find(f"{{{W}}}ind")
        assert indent is None or indent.get(f"{{{W}}}firstLine") == "0", (
            f"Heading {level} must not carry a first-line indent"
        )


def test_first_line_indent_can_be_switched_off(translated_book, tmp_path):
    path, _ = _build(translated_book, tmp_path, first_line_indent=0.0)
    properties = _style_pPr(_xml(path, "word/styles.xml"), "Normal")
    assert properties.find(f"{{{W}}}ind").get(f"{{{W}}}firstLine") == "0"


def test_mirrored_margins_and_gutter_reach_the_section(translated_book, tmp_path):
    path, report = _build(translated_book, tmp_path, mirror_margins=True, gutter=36.0)
    assert report["layout"]["mirror_margins"] is True

    document = _xml(path, "word/document.xml")
    section = document.find(f"{{{W}}}body").find(f"{{{W}}}sectPr")
    assert section.find(f"{{{W}}}mirrorMargins") is not None
    assert section.find(f"{{{W}}}pgMar").get(f"{{{W}}}gutter") == str(layout.twips(36.0))


def test_mirrored_margins_are_off_by_default(translated_book, tmp_path):
    path, _ = _build(translated_book, tmp_path)
    document = _xml(path, "word/document.xml")
    section = document.find(f"{{{W}}}body").find(f"{{{W}}}sectPr")
    assert section.find(f"{{{W}}}mirrorMargins") is None


def test_page_number_is_a_live_field_not_literal_text(translated_book, tmp_path):
    """Persian reflows to a different page count, so a typed number would lie."""
    path, report = _build(translated_book, tmp_path)
    assert report["layout"]["page_numbers"] is True

    with zipfile.ZipFile(path) as archive:
        footers = [n for n in archive.namelist() if n.startswith("word/footer")]
        assert footers, "no footer part was written"
        footer = archive.read(footers[0]).decode("utf-8")
    assert "PAGE" in footer
    assert 'w:fldCharType="begin"' in footer
    assert 'w:fldCharType="end"' in footer


def test_page_numbers_can_be_switched_off(translated_book, tmp_path):
    _, report = _build(translated_book, tmp_path, page_numbers=False)
    assert report["layout"]["page_numbers"] is False


def test_the_profile_does_not_break_anything_else(translated_book, tmp_path):
    """Footnotes, bookmarks, images and RTL must all still be intact."""
    import qa

    book, _assets = translated_book
    path, report = _build(translated_book, tmp_path)
    assert report["warning_count"] == 0, report["warnings"]
    assert report["footnotes"] >= 1
    assert report["bookmarks"] == report["headings"]
    assert qa.check_docx(path, book).summary()["ok"]

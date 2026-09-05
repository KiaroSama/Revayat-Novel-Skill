"""Completeness gates: the failures a green QA run used to sail straight past.

Every check here answers a question the existing gates never asked. They all
measured whether something was *missing*; none asked whether something was
present twice, in the wrong order, or still in English.
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

import pytest

import bookir as ir
import qa
from tests_support import png_bytes


def _book(*paragraphs: tuple[str, str | None]) -> dict:
    """A book of paragraphs given as ``(source, target)`` pairs."""
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", index, text=source)
        for index, (source, _) in enumerate(paragraphs, start=1)
    ]
    for block, (_, target) in zip(book["blocks"], paragraphs):
        block["target"] = target
    return book


def _codes(report: qa.Report) -> list[str]:
    return [finding["code"] for finding in report.findings]


# --------------------------------------------------------------------------- #
# Coverage counts
# --------------------------------------------------------------------------- #

def test_coverage_counts_headings_apart_from_the_rest():
    """A book that lost only its headings still reads as almost complete.

    The per-block findings are truncated, so on their own they cannot tell one
    missed heading from four untranslated chunks.
    """
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("heading", 1, level=1, text="Chapter One"),
        ir.make_block("heading", 2, level=1, text="Chapter Two"),
        ir.make_block("paragraph", 3, text="First paragraph."),
        ir.make_block("paragraph", 4, text="Second paragraph."),
        ir.make_block("paragraph", 5, text="Third paragraph."),
    ]
    book["blocks"][0]["target"] = "فصل یکم"
    book["blocks"][2]["target"] = "بند نخست."
    book["blocks"][3]["target"] = "بند دوم."

    summary = qa.check_book(book).summary()
    assert summary["counts"]["headings"] == 2
    assert summary["counts"]["headings_translated"] == 1
    assert summary["counts"]["paragraphs"] == 3
    assert summary["counts"]["paragraphs_translated"] == 2
    assert summary["by_code"]["untranslated-block"] == 2


# --------------------------------------------------------------------------- #
# The same translation twice
# --------------------------------------------------------------------------- #

_LONG_FA = "متن فارسی برای آزمون کامل بودن ترجمه است و باید بلند باشد. " * 3


def test_two_sources_with_one_translation_is_a_pasted_worksheet():
    report = qa.Report()
    qa._check_duplicate_targets(
        _book(("The morning came slowly over the ridge.", _LONG_FA),
              ("Nobody mentioned the letter at breakfast.", _LONG_FA)),
        report,
    )
    assert _codes(report) == ["duplicate-translation"]
    assert "b00001" in report.findings[0]["detail"]


def test_a_repeated_source_may_repeat_its_translation():
    """A refrain or a running head really should translate the same way twice."""
    report = qa.Report()
    qa._check_duplicate_targets(
        _book(("The same line, twice.", _LONG_FA),
              ("The same line, twice.", _LONG_FA)),
        report,
    )
    assert _codes(report) == []


def test_a_short_repeated_answer_is_not_evidence_of_anything():
    report = qa.Report()
    qa._check_duplicate_targets(
        _book(("Yes.", "بله."), ("Indeed.", "بله.")), report
    )
    assert _codes(report) == []
    assert len("بله.") <= qa.DUPLICATE_MIN_CHARS


# --------------------------------------------------------------------------- #
# Source text left inside the translation
# --------------------------------------------------------------------------- #

def test_an_english_clause_left_inside_fluent_persian_is_caught():
    """The shape the script-ratio gate cannot see: mostly Persian, one clause not."""
    leaked = "and she stood beside the window all that night"
    source = f"The morning came slowly over the ridge {leaked}."
    assert len(leaked) >= qa.COPIED_RUN_CHARS

    report = qa.Report()
    qa._check_copied_runs(
        _book((source, f"صبح به آرامی از بالای تپه آمد {leaked} و او آنجا ماند.")),
        report,
    )
    assert _codes(report) == ["copied-source-run"]
    assert "re-run this chunk" in report.findings[0]["detail"]


def test_a_verbatim_span_is_meant_to_survive():
    """`` `literal` `` is carried through byte for byte on purpose.

    Counting it as a leak would report the feature working as a defect, so both
    sides drop verbatim spans before the comparison. The second assertion is
    what makes this a test rather than a coincidence: without that exclusion the
    very same block is flagged.
    """
    command = "python revayat.py qa check --book work/book.json"
    assert len(command) >= qa.COPIED_RUN_CHARS
    source = f"Run the command `{command}` before you build the document, please."
    target = f"پیش از ساخت سند، فرمان `{command}` را اجرا کنید، لطفاً."

    report = qa.Report()
    qa._check_copied_runs(_book((source, target)), report)
    assert _codes(report) == []

    # Verbatim included, the same pair is a leak — the exclusion is load-bearing.
    assert qa._copied_run(ir.plain_text(source), ir.plain_text(target))


def test_a_row_of_dots_is_not_a_copied_clause():
    leader = "." * 60
    assert not qa._copied_run(leader, leader)


# --------------------------------------------------------------------------- #
# The first-mention parenthetical
# --------------------------------------------------------------------------- #

def _glossary(first_form: str = "الیزابت بنت (Elizabeth Bennet)") -> dict:
    return {
        "entries": [{
            "id": "g0001", "source": "Elizabeth Bennet",
            "target": "الیزابت بنت", "later_form": "الیزابت بنت",
            "first_form": first_form, "locked": False,
            "first_block_id": "b00001", "aliases": [],
        }],
    }


def test_the_original_spelling_may_be_introduced_only_once():
    """Parallel chunks cannot see each other, so each answers "first mention: yes"."""
    report = qa.Report()
    qa._check_first_mentions(
        _book(("Elizabeth Bennet arrived.", "الیزابت بنت (Elizabeth Bennet) رسید."),
              ("Elizabeth Bennet sat.", "الیزابت بنت (Elizabeth Bennet) نشست.")),
        _glossary(), report,
    )
    assert _codes(report) == ["first-mention-repeated"]
    detail = report.findings[0]["detail"]
    assert "b00001" in detail and "b00002" in detail


def test_one_introduction_is_the_point_of_the_rule():
    report = qa.Report()
    qa._check_first_mentions(
        _book(("Elizabeth Bennet arrived.", "الیزابت بنت (Elizabeth Bennet) رسید."),
              ("Elizabeth Bennet sat.", "الیزابت بنت نشست.")),
        _glossary(), report,
    )
    assert _codes(report) == []


def test_an_entry_with_no_parenthetical_is_not_policed():
    report = qa.Report()
    qa._check_first_mentions(
        _book(("Elizabeth Bennet arrived.", "الیزابت بنت رسید."),
              ("Elizabeth Bennet sat.", "الیزابت بنت نشست.")),
        _glossary(first_form="الیزابت بنت"), report,
    )
    assert _codes(report) == []


def test_the_glossary_gate_is_wired_into_check_book():
    book = _book(("Elizabeth Bennet arrived.", "الیزابت بنت (Elizabeth Bennet) رسید."),
                 ("Elizabeth Bennet sat.", "الیزابت بنت (Elizabeth Bennet) نشست."))
    summary = qa.check_book(book, glossary=_glossary()).summary()
    assert summary["ok"] is False
    assert "first-mention-repeated" in summary["by_code"]


# --------------------------------------------------------------------------- #
# Package gates
# --------------------------------------------------------------------------- #

def test_duplicate_bookmark_names_send_every_link_to_the_first():
    report = qa.Report()
    qa._check_bookmarks(["rv_0001", "rv_0002", "rv_0001"], None, report)
    assert _codes(report) == ["bookmark-duplicate"]
    assert report.counts["bookmarks"] == 3
    assert report.findings[0]["unit"] == "rv_0001"


def test_headings_with_no_bookmarks_leave_the_contents_with_nothing_to_link_to():
    book = ir.new_book()
    book["blocks"] = [ir.make_block("heading", 1, level=1, text="Chapter One")]
    report = qa.Report()
    qa._check_bookmarks([], book, report)
    assert _codes(report) == ["bookmarks-missing"]


@pytest.fixture(scope="module")
def two_picture_docx(tmp_path_factory) -> tuple[Path, dict]:
    """A real build with two *different* pictures, so a swap is detectable."""
    from build_docx import Builder, add_arguments

    work = tmp_path_factory.mktemp("pictures")
    assets = work / "assets"
    assets.mkdir()
    first = png_bytes(120, 80, (200, 60, 60))
    second = png_bytes(60, 40, (40, 60, 200))
    (assets / "one.png").write_bytes(first)
    (assets / "two.png").write_bytes(second)

    book = ir.new_book(title="Two Plates", author="Test")
    book["blocks"] = [
        ir.make_block("heading", 1, level=1, text="Chapter One"),
        ir.make_block("image", 2, asset="one.png", sha256=ir.sha256_bytes(first),
                      width_pt=180.0, height_pt=120.0,
                      pixel_width=120, pixel_height=80),
        ir.make_block("heading", 3, level=1, text="Chapter Two"),
        ir.make_block("image", 4, asset="two.png", sha256=ir.sha256_bytes(second),
                      width_pt=90.0, height_pt=60.0,
                      pixel_width=60, pixel_height=40),
    ]
    for block in ir.iter_text_blocks(book):
        block["target"] = "فصل"

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma"])
    path = work / "two.docx"
    Builder(book, assets, options).build(path)
    return path, book


def _repack(source: Path, target: Path, transform) -> Path:
    """Copy a .docx, rewriting ``word/document.xml`` through ``transform``.

    Breaking one thing inside a package the builder actually produced is the
    only honest way to test a package gate: the builder cannot be persuaded to
    emit pictures out of order, and a hand-written zip would prove nothing about
    a real Word file.
    """
    with zipfile.ZipFile(source) as original, \
            zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as copy:
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename == "word/document.xml":
                data = transform(data.decode("utf-8")).encode("utf-8")
            copy.writestr(item, data)
    return target


def test_a_correct_build_places_its_pictures_in_the_books_order(two_picture_docx):
    path, book = two_picture_docx
    summary = qa.check_docx(path, book).summary()
    assert summary["ok"], summary
    assert summary["counts"]["pictures_placed"] == 2
    assert summary["counts"]["bookmarks"] == 2


def test_swapped_pictures_are_caught_where_counting_them_never_could(
        two_picture_docx, tmp_path):
    path, book = two_picture_docx

    def swap(document: str) -> str:
        first, second = qa._BLIP.findall(document)[:2]
        return (document
                .replace(f'r:embed="{first}"', 'r:embed="__swap__"', 1)
                .replace(f'r:embed="{second}"', f'r:embed="{first}"', 1)
                .replace('r:embed="__swap__"', f'r:embed="{second}"', 1))

    broken = _repack(path, tmp_path / "swapped.docx", swap)
    summary = qa.check_docx(broken, book).summary()
    assert summary["ok"] is False
    assert "image-order" in summary["by_code"]
    # The old gate — counting media parts — still sees nothing wrong.
    with zipfile.ZipFile(broken) as archive:
        media = [n for n in archive.namelist() if n.startswith("word/media/")]
    assert len(media) == 2
    assert "images-lost" not in summary["by_code"]

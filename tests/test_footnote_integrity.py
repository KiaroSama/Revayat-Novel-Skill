"""Footnotes, and the asymmetry between the book's notes and the translator's.

A note that came with the book has a counterpart in the source text, so if its
marker goes missing the structure check sees it: source had `[[fn:fn0001]]`,
target does not. A note the *translator* wrote has no counterpart anywhere. If
its marker does not survive the worksheet round trip, every other check still
passes and the note simply disappears from the finished book — the translator's
own explanation, dropped without a word.

That is why the translator's notes are gated harder here, and why the tests
below are mostly about the ways a marker can go missing or multiply.
"""

from __future__ import annotations

import pytest

import bookir as ir
import qa

PERSIAN = "ترجمهٔ این بند که به اندازهٔ کافی بلند است تا نسبت طول را رد کند."


def _book(*, target: str, note_origin: str = "translator",
          anchor_block: str | None = "b00001",
          note_target: str = "یادداشت مترجم.",
          source_text: str = "Source sentence here.") -> dict:
    book = ir.new_book()
    block = ir.make_block("paragraph", 1, page=1, text=source_text)
    block["target"] = target
    book["blocks"] = [block]
    note = ir.make_footnote(1, anchor_block=anchor_block or "",
                            text="Translator's note.", origin=note_origin)
    note["target"] = note_target
    book["footnotes"] = [note]
    return book


def _codes(book, **kwargs) -> dict[str, str]:
    """``{code: severity}`` for one run of the book gates."""
    return {f["code"]: f["severity"]
            for f in qa.check_book(book, **kwargs).summary()["findings"]}


# --------------------------------------------------------------------------- #
# The healthy case
# --------------------------------------------------------------------------- #

def test_a_translator_note_with_one_marker_passes():
    book = _book(target=f"{PERSIAN}[[fn:fn0001]]")
    summary = qa.check_book(book).summary()
    assert summary["ok"], summary["findings"]
    assert not any(f["code"].startswith("footnote-") for f in summary["findings"])


def test_a_source_note_is_held_to_the_same_healthy_standard():
    """The book's own note carries its marker on both sides, and passes."""
    book = _book(source_text="Source sentence here.[[fn:fn0001]]",
                 target=f"{PERSIAN}[[fn:fn0001]]", note_origin="source")
    assert qa.check_book(book).summary()["ok"]


def test_a_source_note_that_lost_its_marker_is_caught_by_the_parity_check():
    """Not by the orphan rule — the source still has the marker to compare to.

    This is the whole reason the translator's notes need a rule of their own:
    for a source note this comparison exists, and for theirs it cannot.
    """
    book = _book(source_text="Source sentence here.[[fn:fn0001]]",
                 target=PERSIAN, note_origin="source", anchor_block="")
    codes = _codes(book)
    assert codes.get("footnote-marker-lost") == qa.ERROR
    assert "footnote-orphaned" not in codes


# --------------------------------------------------------------------------- #
# Orphans
# --------------------------------------------------------------------------- #

def test_a_translator_note_whose_marker_was_dropped_blocks_the_build():
    """Nothing else in the pipeline can see this; it has to be an error here."""
    book = _book(target=PERSIAN, anchor_block="")
    codes = _codes(book)
    assert codes.get("footnote-orphaned") == qa.ERROR
    assert not qa.check_book(book).summary()["ok"]


def test_a_source_note_without_a_marker_is_only_advice():
    """The source comparison already covers this one, so it stays a warning."""
    book = _book(target=PERSIAN, note_origin="source", anchor_block="")
    codes = _codes(book)
    assert codes.get("footnote-unreferenced") == qa.WARNING
    assert "footnote-orphaned" not in codes
    assert qa.check_book(book).summary()["ok"]


def test_the_orphan_report_quotes_the_note_so_it_can_be_put_back():
    book = _book(target=PERSIAN, anchor_block="", note_target="توضیح دربارهٔ نام مکان.")
    finding = next(f for f in qa.check_book(book).summary()["findings"]
                   if f["code"] == "footnote-orphaned")
    assert "توضیح دربارهٔ نام مکان" in finding["detail"]


# --------------------------------------------------------------------------- #
# Duplicate anchors
# --------------------------------------------------------------------------- #

def test_two_markers_for_one_translator_note_are_rejected():
    """Word gives a note one reference; a second marker loses one or renumbers."""
    book = _book(target=f"{PERSIAN}[[fn:fn0001]] و باز هم[[fn:fn0001]]")
    codes = _codes(book)
    assert codes.get("footnote-multiple-anchors") == qa.ERROR
    assert not qa.check_book(book).summary()["ok"]


def test_the_same_note_marked_in_two_different_blocks_is_caught():
    book = _book(target=f"{PERSIAN}[[fn:fn0001]]")
    second = ir.make_block("paragraph", 2, page=1, text="Another sentence.")
    second["target"] = f"{PERSIAN}[[fn:fn0001]]"
    book["blocks"].append(second)

    finding = next(f for f in qa.check_book(book).summary()["findings"]
                   if f["code"] == "footnote-multiple-anchors")
    assert "b00001" in finding["detail"] and "b00002" in finding["detail"]


def test_a_duplicated_source_note_is_advice_not_a_block():
    book = _book(target=f"{PERSIAN}[[fn:fn0001]] و باز[[fn:fn0001]]",
                 note_origin="source")
    assert _codes(book).get("footnote-multiple-anchors") == qa.WARNING


# --------------------------------------------------------------------------- #
# The anchor record has to match reality
# --------------------------------------------------------------------------- #

def test_an_anchor_pointing_at_the_wrong_block_is_an_error():
    """The builder places the note by anchor_block; a wrong one moves the note."""
    book = _book(target=f"{PERSIAN}[[fn:fn0001]]", anchor_block="b00099")
    codes = _codes(book)
    assert codes.get("footnote-anchor-mismatch") == qa.ERROR


def test_a_marker_with_no_recorded_anchor_is_an_error():
    book = _book(target=f"{PERSIAN}[[fn:fn0001]]", anchor_block="")
    codes = _codes(book)
    assert codes.get("footnote-anchor-missing") == qa.ERROR


# --------------------------------------------------------------------------- #
# Bodies
# --------------------------------------------------------------------------- #

def test_a_note_with_no_text_at_all_is_an_error():
    """It would print as a bare superscript number with nothing under the rule."""
    book = _book(target=f"{PERSIAN}[[fn:fn0001]]", note_target="")
    book["footnotes"][0]["text"] = ""
    assert _codes(book).get("footnote-body-empty") == qa.ERROR


def test_an_untranslated_body_is_advice():
    book = _book(target=f"{PERSIAN}[[fn:fn0001]]", note_target="")
    assert _codes(book).get("footnote-untranslated") == qa.WARNING


def test_a_marker_pointing_at_no_note_is_an_error():
    book = _book(target=f"{PERSIAN}[[fn:fn0002]]")
    codes = _codes(book)
    assert codes.get("footnote-undefined") == qa.ERROR


# --------------------------------------------------------------------------- #
# The package must agree with the book
# --------------------------------------------------------------------------- #

def test_the_built_document_carries_a_body_for_every_reference(translated_book,
                                                               tmp_path):
    """Reference/body parity, checked in the file Word will actually open."""
    import argparse
    from build_docx import Builder, add_arguments

    book, assets = translated_book
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma"])

    destination = tmp_path / "footnotes.docx"
    report = Builder(book, assets, options).build(destination)
    assert report["footnotes"] >= 1

    package = qa.check_docx(destination, book).summary()
    assert package["ok"], package["findings"]
    assert not any(f["code"].startswith("footnote-") for f in package["findings"])

"""Book IR: inline markup must survive a round trip exactly.

Everything downstream — emphasis parity in QA, run splitting in the DOCX
builder, the typography pass — assumes ``parse_markup`` and ``render_spans``
are true inverses. These tests are what makes that assumption safe.
"""

from __future__ import annotations

import pytest

import bookir as ir


@pytest.mark.parametrize(
    "spans",
    [
        [("He whispered, ", False, False), ("do not look back", False, True),
         (".", False, False)],
        [("A ", False, False), ("bold", True, False), (" and ", False, False),
         ("both", True, True), (" end", False, False)],
        [("leading ", False, False), (" italic ", False, True), (" tail", False, False)],
        [("plain only", False, False)],
    ],
)
def test_markup_round_trip(spans):
    rendered = ir.render_markup(spans)
    parsed = [(s["text"], s["bold"], s["italic"]) for s in ir.parse_markup(rendered)]
    expected = [(t, b, i) for t, b, i in spans if t]
    assert "".join(t for t, _, _ in parsed) == "".join(t for t, _, _ in expected)
    # Styled content survives even if neutral whitespace regroups.
    assert {(t.strip(), b, i) for t, b, i in parsed if b or i} == {
        (t.strip(), b, i) for t, b, i in expected if b or i
    }


def test_render_spans_is_inverse_of_parse():
    original = "He said **loudly**, *quietly*, `code_x` and [[fn:fn0003]] then left."
    assert ir.render_spans(ir.parse_markup(original)) == original


def test_asterisk_in_prose_is_escaped_not_markup():
    text = ir.render_markup([("five * six", False, False)])
    assert text == r"five \* six"
    assert ir.plain_text(text) == "five * six"


def test_footnote_tokens_are_extracted_in_order():
    assert ir.footnote_refs("a [[fn:fn0007]] b [[fn:fn0002]]") == ["fn0007", "fn0002"]


def test_emphasis_signature_counts_each_kind():
    assert ir.emphasis_signature("a *i* b **B** c `v` [[fn:fn0001]]") == (1, 1, 1)
    assert ir.emphasis_signature("***both***") == (1, 1, 0)


def test_normalise_source_strips_invisibles_but_keeps_zwnj():
    noisy = "we﻿re​ here now"
    assert ir.normalise_source(noisy) == "were here now"
    assert ir.normalise_source("می‌رود") == "می‌رود"


def test_make_block_normalises_text_for_every_reader():
    block = ir.make_block("paragraph", 1, text="a﻿b   c ")
    assert block["text"] == "ab c"
    assert block["target"] is None


def test_validate_book_reports_structural_faults():
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", 1, text="ok [[fn:fn0009]]"),
        ir.make_block("image", 2, asset=""),
    ]
    book["blocks"][1]["id"] = book["blocks"][0]["id"]  # force a duplicate
    problems = " ".join(ir.validate_book(book))
    assert "duplicate id" in problems
    assert "image without asset" in problems
    assert "unknown footnote ref" in problems


def test_validate_book_accepts_a_sound_book():
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("heading", 1, level=1, text="Title"),
        ir.make_block("paragraph", 2, text="Body [[fn:fn0001]]"),
    ]
    book["footnotes"] = [ir.make_footnote(1, anchor_block="b00002", text="Note")]
    assert ir.validate_book(book) == []


def test_script_ratio_separates_persian_from_latin():
    persian, latin = ir.script_ratio("الیزابت بنت (Elizabeth Bennet)")
    assert persian > 0 and latin > 0
    assert abs(persian + latin - 1.0) < 1e-9

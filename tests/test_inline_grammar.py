"""One inline grammar, and checks that look inside what they protect.

Two defects, both of which let a real change through a green gate:

* `parse_markup` was single-level. `*واژه [[fn:fn0001]]*` produced one italic span
  whose *text* contained the marker, so `Builder.write_markup` — which only makes
  a note from a footnote span — had nothing to make, and the footnote was simply
  absent from the DOCX. `footnote_refs` still found the marker by regex, so
  footnote parity passed while the note was gone.
* `emphasis_signature` counts verbatim runs. Counting cannot see inside them, so
  ``ABC-123`` becoming ``XYZ-999`` passed: same count, different protected text.

The first is fixed by the emphasis branch re-parsing its body with the same
grammar — and `render_spans` grouping consecutive spans of one style, so the
round-trip survives. The second by comparing the contents beside the counts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import qa  # noqa: E402


def _notes(spans: list[dict]) -> list[str]:
    return [span["footnote"] for span in spans if span.get("footnote")]


# --------------------------------------------------------------------------- #
# A footnote inside emphasis is a footnote
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text, style", [
    ("*واژه [[fn:fn0001]]*", "italic"),
    ("**واژه [[fn:fn0001]]**", "bold"),
    ("***واژه [[fn:fn0001]]***", "bold-italic"),
])
def test_a_marker_inside_emphasis_becomes_a_footnote_span(text, style):
    """The defect: the marker stayed literal text, so nothing downstream could
    build the note. A readable marker in the prose is not a footnote."""
    spans = ir.parse_markup(text)

    assert _notes(spans) == ["fn0001"], f"{style}: no footnote span — {spans}"
    assert not any("[[fn:" in (span.get("text") or "") for span in spans), (
        f"{style}: the marker is still sitting in a text span — {spans}")


@pytest.mark.parametrize("text", [
    "*واژه [[fn:fn0001]]*",
    "**واژه [[fn:fn0001]]**",
    "***واژه [[fn:fn0001]]***",
    "متن *با `کد` درون* آن",
    "*تاکید* و **پررنگ** و ***هر دو***",
    "`ABC-123` بیرون",
    "*a*b*c*",
    "بدون هیچ نشانه‌ای",
])
def test_the_round_trip_is_exact(text):
    """`render_spans(parse_markup(t)) == t`. Making the parser nest would have
    broken this on its own — one wrapper per span emits `*واژه *[[fn:fn0001]]` —
    so the renderer groups consecutive spans of one style and wraps once."""
    assert ir.render_spans(ir.parse_markup(text)) == text


def test_the_style_carries_onto_the_nested_span():
    """The note is inside the italic phrase, so its span is italic too — which is
    what lets a builder reproduce the emphasis around it."""
    spans = ir.parse_markup("*واژه [[fn:fn0001]]*")

    note = next(span for span in spans if span.get("footnote"))
    assert note["italic"] is True, note


def test_a_marker_inside_backticks_stays_an_example():
    """Verbatim does not recurse, deliberately: `[[fn:fn0001]]` shown in a
    technical passage is an example of a marker, not a call-out. Recursing there
    would turn documentation into a footnote."""
    spans = ir.parse_markup("`[[fn:fn0001]]` is how you write one")

    assert _notes(spans) == [], spans
    assert spans[0]["verbatim"] is True
    assert spans[0]["text"] == "[[fn:fn0001]]"


# --------------------------------------------------------------------------- #
# Protected content is compared, not counted
# --------------------------------------------------------------------------- #

def _one_block_book(source: str, target: str) -> dict:
    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    block = ir.make_block("paragraph", 1, text=source)
    block["target"] = target
    book["blocks"] = [block]
    return book


def test_changed_literal_text_is_an_error_even_though_the_count_matches():
    """The defect: one verbatim run before, one after, so the signature agreed —
    and the identifier the span exists to protect had been rewritten."""
    book = _one_block_book("run `ABC-123` now", "اجرا کن `XYZ-999` حالا")

    findings = qa.check_book(book, strict=True).summary()["findings"]
    codes = [finding["code"] for finding in findings]

    assert "verbatim-content-changed" in codes, findings


def test_identical_literal_text_is_not_reported():
    """The negative control: a translation that correctly leaves the literal
    alone must not be flagged, or the check is noise."""
    book = _one_block_book("run `ABC-123` now", "`ABC-123` را اجرا کن")

    findings = qa.check_book(book, strict=True).summary()["findings"]
    codes = [finding["code"] for finding in findings]

    assert "verbatim-content-changed" not in codes, findings


def test_a_dropped_literal_is_reported():
    """A verbatim run removed entirely. The count signature does catch this one —
    both checks firing on the same block is fine, and the content message says
    which text went missing."""
    book = _one_block_book("run `ABC-123` now", "اجرا کن حالا")

    findings = qa.check_book(book, strict=True).summary()["findings"]
    codes = [finding["code"] for finding in findings]

    assert "verbatim-content-changed" in codes, findings


def test_a_note_inside_emphasis_survives_into_the_built_document(tmp_path):
    """End to end, because the parser was only half of it: the note has to reach
    the DOCX. This is the assertion the original defect would fail while every
    count-based gate reported success."""
    pytest.importorskip("docx")
    import argparse

    from build_docx import Builder, add_arguments

    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    block = ir.make_block("paragraph", 1, text="a *word [[fn:fn0001]]* here")
    block["target"] = "یک *واژه [[fn:fn0001]]* اینجا"
    book["blocks"] = [block]
    book["footnotes"] = [ir.make_footnote(1, anchor_block=block["id"],
                                          text="The note.")]
    book["footnotes"][0]["target"] = "یادداشت."

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(["--book", "x", "--out", "y",
                                 "--font", "Tahoma", "--no-toc"])
    docx = tmp_path / "book.fa.docx"
    Builder(book, tmp_path, options).build(docx)

    import zipfile
    with zipfile.ZipFile(docx) as archive:
        names = archive.namelist()
        assert "word/footnotes.xml" in names, names
        footnotes = archive.read("word/footnotes.xml").decode("utf-8")
    assert "یادداشت." in footnotes, (
        "the note inside emphasis never reached the document's footnote part")

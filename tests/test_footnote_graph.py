"""A footnote graph is validated whole, before the book is written.

Four things could reach the book unchallenged, and each one prints:

* a **reused** note kept the anchor it had before the marker moved, because
  `_anchor_notes` is handed only the newly allocated notes and skips any note that
  already has an anchor. Edit a reply so `tr-01`'s marker moves from the first
  paragraph to the second, and the note still claims the first.
* a marker with **no body** — `[[fn:tr-01]]` in the prose and no `@@ tr-01`
  section — left an unresolved token in the translation.
* a body with **no marker** became a footnote nothing refers to, still printing.
* `@@ tr-01 heading1` adopted a note answered as a heading, because the kind was
  never compared for ids outside the manifest.

All four are the same omission: the notes were committed one reply at a time
without anyone asking whether the resulting graph made sense.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import bookwrite  # noqa: E402
import chunk as chunking  # noqa: E402
import merge as merging  # noqa: E402
import notegraph  # noqa: E402
from tests_support import reply_text  # noqa: E402


def _book(tmp_path: Path) -> Path:
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    book["blocks"].append(ir.make_block("paragraph", 1, text="The first paragraph."))
    book["blocks"].append(ir.make_block("paragraph", 2, text="The second paragraph."))
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _built(tmp_path: Path) -> tuple[Path, Path, dict]:
    book_path = _book(tmp_path)
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None)
    assert len(manifest["chunks"]) == 1, manifest
    return book_path, chunks, manifest["chunks"][0]


def _reply(chunks: Path, entry: dict, first: str, second: str,
           note: str | None = "یادداشت مترجم.") -> None:
    body = f"@@ b00001 para\n{first}\n\n@@ b00002 para\n{second}\n"
    if note is not None:
        body += f"\n@@ tr-01 footnote\n{note}\n"
    ir.write_text(chunks / entry["output"],
                  reply_text(chunks / entry["file"], body))


def _notes(book_path: Path) -> list[dict]:
    return ir.load_book(book_path)["footnotes"]


# --------------------------------------------------------------------------- #
# A reused note follows its marker
# --------------------------------------------------------------------------- #

def test_a_reused_note_is_reanchored_when_its_marker_moves(tmp_path):
    """The defect. Replay the same worksheet with the marker on the other
    paragraph: the note is correctly *reused* rather than duplicated, and its
    anchor must move with it. Left behind, the note prints under a paragraph that
    does not refer to it."""
    book_path, chunks, entry = _built(tmp_path)

    _reply(chunks, entry, "اول [[fn:tr-01]]", "دوم")
    assert merging.merge(book_path, chunks, strict=True)["ok"]
    notes = _notes(book_path)
    assert len(notes) == 1, notes
    assert notes[0]["anchor_block"] == "b00001", notes

    # The translator moves the marker to the second paragraph and re-answers.
    _reply(chunks, entry, "اول", "دوم [[fn:tr-01]]")
    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"], report
    notes = _notes(book_path)
    assert len(notes) == 1, f"the note was duplicated instead of reused: {notes}"
    assert notes[0]["anchor_block"] == "b00002", (
        "the reused note still claims the paragraph its marker left")


# --------------------------------------------------------------------------- #
# The graph has to resolve before anything is written
# --------------------------------------------------------------------------- #

def test_a_marker_with_no_body_is_refused(tmp_path):
    """`[[fn:tr-01]]` in the prose with no `@@ tr-01` section is an unresolved
    token. Merging it leaves the literal marker in the finished book."""
    book_path, chunks, entry = _built(tmp_path)

    _reply(chunks, entry, "اول [[fn:tr-01]]", "دوم", note=None)
    report = merging.merge(book_path, chunks, strict=True)

    assert not report["ok"], f"an unresolved marker was merged: {report}"
    assert ir.load_book(book_path)["blocks"][0].get("target") in (None, ""), (
        "the book was mutated for a reply whose footnote graph does not resolve")


def test_a_body_with_no_marker_is_refused(tmp_path):
    """The mirror case: a note nobody refers to. It would print at the foot of a
    page with no number pointing at it."""
    book_path, chunks, entry = _built(tmp_path)

    _reply(chunks, entry, "اول", "دوم")          # body offered, marker absent
    report = merging.merge(book_path, chunks, strict=True)

    assert not report["ok"], f"an orphan note body was merged: {report}"


def test_the_same_marker_twice_is_refused(tmp_path):
    """One note, two call-outs. Which paragraph owns it cannot be decided, and
    guessing picks one and silently drops the other."""
    book_path, chunks, entry = _built(tmp_path)

    _reply(chunks, entry, "اول [[fn:tr-01]]", "دوم [[fn:tr-01]]")
    report = merging.merge(book_path, chunks, strict=True)

    assert not report["ok"], f"a marker used twice was merged: {report}"


@pytest.mark.parametrize("kind", ["footnote", "note"])
def test_both_spellings_of_a_note_kind_are_accepted(tmp_path, kind):
    """`footnote` is what SKILL.md and the translation policy ask for. `note` is
    the obvious near-miss of that word, and refusing it would fail a translator
    whose intent is unambiguous for no safety gain — the hazard this check exists
    for is a note answered as *structure*, which the next test covers."""
    book_path, chunks, entry = _built(tmp_path)
    ir.write_text(chunks / entry["output"], reply_text(
        chunks / entry["file"],
        f"@@ b00001 para\nاول [[fn:tr-01]]\n\n@@ b00002 para\nدوم\n"
        f"\n@@ tr-01 {kind}\nیادداشت.\n"))

    assert merging.merge(book_path, chunks, strict=True)["ok"], kind


@pytest.mark.parametrize("kind", ["heading1", "para", "alt", "quote"])
def test_a_note_answered_as_structure_is_refused(tmp_path, kind):
    """`@@ tr-01 heading1` is not a footnote. The kind was never compared for ids
    outside the manifest, so the note was adopted on the strength of its id."""
    book_path, chunks, entry = _built(tmp_path)

    ir.write_text(chunks / entry["output"],
                  "@@ b00001 para\nاول [[fn:tr-01]]\n\n@@ b00002 para\nدوم\n"
                  "\n@@ tr-01 heading1\nیادداشت.\n")
    report = merging.merge(book_path, chunks, strict=True)

    assert not report["ok"], f"a note answered as a heading was adopted: {report}"


# --------------------------------------------------------------------------- #
# What must keep working
# --------------------------------------------------------------------------- #

def test_the_ordinary_translator_note_still_works(tmp_path):
    """The negative control. A gate that refuses every note would be worse than
    the four holes it closes."""
    book_path, chunks, entry = _built(tmp_path)

    _reply(chunks, entry, "اول [[fn:tr-01]]", "دوم")
    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"], report
    notes = _notes(book_path)
    assert len(notes) == 1 and notes[0]["origin"] == "translator", notes
    assert notes[0]["anchor_block"] == "b00001"
    # The local id is rewritten to the book-wide one in the prose.
    target = ir.load_book(book_path)["blocks"][0]["target"]
    assert "tr-01" not in target, target
    assert notes[0]["id"] in target, target


def test_a_refused_reply_leaves_the_earlier_note_intact(tmp_path):
    """Transactional: a second reply that does not validate must not retire,
    re-anchor or renumber the note the first one legitimately created."""
    book_path, chunks, entry = _built(tmp_path)

    _reply(chunks, entry, "اول [[fn:tr-01]]", "دوم")
    assert merging.merge(book_path, chunks, strict=True)["ok"]
    before = _notes(book_path)

    # Now an unresolvable graph.
    _reply(chunks, entry, "اول [[fn:tr-01]]", "دوم [[fn:tr-02]]")
    assert not merging.merge(book_path, chunks, strict=True)["ok"]

    assert _notes(book_path) == before, "a refused reply changed the book's notes"


# --------------------------------------------------------------------------- #
# A marker shown as an example is not a reference
# --------------------------------------------------------------------------- #

def test_a_marker_inside_a_code_span_is_an_example_not_a_reference(tmp_path):
    """The graph is read from the parsed markup, not from a scan of the string.

    A raw regex counted `[[fn:fn0001]]` shown in backticks as a reference, so the
    integrity gate demanded a note for a line that was documenting the notation.
    The translation policy itself shows the marker that way, which is how this
    reached real prose.
    """
    book_path, chunks, entry = _built(tmp_path)
    _reply(chunks, entry,
           "اول، و نشانه چنین نوشته می‌شود: `[[fn:tr-01]]`",
           "دوم", note=None)

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"], report
    assert _notes(book_path) == [], "an example created a footnote"
    merged = ir.load_book(book_path)["blocks"][0]["target"]
    assert "`[[fn:tr-01]]`" in merged, "the example itself was rewritten"


def test_a_literal_example_is_not_rewritten_when_another_unit_offers_that_name(
        tmp_path):
    """The second half: the substitution also has to respect a protected span.

    One unit documents `[[fn:tr-01]]` inside backticks while another really uses
    it. The rewrite ran over the raw string, so the documented example became a
    live reference to the allocated note.
    """
    book_path, chunks, entry = _built(tmp_path)
    _reply(chunks, entry,
           "اول، برای نمونه `[[fn:tr-01]]` نوشته می‌شود",
           "دوم با یادداشت واقعی[[fn:tr-01]]")

    report = merging.merge(book_path, chunks, strict=True)
    assert report["ok"], report

    blocks = {block["id"]: block for block in ir.load_book(book_path)["blocks"]}
    assert "`[[fn:tr-01]]`" in blocks["b00001"]["target"], (
        "the example was repointed at a real footnote id")
    allocated = _notes(book_path)
    assert len(allocated) == 1
    assert f"[[fn:{allocated[0]['id']}]]" in blocks["b00002"]["target"], (
        "the real reference was not repointed")


def test_a_translator_id_surviving_into_the_book_is_still_caught():
    """The negative control for making `validate_book` markup-aware.

    Parsing must not make the check blind to the thing it exists for: a `tr-NN`
    left in a finished book refers to nothing and prints the marker.
    """
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    book["blocks"].append(ir.make_block("paragraph", 1, text="A paragraph."))
    book["blocks"][0]["target"] = "بندی با نشانهٔ جامانده[[fn:tr-01]]"

    problems = ir.validate_book(book)

    assert any("tr-01" in problem for problem in problems), problems


# --------------------------------------------------------------------------- #
# The candidate book is validated before it is written
# --------------------------------------------------------------------------- #

def test_a_reference_to_a_footnote_the_book_does_not_have_is_refused(tmp_path):
    """Merge used to write a book its own validator rejects.

    `validate_book` has always reported `unknown footnote ref fn4321`; nothing
    asked it before saving, so the gate that exists to catch this ran only when
    someone later thought to run QA — by which time `book.json` already held it.
    """
    book_path, chunks, entry = _built(tmp_path)
    before = book_path.read_bytes()
    _reply(chunks, entry, "اول، با ارجاعی که وجود ندارد[[fn:fn4321]]", "دوم",
           note=None)

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    assert any("fn4321" in problem for problem in report.get("invalid_ir") or [])
    assert book_path.read_bytes() == before, "a rejected book was still written"


def test_a_note_body_referring_to_another_note_is_refused(tmp_path):
    """A footnote inside a footnote has nowhere to anchor, so the marker prints.

    The graph scanned the units; the note bodies sit beside them, so a `tr-02`
    inside `tr-01`'s body was never looked at and merged unchallenged.
    """
    book_path, chunks, entry = _built(tmp_path)
    _reply(chunks, entry, "اول[[fn:tr-01]]", "دوم",
           note="یادداشتی که خودش ارجاع دارد[[fn:tr-02]]")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    named = " ".join(report["malformed"].get(entry["id"], []))
    assert "tr-02" in named and "note inside a note" in named, named
    assert _notes(book_path) == [], "a refused reply still allocated a note"


# --------------------------------------------------------------------------- #
# The whole graph, not only the reply's own corner of it
# --------------------------------------------------------------------------- #
# `validate_note_graph` checks a reply before any id is allocated, and cannot see
# the book. Three kinds of edge were therefore checked by nothing at the moment of
# writing: a canonical reference inside a note body, a note referring to the id it
# is about to be given, and a marker on a surface Word cannot place a note on.

def _heads(book_path: Path) -> None:
    """Give the book a running head, so a marker can be put in one."""
    book = ir.load_book(book_path)
    book["sections"] = [{
        "index": 0, "start_block": None, "footers": {},
        "headers": {"default": {"paragraphs": [
            {"align": None, "pieces": [{"id": "rh0001", "text": "Chapter One",
                                        "target": None}]}]}},
    }]
    ir.save_book(book, book_path)


def test_a_note_asked_for_from_a_running_head_is_refused_by_name(tmp_path):
    """Measured: it merged, with an empty anchor.

    Word has no footnotes in a header, `_anchor_notes` walks text blocks only, and
    `build_docx` would try to place one there — so the note printed nowhere and
    the marker printed as itself. The refusal names the surface it is refusing
    rather than fabricating an anchor or dropping the note.
    """
    book_path = _book(tmp_path)
    _heads(book_path)
    chunks = tmp_path / "chunks"
    entry = chunking.build(book_path, chunks, glossary_path=None)["chunks"][0]
    before = book_path.read_bytes()
    ir.write_text(chunks / entry["output"], reply_text(
        chunks / entry["file"],
        "@@ b00001 para\nاول\n\n@@ b00002 para\nدوم\n\n"
        "@@ rh0001 header\nفصل یکم[[fn:tr-01]]\n\n"
        "@@ tr-01 footnote\nیادداشت مترجم.\n"))

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    assert report["refused"] == "invalid-book", report
    assert "running" in report["detail"] and "rh0001" in report["detail"]
    assert book_path.read_bytes() == before
    assert _notes(book_path) == []


def test_a_canonical_reference_inside_a_note_body_is_refused(tmp_path):
    """The hole the local-only check left: `[[fn:fn4321]]` in a note's own text."""
    book_path, chunks, entry = _built(tmp_path)
    before = book_path.read_bytes()
    _reply(chunks, entry, "اول[[fn:tr-01]]", "دوم",
           note="یادداشتی که به [[fn:fn4321]] اشاره می‌کند.")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    assert "fn4321" in json.dumps(report, ensure_ascii=False)
    assert book_path.read_bytes() == before
    assert _notes(book_path) == []


def test_a_note_referring_to_the_id_it_is_about_to_be_given_is_refused(tmp_path):
    """A self-cycle, spelled canonically so the local check cannot see it."""
    book_path, chunks, entry = _built(tmp_path)
    before = book_path.read_bytes()
    _reply(chunks, entry, "اول[[fn:tr-01]]", "دوم", note="خودارجاع [[fn:fn0001]]")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    assert "fn0001" in report.get("detail", ""), report
    assert book_path.read_bytes() == before
    assert _notes(book_path) == []


def test_a_marker_in_an_illustration_caption_is_refused(tmp_path):
    """`build_docx` writes alt text through `write_markup`; nothing anchors it."""
    book = ir.load_book(_book(tmp_path))
    book["blocks"].append(ir.make_block("image", 3, asset="fig.png",
                                        alt="A red rectangle", width_pt=120.0,
                                        height_pt=80.0))
    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)
    chunks = tmp_path / "chunks"
    entry = chunking.build(book_path, chunks, glossary_path=None)["chunks"][0]
    ir.write_text(chunks / entry["output"], reply_text(
        chunks / entry["file"],
        "@@ b00001 para\nاول\n\n@@ b00002 para\nدوم\n\n"
        "@@ b00003#alt alt\nمستطیلی سرخ[[fn:tr-01]]\n\n"
        "@@ tr-01 footnote\nیادداشت مترجم.\n"))

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    assert "b00003#alt" in report.get("detail", ""), report
    assert _notes(book_path) == []


def test_a_note_whose_anchor_is_not_the_paragraph_pointing_at_it_is_refused(
        tmp_path):
    """Checked at the write, so a hand-edited anchor cannot pass either."""
    book_path, chunks, entry = _built(tmp_path)
    _reply(chunks, entry, "اول[[fn:tr-01]]", "دوم")
    assert merging.merge(book_path, chunks, strict=True)["ok"] is True
    assert _notes(book_path)[0]["anchor_block"] == "b00001"

    moved = ir.load_book(book_path)
    moved["footnotes"][0]["anchor_block"] = "b00002"
    assert any("anchor" in problem and "fn0001" in problem
               for problem in notegraph.problems(moved))

    with pytest.raises(bookwrite.Refused) as stopped:
        with bookwrite.transaction(book_path, actor="probe") as tx:
            tx.book["footnotes"][0]["anchor_block"] = "b00002"
    assert stopped.value.reason == "invalid-book"


def test_a_marker_pointing_at_a_note_with_no_body_at_all_is_refused(tmp_path):
    book_path, chunks, entry = _built(tmp_path)
    _reply(chunks, entry, "اول[[fn:tr-01]]", "دوم")
    assert merging.merge(book_path, chunks, strict=True)["ok"] is True

    hollow = ir.load_book(book_path)
    hollow["footnotes"][0]["text"] = ""
    hollow["footnotes"][0]["target"] = ""

    assert any("no body" in problem for problem in notegraph.problems(hollow))


def test_a_source_note_the_translation_has_not_reached_yet_is_not_a_problem(
        tmp_path):
    """The ordinary half-translated state, which must not read as a defect."""
    book = ir.load_book(_book(tmp_path))
    book["blocks"][0]["text"] += "[[fn:fn0001]]"
    book["footnotes"] = [ir.make_footnote(1, anchor_block="b00001",
                                          text="A note that came with the book.")]

    assert notegraph.problems(book) == []


def test_a_backticked_marker_is_still_an_example_not_an_edge(tmp_path):
    """Span-aware, not a scan: the notation has to be quotable in the book."""
    book = ir.load_book(_book(tmp_path))
    book["blocks"][0]["target"] = "نمونهٔ نشانه‌گذاری: `[[fn:fn4321]]` است."

    assert notegraph.problems(book) == []

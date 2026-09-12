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

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import chunk as chunking  # noqa: E402
import merge as merging  # noqa: E402


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
    ir.write_text(chunks / entry["output"], body)


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
    ir.write_text(chunks / entry["output"],
                  f"@@ b00001 para\nاول [[fn:tr-01]]\n\n@@ b00002 para\nدوم\n"
                  f"\n@@ tr-01 {kind}\nیادداشت.\n")

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

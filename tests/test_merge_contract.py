"""The worksheet contract and the merge transaction.

Merge is the only door a translation walks through. Everything downstream — the
QA gates, the renderer, the DOCX — trusts that what is in ``book.json`` is what
a translator actually answered, for the unit it was actually asked about, and
that a merge which reports failure changed nothing.

These tests are about that trust rather than about translating well. Each one
started as a reproduction of a way the door could be walked past, and each was
watched to fail before the fix existed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import chunk as chunking  # noqa: E402
import merge as merging  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures: a real book and a real manifest, built by the real builder
# --------------------------------------------------------------------------- #

def _book(tmp_path: Path, texts: list[str], *, note: str = "") -> Path:
    """A small book on disk. ``note`` adds one source footnote to block 1."""
    book = ir.new_book(source_path="sample.epub", source_format="epub",
                       title="A Small Book", author="Test Author")
    for index, text in enumerate(texts, start=1):
        book["blocks"].append(ir.make_block("paragraph", index, text=text))
    if note:
        book["footnotes"].append(
            ir.make_footnote(1, anchor_block="b00001", text=note))
        book["blocks"][0]["text"] += "[[fn:fn0001]]"
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _build(book_path: Path, tmp_path: Path, *, budget: int = 6000) -> Path:
    """Worksheets and a manifest, from the real builder."""
    chunks = tmp_path / "chunks"
    chunking.build(book_path, chunks, glossary_path=None, budget=budget)
    return chunks


def _manifest(chunks: Path) -> dict:
    return json.loads((chunks / "manifest.json").read_text(encoding="utf-8"))


def _reply(chunks: Path, chunk_id: str, body: str) -> None:
    ir.write_text(chunks / f"out_{chunk_id}.md", body)


def _answer_every_unit(chunks: Path, chunk_id: str, *,
                       text: str = "ترجمهٔ آزمون.") -> None:
    """A well-formed reply: every header back, same order, same kind."""
    entry = next(c for c in _manifest(chunks)["chunks"] if c["id"] == chunk_id)
    kinds = entry.get("unit_kinds") or {}
    lines = []
    for unit_id in entry["unit_ids"]:
        lines += [f"@@ {unit_id} {kinds.get(unit_id, 'para')}", text, ""]
    _reply(chunks, chunk_id, "\n".join(lines))


# --------------------------------------------------------------------------- #
# A02 — the transaction
# --------------------------------------------------------------------------- #

def test_a_strict_merge_missing_one_unit_leaves_the_book_byte_identical(tmp_path):
    """The whole point of strict mode.

    Reporting ``ok=false`` and writing anyway is the worst of both: the operator
    is told to fix the worksheet, and meanwhile half the chunk has already
    landed, so the "before" they would fix against no longer exists.
    """
    book_path = _book(tmp_path, ["First paragraph.", "Second paragraph."])
    chunks = _build(book_path, tmp_path)
    before = book_path.read_bytes()

    # b00001 answered, b00002 simply not there.
    _reply(chunks, "chunk0001", "@@ b00001 para\nاول.\n")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    assert report["missing_units"], "the missing unit must be reported"
    assert book_path.read_bytes() == before, (
        "a failed strict merge wrote to the book")


def test_a_unit_the_book_does_not_have_is_not_reported_as_success(tmp_path):
    """A manifest id that resolves to nothing must not pass as a merge.

    ``apply_units`` has always known the id was unresolvable; the report simply
    threw that away and counted the chunk as merged.
    """
    book_path = _book(tmp_path, ["First paragraph."])
    chunks = _build(book_path, tmp_path)

    manifest = _manifest(chunks)
    manifest["chunks"][0]["unit_ids"] = ["b00099"]
    manifest["chunks"][0].pop("unit_kinds", None)
    manifest["chunks"][0]["source_sha256"] = ""
    ir.write_text(chunks / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    _reply(chunks, "chunk0001", "@@ b00099 para\nمتنی که جایی ندارد.\n")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["units_applied"] == 0
    assert report["ok"] is False, (
        "zero units applied was reported as a successful merge")


def test_a_rejected_merge_does_not_consume_the_footnote_numbering(tmp_path):
    """A failed attempt must not leave a gap a later good merge has to step over."""
    book_path = _book(tmp_path, ["First paragraph.", "Second paragraph."])
    chunks = _build(book_path, tmp_path)

    # A translator note, but b00002 is missing, so the whole thing is rejected.
    _reply(chunks, "chunk0001",
           "@@ b00001 para\nاول.[[fn:tr-01]]\n\n@@ tr-01 note\nیادداشت.\n")
    first = merging.merge(book_path, chunks, strict=True)
    assert first["ok"] is False

    assert ir.load_book(book_path)["footnotes"] == [], (
        "a rejected merge allocated a footnote anyway")


# --------------------------------------------------------------------------- #
# A01 — the worksheet contract
# --------------------------------------------------------------------------- #

def test_a_unit_answered_twice_is_refused_rather_than_overwritten(tmp_path):
    """Two headers with one id is a malformed reply, not a last-one-wins vote.

    Silently keeping either occurrence is a coin toss over which translation of
    the paragraph reaches the reader.
    """
    book_path = _book(tmp_path, ["First paragraph."])
    chunks = _build(book_path, tmp_path)
    before = book_path.read_bytes()

    _reply(chunks, "chunk0001",
           "@@ b00001 para\nترجمهٔ یکم.\n\n@@ b00001 para\nترجمهٔ دوم.\n")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    assert book_path.read_bytes() == before
    assert any("b00001" in problem
               for problems in report["malformed"].values()
               for problem in problems), report


def test_a_unit_answered_under_a_different_kind_is_refused(tmp_path):
    """The kind is part of the question. A heading answered as a paragraph is
    not the same answer — it is how a chapter title becomes body text."""
    book_path = _book(tmp_path, ["First paragraph."])
    chunks = _build(book_path, tmp_path)
    before = book_path.read_bytes()

    entry = _manifest(chunks)["chunks"][0]
    assert entry["unit_kinds"]["b00001"] == "para", entry

    _reply(chunks, "chunk0001", "@@ b00001 heading\nترجمه.\n")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    assert book_path.read_bytes() == before


def test_headers_that_come_back_reordered_are_refused(tmp_path):
    """The worksheet says "in the same order" and nothing enforced it.

    Order is the only thing that distinguishes two paragraphs a translator has
    swapped from two paragraphs a translator has rendered freely.
    """
    book_path = _book(tmp_path, ["First paragraph.", "Second paragraph."])
    chunks = _build(book_path, tmp_path)
    before = book_path.read_bytes()

    _reply(chunks, "chunk0001",
           "@@ b00002 para\nدوم.\n\n@@ b00001 para\nاول.\n")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    assert book_path.read_bytes() == before


def test_a_reply_wrapped_in_a_code_fence_does_not_publish_the_fence(tmp_path):
    """Models fence their output. The closing fence is not part of the novel."""
    book_path = _book(tmp_path, ["First paragraph."])
    chunks = _build(book_path, tmp_path)

    _reply(chunks, "chunk0001", "```\n@@ b00001 para\nترجمهٔ آزمون.\n```\n")

    merging.merge(book_path, chunks, strict=False)

    target = ir.load_book(book_path)["blocks"][0]["target"] or ""
    assert "`" not in target, f"the fence reached the book: {target!r}"
    assert target == "ترجمهٔ آزمون."


def test_a_source_line_that_looks_like_a_header_cannot_forge_a_unit(tmp_path):
    """A paragraph whose own text begins ``@@ b00002 para`` must not be able to
    truncate itself and invent a second unit out of the source text.

    This is the ``@@`` transport rule, tested from the side that matters: the
    worksheet is *rendered* from source text, so the escape has to exist on the
    way out as well as the way back.
    """
    forged = "@@ b00002 para"
    book_path = _book(tmp_path, [f"Real first line.\n{forged}\nstill block one.",
                                 "Second paragraph."])
    chunks = _build(book_path, tmp_path)

    worksheet = (chunks / "chunk0001.md").read_text(encoding="utf-8")
    units = merging.parse_worksheet(worksheet)

    # The worksheet offers exactly the units the manifest promised, and block
    # one still holds all three of its lines.
    assert set(units) == set(_manifest(chunks)["chunks"][0]["unit_ids"])
    assert "still block one." in units["b00001"]


# --------------------------------------------------------------------------- #
# A03 — partial segment coverage
# --------------------------------------------------------------------------- #

def _segmented(tmp_path: Path) -> tuple[Path, Path]:
    """A manifest whose chunk owns one block as two segments.

    Hand-written on purpose: this is merge's own input contract, and the page
    route that normally produces segment ids has a separate wrapper guard which
    this hole sits underneath.
    """
    book_path = _book(tmp_path, ["Part one and part two together."])
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    manifest = {
        "schema": "revayat-novel/chunks@1",
        "book": str(book_path),
        "book_sha256": "",
        "glossary": "",
        "budget": 6000,
        "chunks": [{
            "id": "chunk0001",
            "file": "chunk0001.md",
            "output": "out_chunk0001.md",
            "block_ids": ["b00001"],
            "unit_ids": ["b00001#1", "b00001#2"],
            "unit_kinds": {"b00001#1": "para", "b00001#2": "para"},
            "units": 2,
            "source_chars": 31,
            "source_sha256": "",
        }],
    }
    ir.write_text(chunks / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    return book_path, chunks


def _split_across_two_chunks(tmp_path: Path) -> tuple[Path, Path]:
    """One block, its two segments owned by two different worksheets.

    This is the shape that matters for ``--only``: selecting one worksheet
    leaves nothing "missing" from the selected set, so the ordinary missing-unit
    check cannot see that half the paragraph is absent.
    """
    book_path = _book(tmp_path, ["Part one and part two together."])
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    manifest = {
        "schema": "revayat-novel/chunks@1",
        "book": str(book_path), "book_sha256": "", "glossary": "",
        "budget": 6000,
        "chunks": [
            {"id": "chunk0001", "file": "chunk0001.md",
             "output": "out_chunk0001.md", "block_ids": ["b00001"],
             "unit_ids": ["b00001#1"], "unit_kinds": {"b00001#1": "para"},
             "units": 1, "source_chars": 15, "source_sha256": ""},
            {"id": "chunk0002", "file": "chunk0002.md",
             "output": "out_chunk0002.md", "block_ids": ["b00001"],
             "unit_ids": ["b00001#2"], "unit_kinds": {"b00001#2": "para"},
             "units": 1, "source_chars": 16, "source_sha256": ""},
        ],
    }
    ir.write_text(chunks / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    return book_path, chunks


def test_merging_one_worksheet_of_a_split_block_does_not_truncate_it(tmp_path):
    """Half a paragraph, reported as a success, is the quietest data loss here.

    ``rejoin`` joins whatever parts it was handed, so one part arrives as the
    entire block and the other half of the paragraph is gone with nothing said.
    Nothing is "missing" from the selected worksheet — the other half was never
    asked of it — so only the manifest as a whole knows the block is split.
    """
    book_path, chunks = _split_across_two_chunks(tmp_path)
    _reply(chunks, "chunk0001", "@@ b00001#1 para\nبخش یک.\n")
    _reply(chunks, "chunk0002", "@@ b00001#2 para\nبخش دو.\n")
    before = book_path.read_bytes()

    report = merging.merge(book_path, chunks, only=["chunk0001"], strict=True)

    assert report["ok"] is False
    assert book_path.read_bytes() == before, (
        "a block was replaced by one of its two segments")


def test_merging_both_worksheets_of_a_split_block_joins_it(tmp_path):
    """The positive control for the same manifest."""
    book_path, chunks = _split_across_two_chunks(tmp_path)
    _reply(chunks, "chunk0001", "@@ b00001#1 para\nبخش یک.\n")
    _reply(chunks, "chunk0002", "@@ b00001#2 para\nبخش دو.\n")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is True, report
    assert ir.load_book(book_path)["blocks"][0]["target"] == "بخش یک. بخش دو."


def test_one_segment_missing_from_its_own_worksheet_is_still_refused(tmp_path):
    """The simpler shape, where the missing-unit check does see it."""
    book_path, chunks = _segmented(tmp_path)
    before = book_path.read_bytes()

    _reply(chunks, "chunk0001", "@@ b00001#1 para\nبخش یک.\n")

    report = merging.merge(book_path, chunks, only=["chunk0001"], strict=True)

    assert report["ok"] is False
    assert book_path.read_bytes() == before, (
        "a block was replaced by one of its two segments")


def test_every_segment_answered_still_merges(tmp_path):
    """The positive control: complete coverage must keep working."""
    book_path, chunks = _segmented(tmp_path)

    _reply(chunks, "chunk0001",
           "@@ b00001#1 para\nبخش یک.\n\n@@ b00001#2 para\nبخش دو.\n")

    report = merging.merge(book_path, chunks, only=["chunk0001"], strict=True)

    assert report["ok"] is True, report
    assert ir.load_book(book_path)["blocks"][0]["target"] == "بخش یک. بخش دو."


def test_a_segment_answered_twice_is_refused(tmp_path):
    """Duplicate indices are a coverage problem, not a join problem."""
    book_path, chunks = _segmented(tmp_path)
    before = book_path.read_bytes()

    _reply(chunks, "chunk0001",
           "@@ b00001#1 para\nیک.\n\n@@ b00001#1 para\nیک دیگر.\n"
           "\n@@ b00001#2 para\nدو.\n")

    report = merging.merge(book_path, chunks, only=["chunk0001"], strict=True)

    assert report["ok"] is False
    assert book_path.read_bytes() == before


def test_a_whole_unit_and_its_segment_cannot_both_be_answered(tmp_path):
    """``b00001`` and ``b00001#1`` in one reply is a contradiction about what
    the unit even is, and whichever lands last would decide the paragraph."""
    book_path, chunks = _segmented(tmp_path)
    manifest = _manifest(chunks)
    manifest["chunks"][0]["unit_ids"] = ["b00001", "b00001#1"]
    manifest["chunks"][0]["unit_kinds"] = {"b00001": "para", "b00001#1": "para"}
    ir.write_text(chunks / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    before = book_path.read_bytes()

    _reply(chunks, "chunk0001",
           "@@ b00001 para\nکل.\n\n@@ b00001#1 para\nبخش.\n")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    assert book_path.read_bytes() == before


# --------------------------------------------------------------------------- #
# A04, A05 — translator notes
# --------------------------------------------------------------------------- #

def test_merging_the_same_reply_twice_leaves_one_footnote(tmp_path):
    """Replaying a worksheet is an ordinary thing to do — a corrected reply, a
    resumed run, an operator who is not sure it landed. Each replay allocating
    a fresh ``fnNNNN`` leaves the previous one in the book referenced by
    nothing, and it prints."""
    book_path = _book(tmp_path, ["First paragraph."])
    chunks = _build(book_path, tmp_path)

    reply = ("@@ b00001 para\nاول.[[fn:tr-01]]\n\n"
             "@@ tr-01 note\nیادداشت مترجم.\n")
    _reply(chunks, "chunk0001", reply)

    assert merging.merge(book_path, chunks, strict=True)["ok"] is True
    first = ir.load_book(book_path)
    assert len(first["footnotes"]) == 1

    assert merging.merge(book_path, chunks, strict=True)["ok"] is True
    second = ir.load_book(book_path)

    assert len(second["footnotes"]) == 1, (
        f"replay allocated another note: "
        f"{[n['id'] for n in second['footnotes']]}")
    assert ir.footnote_refs(second["blocks"][0]["target"]) == \
        [second["footnotes"][0]["id"]]
    assert not ir.validate_book(second)


def test_two_segments_with_their_own_local_note_ids_keep_both_notes(tmp_path):
    """``tr-01`` is scoped to the worksheet that used it.

    Two segments of one block, answered in two worksheets, each with its own
    ``tr-01`` meaning a different thing. Rewriting tokens over the rejoined
    block cannot tell the two scopes apart, so both references point at the
    first note and the second is orphaned.
    """
    book_path = _book(tmp_path, ["Part one and part two together."])
    chunks = tmp_path / "chunks"
    chunks.mkdir()
    manifest = {
        "schema": "revayat-novel/chunks@1",
        "book": str(book_path), "book_sha256": "", "glossary": "",
        "budget": 6000,
        "chunks": [
            {"id": "chunk0001", "file": "chunk0001.md",
             "output": "out_chunk0001.md", "block_ids": ["b00001"],
             "unit_ids": ["b00001#1"], "unit_kinds": {"b00001#1": "para"},
             "units": 1, "source_chars": 15, "source_sha256": ""},
            {"id": "chunk0002", "file": "chunk0002.md",
             "output": "out_chunk0002.md", "block_ids": ["b00001"],
             "unit_ids": ["b00001#2"], "unit_kinds": {"b00001#2": "para"},
             "units": 1, "source_chars": 16, "source_sha256": ""},
        ],
    }
    ir.write_text(chunks / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")

    _reply(chunks, "chunk0001",
           "@@ b00001#1 para\nبخش یک.[[fn:tr-01]]\n\n"
           "@@ tr-01 note\nیادداشت یکم.\n")
    _reply(chunks, "chunk0002",
           "@@ b00001#2 para\nبخش دو.[[fn:tr-01]]\n\n"
           "@@ tr-01 note\nیادداشت دوم.\n")

    report = merging.merge(book_path, chunks, strict=True)
    assert report["ok"] is True, report

    book = ir.load_book(book_path)
    refs = ir.footnote_refs(book["blocks"][0]["target"])
    bodies = {note["id"]: note["text"] for note in book["footnotes"]}

    assert len(set(refs)) == 2, (
        f"the two scopes collapsed onto one note: {refs}")
    assert sorted(bodies[ref] for ref in refs) == \
        ["یادداشت دوم.", "یادداشت یکم."]
    assert not ir.validate_book(book)


# --------------------------------------------------------------------------- #
# A06 — freshness
# --------------------------------------------------------------------------- #

def test_a_reply_written_against_source_that_has_since_changed_is_refused(tmp_path):
    """The negation case, which is the one that matters.

    ``chunk build`` records what each worksheet was cut from and merge never
    read it, so a translation of "Ali arrived." is accepted for a source that
    now says "Ali never arrived." Matching ids are not freshness.
    """
    book_path = _book(tmp_path, ["Ali arrived."])
    chunks = _build(book_path, tmp_path)
    _reply(chunks, "chunk0001", "@@ b00001 para\nعلی رسید.\n")

    book = ir.load_book(book_path)
    book["blocks"][0]["text"] = "Ali never arrived."
    ir.save_book(book, book_path)
    before = book_path.read_bytes()

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is False
    assert report.get("stale"), report
    assert book_path.read_bytes() == before


def test_an_unchanged_source_is_not_reported_stale(tmp_path):
    """The positive control. A freshness check that refuses everything is not a
    freshness check."""
    book_path = _book(tmp_path, ["Ali arrived.", "He said nothing."])
    chunks = _build(book_path, tmp_path)
    _answer_every_unit(chunks, "chunk0001")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is True, report
    assert not report.get("stale")


def test_a_manifest_without_a_recorded_source_hash_still_merges(tmp_path):
    """A working directory built before freshness was recorded must keep
    working. Refusing it would strand real translations, and inventing a hash
    for it would be a lie about what was checked."""
    book_path = _book(tmp_path, ["Ali arrived."])
    chunks = _build(book_path, tmp_path)

    manifest = _manifest(chunks)
    manifest["chunks"][0].pop("source_sha256", None)
    ir.write_text(chunks / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    _reply(chunks, "chunk0001", "@@ b00001 para\nعلی رسید.\n")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is True, report
    assert report.get("unverified_freshness") == ["chunk0001"], report


def test_a_manifest_without_recorded_kinds_still_merges(tmp_path):
    """Same compatibility question for the kind check."""
    book_path = _book(tmp_path, ["Ali arrived."])
    chunks = _build(book_path, tmp_path)

    manifest = _manifest(chunks)
    manifest["chunks"][0].pop("unit_kinds", None)
    ir.write_text(chunks / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    _reply(chunks, "chunk0001", "@@ b00001 whatever\nعلی رسید.\n")

    report = merging.merge(book_path, chunks, strict=True)

    assert report["ok"] is True, report


# --------------------------------------------------------------------------- #
# Lenient mode is not permission to corrupt
# --------------------------------------------------------------------------- #

def test_lenient_mode_applies_what_is_valid_and_still_refuses_the_malformed(
        tmp_path):
    """``--lenient`` exists so an operator can land the chunks that are good.

    It was never meant to mean "write a duplicate-id reply anyway": exit code
    and data integrity are different questions.
    """
    book_path = _book(tmp_path, ["First paragraph.", "Second paragraph."])
    chunks = _build(book_path, tmp_path)

    _reply(chunks, "chunk0001",
           "@@ b00001 para\nیکم.\n\n@@ b00001 para\nدوباره.\n"
           "\n@@ b00002 para\nدوم.\n")

    report = merging.merge(book_path, chunks, strict=False)

    book = ir.load_book(book_path)
    assert report["ok"] is False
    assert book["blocks"][0]["target"] is None, (
        "the ambiguous unit was written under lenient mode")


@pytest.mark.parametrize("strict", [True, False])
def test_a_good_reply_merges_in_both_modes(tmp_path, strict):
    """The plain path, which none of this may break."""
    book_path = _book(tmp_path, ["First paragraph.", "Second paragraph."])
    chunks = _build(book_path, tmp_path)
    _answer_every_unit(chunks, "chunk0001", text="ترجمهٔ درست.")

    report = merging.merge(book_path, chunks, strict=strict)

    assert report["ok"] is True, report
    book = ir.load_book(book_path)
    assert [block["target"] for block in book["blocks"]] == \
        ["ترجمهٔ درست.", "ترجمهٔ درست."]
    assert not ir.validate_book(book)

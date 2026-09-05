"""Nothing a page carries may grow with the book.

A page job is only worth cutting if it stays a page. Every piece of context a
page is given — the neighbouring prose, the glossary, the voices, the words OCR
misread — grows with the book rather than with the page, so each one needs its
own ceiling. Without them the "one page at a time" run quietly becomes the
whole-book context it was built to replace.

The one thing that is never trimmed to fit is the page's own text: a job too
big for the budget is *reported*, because a silently shortened page is a page
translated wrong.
"""

from __future__ import annotations

from pathlib import Path

import bookir as ir
import glossary as gl
import pagerun
import runstate


def _paged_book(pages: int, *, sentences: int = 4) -> dict:
    book = ir.new_book()
    blocks = []
    index = 0
    for page in range(1, pages + 1):
        index += 1
        blocks.append(ir.make_block("pagebreak", index, page=page, soft=True))
        index += 1
        blocks.append(ir.make_block(
            "paragraph", index, page=page,
            text=" ".join(f"Page {page} sentence {n} of quite ordinary prose."
                          for n in range(sentences)),
        ))
    book["blocks"] = blocks
    return book


def _build(book: dict, tmp_path: Path, **options) -> tuple[dict, Path]:
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return pagerun.build(path, tmp_path / "pages", **options), tmp_path / "pages"


def _worksheet(pages: Path, page: int) -> str:
    return (pages / f"page{page:04d}.md").read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """The body of one ``## `` section of a worksheet."""
    start = text.index(heading)
    rest = text[start + len(heading):]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


# --------------------------------------------------------------------------- #
# The payload budget
# --------------------------------------------------------------------------- #

def test_a_job_over_the_budget_is_reported_not_truncated(tmp_path):
    book = _paged_book(3, sentences=60)
    manifest, pages = _build(book, tmp_path, budget=500)

    assert manifest["over_budget"] == [1, 2, 3]
    for entry in manifest["chunks"]:
        assert entry["over_budget"] is True
        assert entry["payload_chars"] > 500

    # Every source character is still on the worksheet. Reporting the overrun
    # is the whole point: cutting to fit would lose prose nobody would miss
    # until the book was printed.
    worksheet = _worksheet(pages, 1)
    source = next(block["text"] for block in ir.iter_text_blocks(book)
                  if block["page"] == 1)
    assert source in worksheet
    assert len(worksheet) == manifest["chunks"][0]["payload_chars"]


def test_an_over_budget_page_is_visible_from_the_resume_view(tmp_path):
    _, pages = _build(_paged_book(2, sentences=60), tmp_path, budget=500)
    progress = pagerun.status(pages)
    assert progress["over_budget"] == [1, 2]
    assert all(page["over_budget"] for page in progress["pages"])


def test_an_ordinary_page_sits_well_inside_the_default_budget(tmp_path):
    manifest, _ = _build(_paged_book(20), tmp_path)
    assert manifest["over_budget"] == []
    assert max(entry["payload_chars"] for entry in manifest["chunks"]) \
        < pagerun.DEFAULT_BUDGET / 2


# --------------------------------------------------------------------------- #
# Neighbour context
# --------------------------------------------------------------------------- #

def test_neighbour_context_has_a_hard_maximum_per_side(tmp_path):
    """A dense neighbour cannot outweigh the page it is context for."""
    limit = 200
    _, pages = _build(_paged_book(3, sentences=80), tmp_path,
                      neighbour_chars=limit)

    context = _section(_worksheet(pages, 2), "## Surrounding pages")
    before = next(line for line in context.splitlines() if line.startswith("Before"))
    after = next(line for line in context.splitlines() if line.startswith("After"))
    # The ellipsis and the "Before (page 1): " label are the only additions.
    assert len(before) < limit + 40
    assert len(after) < limit + 40


def test_the_context_ceiling_holds_however_long_the_neighbour_is(tmp_path):
    book = _paged_book(3, sentences=400)
    jobs = pagerun.owners(book)
    before, after = pagerun.neighbour_context(book, jobs, 1, limit=150)
    assert len(before) == len(after) == 150


def test_the_first_and_last_page_have_only_one_neighbour(tmp_path):
    book = _paged_book(3)
    jobs = pagerun.owners(book)
    assert pagerun.neighbour_context(book, jobs, 0)[0] == ""
    assert pagerun.neighbour_context(book, jobs, 2)[1] == ""


def test_context_is_labelled_as_context_on_every_page_that_gets_it(tmp_path):
    _, pages = _build(_paged_book(4), tmp_path)
    for page in (2, 3):
        worksheet = _worksheet(pages, page)
        assert "context only, do not translate or output" in worksheet
        assert worksheet.index("## Surrounding pages") < worksheet.index("## Translate")


# --------------------------------------------------------------------------- #
# Continuity state, not more prose
# --------------------------------------------------------------------------- #

def _glossary_with(names: int) -> dict:
    glossary = gl.new_glossary()
    glossary["entries"] = [
        dict(gl.make_entry(index, f"Character{index}"),
             later_form=f"شخصیت {index}", first_form=f"شخصیت {index} (Character{index})",
             locked=True, frequency=1000 - index)
        for index in range(1, names + 1)
    ]
    glossary["voices"] = [
        {"character": f"Character{index}", "register": "formal",
         "persian_policy": "keeps the older forms"}
        for index in range(1, names + 1)
    ]
    return glossary


def test_the_glossary_a_page_carries_is_bounded(tmp_path):
    """The term table is book-wide; only a page's worth may ride along."""
    glossary_path = tmp_path / "glossary.json"
    gl.save(_glossary_with(120), glossary_path)

    book = _paged_book(2)
    book["blocks"][1]["text"] = " ".join(f"Character{n} spoke." for n in range(1, 121))
    _, pages = _build(book, tmp_path, glossary_path=glossary_path)

    rows = [line for line in _section(_worksheet(pages, 1), "## Names").splitlines()
            if line.startswith("| ")]
    assert 0 < len(rows) - 2 <= pagerun.MAX_TERMS      # minus header and rule


def test_the_voice_cards_a_page_carries_are_bounded(tmp_path):
    glossary_path = tmp_path / "glossary.json"
    gl.save(_glossary_with(40), glossary_path)

    book = _paged_book(2)
    book["blocks"][1]["text"] = " ".join(f"Character{n} spoke." for n in range(1, 41))
    _, pages = _build(book, tmp_path, glossary_path=glossary_path)

    cards = [line for line in _section(_worksheet(pages, 1), "## Character voices")
             .splitlines() if line.startswith("- ")]
    assert 0 < len(cards) <= pagerun.MAX_VOICE_CARDS


def test_first_mention_is_decided_for_the_page_not_left_to_it(tmp_path):
    """Parallel page jobs cannot each answer "is this the first mention?" —
    every one of them would say yes."""
    glossary = gl.new_glossary()
    glossary["entries"] = [dict(gl.make_entry(1, "Elizabeth"),
                                later_form="الیزابت",
                                first_form="الیزابت (Elizabeth)",
                                first_block_id="b00002", frequency=90)]
    glossary_path = tmp_path / "glossary.json"
    gl.save(glossary, glossary_path)

    book = _paged_book(2)
    for block in ir.iter_text_blocks(book):
        block["text"] = f"Elizabeth walked on. {block['text']}"
    _, pages = _build(book, tmp_path, glossary_path=glossary_path)

    assert "first mention, introduce it here" in _worksheet(pages, 1)
    assert "first mention, introduce it here" not in _worksheet(pages, 2)


def test_ocr_uncertainty_travels_with_the_page_that_has_it(tmp_path):
    book = _paged_book(2)
    scanned = book["blocks"][1]
    scanned["ocr"] = {"confidence": 61.5, "grade": "low", "source_block": "o0001-001",
                      "low_words": ["Bottorn", "tbe", "rnorning"]}
    clean = book["blocks"][3]
    clean["ocr"] = {"confidence": 98.0, "grade": "high", "low_words": []}

    manifest, pages = _build(book, tmp_path)

    first = manifest["chunks"][0]["ocr"]
    assert first["by_grade"] == {"low": 1} and first["min_confidence"] == 61.5
    assert first["uncertain"] == [{"block": scanned["id"],
                                   "words": ["Bottorn", "tbe", "rnorning"]}]
    assert "Bottorn" in _worksheet(pages, 1)

    # A page read confidently carries no uncertainty section at all.
    assert manifest["chunks"][1]["ocr"]["uncertain"] == []
    assert "Read poorly by OCR" not in _worksheet(pages, 2)


def test_the_ocr_notes_a_page_carries_are_bounded(tmp_path):
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", index, page=1, text=f"Paragraph {index} of prose.",
                      ocr={"confidence": 55.0, "grade": "low",
                           "low_words": [f"word{index}x{n}" for n in range(20)]})
        for index in range(1, 41)
    ]
    manifest, _ = _build(book, tmp_path)

    state = manifest["chunks"][0]["ocr"]
    assert len(state["uncertain"]) == pagerun.MAX_OCR_NOTES
    assert state["truncated"] == 40 - pagerun.MAX_OCR_NOTES
    assert all(len(note["words"]) <= pagerun.MAX_LOW_WORDS
               for note in state["uncertain"])


def test_a_page_records_the_geometry_the_render_check_needs(tmp_path):
    book = _paged_book(2)
    book["blocks"][1]["bbox"] = [54.0, 90.0, 340.0, 300.0]
    book["blocks"][3]["bbox"] = [54.0, 100.0, 320.0, 280.0]
    manifest, _ = _build(book, tmp_path)

    geometry = manifest["chunks"][0]["geometry"]
    assert geometry["width_pt"] == book["page"]["width_pt"]
    assert geometry["text_bbox"] == [54.0, 90.0, 340.0, 300.0]


# --------------------------------------------------------------------------- #
# Per-page identity in the run state
# --------------------------------------------------------------------------- #

def test_a_page_carries_its_own_lifecycle_and_evidence(tmp_path):
    state = runstate.RunState(tmp_path)
    state.set_page(7, "translated", hashes={"source": "aaa", "translation": "bbb"})
    state.set_page(7, "rendered", hashes={"render": "ccc"})

    record = runstate.RunState(tmp_path).page(7)
    assert record["state"] == "rendered"
    assert record["hashes"] == {"source": "aaa", "translation": "bbb", "render": "ccc"}
    assert record["attempts"] == 0 and record["last_error"] == ""


def test_only_a_failure_spends_an_attempt(tmp_path):
    state = runstate.RunState(tmp_path)
    for _ in range(4):
        state.set_page(3, "qa_passed")
    assert state.page(3)["attempts"] == 0

    state.set_page(3, "failed", error="a picture is missing")
    state.set_page(3, "failed", error="a picture is still missing")
    assert state.page(3)["attempts"] == 2
    assert state.page(3)["last_error"] == "a picture is still missing"

    state.set_page(3, "accepted")
    assert state.page(3)["attempts"] == 2, "history is not erased by a later pass"
    assert state.page(3)["last_error"] == ""


def test_a_changed_source_page_invalidates_that_page_and_nothing_else(tmp_path):
    state = runstate.RunState(tmp_path)
    for page in (1, 2, 3):
        state.note_page_source(page, f"hash-{page}")
        state.set_page(page, "accepted",
                       hashes={"translation": "t", "render": "r", "qa": "q"})

    assert state.note_page_source(2, "hash-2-corrected") is True
    assert state.note_page_source(2, "hash-2-corrected") is False

    reset = state.page(2)
    assert reset["state"] == "pending"
    assert reset["hashes"] == {"source": "hash-2-corrected"}, \
        "the translation, render and QA report survived a source that moved"
    for page in (1, 3):
        assert state.page(page)["state"] == "accepted"
        assert state.page(page)["hashes"]["qa"] == "q"


def test_an_unknown_page_state_is_rejected(tmp_path):
    state = runstate.RunState(tmp_path)
    for bad in ("done", "ok", "qa-passed"):
        try:
            state.set_page(1, bad)
        except ValueError as refusal:
            assert bad in str(refusal)
        else:  # pragma: no cover - the assertion below reports it
            raise AssertionError(f"{bad!r} was accepted as a page state")


def test_an_unknown_page_hash_is_rejected(tmp_path):
    state = runstate.RunState(tmp_path)
    try:
        state.set_page(1, "rendered", hashes={"screenshot": "abc"})
    except ValueError as refusal:
        assert "screenshot" in str(refusal)
    else:  # pragma: no cover
        raise AssertionError("an unknown hash slot was accepted")


def test_page_records_and_stage_records_do_not_disturb_each_other(tmp_path):
    """``chunk`` still resumes from the same file a page run writes into."""
    state = runstate.RunState(tmp_path)
    state.record("chunk", {"book": "abc", "budget": "6000"})
    state.set_page(1, "accepted")

    reopened = runstate.RunState(tmp_path)
    assert reopened.is_stale("chunk", {"book": "abc", "budget": "6000"}) == (False, "")
    assert reopened.page(1)["state"] == "accepted"
    assert reopened.pages() == {1: reopened.page(1)}


def test_a_page_run_leaves_the_chunk_stage_record_alone(tmp_path):
    """A page run is not a chunk run; it must not answer for one."""
    book = _paged_book(2)
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    pagerun.build(path, tmp_path / "pages")

    state = runstate.RunState(tmp_path)
    assert state.recorded("chunk") is None
    assert sorted(state.pages()) == [1, 2]

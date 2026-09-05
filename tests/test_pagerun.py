"""One job per source page, and every block in exactly one of them.

The failure this whole stage exists to prevent is a block that goes out to be
translated twice — the paragraph a page break cut in half, the line of dialogue
that runs on, the footnote two pages both point at. Translating one twice
produces a book that passes every count-based gate and reads as if a character
said the same thing in two different ways.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bookir as ir
import pagerun
import runstate
from read_pdf import _merge_split_paragraphs


# --------------------------------------------------------------------------- #
# Fixture helpers — books are built here, never committed
# --------------------------------------------------------------------------- #

def _book(items: list[tuple[int, str, dict]]) -> dict:
    """``items`` are ``(page, block type, fields)`` in reading order."""
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block(block_type, index, page=page, **fields)
        for index, (page, block_type, fields) in enumerate(items, start=1)
    ]
    return book


def _prose(page: int, marker: str, sentences: int = 3) -> tuple[int, str, dict]:
    text = " ".join(f"{marker} sentence {n} of ordinary prose." for n in range(sentences))
    return (page, "paragraph", {"text": text, "bbox": [54, 100, 340, 200]})


def _save(book: dict, tmp_path: Path) -> Path:
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _built(book: dict, tmp_path: Path, **options) -> tuple[dict, Path]:
    manifest = pagerun.build(_save(book, tmp_path), tmp_path / "pages", **options)
    return manifest, tmp_path / "pages"


def _job(manifest: dict, page: int) -> dict:
    return next(entry for entry in manifest["chunks"] if entry["page"] == page)


def _worksheet(pages: Path, page: int) -> str:
    return (pages / f"page{page:04d}.md").read_text(encoding="utf-8")


def _translate_section(text: str) -> str:
    """Only the part of a worksheet the translator is told to answer."""
    marker = "## Translate"
    assert marker in text
    return text[text.index(marker):]


# --------------------------------------------------------------------------- #
# Ownership
# --------------------------------------------------------------------------- #

def test_every_block_is_owned_by_exactly_one_page(tmp_path):
    book = _book([
        _prose(1, "One"), (1, "image", {"asset": "a.png", "alt": "A picture"}),
        _prose(2, "Two"), _prose(2, "Also two"),
        _prose(3, "Three"),
    ])
    manifest, _ = _built(book, tmp_path)

    owned = [block_id for entry in manifest["chunks"] for block_id in entry["block_ids"]]
    assert sorted(owned) == sorted(block["id"] for block in book["blocks"])
    assert len(owned) == len(set(owned)), "a block was claimed by two pages"


def test_a_paragraph_that_spans_a_page_break_is_translated_once(tmp_path):
    """``read_pdf`` merges the two halves into one block on the first page.

    That block is page one's to translate. Page two must see it only as
    context — the failure being guarded against is both pages sending it out.
    """
    blocks = [
        ir.make_block("paragraph", 1, page=1, text="She turned away from him and"),
        ir.make_block("pagebreak", 2, page=2, soft=True),
        ir.make_block("paragraph", 3, page=2,
                      text="betrayed what she had thought she wanted."),
        ir.make_block("paragraph", 4, page=2, text="Darcy said nothing at all."),
    ]
    book = ir.new_book()
    book["blocks"] = _merge_split_paragraphs(blocks)
    assert len(book["blocks"]) == 3, "the fixture is not actually a split paragraph"

    manifest, pages = _built(book, tmp_path)
    spanning = book["blocks"][0]
    assert "betrayed what she had thought" in spanning["text"]
    assert spanning["page"] == 1

    assert spanning["id"] in _job(manifest, 1)["unit_ids"]
    assert spanning["id"] not in _job(manifest, 2)["block_ids"]
    assert spanning["id"] not in _job(manifest, 2)["unit_ids"]

    # …and page two is told about it, under a header that forbids translating it.
    page_two = _worksheet(pages, 2)
    assert "betrayed what she had thought" in page_two
    assert "do not translate" in page_two
    assert "betrayed what she had thought" not in _translate_section(page_two)


def test_dialogue_that_runs_on_over_a_page_break_is_translated_once(tmp_path):
    blocks = [
        ir.make_block("paragraph", 1, page=4,
                      text="«I never meant it that way,» she said, and then, after"),
        ir.make_block("pagebreak", 2, page=5, soft=True),
        ir.make_block("paragraph", 3, page=5,
                      text="a long silence, «not the way you think.»"),
    ]
    book = ir.new_book()
    book["blocks"] = _merge_split_paragraphs(blocks)
    assert len(book["blocks"]) == 2

    manifest, pages = _built(book, tmp_path)
    line = book["blocks"][0]
    assert line["page"] == 4 and "not the way you think" in line["text"]

    assert [line["id"]] == _job(manifest, 4)["unit_ids"]
    assert _job(manifest, 5)["unit_ids"] == []
    assert "not the way you think" not in _translate_section(_worksheet(pages, 5))
    assert "not the way you think" in _worksheet(pages, 5)


def test_an_illustration_beside_a_page_break_lands_on_its_own_page(tmp_path):
    """A picture at the top of page three belongs to page three, not to the
    paragraph that ended page two."""
    book = _book([
        _prose(2, "Two"),
        (3, "pagebreak", {"soft": True}),
        (3, "image", {"asset": "plate.png", "alt": "The frontispiece",
                      "width_pt": 180.0, "height_pt": 120.0}),
        _prose(3, "Three"),
        (4, "pagebreak", {"soft": True}),
        (4, "image", {"asset": "later.png", "alt": "A later plate",
                      "width_pt": 90.0, "height_pt": 120.0}),
    ])
    manifest, _ = _built(book, tmp_path)

    assert _job(manifest, 2)["image_ids"] == []
    assert _job(manifest, 3)["image_ids"] == ["b00003"]
    assert _job(manifest, 4)["image_ids"] == ["b00006"]
    # The caption travels with the picture, on the picture's page.
    assert "b00003#alt" in _job(manifest, 3)["unit_ids"]
    assert "b00003#alt" not in _job(manifest, 4)["unit_ids"]


def test_a_footnote_anchored_on_another_page_resolves_to_where_it_is_read(tmp_path):
    """The body is printed at the foot of the next page; the marker is not.

    A reader meets the note where the marker is, so that is the page that has
    to translate it — and only that page.
    """
    book = _book([
        (7, "paragraph", {"text": "A cultural reference [[fn:fn0001]] follows here."}),
        (8, "pagebreak", {"soft": True}),
        (8, "paragraph", {"text": "The note itself was set at the foot of this page."}),
    ])
    book["footnotes"] = [
        ir.make_footnote(1, anchor_block="b00003", text="1. Thanksgiving is a holiday.")
    ]
    manifest, _ = _built(book, tmp_path)

    assert _job(manifest, 7)["footnote_ids"] == ["fn0001"]
    assert _job(manifest, 8)["footnote_ids"] == []
    assert "fn0001" in _job(manifest, 7)["unit_ids"]
    assert "fn0001" not in _job(manifest, 8)["unit_ids"]


def test_a_footnote_two_pages_refer_to_is_still_translated_once(tmp_path):
    book = _book([
        (1, "paragraph", {"text": "First mention of the custom [[fn:fn0001]] here."}),
        (2, "pagebreak", {"soft": True}),
        (2, "paragraph", {"text": "It comes up again [[fn:fn0001]] later on."}),
    ])
    book["footnotes"] = [
        ir.make_footnote(1, anchor_block="b00001", text="1. A note about the custom.")
    ]
    manifest, _ = _built(book, tmp_path)

    emitted = [entry["id"] for entry in manifest["chunks"]
               if "fn0001" in entry["unit_ids"]]
    assert emitted == ["page0001"]
    assert _job(manifest, 2)["footnote_ids"] == []


def test_a_neighbour_is_never_a_second_owner(tmp_path):
    """Context is read-only: nothing that appears as a neighbour is also a unit
    on the page that was shown it."""
    book = _book([_prose(page, f"Page{page}") for page in range(1, 7)])
    manifest, pages = _built(book, tmp_path)

    for entry in manifest["chunks"]:
        neighbours = [other["block_ids"] for other in manifest["chunks"]
                      if abs(other["page"] - entry["page"]) == 1]
        borrowed = {block_id for ids in neighbours for block_id in ids}
        assert not borrowed & set(entry["block_ids"])
        translate = _translate_section(_worksheet(pages, entry["page"]))
        for other in neighbours:
            for block_id in other:
                assert f"@@ {block_id} " not in translate


# --------------------------------------------------------------------------- #
# Scale
# --------------------------------------------------------------------------- #

def test_a_long_book_becomes_one_job_per_page_not_a_few_giant_ones(tmp_path):
    """512 pages must produce 512 independent jobs, each small enough to be a
    single translation task."""
    total = 512
    items: list[tuple[int, str, dict]] = []
    for page in range(1, total + 1):
        items.append((page, "pagebreak", {"soft": True}))
        items += [_prose(page, f"P{page}n{n}", sentences=4) for n in range(3)]

    manifest, pages = _built(_book(items), tmp_path, budget=pagerun.DEFAULT_BUDGET)

    assert manifest["pages"] == total
    assert len(manifest["chunks"]) == total
    assert manifest["over_budget"] == []
    assert [entry["page"] for entry in manifest["chunks"]] == list(range(1, total + 1))

    payloads = [entry["payload_chars"] for entry in manifest["chunks"]]
    assert max(payloads) < pagerun.DEFAULT_BUDGET
    book_chars = sum(entry["source_chars"] for entry in manifest["chunks"])
    assert max(payloads) < book_chars / 100, "a job is carrying the whole book"
    assert (pages / f"page{total:04d}.md").exists()


# --------------------------------------------------------------------------- #
# Resume
# --------------------------------------------------------------------------- #

def _mark(tmp_path: Path, page: int, state: str, **kwargs) -> None:
    runstate.RunState(tmp_path).set_page(page, state, **kwargs)


def test_the_next_page_is_the_first_one_not_accepted(tmp_path):
    book = _book([_prose(page, f"Page{page}") for page in range(1, 6)])
    _, pages = _built(book, tmp_path)

    assert pagerun.status(pages)["next"] == 1
    for page in (1, 2, 3):
        _mark(tmp_path, page, "accepted")
    _mark(tmp_path, 4, "failed", error="render QA found a missing picture")

    progress = pagerun.status(pages)
    assert progress["next"] == 4
    assert progress["accepted"] == 3
    assert progress["failed"] == [4]
    assert progress["by_state"] == {"accepted": 3, "failed": 1, "pending": 1}
    assert pagerun.next_page(pages)["attempts"] == 1
    assert "missing picture" in pagerun.next_page(pages)["last_error"]


def test_resuming_after_a_failure_re_runs_only_the_page_that_failed(tmp_path):
    book = _book([_prose(page, f"Page{page}") for page in range(1, 6)])
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)

    for page in (1, 2, 3):
        _mark(tmp_path, page, "accepted")
    _mark(tmp_path, 4, "failed", error="the page came out blank")

    # Rebuilding an unchanged book disturbs nothing.
    assert pagerun.build(book_path, pages)["invalidated"] == []
    assert pagerun.status(pages)["by_state"] == {"accepted": 3, "failed": 1,
                                                 "pending": 1}

    # Correcting page 4 invalidates page 4 and nothing else — not the pages
    # whose only change is what they now see as neighbouring context.
    corrected = ir.load_book(book_path)
    corrected["blocks"][3]["text"] += " A sentence the scan had swallowed."
    ir.save_book(corrected, book_path)

    assert pagerun.build(book_path, pages)["invalidated"] == [4]
    after = pagerun.status(pages)
    assert after["next"] == 4
    assert after["by_state"] == {"accepted": 3, "pending": 2}
    assert runstate.RunState(tmp_path).page(4)["attempts"] == 0
    assert runstate.RunState(tmp_path).page(1)["state"] == "accepted"


def test_an_accepted_page_keeps_its_answer_when_the_run_is_rebuilt(tmp_path):
    book = _book([_prose(page, f"Page{page}") for page in range(1, 4)])
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"
    manifest = pagerun.build(book_path, pages)

    answer = pages / _job(manifest, 1)["output"]
    ir.write_text(answer, "@@ b00001 para\nمتن فارسی\n")
    _mark(tmp_path, 1, "accepted")

    pagerun.build(book_path, pages)
    assert answer.read_text(encoding="utf-8").strip().endswith("فارسی")
    assert pagerun.status(pages)["pages"][0]["answered"] is True


def test_every_page_accepted_leaves_nothing_to_do(tmp_path):
    book = _book([_prose(page, f"Page{page}") for page in range(1, 4)])
    _, pages = _built(book, tmp_path)
    for page in (1, 2, 3):
        _mark(tmp_path, page, "accepted")

    assert pagerun.status(pages)["next"] is None
    assert pagerun.next_page(pages) is None


# --------------------------------------------------------------------------- #
# Odd shapes
# --------------------------------------------------------------------------- #

def test_a_book_with_no_pages_at_all_is_still_one_job(tmp_path):
    """EPUB and DOCX carry no page numbers; the run must not fall apart."""
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", index, text=f"Paragraph {index} of prose.")
        for index in range(1, 4)
    ]
    manifest, _ = _built(book, tmp_path)
    assert manifest["pages"] == 1
    assert len(_job(manifest, 1)["block_ids"]) == 3


def test_pages_that_are_not_contiguous_keep_their_own_numbers(tmp_path):
    book = _book([_prose(1, "One"), _prose(9, "Nine"), _prose(40, "Forty")])
    manifest, _ = _built(book, tmp_path)
    assert [entry["page"] for entry in manifest["chunks"]] == [1, 9, 40]
    assert _job(manifest, 40)["file"] == "page0040.md"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def test_the_cli_builds_reports_and_selects_the_next_page(tmp_path, capsys):
    book = _book([_prose(page, f"Page{page}") for page in range(1, 4)])
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"

    assert pagerun.main(["build", "--book", str(book_path), "--out", str(pages)]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["pages"] == 3 and built["over_budget"] == []

    assert pagerun.main(["status", "--pages", str(pages)]) == 0
    progress = json.loads(capsys.readouterr().out)
    assert progress["next"] == 1 and progress["accepted"] == 0

    _mark(tmp_path, 1, "accepted")
    assert pagerun.main(["next", "--pages", str(pages)]) == 0
    upcoming = json.loads(capsys.readouterr().out)
    assert upcoming["page"] == 2 and upcoming["remaining"] == 2
    assert Path(upcoming["worksheet"]).exists()


def test_the_worksheets_a_page_run_writes_can_be_merged(tmp_path):
    """A page job is a chunk of exactly one page, so merge reads it unchanged."""
    import merge as merging

    book = _book([_prose(page, f"Page{page}") for page in range(1, 4)])
    book_path = _save(book, tmp_path)
    manifest = pagerun.build(book_path, tmp_path / "pages")

    for entry in manifest["chunks"]:
        ir.write_text(tmp_path / "pages" / entry["output"],
                      "\n".join(f"@@ {unit} para\nمتن آزمون\n"
                                for unit in entry["unit_ids"]))

    report = merging.merge(book_path, tmp_path / "pages")
    assert report["ok"], report
    assert report["chunks_merged"] == 3
    assert all(block.get("target") for block in ir.iter_text_blocks(ir.load_book(book_path)))


# --------------------------------------------------------------------------- #
# Against a real PDF
# --------------------------------------------------------------------------- #

def test_a_real_pdf_page_run_owns_the_split_paragraph_once(tmp_path, sample_pdf):
    """The generated fixture has a paragraph that runs from page one to two."""
    pytest.importorskip("pymupdf")
    from read_pdf import read_pdf

    book = read_pdf(str(sample_pdf), tmp_path / "assets")
    manifest, pages = _built(book, tmp_path)

    owned = [block_id for entry in manifest["chunks"] for block_id in entry["block_ids"]]
    assert len(owned) == len(set(owned)) == len(book["blocks"])

    spanning = [block for block in ir.iter_text_blocks(book)
                if "betrayed what she had thought" in (block.get("text") or "")]
    assert len(spanning) == 1, "the fixture no longer splits a paragraph"
    block = spanning[0]

    home = [entry for entry in manifest["chunks"] if block["id"] in entry["unit_ids"]]
    assert len(home) == 1
    assert home[0]["page"] == block["page"]

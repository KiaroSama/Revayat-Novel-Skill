"""One source owner per unit, complete reversible segmentation and request identity."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import bookir as ir
import pagerun
from tests_support import reply_text
from read_pdf import _merge_split_paragraphs
from page_fixtures import TARGET, _book, _built, _job, _mark, _prose, _save, _translate_section, _worksheet

def test_every_block_is_owned_by_exactly_one_page(tmp_path):
    book = _book([
        _prose(1, "One"), (1, "image", {"asset": "a.png", "alt": "A picture"}),
        _prose(2, "Two"), _prose(2, "Also two"),
        _prose(3, "Three"),
    ])
    manifest, _ = _built(book, tmp_path)

    # By page, not by job: a split page's sub-jobs all describe the same page,
    # so ``block_ids`` repeats across them by design. Ownership is a claim
    # about pages.
    by_page = {entry["page"]: entry["block_ids"] for entry in manifest["chunks"]}
    owned = [block_id for ids in by_page.values() for block_id in ids]
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
    assert manifest["split"] == []
    assert [entry["page"] for entry in manifest["chunks"]] == list(range(1, total + 1))

    payloads = [entry["payload_chars"] for entry in manifest["chunks"]]
    assert max(payloads) < pagerun.DEFAULT_BUDGET
    book_chars = sum(entry["source_chars"] for entry in manifest["chunks"])
    assert max(payloads) < book_chars / 100, "a job is carrying the whole book"
    assert (pages / f"page{total:04d}.md").exists()


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


def test_the_cli_builds_reports_and_selects_the_next_page(tmp_path, capsys):
    book = _book([_prose(page, f"Page{page}") for page in range(1, 4)])
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"

    assert pagerun.main(["build", "--book", str(book_path), "--out", str(pages)]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["pages"] == 3 and built["split"] == []

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
        ir.write_text(tmp_path / "pages" / entry["output"], reply_text(
            tmp_path / "pages" / entry["file"],
            "\n".join(f"@@ {unit} para\nمتن آزمون\n"
                      for unit in entry["unit_ids"])))

    report = merging.merge(book_path, tmp_path / "pages")
    assert report["ok"], report
    assert report["chunks_merged"] == 3
    assert all(block.get("target") for block in ir.iter_text_blocks(ir.load_book(book_path)))


def test_a_real_pdf_page_run_owns_the_split_paragraph_once(tmp_path, sample_pdf):
    """The generated fixture has a paragraph that runs from page one to two."""
    pytest.importorskip("pymupdf")
    from read_pdf import read_pdf

    book = read_pdf(str(sample_pdf), tmp_path / "assets")
    manifest, pages = _built(book, tmp_path)

    by_page = {entry["page"]: entry["block_ids"] for entry in manifest["chunks"]}
    owned = [block_id for ids in by_page.values() for block_id in ids]
    assert len(owned) == len(set(owned)) == len(book["blocks"])

    spanning = [block for block in ir.iter_text_blocks(book)
                if "betrayed what she had thought" in (block.get("text") or "")]
    assert len(spanning) == 1, "the fixture no longer splits a paragraph"
    block = spanning[0]

    home = [entry for entry in manifest["chunks"] if block["id"] in entry["unit_ids"]]
    assert len(home) == 1
    assert home[0]["page"] == block["page"]


def test_a_single_oversized_paragraph_never_asks_for_a_bigger_budget(tmp_path):
    """The refusal this replaces was correct and useless.

    Raising `--budget` to fit the one paragraph that overflowed raises it for
    every job on the run — which is the context-limit failure the budget exists
    to prevent, arrived at by following the error message.
    """
    import merge as mg

    long_one = ("A very long paragraph that keeps going and going and shows no "
                "sign at all of stopping any time soon. ") * 60
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=1, text="A short opening line."),
        ir.make_block("paragraph", 2, page=1, text=long_one),
        ir.make_block("paragraph", 3, page=1, text="A short closing line."),
    ]
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"

    budget = 3000
    manifest = pagerun.build(book_path, pages, budget=budget)

    assert manifest["pages"] == 1
    assert len(manifest["chunks"]) > 1, "the page was not split at all"
    for entry in manifest["chunks"]:
        worksheet = (pages / entry["file"]).read_text(encoding="utf-8")
        assert len(worksheet) <= budget, (
            f"{entry['file']} is {len(worksheet)} characters against a "
            f"{budget} budget — that is the payload a model would receive"
        )

    # Every unit answered verbatim, the way a translator would return it. Read
    # back with the real parser: a worksheet's own instructions mention
    # `@@ headers`, and a hand-rolled reader takes that line for a header.
    for entry in manifest["chunks"]:
        given = mg.parse_worksheet(
            (pages / entry["file"]).read_text(encoding="utf-8"))
        reply = "".join("@@ {0} para\n{1}\n".format(unit_id, given[unit_id])
                        for unit_id in entry["unit_ids"])
        ir.write_text(pages / entry["output"],
                      reply_text(pages / entry["file"], reply))

    merged = pagerun.merge_page(book_path, pages, 1)
    assert merged["ok"], merged

    after = ir.load_book(book_path)
    blocks = {block["id"]: block for block in after["blocks"]}
    assert len(after["blocks"]) == 3, "the book gained or lost a block"
    assert blocks["b00001"]["target"] == "A short opening line."
    assert blocks["b00003"]["target"] == "A short closing line."
    # Word for word, in order. The space the cut fell on is normalised to one:
    # a reply arrives stripped, so the original separator is not recoverable
    # and `segments.rejoin` says so rather than pretending.
    assert blocks["b00002"]["target"].split() == long_one.split(), (
        "the paragraph did not come back whole"
    )


def test_a_segmented_page_is_complete_once_every_part_is_answered(tmp_path):
    """`untranslated` must ask the book about the block, not about a segment.

    The book has never heard of `b00002#2`; asking it would report the unit
    missing from a page whose every word is translated, and `accept` would
    refuse for ever.
    """
    import merge as mg

    long_one = ("Another long paragraph, of the kind that runs past any "
                "sensible worksheet budget on its own. ") * 60
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=1, text=long_one)]
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"
    manifest = pagerun.build(book_path, pages, budget=3000)

    unit_ids = [u for entry in manifest["chunks"] for u in entry["unit_ids"]]
    assert any("#" in unit_id for unit_id in unit_ids), "nothing was segmented"
    assert pagerun.untranslated(ir.load_book(book_path), unit_ids) == ["b00001"]

    for entry in manifest["chunks"]:
        given = mg.parse_worksheet(
            (pages / entry["file"]).read_text(encoding="utf-8"))
        ir.write_text(pages / entry["output"], reply_text(
            pages / entry["file"],
            "".join("@@ {0} para\n{1}\n".format(u, given[u])
                    for u in entry["unit_ids"])))
    assert pagerun.merge_page(book_path, pages, 1)["ok"]

    assert pagerun.untranslated(ir.load_book(book_path), unit_ids) == [], (
        "a page whose every segment is answered still reads as untranslated"
    )


def test_every_page_job_states_the_request_its_answer_must_echo(tmp_path):
    """The page route binds an answer to a request, exactly as the chunk route does.

    `out_page0007.md` is as reusable a name as `out_chunk0002.md`: rebuild and an
    answer to the previous cut of that page sits where the new one expects it. The
    page digest answers "has the page moved"; only the echoed token answers "was
    this written before the rebuild".
    """
    import worksheet as ws

    book = _book([_prose(page, f"Page{page}") for page in range(1, 3)])
    book_path = _save(book, tmp_path)
    manifest = pagerun.build(book_path, tmp_path / "pages")

    assert manifest["chunks"], manifest
    for entry in manifest["chunks"]:
        assert entry.get("request", "").startswith("req1:"), entry
        sheet = (tmp_path / "pages" / entry["file"]).read_text(encoding="utf-8")
        assert ws.request_of(sheet) == entry["request"], (
            "the worksheet states a different token from the one recorded")


def test_a_page_answer_written_before_a_rebuild_is_refused(tmp_path):
    """The late-reply case, on the page route."""
    import merge as merging
    import worksheet as ws

    book = _book([_prose(1, "Page1")])
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"
    first = pagerun.build(book_path, pages)
    stale = first["chunks"][0]["request"]

    # The page's source text changes, so the rebuild asks a different question.
    book = ir.load_book(book_path)
    book["blocks"][0]["text"] = "Page1 now says something else entirely."
    ir.save_book(book, book_path)
    second = pagerun.build(book_path, pages)
    entry = second["chunks"][0]
    assert entry["request"] != stale

    ir.write_text(pages / entry["output"],
                  f"{ws.request_line(stale)}\n@@ b00001 para\n{TARGET}\n")
    report = merging.merge(book_path, pages, strict=True)

    assert report["ok"] is False, report
    named = " ".join(report["malformed"].get(entry["id"], []))
    assert stale in named, named

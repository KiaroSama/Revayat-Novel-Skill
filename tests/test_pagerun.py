"""One job per source page, and every block in exactly one of them.

The failure this whole stage exists to prevent is a block that goes out to be
translated twice — the paragraph a page break cut in half, the line of dialogue
that runs on, the footnote two pages both point at. Translating one twice
produces a book that passes every count-based gate and reads as if a character
said the same thing in two different ways.

Two more things a page run owes its reviewer are tested here: the single-page
PDF that is the *printed* page, copied rather than photographed, and a
lifecycle that moves because something happened rather than because somebody
wrote the record by hand.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bookir as ir
import pagecheck
import pagerun
import preview
import renderqa
import review
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
    assert manifest["split"] == []
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
    assert progress["by_state"] == {"accepted": 3, "extracted": 1, "failed": 1}
    assert pagerun.next_page(pages)["attempts"] == 1
    assert "missing picture" in pagerun.next_page(pages)["last_error"]


def test_building_a_page_is_itself_the_first_step_of_its_lifecycle(tmp_path):
    """Cutting the page out of the book is a thing that happened to it."""
    book = _book([_prose(page, f"Page{page}") for page in range(1, 4)])
    book_path = _save(book, tmp_path)
    assert runstate.RunState(tmp_path).pages() == {}

    manifest = pagerun.build(book_path, tmp_path / "pages")
    assert pagerun.status(tmp_path / "pages")["by_state"] == {"extracted": 3}
    assert [entry["status"] for entry in manifest["chunks"]] == ["extracted"] * 3


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
    assert pagerun.status(pages)["by_state"] == {"accepted": 3, "extracted": 1,
                                                 "failed": 1}

    # Correcting page 4 invalidates page 4 and nothing else — not the pages
    # whose only change is what they now see as neighbouring context.
    corrected = ir.load_book(book_path)
    corrected["blocks"][3]["text"] += " A sentence the scan had swallowed."
    ir.save_book(corrected, book_path)

    assert pagerun.build(book_path, pages)["invalidated"] == [4]
    after = pagerun.status(pages)
    assert after["next"] == 4
    assert after["by_state"] == {"accepted": 3, "extracted": 2}
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
        ir.write_text(tmp_path / "pages" / entry["output"],
                      "\n".join(f"@@ {unit} para\nمتن آزمون\n"
                                for unit in entry["unit_ids"]))

    report = merging.merge(book_path, tmp_path / "pages")
    assert report["ok"], report
    assert report["chunks_merged"] == 3
    assert all(block.get("target") for block in ir.iter_text_blocks(ir.load_book(book_path)))


# --------------------------------------------------------------------------- #
# One real PDF per source page
# --------------------------------------------------------------------------- #

def _odd_pdf(path: Path, png: Path) -> Path:
    """Three pages nothing about is standard: a rotation, two odd trims, a
    picture. The point of splitting by copy rather than by camera is that all
    of it survives."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()

    first = doc.new_page(width=396, height=612)          # not A4
    first.insert_text((40, 60), "Page one of the source.", fontsize=11)
    first.insert_image(pymupdf.Rect(40, 100, 220, 220), filename=str(png))

    second = doc.new_page(width=842, height=595)         # landscape, then turned
    second.insert_text((40, 60), "Page two of the source.", fontsize=11)
    second.set_rotation(90)

    third = doc.new_page(width=200, height=800)          # a tall narrow trim
    third.insert_text((20, 60), "Page three of the source.", fontsize=11)

    doc.save(str(path))
    doc.close()
    return path


def _book_from_pdf(pdf: Path, pages: int) -> dict:
    """A book that says, as an extraction does, which PDF it was read from."""
    book = ir.new_book(source_path=str(pdf), source_format="pdf",
                       source_sha256=ir.sha256_file(pdf), pages=pages)
    book["blocks"] = [
        ir.make_block("paragraph", number, page=number,
                      text=f"Page {number} of the source.")
        for number in range(1, pages + 1)
    ]
    return book


def test_each_source_page_becomes_a_pdf_of_exactly_that_one_page(tmp_path, sample_png):
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    pymupdf = pytest.importorskip("pymupdf")
    manifest, pages = _built(_book_from_pdf(pdf, 3), tmp_path)

    for number in (1, 2, 3):
        entry = _job(manifest, number)
        assert entry["source_pdf"] == f"source/page-{number:04d}.pdf"
        assert entry["source_pdf_page"] == number
        split = pages / entry["source_pdf"]
        with pymupdf.open(str(split)) as one:
            assert one.page_count == 1


def test_the_split_page_is_the_original_page_and_not_a_picture_of_it(
        tmp_path, sample_png):
    """Copied with its resources: the text layer and the embedded image stream
    both survive, which a rasterised split would destroy."""
    pymupdf = pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    manifest, pages = _built(_book_from_pdf(pdf, 3), tmp_path)

    with pymupdf.open(str(pdf)) as source:
        for number in (1, 2, 3):
            with pymupdf.open(str(pages / _job(manifest, number)["source_pdf"])) as one:
                original, split = source[number - 1], one[0]
                assert split.get_text() == original.get_text()
                assert len(split.get_images(full=True)) == \
                    len(original.get_images(full=True))
    # …and the picture really was there to be kept.
    with pymupdf.open(str(pages / _job(manifest, 1)["source_pdf"])) as one:
        assert one[0].get_images(full=True)


def test_rotation_and_page_box_survive_the_split(tmp_path, sample_png):
    pymupdf = pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    manifest, pages = _built(_book_from_pdf(pdf, 3), tmp_path)

    with pymupdf.open(str(pdf)) as source:
        for number in (1, 2, 3):
            with pymupdf.open(str(pages / _job(manifest, number)["source_pdf"])) as one:
                original, split = source[number - 1], one[0]
                assert split.rotation == original.rotation
                assert tuple(split.mediabox) == tuple(original.mediabox)
                assert tuple(split.cropbox) == tuple(original.cropbox)
                assert tuple(split.rect) == tuple(original.rect)

    # The rotated page is the one that would silently come back upright, and
    # the narrow one the one that would come back A4.
    with pymupdf.open(str(pages / _job(manifest, 2)["source_pdf"])) as turned:
        assert turned[0].rotation == 90
    with pymupdf.open(str(pages / _job(manifest, 3)["source_pdf"])) as narrow:
        assert (narrow[0].rect.width, narrow[0].rect.height) == (200, 800)


def test_the_manifest_hash_is_the_hash_of_the_file_on_disk(tmp_path, sample_png):
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    manifest, pages = _built(_book_from_pdf(pdf, 3), tmp_path)

    for number in (1, 2, 3):
        entry = _job(manifest, number)
        assert entry["source_pdf_sha256"] == \
            ir.sha256_file(pages / entry["source_pdf"])
    hashes = {_job(manifest, n)["source_pdf_sha256"] for n in (1, 2, 3)}
    assert len(hashes) == 3, "three different pages hashed the same"


def test_a_stranger_in_the_page_pdf_directory_stops_the_run_by_name(
        tmp_path, sample_png):
    """Another stage packages this tree. Nothing here may be overwritten, and
    nothing that is not ours may be swept up with it."""
    pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    book_path = _save(_book_from_pdf(pdf, 3), tmp_path)
    pages = tmp_path / "pages"

    pagerun.build(book_path, pages)
    theirs = pages / "source" / "package.manifest"
    theirs.write_text("someone else's work", encoding="utf-8")

    with pytest.raises(pagerun.SourceCollision) as refusal:
        pagerun.build(book_path, pages)
    assert "package.manifest" in str(refusal.value)
    assert theirs.read_text(encoding="utf-8") == "someone else's work"

    theirs.unlink()
    assert pagerun.build(book_path, pages)["chunks"], "the refusal was not the end"


def test_the_next_job_names_its_own_page_and_the_whole_book_separately(
        tmp_path, sample_png):
    """Two different files, and handing render QA the wrong one renders the
    wrong page: it indexes ``--source-pdf`` by page number."""
    pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    _, pages = _built(_book_from_pdf(pdf, 3), tmp_path)

    upcoming = pagerun.next_page(pages)
    assert Path(upcoming["page_pdf"]) == pages / "source" / "page-0001.pdf"
    assert Path(upcoming["reference_pdf"]) == pdf
    assert Path(upcoming["page_pdf"]).exists()


def test_a_book_with_no_pdf_behind_it_simply_has_no_page_pdfs(tmp_path):
    """EPUB and DOCX have no printed page to compare against, and saying so is
    the answer — not a path to a file nobody wrote."""
    manifest, pages = _built(_book([_prose(1, "One"), _prose(2, "Two")]), tmp_path)
    assert manifest["reference_pdf"] == ""
    assert all(entry["source_pdf"] == "" for entry in manifest["chunks"])
    assert not (pages / "source").exists()


# --------------------------------------------------------------------------- #
# The reference PDF is recorded, never assumed
# --------------------------------------------------------------------------- #

def test_the_manifest_names_the_pdf_the_book_was_actually_read_from(
        tmp_path, sample_pdf):
    """A born-digital book has no ``ocr.pdf`` at all, so a hardcoded name is
    wrong for it; a mixed one has two candidates, so a name is a coin toss."""
    pytest.importorskip("pymupdf")
    from read_pdf import read_pdf

    book = read_pdf(str(sample_pdf), tmp_path / "assets")
    manifest, _ = _built(book, tmp_path)
    assert Path(manifest["reference_pdf"]) == sample_pdf
    assert pagerun.status(tmp_path / "pages")["reference_pdf"] == str(sample_pdf)


def test_the_ocr_copy_is_the_reference_when_that_is_what_was_read(
        tmp_path, sample_png):
    """The mixed case: an original beside an OCR-normalised copy. Whichever the
    extractor opened is the one the manifest has to name."""
    pytest.importorskip("pymupdf")
    original = _odd_pdf(tmp_path / "original.pdf", sample_png)
    normalised = _odd_pdf(tmp_path / "ocr.pdf", sample_png)

    manifest, _ = _built(_book_from_pdf(normalised, 3), tmp_path)
    assert Path(manifest["reference_pdf"]) == normalised
    assert Path(manifest["reference_pdf"]) != original


def test_the_reference_is_found_again_from_another_directory(
        tmp_path, sample_png, monkeypatch):
    """The path is stored as it was typed; a resume from elsewhere must not
    decide the book has no source."""
    pytest.importorskip("pymupdf")
    _odd_pdf(tmp_path / "source.pdf", sample_png)
    monkeypatch.chdir(tmp_path)
    book = _book_from_pdf(Path("source.pdf"), 3)
    ir.save_book(book, tmp_path / "book.json")

    monkeypatch.chdir(tmp_path.parent)
    manifest = pagerun.build(tmp_path / "book.json", tmp_path / "pages")
    assert Path(manifest["reference_pdf"]) == tmp_path / "source.pdf"
    assert _job(manifest, 1)["source_pdf_sha256"]


# --------------------------------------------------------------------------- #
# The lifecycle, walked by the operations
# --------------------------------------------------------------------------- #

TARGET = "Sample rendered line that must appear on the page."
SETUP = ir.default_page_setup()


def _one_page_book(tmp_path: Path) -> Path:
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=1,
                                    text="A line of source prose.")]
    return _save(book, tmp_path)


def _rendered(path: Path, text: str) -> Path:
    """The built document, laid out — what render QA is handed to look at."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page(width=SETUP["width_pt"], height=SETUP["height_pt"])
    page.insert_text((100, 150), text, fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


def _reviewed(work_dir, page: int = 1) -> dict:
    """File a clean reviewer verdict, the way a reviewer with eyes would.

    Every question answered explicitly: `review.record` refuses a partial
    answer sheet, so a test cannot accidentally pass a page by leaving one out.
    """
    filed = review.record(work_dir, page,
                          dict.fromkeys(review.QUESTIONS, True))
    assert filed["ok"], filed
    return filed


def test_a_page_walks_from_pending_to_accepted_through_the_operations(tmp_path):
    """Every step here is a production entry point; nothing writes the record.

    This is the whole claim of the page run: ``status`` and ``next`` are right
    because the operations moved the page, not because a caller remembered to
    say so afterwards.
    """
    pytest.importorskip("pymupdf")
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"

    assert runstate.RunState(tmp_path).page(1) is None
    manifest = pagerun.build(book_path, pages)
    assert pagerun.status(pages)["pages"][0]["state"] == "extracted"

    # The translator answers the worksheet it was given.
    upcoming = pagerun.next_page(pages)
    assert upcoming["page"] == 1
    ir.write_text(Path(upcoming["output"]), f"@@ b00001 para\n{TARGET}\n")

    merged = pagerun.merge_page(book_path, pages, 1)
    assert merged["ok"], merged
    assert pagerun.status(pages)["pages"][0]["state"] == "merged"

    # Not accepted yet: nobody has looked at the page.
    too_soon = pagerun.accept(book_path, pages, 1)
    assert too_soon["ok"] is False and too_soon["refused"] == "not-qa-passed"

    written = renderqa.check(tmp_path, book_path, 1,
                             target_pdf=_rendered(tmp_path / "target.pdf", TARGET))
    assert written["ok"] is True, written
    assert pagerun.status(pages)["pages"][0]["state"] == "qa_passed"

    # Still not accepted: the checks are geometric, and nobody has looked yet.
    unseen = pagerun.accept(book_path, pages, 1)
    assert unseen["ok"] is False and unseen["refused"] == "not-reviewed"

    _reviewed(tmp_path)
    accepted = pagerun.accept(book_path, pages, 1)
    assert accepted["ok"] is True and accepted["state"] == "accepted"

    progress = pagerun.status(pages)
    assert progress["next"] is None and progress["accepted"] == 1
    assert pagerun.next_page(pages) is None
    assert manifest["pages"] == 1


def test_merging_a_page_nobody_translated_is_refused_not_recorded(tmp_path):
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)

    refused = pagerun.merge_page(book_path, pages, 1)
    assert refused["ok"] is False and refused["refused"] == "not-translated"
    assert runstate.RunState(tmp_path).page(1)["state"] == "extracted"


def test_a_split_page_merges_only_when_every_part_is_answered(tmp_path):
    book = _book([
        (1, "paragraph", {"text": "The first half of the page. " * 12}),
        (1, "paragraph", {"text": "The second half of the page. " * 12}),
    ])
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"
    manifest = pagerun.build(book_path, pages, budget=900)
    entries = pagerun.jobs_for(manifest, 1)
    assert len(entries) == 2, "the fixture did not actually split"

    ir.write_text(pages / entries[0]["output"], "@@ b00001 para\nمتن آزمون\n")
    half = pagerun.merge_page(book_path, pages, 1)
    assert half["ok"] is False and half["refused"] == "not-translated"
    assert entries[1]["id"] in half["detail"]
    assert not ir.load_book(book_path)["blocks"][0].get("target"), \
        "half a page was written into the book"

    ir.write_text(pages / entries[1]["output"], "@@ b00002 para\nمتن دیگر\n")
    assert pagerun.merge_page(book_path, pages, 1)["ok"]
    assert runstate.RunState(tmp_path).page(1)["state"] == "merged"


def test_a_page_render_qa_never_saw_cannot_be_accepted(tmp_path):
    """A gate nobody ran must never read as a gate that passed."""
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)
    ir.write_text(pages / "out_page0001.md", f"@@ b00001 para\n{TARGET}\n")
    assert pagerun.merge_page(book_path, pages, 1)["ok"]

    refused = pagerun.accept(book_path, pages, 1)
    assert refused["ok"] is False and refused["refused"] == "not-qa-passed"
    assert runstate.RunState(tmp_path).page(1)["state"] == "merged"
    assert pagerun.status(pages)["next"] == 1


def test_a_page_render_qa_failed_cannot_be_accepted(tmp_path):
    pytest.importorskip("pymupdf")
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)
    ir.write_text(pages / "out_page0001.md", f"@@ b00001 para\n{TARGET}\n")
    pagerun.merge_page(book_path, pages, 1)

    blank = _rendered(tmp_path / "target.pdf", "Something else entirely.")
    assert renderqa.check(tmp_path, book_path, 1, target_pdf=blank)["ok"] is False

    refused = pagerun.accept(book_path, pages, 1)
    assert refused["ok"] is False and refused["refused"] == "not-qa-passed"
    assert runstate.RunState(tmp_path).page(1)["state"] == "failed"


def test_a_page_the_book_holds_no_persian_for_cannot_be_accepted(tmp_path):
    """Render QA can pass on a page whose text was never merged; the book is
    the one that knows."""
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)
    runstate.RunState(tmp_path).set_page(1, "qa_passed")

    refused = pagerun.accept(book_path, pages, 1)
    assert refused["ok"] is False and refused["refused"] == "not-merged"
    assert "b00001" in refused["detail"]


def test_re_translating_an_accepted_page_takes_its_acceptance_away(tmp_path):
    """The old report passed, but it passed for text the book no longer holds."""
    pytest.importorskip("pymupdf")
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)
    ir.write_text(pages / "out_page0001.md", f"@@ b00001 para\n{TARGET}\n")
    pagerun.merge_page(book_path, pages, 1)
    renderqa.check(tmp_path, book_path, 1,
                   target_pdf=_rendered(tmp_path / "target.pdf", TARGET))
    _reviewed(tmp_path)
    assert pagerun.accept(book_path, pages, 1)["ok"]

    corrected = "A second attempt at the very same line of prose entirely."
    ir.write_text(pages / "out_page0001.md", f"@@ b00001 para\n{corrected}\n")
    assert pagerun.merge_page(book_path, pages, 1)["ok"]
    assert pagerun.status(pages)["next"] == 1
    assert pagerun.accept(book_path, pages, 1)["refused"] == "not-qa-passed"


def test_the_page_qa_report_is_looked_for_where_render_qa_files_it(tmp_path):
    """The one convention two modules have to agree on, asserted rather than
    hoped for — ``renderqa`` imports ``pagerun``, so it cannot be shared."""
    assert pagerun.qa_report_path(tmp_path, 12) == renderqa.report_path(tmp_path, 12)


def test_the_cli_merges_and_accepts_one_page(tmp_path, capsys):
    pytest.importorskip("pymupdf")
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)
    ir.write_text(pages / "out_page0001.md", f"@@ b00001 para\n{TARGET}\n")

    arguments = ["--book", str(book_path), "--pages", str(pages), "--page", "1"]
    assert pagerun.main(["merge", *arguments]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert pagerun.main(["accept", *arguments]) == 2
    assert json.loads(capsys.readouterr().out)["refused"] == "not-qa-passed"

    renderqa.check(tmp_path, book_path, 1,
                   target_pdf=_rendered(tmp_path / "target.pdf", TARGET))

    assert pagerun.main(["accept", *arguments]) == 2
    assert json.loads(capsys.readouterr().out)["refused"] == "not-reviewed"

    assert pagerun.main(["review", "--pages", str(pages), "--page", "1",
                         *[f"--answer={name}=yes" for name in review.QUESTIONS],
                         "--note", "looked at both renders"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert pagerun.main(["accept", *arguments]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "accepted"


# --------------------------------------------------------------------------- #
# Against a real PDF
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# One paragraph bigger than the whole budget
# --------------------------------------------------------------------------- #

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
        ir.write_text(pages / entry["output"], reply)

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
        ir.write_text(pages / entry["output"],
                      "".join("@@ {0} para\n{1}\n".format(u, given[u])
                              for u in entry["unit_ids"]))
    assert pagerun.merge_page(book_path, pages, 1)["ok"]

    assert pagerun.untranslated(ir.load_book(book_path), unit_ids) == [], (
        "a page whose every segment is answered still reads as untranslated"
    )


# --------------------------------------------------------------------------- #
# The documented workflow, executed in the documented order
# --------------------------------------------------------------------------- #

def test_the_documented_page_loop_runs_in_order_with_nothing_missing(tmp_path,
                                                                     capsys):
    """Every command finds what the one before it left, and refuses if run early.

    This is the failure the loop was rewritten for: `SKILL.md` used to put
    `accept` immediately after `merge`, before the QA and review it depends on,
    and told the reader to hand render-qa a `$WORK/book.docx` that no documented
    command ever produced. Following it literally could not work.
    """
    pytest.importorskip("pymupdf")
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    page = ["--page", "1"]
    at = ["--book", str(book_path), "--pages", str(pages)]

    assert pagerun.main(["build", "--book", str(book_path), "--out", str(pages)]) == 0
    capsys.readouterr()

    # 1 — next names the worksheet, and it is on disk.
    assert pagerun.main(["next", "--pages", str(pages)]) == 0
    upcoming = json.loads(capsys.readouterr().out)
    assert Path(upcoming["worksheet"]).exists(), (
        "next named a worksheet nobody wrote")

    # 2 — the translator answers it.
    ir.write_text(Path(upcoming["output"]), f"@@ b00001 para\n{TARGET}\n")

    # 3 — merge, before which accept must refuse.
    assert pagerun.main(["accept", *at, *page]) == 2
    assert json.loads(capsys.readouterr().out)["refused"] == "not-merged"
    assert pagerun.main(["merge", *at, *page]) == 0
    capsys.readouterr()

    # 4 — the preview, which is the artefact step 5 consumes.
    assert pagerun.main(["preview", *at, *page]) == 0
    made = json.loads(capsys.readouterr().out)
    preview_docx = Path(made["output"])
    assert preview_docx.exists(), "preview reported a file it did not write"

    # 5 — render QA, handed exactly that file.
    written = renderqa.check(tmp_path, book_path, 1, docx=preview_docx)
    if not written.get("verified"):
        pytest.skip(f"nothing here lays a document out: {written['unverified']}")
    assert written["ok"], written["findings"]
    for name in written["renders"]["target_sheets"]:
        assert (tmp_path / name).exists(), f"{name} was reported, not written"

    # 6 — the review, before which accept must still refuse.
    assert pagerun.main(["accept", *at, *page]) == 2
    assert json.loads(capsys.readouterr().out)["refused"] == "not-reviewed"
    assert pagerun.main(["review", "--pages", str(pages), *page,
                         *[f"--answer={name}=yes" for name in review.QUESTIONS]]) == 0
    capsys.readouterr()

    # 7 — and only now.
    assert pagerun.main(["accept", *at, *page]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "accepted"
    assert pagerun.status(pages)["next"] is None


def test_page_qa_is_unaffected_by_how_far_the_book_has_reflowed(tmp_path):
    """Source page 2 is checked as itself, not as the finished book's page 2.

    Page 1 here carries far more Persian than English, which is ordinary and is
    exactly what moves everything after it. Rendered as a whole book, source
    page 2's material would be several sheets further on; the old check looked
    at the book's second sheet and would have found page 1's overflow there —
    reporting page 2's blocks missing and page 1's as unexpected.
    """
    pytest.importorskip("pymupdf")
    # Varied on purpose: forty copies of one sentence would trip
    # `text-duplicated`, which would be the check working correctly on a fixture
    # that does not resemble prose.
    swollen = " ".join(
        f"بند شماره {n} به فارسی بسیار بلندتر از اصل "
        f"انگلیسی آن است و همین هر چه را که پس از آن می‌آید جابه‌جا می‌کند."
        for n in range(1, 41))
    own = "بند کوتاه صفحهٔ دوم که باید همان‌جا بماند و جابه‌جا نشود."

    book = ir.new_book()
    first = ir.make_block("paragraph", 1, page=1, text="A short English source line.")
    first["target"] = swollen
    second = ir.make_block("paragraph", 2, page=2, text="The second page's line.")
    second["target"] = own
    book["blocks"] = [first, second]
    book_path = _save(book, tmp_path)

    written = renderqa.check(tmp_path, book_path, 2)
    if not written.get("verified"):
        pytest.skip(f"nothing here lays a document out: {written['unverified']}")

    assert written["ok"], written["findings"]
    assert written["counts"]["expected_blocks"] == 1, (
        "page 2 owns one block; anything else means the preview carried page 1"
    )
    # And page 1, whose Persian runs long, is still judged on all of its sheets.
    first_page = renderqa.check(tmp_path, book_path, 1)
    assert first_page["ok"], first_page["findings"]
    assert first_page["sheets"] >= 1


# --------------------------------------------------------------------------- #
# A page's source is more than its prose
# --------------------------------------------------------------------------- #

def _mixed_pdf(path: Path) -> Path:
    """Three pages, three shapes: portrait, landscape, and a wider trim.

    A real book has these — a map, a plate, a differently trimmed front matter
    leaf — and every one of them is a page the run has to lay out on its own
    paper rather than on the book's average.
    """
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for width, height, text in ((396, 612, "The portrait page of ordinary prose."),
                                (612, 396, "The landscape plate and its caption."),
                                (468, 612, "A wider leaf, trimmed differently.")):
        page = doc.new_page(width=width, height=height)
        page.insert_text((60, 100), text, fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


def test_each_source_page_is_laid_out_on_its_own_paper(tmp_path):
    """`book["page"]` is the dominant shape and the wrong paper for the rest.

    A preview built from the dominant setup puts the landscape plate on a
    portrait sheet, and then render QA reports the page it just mis-built.
    """
    pytest.importorskip("pymupdf")
    from read_pdf import read_pdf

    book = read_pdf(str(_mixed_pdf(tmp_path / "mixed.pdf")), tmp_path / "assets")
    book_path = _save(book, tmp_path)
    lookup = ir.blocks_by_id(book)
    owners = {job["page"]: job for job in pagerun.owners(book)}

    shapes = {page: (round(g["width_pt"]), round(g["height_pt"]))
              for page, job in owners.items()
              for g in [pagerun.geometry(book, job["block_ids"], lookup, page)]}

    assert shapes[1] == (396, 612), shapes
    assert shapes[2] == (612, 396), (
        f"the landscape page came back as {shapes[2]} — it was given the "
        f"book's dominant portrait geometry"
    )
    assert shapes[3] == (468, 612), shapes

    # And the preview a reviewer actually looks at uses it.
    only = preview.page_book(book, 2)
    assert (round(only["page"]["width_pt"]), round(only["page"]["height_pt"])) \
        == (612, 396)


def test_a_new_illustration_invalidates_the_page_it_is_on(tmp_path):
    """Prose unchanged, picture replaced: the accepted page must not survive.

    This is the shape that made the gate worth widening. Every translatable
    word is identical, so a text-only digest reports nothing — and the page
    keeps an `accepted` state carrying a visual review of an image that is no
    longer in the book.
    """
    pymupdf = pytest.importorskip("pymupdf")
    from read_pdf import read_pdf

    def paint(path: Path, colour: tuple[float, float, float]) -> Path:
        doc = pymupdf.open()
        page = doc.new_page(width=396, height=612)
        page.insert_text((60, 100), "The prose that never changes at all.",
                         fontsize=11, fontname="helv")
        page.draw_rect(pymupdf.Rect(60, 200, 300, 400), color=colour, fill=colour)
        doc.save(str(path))
        doc.close()
        return path

    source = paint(tmp_path / "book.pdf", (1, 0, 0))
    book_path = _save(read_pdf(str(source), tmp_path / "assets"), tmp_path)
    pages = tmp_path / "pages"

    assert pagerun.build(book_path, pages)["invalidated"] == []
    assert pagerun.build(book_path, pages)["invalidated"] == [], (
        "an unchanged book invalidated itself on rebuild — every page of every "
        "run would be thrown away and re-translated"
    )
    _mark(tmp_path, 1, "accepted")

    paint(source, (0, 0, 1))          # same words, same file, new picture
    assert pagerun.build(book_path, pages)["invalidated"] == [1], (
        "the picture changed and the page was not invalidated — an accepted "
        "page still carries a review of an image the book no longer has"
    )


def test_a_page_fingerprint_is_reproducible(tmp_path):
    """The property the whole invalidation rests on.

    The obvious identity — the hash of the split one-page PDF — is not
    reproducible: PyMuPDF stamps what it writes, and splitting one unchanged
    source three times measured three different hashes. Keying a page on that
    invalidates the entire book on every rebuild.
    """
    pymupdf = pytest.importorskip("pymupdf")

    def make(path: Path, width: int = 396, colour=(1, 0, 0)) -> Path:
        doc = pymupdf.open()
        page = doc.new_page(width=width, height=612)
        page.insert_text((60, 100), "Identical prose.", fontsize=11, fontname="helv")
        page.draw_rect(pymupdf.Rect(60, 200, 300, 400), color=colour, fill=colour)
        doc.save(str(path))
        doc.close()
        return path

    same = pagerun.page_fingerprint(make(tmp_path / "a.pdf"), 1)
    assert same, "no fingerprint at all"
    assert pagerun.page_fingerprint(make(tmp_path / "b.pdf"), 1) == same, (
        "two identical sources fingerprinted differently — every rebuild would "
        "invalidate every page"
    )
    assert pagerun.page_fingerprint(make(tmp_path / "c.pdf", colour=(0, 0, 1)), 1)         != same, "a repainted plate did not change the fingerprint"
    assert pagerun.page_fingerprint(make(tmp_path / "d.pdf", width=612), 1) != same,         "a different trim did not change the fingerprint"


def test_the_documented_loop_previews_the_page_it_is_on_not_page_twelve(tmp_path):
    """Run the loop on a page that is not the example, and follow the artefact.

    The loop's example sets `P=12`. A `page-0012.docx` hard-coded beside `$P`
    reads as consistent and sends every other page's QA at page 12's preview —
    or at nothing, on a book with fewer pages. Only executing it on another page
    catches that; a parser sees a valid command line either way.
    """
    pytest.importorskip("pymupdf")
    book = _book([_prose(1, "First page"), _prose(2, "Second page")])
    for block in book["blocks"]:
        block["target"] = f"ترجمهٔ {block['id']} با طول کافی برای آزمون."
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)

    written = renderqa.check(tmp_path, book_path, 2)
    if not written.get("verified"):
        pytest.skip(f"nothing here lays a document out: {written['unverified']}")

    used = Path(written["preview"])
    assert used.name == "page-0002.docx", (
        f"page 2's QA consumed {used.name} — the preview belongs to another page"
    )
    assert used.exists()
    assert written["counts"]["expected_blocks"] == 1, (
        "page 2 owns one block; more means the preview carried page 1 as well"
    )
    # And the artefact it built is page 2's own, not a leftover.
    assert "b00002" in pagecheck.document_text(used) or \
        "ترجمهٔ b00002" in pagecheck.document_text(used), (
            "the preview does not contain page 2's translation"
        )


def test_a_crop_that_moves_changes_the_page_a_reader_sees(tmp_path):
    """The escape `page.rect` cannot see, because it normalises the origin.

    Two files, one content stream, one 400x600 rect, one rotation — and crop
    boxes at x=0 and x=50. `page.rect` reports `(0, 0, 400, 600)` for both,
    which is why hashing width and height alone let a visibly different page
    through. Measured before the fix: identical fingerprints, different pixels.
    """
    pymupdf = pytest.importorskip("pymupdf")

    def make(path: Path, crop, media=(500, 600)) -> Path:
        doc = pymupdf.open()
        page = doc.new_page(width=media[0], height=media[1])
        page.insert_text((60, 100), "Identical prose.", fontsize=11, fontname="helv")
        page.set_cropbox(pymupdf.Rect(*crop))
        doc.save(str(path))
        doc.close()
        return path

    left = make(tmp_path / "left.pdf", (0, 0, 400, 600))
    moved = make(tmp_path / "moved.pdf", (50, 0, 450, 600))

    def seen(path: Path) -> bytes:
        document = pymupdf.open(str(path))
        try:
            return document[0].get_pixmap(dpi=72).samples
        finally:
            document.close()

    with pymupdf.open(str(left)) as a, pymupdf.open(str(moved)) as b:
        assert a[0].rect.width == b[0].rect.width
        assert a[0].rect.height == b[0].rect.height
        assert a[0].rotation == b[0].rotation

    assert seen(left) != seen(moved), (
        "the fixture is wrong: these two are supposed to render differently"
    )
    assert pagerun.page_fingerprint(left, 1) != pagerun.page_fingerprint(moved, 1), (
        "a crop box moved across the media left the fingerprint unchanged — an "
        "accepted page can now change visibly and stay accepted"
    )

    # A media box widened under an unchanged crop is the other half of the pair.
    wider = make(tmp_path / "wider.pdf", (0, 0, 400, 600), media=(700, 600))
    assert pagerun.page_fingerprint(wider, 1) != pagerun.page_fingerprint(left, 1), (
        "a changed media box left the fingerprint unchanged"
    )


def test_an_annotation_drawn_over_a_page_is_part_of_it(tmp_path):
    """Annotations are not in the content stream, and they are on the paper.

    Found while proving the crop-box fix, by asking what else renders without
    touching a stream. A highlight left on a scan measured the same stream hash
    and different pixels — the same escape, through another door.
    """
    pymupdf = pytest.importorskip("pymupdf")

    def make(path: Path, *, highlight: bool) -> Path:
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=600)
        page.insert_text((60, 100), "Identical prose.", fontsize=11, fontname="helv")
        if highlight:
            page.add_highlight_annot(pymupdf.Rect(55, 85, 300, 108))
        doc.save(str(path))
        doc.close()
        return path

    plain = make(tmp_path / "plain.pdf", highlight=False)
    noted = make(tmp_path / "noted.pdf", highlight=True)

    def streams(path: Path) -> bytes:
        document = pymupdf.open(str(path))
        try:
            page = document[0]
            return b"".join(document.xref_stream(x) or b""
                            for x in page.get_contents())
        finally:
            document.close()

    assert streams(plain) == streams(noted), (
        "the fixture is wrong: the highlight was supposed to leave the content "
        "stream alone, which is the whole point of the test"
    )
    assert pagerun.page_fingerprint(plain, 1) != pagerun.page_fingerprint(noted, 1), (
        "an annotation drawn over the page left the fingerprint unchanged"
    )


def test_a_moved_crop_invalidates_its_own_page_and_no_other(tmp_path):
    """Local damage stays local: the point of a per-page identity.

    A source identity that changes for every page whenever one page changes is
    the failure this whole design exists to avoid — it throws away a resumable
    run's progress on every rebuild.
    """
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "book.pdf"

    def make(second_crop) -> Path:
        doc = pymupdf.open()
        for index, crop in enumerate((None, second_crop, None)):
            page = doc.new_page(width=500, height=600)
            page.insert_text((60, 100), f"Page {index + 1}.", fontsize=11,
                             fontname="helv")
            page.set_cropbox(pymupdf.Rect(*(crop or (0, 0, 400, 600))))
        if path.exists():
            path.unlink()
        doc.save(str(path))
        doc.close()
        return path

    make(None)
    before = [pagerun.page_fingerprint(path, n) for n in (1, 2, 3)]
    make((50, 0, 450, 600))
    after = [pagerun.page_fingerprint(path, n) for n in (1, 2, 3)]

    assert after[1] != before[1], "the page whose crop moved was not invalidated"
    assert [after[0], after[2]] == [before[0], before[2]], (
        f"a change to page 2 invalidated its neighbours too: {before} -> {after}"
    )


def _translated_pdf_run(tmp_path: Path, png: Path) -> tuple[Path, Path, Path]:
    """A real PDF book, built into pages, with every page's Persian in place."""
    pdf = _odd_pdf(tmp_path / "source.pdf", png)
    book = _book_from_pdf(pdf, 3)
    for block in book["blocks"]:
        block["target"] = f"ترجمهٔ {block['id']} با طول کافی برای آزمون."
    book_path = _save(book, tmp_path)
    pagerun.build(book_path, tmp_path / "pages")
    return pdf, book_path, tmp_path / "pages"


def test_render_qa_finds_the_source_page_without_being_told_where_it_is(
        tmp_path, sample_png):
    """The README route: `render-qa --book --work --page N` and nothing else.

    Both READMEs tell the reader to run exactly that and then compare
    `renders/source/page-0001.png` with the target — while `--source-pdf` was
    optional and omitted, so the source PNG the instruction names was never
    written. The manifest has known where that page lives all along.
    """
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)

    written = renderqa.check(tmp_path, book_path, 2)

    source = written.get("renders", {}).get("source", "")
    assert source, (
        f"no source render was produced for the documented command: {written}")
    assert (tmp_path / source).exists(), f"{source} was named but not written"
    assert written.get("source_evidence") == source, (
        "the source was rendered but the report did not name it; a gate reading "
        "this cannot tell a missing converter from a missing source page")
    assert pages.name == "pages"
    if written.get("verified"):
        assert written["renders"].get("target_sheets"), "no target sheets"


def test_a_source_page_that_went_missing_leaves_the_page_unverified(
        tmp_path, sample_png):
    """Losing one side of the comparison is "we could not look", never a pass."""
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)

    one_page = pages / _job(pagerun.load_manifest(pages), 2)["source_pdf"]
    assert one_page.exists()
    one_page.unlink()

    written = renderqa.check(tmp_path, book_path, 2)
    assert written["ok"] is False and written["verified"] is False, (
        f"a page with no source render came back verified: {written}")
    assert written.get("unverified"), "nothing said why it could not be checked"
    assert "source" in written["unverified"], written["unverified"]


def test_a_pdf_page_cannot_be_accepted_on_target_evidence_alone(
        tmp_path, sample_png):
    """The hole this closes, and the two halves of closing it.

    Reachable before because `--source-pdf` was optional: hand `check` a target
    PDF and no source, every deterministic gate reads the target so they all
    pass, the review is filed against target sheets only, and `accept` took it.
    The page reached `accepted` having never been set beside the page it was
    translated from.

    Both halves are asserted here. There is no longer a target-only route for a
    PDF page - handing over a target still renders the source, because the
    manifest is asked rather than the caller. And when the source genuinely
    cannot be produced, the report that results is refused by review and by
    accept rather than passed.
    """
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)

    built = preview.build(book_path, 2, tmp_path / "only-target.docx",
                          assets=tmp_path / "assets")
    if not built["ok"]:
        pytest.skip(f"no converter here: {built['detail']}")
    try:
        target = renderqa.render_docx(tmp_path / "only-target.docx",
                                      tmp_path / "renders" / "preview")
    except renderqa.RenderError as error:
        pytest.skip(f"nothing here can lay a document out: {error}")

    handed = renderqa.check(tmp_path, book_path, 2, target_pdf=target)
    assert handed.get("source_evidence"), (
        "handing over a target still has to render the source: the old route "
        "in was exactly this call with --source-pdf left off")
    assert pagerun.missing_source_render(pages, 2) is None

    # Now the case the gate is for: the source cannot be produced at all.
    (pages / _job(pagerun.load_manifest(pages), 2)["source_pdf"]).unlink()
    written = renderqa.check(tmp_path, book_path, 2, target_pdf=target)
    assert not written.get("source_evidence"), written

    refused = pagerun.missing_source_render(pages, 2)
    assert refused and refused["refused"] == "no-source-render", (
        f"a report with no source render was not refused: {refused}")

    review.record(tmp_path, 2, {name: True for name in review.QUESTIONS},
                  note="claims to have compared them")
    taken = pagerun.accept(book_path, pages, 2)
    assert taken["ok"] is False, f"accepted with no source render: {taken}"
    assert taken["refused"] in {"no-source-render", "not-qa-passed",
                                "render-qa-failed"}, taken


def test_the_page_loop_still_reaches_accepted_with_both_sides_present(
        tmp_path, sample_png):
    """The gate has to let the correct case through, or it is just an outage."""
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)

    written = renderqa.check(tmp_path, book_path, 1)
    if not written.get("verified"):
        pytest.skip(f"no converter here: {written.get('unverified')}")
    assert written["source_evidence"], "both sides were present; source missing"
    assert pagerun.missing_source_render(pages, 1) is None

    review.record(tmp_path, 1, {name: True for name in review.QUESTIONS},
                  note="looked at both")
    taken = pagerun.accept(book_path, pages, 1)
    if not written["ok"]:
        # The fixture's odd trims can fail a geometric check; the point here is
        # only that the source gate is not what stopped it.
        assert taken.get("refused") != "no-source-render", taken
    else:
        assert taken["ok"], f"a page with both sides was not accepted: {taken}"


# --------------------------------------------------------------------------- #
# Source identity: a PDF book cannot stop being one
# --------------------------------------------------------------------------- #

def test_a_pdf_whose_source_moved_is_refused_a_page_run(tmp_path, sample_png):
    """Losing the source file is not a new source format.

    Before this, `reference_pdf` returned None, `build` carried on, and the
    manifest came out with an empty `reference_pdf` and an empty `source_pdf`
    on every entry — which every later gate read as "a format that has no
    source pages", exactly what DOCX and EPUB look like. The book then walked
    all the way to `accepted` with nothing ever compared against it.
    """
    pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "source.pdf", sample_png)
    book_path = _save(_book_from_pdf(pdf, 3), tmp_path)
    pdf.unlink()

    with pytest.raises(pagerun.SourceUnavailable) as refused:
        pagerun.build(book_path, tmp_path / "pages")
    assert "PDF" in str(refused.value)
    assert not (tmp_path / "pages" / "manifest.json").exists(), (
        "a manifest was written for a run that cannot compare anything")


def test_a_non_pdf_book_still_needs_no_source_page(tmp_path):
    """The gate must not fire on a book that never had a source page."""
    book = _book([_prose(1, "First"), _prose(2, "Second")])
    book["source"]["format"] = "epub"
    book_path = _save(book, tmp_path)

    manifest = pagerun.build(book_path, tmp_path / "pages")
    assert manifest["source_format"] == "epub"
    assert manifest["reference_pdf"] == ""
    assert pagerun.needs_source_page(manifest) is False
    assert pagerun.missing_source_render(tmp_path / "pages", 1) is None


def test_the_source_requirement_survives_an_empty_path(tmp_path, sample_png):
    """`source_format` is the authority, not whether a path is filled in."""
    pytest.importorskip("pymupdf")
    _, _, pages = _translated_pdf_run(tmp_path, sample_png)

    manifest = pagerun.load_manifest(pages)
    assert manifest["source_format"] == "pdf"
    assert pagerun.needs_source_page(manifest) is True

    # Exactly the state the old code produced: a PDF run, every path blanked.
    hollow = json.loads(json.dumps(manifest))
    hollow["reference_pdf"] = ""
    for entry in hollow["chunks"]:
        entry["source_pdf"] = ""
        entry["source_pdf_sha256"] = ""
    assert pagerun.needs_source_page(hollow) is True, (
        "blanking the paths made the source requirement disappear")

    (pages / "manifest.json").write_text(
        json.dumps(hollow, ensure_ascii=False), encoding="utf-8")
    refused = pagerun.missing_source_render(pages, 1)
    assert refused and refused["refused"] in {"no-source-render", "no-render-qa"}


def test_an_older_manifest_with_no_source_format_is_still_read_as_pdf(
        tmp_path, sample_png):
    """A manifest written before the field existed must not lose the gate."""
    pytest.importorskip("pymupdf")
    _, _, pages = _translated_pdf_run(tmp_path, sample_png)
    manifest = pagerun.load_manifest(pages)
    del manifest["source_format"]
    assert pagerun.needs_source_page(manifest) is True, (
        "an older PDF manifest stopped requiring a source page")


def test_an_explicit_source_pdf_cannot_replace_the_manifest_artefact(
        tmp_path, sample_png):
    """Any readable PDF could stand in as a manifested page's evidence.

    The report then said a source had been rendered, and nothing downstream
    could tell it was a different book.
    """
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)
    impostor = _odd_pdf(tmp_path / "someone-elses.pdf", sample_png)

    origin = renderqa.source_evidence(tmp_path, pages, 1, impostor)
    assert origin.path is not None
    assert origin.path != impostor, "the override replaced the manifest's page"
    assert origin.path == pages / _job(pagerun.load_manifest(pages), 1)["source_pdf"]
    assert origin.sha256, "the manifest's recorded hash was not carried"


def test_replacing_the_source_artefact_is_caught_by_its_hash(tmp_path, sample_png):
    """A file at the right path is not the file the run committed to."""
    pymupdf = pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)
    artefact = pages / _job(pagerun.load_manifest(pages), 1)["source_pdf"]

    assert renderqa.source_evidence(tmp_path, pages, 1, None).problem == ""

    # A different, perfectly readable one-page PDF at exactly the right path.
    other = _odd_pdf(tmp_path / "other.pdf", sample_png)
    with pymupdf.open(str(other)) as book, pymupdf.open() as one:
        one.insert_pdf(book, from_page=1, to_page=1)
        artefact.unlink()
        one.save(str(artefact))

    tampered = renderqa.source_evidence(tmp_path, pages, 1, None)
    assert "source-hash-mismatch" in tampered.problem, tampered.problem
    assert tampered.path is None

    written = renderqa.check(tmp_path, book_path, 1)
    assert written["verified"] is False and written["ok"] is False
    assert "source-hash-mismatch" in written.get("unverified", "")
    assert not written.get("source_evidence")

    review.record(tmp_path, 1, dict.fromkeys(review.QUESTIONS, True),
                  note="claims to have compared them")
    refused = pagerun.missing_source_render(pages, 1)
    assert refused and refused["refused"] == "no-source-render"
    taken = pagerun.accept(book_path, pages, 1)
    assert taken["ok"] is False, f"accepted against a swapped source: {taken}"


def test_a_deleted_source_artefact_is_named_missing_not_mismatched(
        tmp_path, sample_png):
    """`source-missing` and `source-hash-mismatch` are different diagnoses."""
    pytest.importorskip("pymupdf")
    _, _, pages = _translated_pdf_run(tmp_path, sample_png)
    (pages / _job(pagerun.load_manifest(pages), 2)["source_pdf"]).unlink()

    gone = renderqa.source_evidence(tmp_path, pages, 2, None)
    assert "source-missing" in gone.problem, gone.problem
    assert gone.required is True


def test_rebuilding_restores_the_source_and_the_loop_works_again(
        tmp_path, sample_png):
    """A tamper must be recoverable, or the gate is a trap rather than a check."""
    pytest.importorskip("pymupdf")
    _, book_path, pages = _translated_pdf_run(tmp_path, sample_png)
    artefact = pages / _job(pagerun.load_manifest(pages), 1)["source_pdf"]
    artefact.write_bytes(b"%PDF-1.4 not really\n")

    assert "source-hash-mismatch" in renderqa.source_evidence(
        tmp_path, pages, 1, None).problem

    pagerun.build(book_path, pages)          # re-cut from the real source
    restored = renderqa.source_evidence(tmp_path, pages, 1, None)
    assert restored.problem == "", restored.problem
    assert restored.path is not None and restored.path.exists()


def test_a_run_with_no_manifest_still_honours_an_explicit_source(
        tmp_path, sample_png):
    """Synthetic fixtures and one-off diagnostics keep working."""
    pytest.importorskip("pymupdf")
    pdf = _odd_pdf(tmp_path / "loose.pdf", sample_png)
    origin = renderqa.source_evidence(tmp_path, tmp_path / "nowhere", 2, pdf)
    assert origin.path == pdf
    assert origin.index == 1, "a multi-page file still needs the page index"
    assert origin.required is False

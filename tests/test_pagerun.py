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
import pagerun
import renderqa
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

"""OCR confidence: the aggregation, the thresholds, and what reaches the book.

None of this needs Tesseract installed. What is being tested is the judgement
built on top of the engine — how word scores fold into a line, which lines are
called a guess, and whether a block in the book gets matched to the region it
was actually recognised from. Whether Tesseract reads Persian well is
Tesseract's problem; whether we correctly report how sure it was is ours.

The TSV fixtures are the real format, taken from `tesseract page.png stdout
-l fas tsv`: level 1 page, 2 block, 3 paragraph, 4 line, 5 word, boxes in
pixels, and -1 confidence on every container row.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bookir as ir
import ocr_sidecar as ocr

HEADER = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
          "\tleft\ttop\twidth\theight\tconf\ttext")


def tsv(*rows: tuple) -> str:
    """Build a TSV dump; container rows are filled in automatically."""
    lines = [HEADER, "1\t1\t0\t0\t0\t0\t0\t0\t2480\t3508\t-1\t"]
    seen_blocks: set[int] = set()
    seen_lines: set[tuple[int, int]] = set()
    for block, line, word, left, top, width, height, conf, text in rows:
        if block not in seen_blocks:
            seen_blocks.add(block)
            lines.append(f"2\t1\t{block}\t0\t0\t0\t{left}\t{top}\t900\t120\t-1\t")
            lines.append(f"3\t1\t{block}\t1\t0\t0\t{left}\t{top}\t900\t120\t-1\t")
        if (block, line) not in seen_lines:
            seen_lines.add((block, line))
            lines.append(f"4\t1\t{block}\t1\t{line}\t0\t{left}\t{top}\t900\t40\t-1\t")
        lines.append(
            f"5\t1\t{block}\t1\t{line}\t{word}\t{left}\t{top}\t{width}\t{height}"
            f"\t{conf:.6f}\t{text}"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Grading
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value,expected", [
    (99.0, "high"), (85.0, "high"), (84.9, "medium"),
    (60.0, "medium"), (59.9, "low"), (0.0, "low"), (None, "unknown"),
])
def test_grade_boundaries_are_inclusive_at_the_top(value, expected):
    assert ocr.grade(value) == expected


def test_thresholds_are_configurable():
    assert ocr.grade(70.0, high=95, low=80) == "low"
    assert ocr.grade(70.0, high=60, low=40) == "high"


def test_long_words_weigh_more_than_short_artefacts():
    """A one-character speck at 3% must not condemn a clean line.

    This is the difference between a check people keep on and one they mute:
    an unweighted mean of these two is 51%, which reads as a guess. The line
    is in fact eleven good characters and one piece of dirt.
    """
    plain = (98.0 + 4.0) / 2
    weighted = ocr._weighted([("recognised", 98.0), (".", 4.0)])
    assert ocr.grade(plain) == "low"
    assert ocr.grade(weighted) == "high"


def test_weighted_ignores_whitespace_only_samples():
    assert ocr._weighted([("  ", 5.0), ("real", 90.0)]) == pytest.approx(90.0)
    assert ocr._weighted([]) is None


# --------------------------------------------------------------------------- #
# Parsing and aggregation
# --------------------------------------------------------------------------- #

def test_tsv_rows_survive_bare_quotation_marks():
    """OCR output is full of stray quotes; csv must not treat them as quoting."""
    dump = tsv((1, 1, 1, 100, 100, 80, 30, 91.0, '"He'),
               (1, 1, 2, 190, 100, 80, 30, 88.0, 'said"'))
    words = [r for r in ocr.read_tsv(dump) if r["level"] == ocr.LEVEL_WORD]
    assert [w["text"] for w in words] == ['"He', 'said"']


def test_junk_rows_are_dropped_not_fatal():
    dump = HEADER + "\n5\t1\t1\t1\t1\t1\tx\ty\t1\t1\tz\toops\n" + \
        "5\t1\t1\t1\t1\t2\t10\t10\t20\t20\t90.0\tgood\n"
    rows = ocr.read_tsv(dump)
    assert [r["text"] for r in rows] == ["good"]


def test_boxes_are_converted_from_pixels_to_points():
    """At 300 DPI a 300-pixel box is 72 points wide, or nothing lines up."""
    page = ocr.build_page(
        tsv((1, 1, 1, 300, 600, 300, 150, 95.0, "word")), 1, dpi=300
    )
    left, top, right, bottom = page["blocks"][0]["lines"][0]["words"][0]["bbox"]
    assert (right - left) == pytest.approx(72.0)
    assert (bottom - top) == pytest.approx(36.0)
    assert (left, top) == pytest.approx((72.0, 144.0))


def test_words_fold_into_lines_and_lines_into_blocks():
    page = ocr.build_page(tsv(
        (1, 1, 1, 100, 100, 90, 30, 96.0, "clean"),
        (1, 1, 2, 200, 100, 90, 30, 94.0, "line"),
        (1, 2, 1, 100, 160, 90, 30, 30.0, "murky"),
        (2, 1, 1, 100, 400, 90, 30, 99.0, "second"),
    ), 1)

    assert [b["id"] for b in page["blocks"]] == ["o0001-000", "o0001-001"]
    assert [b["reading_order"] for b in page["blocks"]] == [0, 1]
    first, second = page["blocks"]
    assert len(first["lines"]) == 2
    assert first["lines"][0]["grade"] == "high"
    assert first["lines"][1]["grade"] == "low"
    # The block sits between its two lines, and the page between its blocks.
    assert first["lines"][1]["confidence"] < first["confidence"] < first["lines"][0]["confidence"]
    assert second["grade"] == "high"
    # One bad line in a good page pulls the page down but does not condemn it.
    assert page["grade"] == "medium"


def test_every_word_keeps_its_own_confidence_and_box():
    """The confident words are evidence too.

    A reviewer judging a doubtful word reads it in the line around it, and a
    later pass that wants a stricter threshold cannot recover words that were
    discarded at write time. So the record is per word, and the file is written
    compactly instead of being made smaller by throwing data away.
    """
    page = ocr.build_page(tsv(
        (1, 1, 1, 100, 100, 90, 30, 97.0, "certain"),
        (1, 1, 2, 200, 100, 90, 30, 12.0, "guess"),
    ), 1, low=60)
    line = page["blocks"][0]["lines"][0]
    assert [w["text"] for w in line["words"]] == ["certain", "guess"]
    assert [w["conf"] for w in line["words"]] == [97.0, 12.0]
    assert all(len(w["bbox"]) == 4 for w in line["words"])
    assert line["text"] == "certain guess"


def test_a_page_of_pure_noise_is_graded_low_not_dropped():
    """A ruined scan must be reported, never quietly treated as a blank page."""
    page = ocr.build_page(tsv(
        (1, 1, 1, 100, 100, 40, 30, 8.0, "|l1I"),
        (1, 1, 2, 150, 100, 40, 30, 3.0, "~~,,"),
    ), 7)
    assert page["grade"] == "low"
    assert page["blocks"], "the noise was discarded instead of flagged"
    words = page["blocks"][0]["lines"][0]["words"]
    assert len(words) == 2 and all(w["conf"] < 60 for w in words)


def test_an_empty_page_yields_no_blocks_and_no_confidence():
    page = ocr.build_page(tsv(), 3)
    assert page["blocks"] == [] and page["confidence"] is None
    assert page["grade"] == "unknown"
    assert ocr.page_text(page) == ""


def test_container_rows_never_become_words():
    """conf is -1 on every container row; counting them would tank the page."""
    page = ocr.build_page(tsv((1, 1, 1, 100, 100, 90, 30, 90.0, "word")), 1)
    assert page["confidence"] == pytest.approx(90.0)


# --------------------------------------------------------------------------- #
# Attaching to the book
# --------------------------------------------------------------------------- #

def _sidecar(pages, *, high=85.0, low=60.0):
    return {"schema": ocr.SCHEMA, "thresholds": {"high": high, "low": low},
            "summary": {"confidence": 80.0}, "pages": pages}


def _page(number, blocks):
    return {"page": number, "confidence": 80.0, "grade": "medium",
            "blocks": [
                {"id": f"o{number:04d}-{i:03d}", "type": "text", "reading_order": i,
                 "bbox": box, "confidence": conf, "grade": ocr.grade(conf),
                 "lines": [{"bbox": box, "confidence": conf, "grade": ocr.grade(conf),
                            "text": text, "words": words}]}
                for i, (box, conf, text, words) in enumerate(blocks)
            ]}


def test_confidence_reaches_the_block_it_belongs_to():
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=1, bbox=[50, 100, 350, 160], text="Top."),
        ir.make_block("paragraph", 2, page=1, bbox=[50, 400, 350, 460], text="Bottom."),
    ]
    sidecar = _sidecar([_page(1, [
        ([52, 102, 348, 158], 95.0, "Top.",
         [{"text": "Top.", "conf": 95.0, "bbox": [52, 102, 348, 158]}]),
        # The second pass misread one letter: near enough to be the same
        # region, doubtful enough to be worth a look.
        ([52, 402, 348, 458], 41.0, "Bottorn.",
         [{"text": "Bottorn.", "conf": 41.0, "bbox": [52, 402, 348, 458]}]),
    ])])

    summary = ocr.attach(book, sidecar)
    assert summary["matched"] == 2 and summary["unmatched"] == 0
    assert summary["by_grade"] == {"high": 1, "medium": 0, "low": 1, "unknown": 0}

    top, bottom = book["blocks"]
    assert top["ocr"]["grade"] == "high"
    assert bottom["ocr"]["grade"] == "low"
    assert bottom["ocr"]["low_words"] == ["Bottorn."]
    assert bottom["ocr"]["source_block"] == "o0001-001"
    assert book["source"]["ocr"]["sidecar"] == "source.ocr.json"


def test_a_block_is_never_matched_to_a_region_on_another_page():
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=2,
                                    bbox=[50, 100, 350, 160], text="p2")]
    # The same geometry, but recognised on page 1.
    summary = ocr.attach(book, _sidecar([_page(1, [([50, 100, 350, 160], 95.0, "x", [])])]))
    assert summary["unmatched"] == 1
    assert "ocr" not in book["blocks"][0]


def test_a_block_the_engine_never_saw_is_left_unstamped():
    """Better no confidence than a confidence borrowed from somewhere else."""
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=1,
                                    bbox=[50, 700, 350, 760], text="footer")]
    summary = ocr.attach(book, _sidecar([_page(1, [([50, 100, 350, 160], 95.0, "x", [])])]))
    assert summary["unmatched"] == 1 and summary["matched"] == 0
    assert "ocr" not in book["blocks"][0]


def test_a_block_with_no_box_is_skipped_rather_than_guessed():
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=1, text="imported, no box")]
    assert ocr.attach(book, _sidecar([_page(1, [([0, 0, 100, 100], 95.0, "x", [])])]))[
        "unmatched"] == 1


def test_overlap_is_a_share_of_the_smaller_box():
    """A short OCR line inside a tall merged paragraph must still match."""
    paragraph = [0.0, 0.0, 300.0, 400.0]
    line = [10.0, 10.0, 290.0, 40.0]
    assert ocr.overlap(paragraph, line) > 0.9
    assert ocr.overlap(paragraph, [400.0, 0.0, 500.0, 100.0]) == 0.0


def test_stamped_book_still_validates():
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=1,
                                    bbox=[50, 100, 350, 160], text="Prose.")]
    ocr.attach(book, _sidecar([_page(1, [([50, 100, 350, 160], 30.0, "Prose.", [])])]))
    assert ir.validate_book(book) == []


# --------------------------------------------------------------------------- #
# Artifacts
# --------------------------------------------------------------------------- #

def test_the_sidecar_is_written_as_utf8_json_and_readable_text(tmp_path):
    sidecar = _sidecar([_page(1, [([0, 0, 100, 50], 92.0, "متن فارسی", [])])])
    sidecar["engine"] = {"name": "tesseract", "language": "fas"}
    paths = ocr.write(sidecar, tmp_path)

    written = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert written["schema"] == ocr.SCHEMA
    assert written["pages"][0]["blocks"][0]["lines"][0]["text"] == "متن فارسی"

    text = Path(paths["text"]).read_text(encoding="utf-8")
    assert "[page 1]" in text and "متن فارسی" in text


def test_preprocessing_history_travels_with_the_sidecar(tmp_path):
    """A low score must be traceable to what was done to the image first."""
    sidecar = _sidecar([_page(1, [([0, 0, 100, 50], 40.0, "x", [])])])
    sidecar["preprocessing"] = {"applied": True, "cleaned_pages": [1],
                                "skipped_pages": [{"page": 2, "reason": "artwork"}],
                                "original": "book.pdf", "output": "cleaned.pdf"}
    written = json.loads(
        Path(ocr.write(sidecar, tmp_path)["json"]).read_text(encoding="utf-8")
    )
    assert written["preprocessing"]["original"] == "book.pdf"
    assert written["preprocessing"]["skipped_pages"][0]["reason"] == "artwork"


def test_build_says_how_to_install_tesseract_when_it_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "find_tesseract", lambda: None)
    with pytest.raises(ocr.SidecarError, match="install"):
        ocr.build(tmp_path / "nothing.pdf", language="fas")


def test_qa_reports_a_low_confidence_block_for_review():
    """A misread word is fluent Persian; only the engine's own score sees it."""
    import qa

    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=4, bbox=[50, 100, 350, 160],
                      text="Source."),
        ir.make_block("paragraph", 2, page=4, bbox=[50, 400, 350, 460],
                      text="Also source."),
    ]
    for block in book["blocks"]:
        block["target"] = "ترجمهٔ این بند که به اندازهٔ کافی بلند است."
    ocr.attach(book, _sidecar([_page(4, [
        ([50, 100, 350, 160], 95.0, "Source.", []),
        ([50, 400, 350, 460], 22.0, "Also source.",
         [{"text": "Alsa", "conf": 22.0, "bbox": [50, 400, 350, 460]}]),
    ])]))

    summary = qa.check_book(book).summary()
    flagged = [f for f in summary["findings"] if f["code"] == "ocr-low-confidence"]
    assert len(flagged) == 1, "the unsure block was not reported"
    assert flagged[0]["unit"] == "b00002"
    assert "page 4" in flagged[0]["detail"] and "Alsa" in flagged[0]["detail"]
    # Advice by default: a low score is a reason to look, not proof of an error.
    assert flagged[0]["severity"] == qa.WARNING
    assert summary["ok"] is True


def test_strict_makes_an_unsure_block_blocking():
    import qa

    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=1,
                                    bbox=[50, 100, 350, 160], text="Source.")]
    book["blocks"][0]["target"] = "ترجمهٔ این بند که به اندازهٔ کافی بلند است."
    ocr.attach(book, _sidecar([_page(1, [([50, 100, 350, 160], 20.0, "Source.", [])])]))

    assert qa.check_book(book, strict=True).summary()["ok"] is False


# --------------------------------------------------------------------------- #
# Provenance: this is a second recognition, not a readout of the embedded layer
# --------------------------------------------------------------------------- #

def test_a_score_for_different_text_is_refused_not_attached():
    """Overlapping boxes are not enough to bind a confidence to a block.

    The sidecar re-recognises the page; on a hard scan the two passes can read
    the same region as different words. Attaching the second pass's confident
    score to the first pass's text would silence the warning instead of raising
    it -- the block would look verified when nobody has read it.
    """
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=1,
                                    bbox=[50, 100, 350, 160],
                                    text="The morning came slowly over the ridge.")]
    summary = ocr.attach(book, _sidecar([_page(1, [
        ([50, 100, 350, 160], 96.0, "Registered trademarks apply herein.",
         [{"text": "Registered", "conf": 96.0, "bbox": [50, 100, 150, 160]}]),
    ])]))

    assert summary["disputed"] == 1 and summary["matched"] == 0
    evidence = book["blocks"][0]["ocr"]
    assert evidence["grade"] == "review-required"
    assert evidence["confidence"] is None, "a score was reported for other text"
    assert "Registered" in evidence["second_pass_text"]


def test_ordinary_ocr_disagreement_still_attaches():
    """Spacing, case and punctuation are what two passes differ about harmlessly."""
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=1,
                                    bbox=[50, 100, 350, 160],
                                    text="The morning came slowly over the ridge.")]
    summary = ocr.attach(book, _sidecar([_page(1, [
        ([50, 100, 350, 160], 91.0, "The morning came slowly over the ridge",
         [{"text": "morning", "conf": 91.0, "bbox": [50, 100, 150, 160]}]),
    ])]))
    assert summary["matched"] == 1 and summary["disputed"] == 0
    assert book["blocks"][0]["ocr"]["grade"] == "high"


def test_text_agreement_ignores_case_spacing_and_punctuation():
    assert ocr.text_agreement("The Ridge.", "the  ridge") == pytest.approx(1.0)
    assert ocr.text_agreement("", "") == pytest.approx(1.0)
    assert ocr.text_agreement("something", "") == 0.0
    assert ocr.text_agreement("morning", "evening") < 0.6


def test_the_sidecar_says_where_its_confidence_came_from():
    """A second-pass score must never be presented as the embedded layer's."""
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=1,
                                    bbox=[50, 100, 350, 160], text="Prose here.")]
    ocr.attach(book, _sidecar([_page(1, [
        ([50, 100, 350, 160], 90.0, "Prose here.",
         [{"text": "Prose", "conf": 90.0, "bbox": [50, 100, 150, 160]}]),
    ])]))
    assert "second independent recognition" in book["source"]["ocr"]["provenance"]


def test_qa_reports_a_disputed_region_for_review():
    import qa

    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=3,
                                    bbox=[50, 100, 350, 160],
                                    text="The morning came slowly over the ridge.")]
    book["blocks"][0]["target"] = "ترجمهٔ این بند که به اندازهٔ کافی بلند است."
    ocr.attach(book, _sidecar([_page(3, [
        ([50, 100, 350, 160], 96.0, "Registered trademarks apply herein.",
         [{"text": "Registered", "conf": 96.0, "bbox": [50, 100, 150, 160]}]),
    ])]))

    findings = qa.check_book(book).summary()["findings"]
    disputed = [f for f in findings if f["code"] == "ocr-disputed-text"]
    assert len(disputed) == 1 and disputed[0]["unit"] == "b00001"
    assert qa.check_book(book, strict=True).summary()["ok"] is False

"""A translated page is not accepted until it has been looked at.

Every check here is structural, never pixel-equal: Persian reflows, so the line
breaks and often the page count differ from the source and always will. What
must survive is the structure — the right blocks, once each; the right pictures
in the right order at the right shape; nothing off the trim; prose set
right-to-left; no hole where a page of text should be.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bookir as ir
import renderqa
import runstate

#: Long enough to have wrapped, and unambiguously Persian.
PERSIAN = "صبح به آرامی از فراز تپه‌ها بالا آمد و الیزابت کنار پنجره ایستاده بود."
PERSIAN_OTHER = "دارسی هیچ نگفت و او رویش را از پنجره برگرداند و به راه افتاد."

SETUP = ir.default_page_setup()          # 396 × 612pt, body 54..351 × 54..558
BODY_RIGHT = SETUP["width_pt"] - SETUP["margin_outer_pt"]


# --------------------------------------------------------------------------- #
# Fixtures, built here
# --------------------------------------------------------------------------- #

def _text(text: str, box: list[float]) -> dict:
    return {"text": text, "bbox": [float(v) for v in box]}


def _image(box: list[float]) -> dict:
    return {"bbox": [float(v) for v in box],
            "width_pt": float(box[2] - box[0]),
            "height_pt": float(box[3] - box[1])}


def _view(*, blocks=(), images=(), width=None, height=None) -> dict:
    return {
        "width_pt": SETUP["width_pt"] if width is None else width,
        "height_pt": SETUP["height_pt"] if height is None else height,
        "blocks": list(blocks),
        "images": list(images),
    }


def _expected(*, texts=(), images=(), page: int = 1, translatable=None) -> dict:
    return {
        "page": page,
        "setup": dict(SETUP),
        "texts": list(texts),
        "images": [{"id": f"b{n:05d}", "aspect": aspect}
                   for n, aspect in enumerate(images, start=1)],
        "translatable": len(texts) if translatable is None else translatable,
    }


def _codes(report) -> set[str]:
    return {finding["code"] for finding in report.summary()["findings"]}


def _well_set_page() -> dict:
    """Two right-anchored Persian paragraphs, inside the body, no pictures."""
    return _view(blocks=[
        _text(PERSIAN, [60, 100, BODY_RIGHT, 140]),
        _text(PERSIAN_OTHER, [60, 160, BODY_RIGHT, 200]),
    ])


# --------------------------------------------------------------------------- #
# The page that is right
# --------------------------------------------------------------------------- #

def test_a_correctly_built_page_passes(tmp_path):
    report = renderqa.check_page(
        _well_set_page(), _expected(texts=[PERSIAN, PERSIAN_OTHER]))
    assert report.summary()["ok"], report.summary()["findings"]
    assert report.summary()["counts"]["rendered_blocks"] == 2


def test_reflow_is_not_a_failure(tmp_path):
    """The translation is set in different lines and a different order of
    boxes than the source; only presence and geometry are checked."""
    reflowed = _view(blocks=[
        _text(PERSIAN[:30], [60, 100, BODY_RIGHT, 118]),
        _text(PERSIAN[30:] + " " + PERSIAN_OTHER, [60, 120, BODY_RIGHT, 190]),
    ])
    # The first block's probe still resolves against the page's whole text.
    report = renderqa.check_page(reflowed, _expected(texts=[PERSIAN_OTHER]))
    assert report.summary()["ok"], report.summary()["findings"]


# --------------------------------------------------------------------------- #
# The eight rejections
# --------------------------------------------------------------------------- #

def test_a_missing_illustration_is_rejected():
    view = _view(
        blocks=[_text(PERSIAN, [60, 100, BODY_RIGHT, 140])],
        images=[_image([80, 200, 260, 320])],
    )
    report = renderqa.check_page(view, _expected(texts=[PERSIAN],
                                                 images=[1.5, 0.75]))
    assert "image-missing" in _codes(report)
    assert not report.summary()["ok"]


def test_an_extra_illustration_is_rejected():
    view = _view(images=[_image([80, 100, 260, 220]), _image([80, 260, 170, 380])])
    report = renderqa.check_page(view, _expected(images=[1.5]))
    assert "image-extra" in _codes(report)


def test_two_swapped_illustrations_are_rejected():
    """Same two pictures, same shapes, wrong order — the classic build slip."""
    view = _view(images=[
        _image([80, 100, 170, 220]),    # 0.75 wide-to-tall, first
        _image([80, 260, 260, 380]),    # 1.5, second
    ])
    report = renderqa.check_page(view, _expected(images=[1.5, 0.75]))
    assert _codes(report) == {"image-reordered"}


def test_an_illustration_at_the_wrong_shape_is_rejected():
    view = _view(images=[_image([80, 100, 260, 100 + 180])])  # 1.0, not 1.5
    report = renderqa.check_page(view, _expected(images=[1.5]))
    assert _codes(report) == {"image-aspect"}


def test_text_clipped_off_the_trim_is_rejected():
    view = _view(blocks=[_text(PERSIAN, [60, 560, 470, 640])])
    assert "text-clipped" in _codes(
        renderqa.check_page(view, _expected(texts=[PERSIAN])))


def test_text_past_the_margins_is_rejected():
    """Inside the paper, outside the body — an overflowing line."""
    view = _view(blocks=[_text(PERSIAN, [20, 100, 385, 140])])
    codes = _codes(renderqa.check_page(view, _expected(texts=[PERSIAN])))
    assert "text-overflow" in codes and "text-clipped" not in codes


def test_a_page_at_the_wrong_size_is_rejected():
    view = _view(blocks=[_text(PERSIAN, [60, 100, BODY_RIGHT, 140])],
                 width=SETUP["height_pt"], height=SETUP["width_pt"])
    report = renderqa.check_page(view, _expected(texts=[PERSIAN]))
    assert "page-size" in _codes(report)
    assert "on its side" in report.summary()["findings"][0]["detail"]


def test_a_paragraph_that_is_not_right_to_left_is_rejected():
    """Flush left, well short of the right margin: the bidi property was lost."""
    view = _view(blocks=[_text(PERSIAN, [54, 100, 250, 140])])
    assert _codes(renderqa.check_page(view, _expected(texts=[PERSIAN]))) == {
        "paragraph-not-rtl"}


def test_a_block_that_appears_twice_on_one_page_is_rejected():
    view = _view(blocks=[
        _text(PERSIAN, [60, 100, BODY_RIGHT, 140]),
        _text(PERSIAN, [60, 200, BODY_RIGHT, 240]),
    ])
    assert _codes(renderqa.check_page(view, _expected(texts=[PERSIAN]))) == {
        "text-duplicated"}


def test_a_block_that_is_missing_from_the_page_is_rejected():
    view = _view(blocks=[_text(PERSIAN, [60, 100, BODY_RIGHT, 140])])
    report = renderqa.check_page(view, _expected(texts=[PERSIAN, PERSIAN_OTHER]))
    assert _codes(report) == {"text-missing"}


# --------------------------------------------------------------------------- #
# The rest of the structural set
# --------------------------------------------------------------------------- #

def test_a_hole_in_the_middle_of_a_page_is_rejected():
    view = _view(blocks=[
        _text(PERSIAN, [60, 60, BODY_RIGHT, 100]),
        _text(PERSIAN_OTHER, [60, 500, BODY_RIGHT, 540]),
    ])
    assert "blank-region" in _codes(
        renderqa.check_page(view, _expected(texts=[PERSIAN, PERSIAN_OTHER])))


def test_a_page_with_nothing_on_it_is_rejected():
    report = renderqa.check_page(_view(), _expected(texts=[PERSIAN]))
    codes = _codes(report)
    assert "blank-region" in codes and "text-missing" in codes


def test_a_short_final_page_is_not_a_hole():
    """A chapter ending a third of the way down is normal typesetting."""
    view = _view(blocks=[_text(PERSIAN, [60, 60, BODY_RIGHT, 120])])
    assert "blank-region" not in _codes(
        renderqa.check_page(view, _expected(texts=[PERSIAN])))


def test_text_printed_over_a_picture_is_rejected():
    view = _view(blocks=[_text(PERSIAN, [60, 100, BODY_RIGHT, 200])],
                 images=[_image([100, 120, 300, 180])])
    assert "text-image-overlap" in _codes(
        renderqa.check_page(view, _expected(texts=[PERSIAN],
                                            images=[200 / 60])))


def test_a_page_nobody_has_translated_yet_does_not_pass_quietly():
    report = renderqa.check_page(_view(), _expected(texts=[], translatable=4))
    assert "text-missing" in _codes(report)
    assert "none of them is translated" in report.summary()["findings"][0]["detail"]


# --------------------------------------------------------------------------- #
# Expectations come from the IR, page by page
# --------------------------------------------------------------------------- #

def _paged_book(tmp_path: Path) -> Path:
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=1, text="The first page's paragraph.",
                      target=PERSIAN),
        ir.make_block("pagebreak", 2, page=2, soft=True),
        ir.make_block("image", 3, page=2, asset="plate.png", alt="",
                      width_pt=180.0, height_pt=120.0),
        ir.make_block("paragraph", 4, page=2, text="The second page's paragraph.",
                      target=PERSIAN_OTHER),
    ]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def test_expectations_only_cover_the_page_they_are_for(tmp_path):
    book = ir.load_book(_paged_book(tmp_path))

    first = renderqa.expectations(book, 1)
    assert first["texts"] == [PERSIAN] and first["images"] == []

    second = renderqa.expectations(book, 2)
    assert second["texts"] == [PERSIAN_OTHER]
    assert [round(entry["aspect"], 3) for entry in second["images"]] == [1.5]


def test_a_page_the_book_does_not_have_expects_nothing(tmp_path):
    book = ir.load_book(_paged_book(tmp_path))
    assert renderqa.expectations(book, 99)["translatable"] == 0


# --------------------------------------------------------------------------- #
# Running one page: artifacts, refusals, bounded retry
# --------------------------------------------------------------------------- #

def _pdf_page(path: Path, lines: list[tuple[str, float, float]], *,
              width: float = 396, height: float = 612) -> Path:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    for text, x, y in lines:
        page.insert_text((x, y), text, fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


def _latin_book(tmp_path: Path, target: str) -> Path:
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=1, text="A line of source prose.",
                      target=target)
    ]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def test_a_page_that_renders_correctly_is_recorded_as_passed(tmp_path):
    pytest.importorskip("pymupdf")
    target = "Sample rendered line that must appear on the page."
    book_path = _latin_book(tmp_path, target)
    source = _pdf_page(tmp_path / "source.pdf", [("A line of source prose.", 54, 100)])
    rendered = _pdf_page(tmp_path / "target.pdf", [(target, 60, 100)])

    written = renderqa.check(tmp_path, book_path, 1,
                             target_pdf=rendered, source_pdf=source)

    assert written["ok"] is True and written["verified"] is True
    assert (tmp_path / "renders" / "source" / "page-0001.png").exists()
    assert (tmp_path / "renders" / "target" / "page-0001.png").exists()
    report = json.loads(renderqa.report_path(tmp_path, 1).read_text(encoding="utf-8"))
    assert report["schema"] == renderqa.SCHEMA and report["page"] == 1

    record = runstate.RunState(tmp_path).page(1)
    assert record["state"] == "qa_passed"
    assert record["attempts"] == 0
    assert set(record["hashes"]) >= {"translation", "render", "qa"}


def test_a_page_that_lost_its_text_is_recorded_as_failed(tmp_path):
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "Sample rendered line that vanished entirely.")
    rendered = _pdf_page(tmp_path / "target.pdf", [("Something else.", 60, 100)])

    written = renderqa.check(tmp_path, book_path, 1, target_pdf=rendered)
    assert written["ok"] is False and written["verified"] is True
    assert "text-missing" in {f["code"] for f in written["findings"]}

    record = runstate.RunState(tmp_path).page(1)
    assert record["state"] == "failed" and record["attempts"] == 1
    assert record["last_error"].startswith("text-missing")


def test_an_unrenderable_page_is_unverified_not_passed(tmp_path):
    book_path = _latin_book(tmp_path, "Anything at all.")
    written = renderqa.check(tmp_path, book_path, 1, target_pdf=None)

    assert written["ok"] is False
    assert written["verified"] is False
    assert "no --target-pdf" in written["unverified"]
    # An absent renderer is not a failed page: no attempt was spent on it.
    assert runstate.RunState(tmp_path).page(1) is None


def test_a_target_that_is_not_there_is_unverified(tmp_path):
    book_path = _latin_book(tmp_path, "Anything at all.")
    written = renderqa.check(tmp_path, book_path, 1,
                             target_pdf=tmp_path / "never-built.pdf")
    assert written["verified"] is False and "not there" in written["unverified"]


def test_a_supplied_image_is_filed_as_the_evidence(tmp_path, sample_png):
    """The caller may render the page however it likes; this never runs Word."""
    pytest.importorskip("pymupdf")
    target = "Sample rendered line that must appear on the page."
    book_path = _latin_book(tmp_path, target)
    rendered = _pdf_page(tmp_path / "target.pdf", [(target, 60, 100)])

    written = renderqa.check(tmp_path, book_path, 1, target_pdf=rendered,
                             target_image=sample_png)
    assert written["ok"] is True
    filed = tmp_path / "renders" / "target" / "page-0001.png"
    assert filed.read_bytes() == sample_png.read_bytes()


def test_retrying_a_page_for_ever_is_impossible(tmp_path):
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "Sample rendered line that vanished entirely.")
    rendered = _pdf_page(tmp_path / "target.pdf", [("Something else.", 60, 100)])

    for attempt in (1, 2, 3):
        written = renderqa.check(tmp_path, book_path, 1, target_pdf=rendered,
                                 max_attempts=3)
        assert written["ok"] is False
        assert runstate.RunState(tmp_path).page(1)["attempts"] == attempt

    refused = renderqa.check(tmp_path, book_path, 1, target_pdf=rendered,
                             max_attempts=3)
    assert refused["refused"] == "retry-exhausted"
    assert "limit 3" in refused["detail"]
    # The wall holds: the count did not move, and nothing was re-rendered.
    assert runstate.RunState(tmp_path).page(1)["attempts"] == 3


def test_a_corrected_source_page_lets_the_page_be_tried_again(tmp_path):
    """The cap bounds retries of the same failure, not the page for ever."""
    state = runstate.RunState(tmp_path)
    state.note_page_source(1, "before")
    for _ in range(3):
        state.set_page(1, "failed", error="still blank")
    assert state.page(1)["attempts"] == 3

    assert runstate.RunState(tmp_path).note_page_source(1, "after") is True
    record = runstate.RunState(tmp_path).page(1)
    assert record["state"] == "pending" and record["attempts"] == 0


def test_the_cli_reports_the_page_and_exits_on_the_outcome(tmp_path, capsys):
    pytest.importorskip("pymupdf")
    target = "Sample rendered line that must appear on the page."
    book_path = _latin_book(tmp_path, target)
    good = _pdf_page(tmp_path / "good.pdf", [(target, 60, 100)])
    bad = _pdf_page(tmp_path / "bad.pdf", [("Nothing like it.", 60, 100)])

    arguments = ["--book", str(book_path), "--work", str(tmp_path), "--page", "1"]
    assert renderqa.main(arguments + ["--target-pdf", str(good)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert renderqa.main(arguments + ["--target-pdf", str(bad)]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False

    assert renderqa.main(arguments + ["--target-pdf", str(bad),
                                      "--max-attempts", "1"]) == 2
    assert json.loads(capsys.readouterr().out)["refused"] == "retry-exhausted"

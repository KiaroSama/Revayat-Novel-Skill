"""A translated page is not accepted until it has been looked at.

Every check here is structural, never pixel-equal: Persian reflows, so the line
breaks and often the page count differ from the source and always will. What
must survive is the structure — the right blocks, once each; the right pictures
in the right order at the right shape; nothing off the trim; prose set
right-to-left; no hole where a page of text should be.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import sys
from pathlib import Path

import pytest

import bookir as ir
import pagecheck
import renderqa
import runstate
import wordrender
from tests_support import png_bytes

#: Long enough to have wrapped, and unambiguously Persian.
PERSIAN = "صبح به آرامی از فراز تپه‌ها بالا آمد و الیزابت کنار پنجره ایستاده بود."
PERSIAN_OTHER = "دارسی هیچ نگفت و او رویش را از پنجره برگرداند و به راه افتاد."

#: Whatever the project's default trim is. Derived, never repeated as a
#: literal: these tests are about geometry *relative* to the body box, and a
#: change of paper size must not read as a rendering defect.
SETUP = ir.default_page_setup()
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
    """Off the paper, not merely outside the body — a line that got cut.

    Stated relative to the trim rather than in absolute points: on a wider
    paper the same literal box sits comfortably inside the sheet, and the test
    would pass while proving nothing.
    """
    beyond = SETUP["width_pt"] + 40
    view = _view(blocks=[_text(PERSIAN, [60, 100, beyond, 140])])
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


def test_direction_is_never_judged_from_the_rendered_page():
    """A Persian paragraph flush left is not evidence of anything.

    There *was* a check here that read the alignment of PyMuPDF's block boxes.
    It is gone, because for Arabic script those boxes do not report where the
    ink actually sits: measured on a real Word render of a document whose every
    paragraph carries `w:bidi`, it reported four of ten paragraphs set
    left-to-right. A check that cannot be right for the only script this
    project renders is not a check.

    `check_direction_in_document` reads the `w:bidi` out of the file instead,
    and `renderqa.check` folds its findings into the page report — see
    `tests/test_docqa.py` for the document side of the same question.
    """
    left = SETUP["margin_inner_pt"] + 2
    view = _view(blocks=[_text(PERSIAN, [left, 100, left + 150, 140])])
    assert _codes(renderqa.check_page(view, _expected(texts=[PERSIAN]),
                                      source=PERSIAN)) == set()


def test_a_block_that_appears_twice_on_one_page_is_rejected():
    view = _view(blocks=[
        _text(PERSIAN, [60, 100, BODY_RIGHT, 140]),
        _text(PERSIAN, [60, 200, BODY_RIGHT, 240]),
    ])
    assert _codes(renderqa.check_page(view, _expected(texts=[PERSIAN]))) == {
        "text-duplicated"}


def test_a_block_that_is_missing_from_the_document_is_rejected():
    """With the file in hand the answer is exact: it is not there."""
    view = _view(blocks=[_text(PERSIAN, [60, 100, BODY_RIGHT, 140])])
    report = renderqa.check_page(view, _expected(texts=[PERSIAN, PERSIAN_OTHER]),
                                 source=PERSIAN)
    assert _codes(report) == {"text-missing"}


def test_persian_missing_from_a_render_alone_is_unverified_not_missing():
    """A rendered page cannot answer this question about Arabic script.

    PyMuPDF drops the zero-width non-joiner and transposes letters, so a
    paragraph that *is* on the page reads as absent. Reporting that as
    `text-missing` fails every correct Persian page; reporting it as a pass
    would be worse. It is neither — it was not asked.
    """
    view = _view(blocks=[_text(PERSIAN, [60, 100, BODY_RIGHT, 140])])
    report = renderqa.check_page(view, _expected(texts=[PERSIAN, PERSIAN_OTHER]))
    assert _codes(report) == {"text-unverified"}
    assert report.summary()["errors"] == 0, "a warning, not a failure"


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
    report = renderqa.check_page(_view(), _expected(texts=[PERSIAN]), source="")
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
              width: float = SETUP["width_pt"],
              height: float = SETUP["height_pt"]) -> Path:
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


def test_a_page_nothing_can_lay_out_is_unverified_not_passed(monkeypatch,
                                                             tmp_path):
    """Nobody has to hand a preview over any more - so the way to have nothing
    to look at is for the machine to be unable to lay one out."""
    book_path = _latin_book(tmp_path, "Anything at all.")
    monkeypatch.setattr(wordrender, "word_available", lambda: False)
    monkeypatch.setattr(wordrender, "find_libreoffice", lambda: None)

    written = renderqa.check(tmp_path, book_path, 1)

    assert written["ok"] is False
    assert written["verified"] is False
    assert "LibreOffice" in written["unverified"]
    # An absent renderer is not a failed page: no attempt was spent on it.
    assert runstate.RunState(tmp_path).page(1) is None


def test_an_image_on_its_own_carries_no_geometry_to_check(tmp_path, sample_png):
    """A picture of a page shows a reviewer the page and tells a check nothing."""
    book_path = _latin_book(tmp_path, "Anything at all.")
    written = renderqa.check(tmp_path, book_path, 1, target_image=sample_png)

    assert written["ok"] is False and written["verified"] is False
    assert "no --target-pdf" in written["unverified"]


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


# --------------------------------------------------------------------------- #
# Laying the document out, rather than making the caller do it
# --------------------------------------------------------------------------- #

@pytest.fixture
def sample_book_and_docx(tmp_path):
    """A saved book and the document built from it — what a caller really has."""
    import argparse

    from build_docx import Builder, add_arguments

    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    block = ir.make_block("paragraph", 1, page=1, bbox=[72, 90, 320, 140],
                          text="A paragraph on the only page of this book.")
    block["target"] = "بندی فارسی که به اندازهٔ کافی بلند است تا از گیت‌ها رد شود."
    book["blocks"] = [block]

    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma",
                                 "--no-toc"])
    docx_path = tmp_path / "book.fa.docx"
    Builder(book, tmp_path, options).build(docx_path)
    return book_path, docx_path


def test_a_machine_with_neither_renderer_says_what_to_install(monkeypatch,
                                                              tmp_path):
    """The message has to name the fix for *this* platform, not a generic one."""
    docx = tmp_path / "book.docx"
    docx.write_bytes(b"a document that exists, so the missing renderer is the "
                     b"only thing left to report")
    monkeypatch.setattr(wordrender, "word_available", lambda: False)
    monkeypatch.setattr(wordrender, "find_libreoffice", lambda: None)
    with pytest.raises(renderqa.RenderError) as raised:
        renderqa.render_docx(docx, tmp_path / "renders")
    assert "LibreOffice" in str(raised.value)


def test_windows_reaches_for_word_and_everywhere_else_for_libreoffice(monkeypatch):
    """The deliverable is a .docx, so Word's pagination is the real one.

    Off Windows a structural check against LibreOffice's layout is still worth
    far more than no check: nothing asked here depends on where a line broke.
    """
    monkeypatch.setattr(wordrender.sys, "platform", "win32")
    monkeypatch.setattr(wordrender, "word_available", lambda: True)
    monkeypatch.setattr(wordrender, "find_libreoffice", lambda: "/usr/bin/soffice")
    assert wordrender.backend() == "word"

    monkeypatch.setattr(wordrender, "word_available", lambda: False)
    assert wordrender.backend() == "libreoffice"


def test_word_runs_in_a_child_process_so_the_timeout_is_real(monkeypatch, tmp_path):
    """COM cannot be cancelled, so a timeout on an in-process call is a lie.

    The guard is that the parent runs Word as a subprocess and kills it. This
    proves the wall clock is enforced without needing Word installed.
    """
    import subprocess

    docx = tmp_path / "book.docx"
    docx.write_bytes(b"not really a document")
    monkeypatch.setattr(wordrender, "word_available", lambda: True)

    reached = []

    def wedged(*args, **kwargs):
        reached.append(args)
        raise subprocess.TimeoutExpired(cmd="word", timeout=1)

    monkeypatch.setattr(wordrender, "_run_bounded", wedged)
    with pytest.raises(wordrender.RenderError) as raised:
        wordrender.render(docx, tmp_path / "renders", timeout=1)
    assert "terminated" in str(raised.value)
    # The message alone does not pin the route down: on a machine that has Word,
    # a render path still calling `subprocess.run` starts the real worker, misses
    # the same one-second clock and raises the same named error. Asserting the
    # bounded runner was the thing that ran is what makes this test about the
    # process-tree kill rather than about how slowly Word starts.
    assert reached, "render() never went through _run_bounded, so nothing kills the tree"


def test_a_conversion_failure_leaves_the_page_unverified_not_passed(
        monkeypatch, tmp_path, sample_book_and_docx):
    """"We could not look" must never be recorded as "we looked and it was fine".

    This is the failure mode worth a test of its own: a converter that is not
    installed is the *normal* state on a fresh machine, and a check that quietly
    passes there would be worse than no check.
    """
    book_path, docx_path = sample_book_and_docx
    monkeypatch.setattr(wordrender, "word_available", lambda: False)
    monkeypatch.setattr(wordrender, "find_libreoffice", lambda: None)

    report = renderqa.check(tmp_path, book_path, 1, docx=docx_path)
    assert report["ok"] is False, report
    assert "LibreOffice" in report["unverified"]
    assert report.get("findings") in (None, []), (
        "an unverified page invented findings it could not have seen"
    )


def test_an_explicit_target_wins_over_conversion(monkeypatch, tmp_path,
                                                 sample_book_and_docx):
    """A caller who rendered the page themselves is not second-guessed."""
    book_path, docx_path = sample_book_and_docx
    called = []
    monkeypatch.setattr(renderqa, "render_docx",
                        lambda *a, **k: called.append(1))

    supplied = tmp_path / "supplied.png"
    supplied.write_bytes(png_bytes(40, 30))

    renderqa.check(tmp_path, book_path, 1, docx=docx_path, target_image=supplied)
    assert not called, "the converter ran even though a target was supplied"


def test_a_target_image_that_is_not_there_is_reported_not_raised(
        tmp_path, sample_book_and_docx):
    """A wrong path is a mistake to report, not a traceback to hand back."""
    book_path, _ = sample_book_and_docx
    report = renderqa.check(tmp_path, book_path, 1,
                            target_image=tmp_path / "missing.png")
    assert report["ok"] is False
    assert "missing.png" in report["unverified"]


# --------------------------------------------------------------------------- #
# The assembled document, not only its pages
# --------------------------------------------------------------------------- #

def test_furniture_is_not_reported_as_overflow():
    """A page number lives below the bottom margin. That is where it belongs.

    Measured on a real three-page build, reporting it produced 60 findings and
    every one was a page number — and a check that fires on correct output
    teaches the next reader to skip the report.
    """
    height = SETUP["height_pt"]
    footer = [220, height - SETUP["margin_bottom_pt"] + 10, 240, height - 20]
    assert renderqa.is_furniture(footer, SETUP, height)

    header = [220, 8, 240, SETUP["margin_top_pt"] - 6]
    assert renderqa.is_furniture(header, SETUP, height)

    body = [62, 200, 400, 240]
    assert not renderqa.is_furniture(body, SETUP, height)


def test_a_leader_dot_is_not_an_illustration():
    """A table of contents overlapping its own tab leaders is not a defect."""
    assert not renderqa.is_illustration({"bbox": [100, 100, 104, 104]})
    assert renderqa.is_illustration({"bbox": [100, 100, 300, 250]})


def test_a_footnote_pinned_to_the_foot_is_not_a_hole():
    """It is anchored there however little text sits above it."""
    left, top, right, bottom = renderqa.body_rect(SETUP)
    view = _view(blocks=[
        _text(PERSIAN, [62, top, BODY_RIGHT, top + 40]),
        _text(PERSIAN_OTHER, [62, bottom - 30, BODY_RIGHT, bottom - 1]),
    ])
    assert "blank-region" not in _codes(
        renderqa.check_page(view, _expected(texts=[PERSIAN, PERSIAN_OTHER])))


def test_a_paragraph_stranded_low_on_an_empty_page_is_still_a_hole():
    """The distinction is where a band *ends*, not where it starts.

    Judging by the start would swallow this — the build-gave-up shape the
    check exists for.
    """
    left, top, right, bottom = renderqa.body_rect(SETUP)
    view = _view(blocks=[
        _text(PERSIAN, [62, top, BODY_RIGHT, top + 40]),
        _text(PERSIAN_OTHER, [62, bottom - 120, BODY_RIGHT, bottom - 80]),
    ])
    assert "blank-region" in _codes(
        renderqa.check_page(view, _expected(texts=[PERSIAN, PERSIAN_OTHER])))


def test_a_page_that_was_laid_out_says_so_even_when_judging_it_fails(tmp_path,
                                                                     monkeypatch):
    """Laying the document out is the slow part; losing that fact wastes it.

    If the run dies between rendering the page and judging it, the record must
    not read `merged` — that says the page was never laid out, and the next run
    pays for the render again. `rendered` was in the state list from the start
    with nothing writing it, which is the same bug wearing a name.
    """
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "A line that was rendered before the crash.")
    rendered = _pdf_page(tmp_path / "target.pdf", [("Anything.", 60, 100)])

    def damaged(*args, **kwargs):
        raise RuntimeError("the PDF ended in the middle of an object")

    monkeypatch.setattr(pagecheck, "page_view", damaged)
    written = renderqa.check(tmp_path, book_path, 1, target_pdf=rendered)

    assert written["verified"] is False, "a page nobody could read is not a pass"
    record = runstate.RunState(tmp_path).page(1)
    assert record["state"] == "rendered"
    assert record["hashes"]["render"], "the render it kept is not identified"


# --------------------------------------------------------------------------- #
# A review is bound to everything the reviewer was shown
# --------------------------------------------------------------------------- #

def _two_sheet_pdf(path: Path, first: str, second: str) -> Path:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for text in (first, second):
        page = doc.new_page(width=SETUP["width_pt"], height=SETUP["height_pt"])
        page.insert_text((100, 150), text, fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


def test_the_review_identity_covers_every_target_sheet(tmp_path):
    """A page that reflows onto two sheets has two sheets of evidence.

    Binding to the first PNG alone left a hole exactly the width of the
    overflow: sheet two could be re-rendered from different text and the
    reviewer's `yes` would still stand, because nothing it was bound to moved.
    """
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "A line that fits on the first sheet.")
    two = _two_sheet_pdf(tmp_path / "two.pdf",
                         "A line that fits on the first sheet.", "The overflow.")

    renderqa.check(tmp_path, book_path, 1, target_pdf=two)
    before = runstate.RunState(tmp_path).page(1)["hashes"]["render"]
    assert len(json.loads(renderqa.report_path(tmp_path, 1).read_text(
        encoding="utf-8"))["renders"]["target_sheets"]) == 2

    # Change ONLY the second sheet. The first is byte-identical.
    changed = _two_sheet_pdf(tmp_path / "changed.pdf",
                             "A line that fits on the first sheet.",
                             "A completely different overflow.")
    renderqa.check(tmp_path, book_path, 1, target_pdf=changed)
    after = runstate.RunState(tmp_path).page(1)["hashes"]["render"]

    assert before != after, (
        "the second sheet changed and the review identity did not — a review "
        "made before this would still read as current"
    )


def test_the_review_identity_covers_the_source_render(tmp_path):
    """The reviewer compares two images. Changing one of them is a change."""
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "The translated line.")
    target = _pdf_page(tmp_path / "target.pdf", [("The translated line.", 60, 100)])
    first = _pdf_page(tmp_path / "s1.pdf", [("An English source line.", 54, 100)])
    second = _pdf_page(tmp_path / "s2.pdf", [("A different source line.", 54, 100)])

    renderqa.check(tmp_path, book_path, 1, target_pdf=target, source_pdf=first)
    before = runstate.RunState(tmp_path).page(1)["hashes"]["render"]
    renderqa.check(tmp_path, book_path, 1, target_pdf=target, source_pdf=second)
    after = runstate.RunState(tmp_path).page(1)["hashes"]["render"]

    assert before != after, "the source page changed under the reviewer's eyes"


def test_identical_evidence_keeps_a_review_current(tmp_path):
    """The mirror: re-running QA over an unchanged page must not invalidate it.

    Without this, the digest could be `random()` and the two tests above would
    still pass — and every review would go stale on every re-run.
    """
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "The translated line.")
    target = _pdf_page(tmp_path / "target.pdf", [("The translated line.", 60, 100)])
    source = _pdf_page(tmp_path / "source.pdf", [("An English source line.", 54, 100)])

    renderqa.check(tmp_path, book_path, 1, target_pdf=target, source_pdf=source)
    before = runstate.RunState(tmp_path).page(1)["hashes"]["render"]
    renderqa.check(tmp_path, book_path, 1, target_pdf=target, source_pdf=source)

    assert runstate.RunState(tmp_path).page(1)["hashes"]["render"] == before


def test_a_one_sheet_page_still_has_an_identity(tmp_path):
    """The ordinary case, unchanged: one sheet, one stable digest."""
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "The translated line.")
    target = _pdf_page(tmp_path / "target.pdf", [("The translated line.", 60, 100)])

    written = renderqa.check(tmp_path, book_path, 1, target_pdf=target)
    assert written["renders"]["target_sheets"] == [written["renders"]["target"]]
    assert runstate.RunState(tmp_path).page(1)["hashes"]["render"]


def test_doctor_reports_the_backend_the_pipeline_will_actually_use():
    """`doctor` and `wordrender` must never disagree about the same machine.

    They did: `doctor` looked for `WINWORD` on PATH, and Word is never on PATH —
    it is driven through COM — so every Windows machine with Word installed was
    told Word was missing, and LibreOffice was not mentioned at all. The pipeline
    meanwhile found Word fine. A health check that contradicts the code it is
    checking on is worse than none; this pins `doctor` to the pipeline's answer.
    """
    import importlib.util
    from pathlib import Path

    entry = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" \
        / "scripts" / "revayat-novel.py"
    spec = importlib.util.spec_from_file_location("revayat_novel_cli", entry)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    reported = cli.doctor()["optional_tools"]["render"]
    backend = wordrender.backend()
    if backend == "word":
        assert reported.startswith("Microsoft Word"), reported
    elif backend == "libreoffice":
        assert reported.startswith("LibreOffice at "), reported
        assert wordrender.find_libreoffice() in reported
    else:
        assert reported.startswith("not found"), reported
        assert wordrender.unavailable_reason() in reported
    assert "WINWORD" not in reported


# --------------------------------------------------------------------------- #
# A page declares its own size, and the renderer has no ceiling of its own
# --------------------------------------------------------------------------- #

def _hostile_pdf(path, inches: float = 200.0):
    """A legal one-page PDF whose page is `inches` square. Tiny on disk."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page(width=inches * 72, height=inches * 72)
    page.insert_text((72, 72), "a page this size is not a book page", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def test_the_render_ceiling_refuses_a_giant_page_and_clears_every_real_one():
    """Measured before this existed: a 40-inch page at 150 dpi rendered 36 Mpx in
    0.02 s with no complaint. A 200-inch page at the crop path's 400 dpi is 6.4
    Gpx. The ceiling has to stop that and never touch a real book."""
    import bookir as ir

    for dpi in (110, 150, 300, 400):
        with pytest.raises(ir.RenderTooLarge):
            ir.check_render_area(200 * 72, 200 * 72, dpi)

    # Every legitimate case, at every dpi this pipeline uses, plus a margin.
    a4 = (595.3, 841.9)
    letter = (612.0, 792.0)
    vaziri = (16.5 / 2.54 * 72, 23.5 / 2.54 * 72)
    for w, h in (a4, letter, vaziri):
        for dpi in (110, 150, 300, 400):
            ir.check_render_area(w, h, dpi)
    ir.check_render_area(*a4, 1200)                 # ~140 Mpx: a real print scan
    w, h = ir.check_render_area(*letter, 400)
    assert (w, h) == (3400, 4400)


def test_render_png_declines_a_hostile_page_instead_of_allocating(tmp_path):
    """`None` is this function's word for "could not render"; every caller
    already turns it into `unverified`. It must not become a 2.5 GB pixmap."""
    pdf = _hostile_pdf(tmp_path / "giant.pdf")
    assert pdf.stat().st_size < 10_000, "the fixture should be tiny on disk"
    out = tmp_path / "renders" / "giant.png"

    assert pagecheck.render_png(pdf, 0, out, 150) is None
    assert not out.exists(), "an artefact was written for a refused render"


def test_the_crop_path_refuses_a_hostile_page_by_name(tmp_path):
    """No embedded raster covers this page, so the crop path would render it at
    400 dpi. It has to refuse with the named error, not degrade quietly."""
    import bookir as ir
    import rasters

    pymupdf = pytest.importorskip("pymupdf")
    pdf = _hostile_pdf(tmp_path / "giant.pdf")
    with pymupdf.open(str(pdf)) as document:
        with pytest.raises(ir.RenderTooLarge) as refused:
            rasters.crop_from_source(document, 1, [72, 72, 720, 720], tmp_path)
    assert "not a book page" in str(refused.value)


def test_a_timed_out_render_kills_the_whole_tree_not_just_the_launcher(
        monkeypatch, tmp_path):
    """Both backends are launchers, so killing the child leaves the renderer.

    The module's docstring promised a process-tree kill; `subprocess.run`
    signals the direct child only, and the real renderer - WINWORD.EXE started
    through COM, or the soffice.bin the launcher forked - is a generation
    further down. It survived, with its COM teardown never reached.
    """
    killed = []

    def record_and_kill(process):
        # It has to kill as well as record. A stub that only records leaves the
        # sleeper below running for its full 30s, and the guarded runner fails
        # the whole run for a leaked process - which is the exact leak this
        # test exists to prove is now closed.
        killed.append(process)
        process.kill()

    monkeypatch.setattr(wordrender, "_kill_tree", record_and_kill)

    # A command that outlives its timeout without doing anything else.
    slow = [sys.executable, "-c", "import time; time.sleep(30)"]
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        wordrender._run_bounded(slow, timeout=1)
    elapsed = time.monotonic() - started

    assert killed, "the tree was never killed; only the direct child was signalled"
    # When the thing under test is a *bound*, assert the bound. `killed`
    # only says the code noticed; the clock says it acted. A version that
    # raised correctly, killed correctly and still blocked until the child
    # finished would satisfy every other assertion here - and would be
    # exactly the failure the timeout exists to prevent.
    assert elapsed < 10.0, (
        f"the call was bounded at 1s and took {elapsed:.1f}s: it waited for "
        f"the child instead of returning when the bound expired")


def test_libreoffice_is_given_a_profile_of_its_own(monkeypatch, tmp_path):
    """Without one it is single-instance, and a second render returns 0 with no PDF.

    That failure is silent by construction: exit code 0, empty stderr, and a
    "produced no PDF" whose detail names nothing.
    """
    seen = {}

    def capture(command, timeout):
        seen["command"] = command
        (tmp_path / "renders").mkdir(parents=True, exist_ok=True)
        (tmp_path / "renders" / "book.pdf").write_bytes(b"%PDF-1.4\n")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(wordrender, "find_libreoffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(wordrender, "_run_bounded", capture)

    docx = tmp_path / "book.docx"
    docx.write_bytes(b"not really a document")
    wordrender._with_libreoffice(docx, tmp_path / "renders", timeout=5)

    profile = [a for a in seen["command"] if a.startswith("-env:UserInstallation=")]
    assert profile, f"no private profile in {seen['command']}"
    assert profile[0].split("=", 1)[1].startswith("file:"), (
        "LibreOffice requires a file:// URL here; a bare path is ignored")
    assert "--norestore" in seen["command"]


def _cli():
    """The dispatcher, loaded by path: its filename is not an importable name."""
    import importlib.util
    from pathlib import Path

    entry = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" \
        / "scripts" / "revayat-novel.py"
    spec = importlib.util.spec_from_file_location("revayat_novel_cli", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_doctor_finds_a_tool_that_is_installed_but_not_on_path(tmp_path, monkeypatch):
    """`shutil.which` alone is not "is it installed" on Windows.

    Measured: MinerU at `G:\Program Files\MinerU`, Tesseract under
    `C:\Program Files`, and OCRmyPDF inside the project venv were all reported
    missing on a machine that had all three — so `doctor` printed install
    instructions for software the reader had already installed, the OCR tier
    skipped itself, and `ocr_sidecar` refused to run. That is the same defect
    the render backend had with `WINWORD`.

    Nothing here depends on what this machine actually has: the search table and
    the drive list are both replaced, so the test measures the *mechanism*.
    """
    import extract

    cli = _cli()
    installed = tmp_path / "Program Files" / "Thing" / "thing.exe"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")

    monkeypatch.setattr(extract.shutil, "which", lambda name: None)
    monkeypatch.setattr(extract, "_drives", lambda: [str(tmp_path)])
    # Built with this platform's separator. The shipped table is Windows-only,
    # but the mechanism it drives is not, and a hard-coded backslash passes on
    # Windows and fails everywhere else — which is exactly what it did.
    pattern = os.sep.join(["<drive>", "Program Files", "Thing", "thing.exe"])
    monkeypatch.setattr(extract, "BUNDLED_TOOLS", {"thing": (pattern,)})

    assert cli.find_tool(["thing"], "thing") == str(installed)
    # A tool with no table entry still answers None rather than raising.
    assert cli.find_tool(["nothing"], "nothing") is None
    # And PATH still wins when it has an answer.
    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/thing")
    assert extract.find_tool(["thing"], "thing") == "/usr/bin/thing"


def test_doctor_picks_the_newest_versioned_install(tmp_path, monkeypatch):
    """Ghostscript installs into `gs10.07.1`; a machine may hold several."""
    import extract

    for version in ("gs10.02.0", "gs10.07.1"):
        binary = tmp_path / "gs" / version / "bin" / "gswin64c.exe"
        binary.parent.mkdir(parents=True)
        binary.write_text("", encoding="utf-8")

    monkeypatch.setattr(extract.shutil, "which", lambda name: None)
    monkeypatch.setattr(extract, "_drives", lambda: [str(tmp_path)])
    pattern = os.sep.join(["<drive>", "gs", "gs*", "bin", "gswin64c.exe"])
    monkeypatch.setattr(extract, "BUNDLED_TOOLS", {"ghostscript": (pattern,)})

    assert extract.find_tool(["gs"], "ghostscript").endswith(
        str(Path("gs10.07.1") / "bin" / "gswin64c.exe"))


def test_doctor_asks_extract_where_ocrmypdf_is(monkeypatch):
    """It can live in this interpreter rather than on PATH, and `which` cannot see that.

    `extract.find_ocrmypdf` already resolves both cases; `doctor` repeats its
    answer instead of guessing a second time — the same rule the render backend
    follows.
    """
    import extract

    cli = _cli()
    monkeypatch.setattr(extract, "find_ocrmypdf",
                        lambda: ["/usr/bin/python", "-m", "ocrmypdf"])
    assert cli.ocrmypdf_launcher() == "/usr/bin/python -m ocrmypdf"

    monkeypatch.setattr(extract, "find_ocrmypdf", lambda: None)
    assert cli.ocrmypdf_launcher() is None

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


def test_a_paragraph_that_is_not_right_to_left_is_rejected():
    """Flush left, well short of the right margin: the bidi property was lost.

    The box has to sit *inside* the body, or overflow is reported too and the
    finding under test is no longer the only one — which is the point of
    asserting an exact set here.
    """
    left = SETUP["margin_inner_pt"] + 2
    view = _view(blocks=[_text(PERSIAN, [left, 100, left + 150, 140])])
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

    def wedged(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="word", timeout=kwargs.get("timeout", 1))

    monkeypatch.setattr(wordrender.subprocess, "run", wedged)
    with pytest.raises(wordrender.RenderError) as raised:
        wordrender.render(docx, tmp_path / "renders", timeout=1)
    assert "terminated" in str(raised.value)


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

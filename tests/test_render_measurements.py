"""Page measurements, structure and bounded raster geometry."""

from __future__ import annotations

import pytest
import bookir as ir
import pagecheck
import renderqa
from render_fixtures import BODY_RIGHT, PERSIAN, PERSIAN_OTHER, SETUP, _codes, _expected, _hostile_pdf, _image, _paged_book, _text, _view, _well_set_page

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

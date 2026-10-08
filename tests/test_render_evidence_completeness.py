"""A missing current sheet cannot be approved using an all-yes answer sheet."""
import json

import bookir as ir
import renderqa
import review
from tests_support import png_bytes
from render_fixtures import _latin_book, _pdf_page, _two_sheet_pdf


def test_supplied_single_image_cannot_hide_second_target_sheet(tmp_path):
    target = "Sample rendered line that must appear on the page."
    book = _latin_book(tmp_path, target)
    source = _pdf_page(tmp_path / "source.pdf", [("A line of source prose.", 54, 100)])
    rendered = _two_sheet_pdf(tmp_path / "target.pdf", target, "Second target sheet.")
    image = tmp_path / "override.png"
    image.write_bytes(png_bytes(8, 8))
    written = renderqa.check(tmp_path, book, 1, target_pdf=rendered,
                             source_pdf=source, target_image=image)
    assert len(written["renders"]["target_sheets"]) == 2
    assert all((tmp_path / name).is_file()
               for name in written["renders"]["target_sheets"])
    assert (tmp_path / written["renders"]["target"]).read_bytes() != image.read_bytes()


def test_visual_review_refuses_missing_expected_current_sheet(tmp_path):
    image = tmp_path / "sheet1.png"
    image.write_bytes(png_bytes(8, 8))
    renders = {"target": image.name, "target_sheets": [image.name, "sheet2.png"],
               "complete": False, "missing": ["sheet2.png"]}
    ir.write_text(tmp_path / "qa/pages/page-0001.json", json.dumps(
        {"ok": False, "verified": False, "renders": renders}))
    result = review.record(tmp_path, 1, {name: True for name in review.QUESTIONS},
                           render=renderqa.evidence(tmp_path, renders))
    assert result["ok"] is False, result
    assert result["refused"] == "incomplete-render-evidence"
    assert not review.review_path(tmp_path, 1).exists()

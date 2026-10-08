"""Explicitly partial OCR extraction cannot authorize delivery."""
import bookir as ir
import qa


def test_unknown_ocr_page_blocks_complete_quality_gate():
    book = ir.new_book(source_format="pdf")
    book["source"]["ocr_coverage"] = {"complete": False, "unknown_pages": [1], "pages": []}
    report = qa.check_book(book, require_complete=True).summary()
    assert not report["ok"], report
    assert "ocr-coverage-unverified" in report["by_code"]

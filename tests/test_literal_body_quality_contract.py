"""Literal body slots print unchanged; mixed untranslated prose remains blocking."""
import bookir as ir
import falint
import qa


def test_literal_body_prints_without_false_translation_or_typography_errors():
    literal = "`A literal English sentence that must remain byte for byte.`"
    book = ir.new_book(source_format="epub")
    book["blocks"] = [ir.make_block("paragraph", 1, text=literal, target=literal)]
    report = qa.check_book(book, strict=True)
    assert report.summary()["ok"], report.summary()
    assert falint.lint_text(literal) == []
    book["blocks"][0]["target"] = ""
    report = qa.check_book(book, strict=True)
    assert report.summary()["ok"], report.summary()
    book["blocks"][0]["text"] = literal + " A normal sentence still needs translation."
    report = qa.check_book(book, strict=True)
    assert not report.summary()["ok"]
    assert any(f["code"] == "untranslated-block" for f in report.findings)

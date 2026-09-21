"""Note occurrences are not distinct blocks; translator prose is not a source."""

import pytest

import bookir as ir
import bookwrite
import meaning
import notegraph
import published
from test_repair_budget import _review


def noted(origin="translator"):
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    book["blocks"] = [ir.make_block("paragraph", 1, text="A local custom.",
                                    target="رسمی محلی. [[fn:fn0001]]"),
                      ir.make_block("paragraph", 2, text="The next passage.", target="بند بعدی.")]
    note = ir.make_footnote(1, anchor_block="b00001", text="Original submitted note.", origin=origin)
    note["target"] = "توضیح مترجم دربارهٔ رسم محلی."
    book["footnotes"] = [note]
    return book


@pytest.mark.parametrize("origin", ["source", "translator"])
@pytest.mark.parametrize("place", ["same", "different"])
def test_repeated_note_occurrences_refuse_before_commit(tmp_path, origin, place):
    book = noted(origin)
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    before = path.read_bytes()
    with pytest.raises(bookwrite.Refused) as refusal:
        with bookwrite.transaction(path, actor="duplicate") as tx:
            tx.book["blocks"][0 if place == "same" else 1]["target"] += " [[fn:fn0001]]"
    assert refusal.value.reason == "invalid-book"
    assert path.read_bytes() == before


def test_duplicate_source_occurrences_are_reported():
    book = noted("source")
    book["blocks"][0]["text"] += " [[fn:fn0001]] [[fn:fn0001]]"
    assert any("fn0001" in problem for problem in notegraph.problems(book))


def test_translator_inventory_keeps_provenance_separate_from_source():
    book = noted()
    unit = next(unit for unit in published.units(book) if unit["id"] == "fn0001")
    assert unit["source"] is None
    assert unit["submitted"] == "Original submitted note."
    assert unit["anchor"] == "b00001" and unit["context"] == "A local custom."


def test_editing_submitted_note_text_cannot_reset_its_repair_budget(tmp_path):
    path, out = tmp_path / "book.json", tmp_path / "meaning"
    book = noted()
    ir.save_book(book, path)
    for index in range(3):
        book = ir.load_book(path)
        book["footnotes"][0]["text"] = f"Changed submitted wording {index}."
        book["footnotes"][0]["target"] = f"توضیح نیازمند بررسی {index}."
        ir.save_book(book, path)
        _review(path, out, f"?? fn0001 addition\nUnsupported claim {index}.\n")
    result = meaning.repair_requests(out)
    assert result["ok"] is False and result["refused"] == "rounds-exhausted"


def test_literal_marker_examples_do_not_add_occurrences():
    book = noted()
    book["blocks"][0]["target"] += " `[[fn:fn0001]]`"
    assert notegraph.problems(book) == []


def test_a_single_caption_anchor_survives_the_production_package(tmp_path):
    from build_docx import Builder
    from test_package_mutations import _options
    import qa

    book = noted()
    book["blocks"][0]["type"] = "caption"
    assert notegraph.problems(book) == []
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    with bookwrite.transaction(path, actor="caption-control"):
        pass
    destination = tmp_path / "caption.docx"
    Builder(ir.load_book(path), tmp_path, _options()).build(destination)
    assert qa.check_docx(destination, ir.load_book(path)).summary()["ok"]


def test_a_note_falling_back_to_submitted_text_is_still_published_and_pending():
    book = noted()
    book["footnotes"][0]["target"] = None
    unit = next(unit for unit in published.units(book) if unit["id"] == "fn0001")
    assert unit["source"] is None and unit["target"] == "Original submitted note."
    assert [unit["id"] for unit in published.pending(book)] == ["fn0001"]

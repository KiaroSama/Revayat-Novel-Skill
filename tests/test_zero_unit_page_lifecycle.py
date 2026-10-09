"""A page with no prose requires no fabricated translation response."""
import json

import bookir as ir
import pagerun
import renderqa
import review
import runstate
from tests_support import png_bytes


def test_zero_unit_page_merges_without_a_reply(tmp_path):
    book = ir.new_book(source_format="epub")
    book["blocks"] = [ir.make_block("layout", 1, page=1, controls="\t\n")]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    pages = tmp_path / "pages"
    manifest = pagerun.build(path, pages)
    assert manifest["chunks"][0]["unit_ids"] == []
    before = path.read_bytes()
    result = pagerun.merge_page(path, pages, 1)
    assert result["ok"], result
    assert not (pages / manifest["chunks"][0]["output"]).exists()
    assert path.read_bytes() == before
    assert pagerun.accept(path, pages, 1)["ok"] is False
    image = tmp_path / "target.png"
    image.write_bytes(png_bytes(8, 8))
    renders = {"target": image.name, "target_sheets": [image.name], "complete": True}
    ir.write_text(pagerun.qa_report_path(tmp_path, 1), json.dumps(
        {"ok": True, "verified": True, "renders": renders}))
    state = runstate.RunState(tmp_path)
    state.set_page(1, "qa_passed", hashes={"translation": pagerun.translation_hash(path, 1)})
    assert pagerun.accept(path, pages, 1)["refused"] == "not-reviewed"
    review.record(tmp_path, 1, {name: True for name in review.QUESTIONS},
                  render=renderqa.evidence(tmp_path, renders))
    assert pagerun.accept(path, pages, 1)["ok"]
    assert runstate.RunState(tmp_path).page(1)["state"] == "accepted"
    assert not (pages / manifest["chunks"][0]["output"]).exists()
    assert path.read_bytes() == before

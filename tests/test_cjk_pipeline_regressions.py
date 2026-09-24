"""Both production routes must preserve an unspaced paragraph through merge."""
from __future__ import annotations

import pytest

import bookir as ir
import chunk
import merge
import pagerun
import worksheet


@pytest.mark.parametrize("route", ["chunks", "pages"])
def test_cjk_source_survives_production_build_and_merge(tmp_path, route):
    text = "彼女は窓を開けた。雨が降っていた。それでも外へ出た。" * 200
    book = ir.new_book(source_format="epub", lang_source="ja", pages=1)
    book["blocks"] = [ir.make_block("paragraph", 1, text=text, page=1)]
    path, out = tmp_path / "book.json", tmp_path / route
    ir.save_book(book, path)
    builder = chunk if route == "chunks" else pagerun
    manifest = builder.build(path, out, glossary_path=None, budget=1400)
    assert len(manifest["chunks"]) > 1
    answers = []
    for entry in manifest["chunks"]:
        sheet = (out / entry["file"]).read_text(encoding="utf-8")
        assert len(sheet) <= 1400
        reply = [worksheet.request_line(entry["request"])]
        for unit_id in entry["unit_ids"]:
            answer = f"ترجمهٔ بخش {len(answers) + 1}."
            answers.append(answer)
            reply.extend([f"@@ {unit_id} {entry['unit_kinds'][unit_id]}", answer])
        ir.write_text(out / entry["output"], "\n".join(reply) + "\n")
    report = (merge.merge(path, out) if route == "chunks"
              else pagerun.merge_page(path, out, 1))
    assert report["ok"], report
    finished = ir.load_book(path)
    assert len(finished["blocks"]) == 1
    assert finished["blocks"][0]["text"] == text
    assert finished["blocks"][0]["target"] == " ".join(answers)

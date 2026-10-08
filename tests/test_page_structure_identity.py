"""Authored empty grids and controls belong to page evidence, not prose units."""
import copy

import bookir as ir
import pageidentity
import preview


def test_empty_table_grid_mutation_moves_page_identity_and_preview_closure(tmp_path):
    book = ir.new_book(source_format="docx")
    book["tables"] = [
        {"id": "t1", "rows": 1, "columns": 1,
         "cells": [{"row": 1, "cell": 1, "row_span": 1, "col_span": 1}]},
        {"id": "t2", "rows": 1, "columns": 1,
         "cells": [{"row": 1, "cell": 1, "row_span": 1, "col_span": 1}]},
    ]
    book["blocks"] = [ir.make_block("table", 1, page=1, table="t1"),
                      ir.make_block("table", 2, page=2, table="t2")]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    before = pageidentity.translation_hash(path, 1)
    page = preview.page_book(book, 1)
    assert page["tables"] == [book["tables"][0]]
    assert preview.page_book(book, 999)["tables"] == []
    changed = copy.deepcopy(book)
    changed["tables"][0]["columns"] = 2
    changed["tables"][0]["cells"][0]["col_span"] = 2
    ir.save_book(changed, path)
    assert pageidentity.translation_hash(path, 1) != before

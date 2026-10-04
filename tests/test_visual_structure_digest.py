"""A visual approval must become stale when structure changes without word edits."""

from __future__ import annotations
import copy
import logging

import pytest
import bookir as ir
import pagerun

LOG = logging.getLogger(__name__)


def fixture(tmp_path):
    book = ir.new_book(source_format="docx", pages=2)
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=1, text="One", target="یک"),
        ir.make_block("paragraph", 2, page=2, text="Two", target="دو"),
    ]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return book, path


@pytest.mark.parametrize(
    "fields",
    [
        {"type": "heading", "level": 2},
        {"type": "blockquote"},
        {"type": "listitem", "ordered": True, "level": 2},
        {"table": "tbl0001", "row": 1, "cell": 1},
        {"bookmarks": ["destination"]},
        {"links": [{"text": "یک", "href": "https://example.org"}]},
        {"font_size_pt": 32},
        {"target": "\nیک\n"},
    ],
)
def test_real_published_structure_changes_invalidate_old_evidence(tmp_path, fields):
    book, path = fixture(tmp_path)
    before = pagerun.translation_hash(path, 1)
    book["blocks"][0].update(fields)
    ir.save_book(book, path)
    assert pagerun.translation_hash(path, 1) != before
    assert (
        pagerun._translation_moved(path, 1, {"hashes": {"translation": before}})[0]
        == "translation-changed"
    )
    LOG.info("Visual structure change correctly invalidated old evidence")


@pytest.mark.parametrize(
    "field,value",
    [
        ("row", 2),
        ("cell", 2),
        ("row_span", 2),
        ("col_span", 2),
        ("parent_table", "tbl0002"),
        ("parent_row", 2),
        ("parent_cell", 2),
    ],
)
def test_table_location_and_ownership_are_part_of_the_page_identity(
    tmp_path, field, value
):
    book, path = fixture(tmp_path)
    book["blocks"][0].update(
        table="tbl0001",
        row=1,
        cell=1,
        row_span=1,
        col_span=1,
        parent_table="tbl0003",
        parent_row=1,
        parent_cell=1,
    )
    ir.save_book(book, path)
    before = pagerun.translation_hash(path, 1)
    book["blocks"][0][field] = value
    ir.save_book(book, path)
    assert pagerun.translation_hash(path, 1) != before


def test_old_page3_approval_is_not_relabelled_as_current(tmp_path):
    _, path = fixture(tmp_path)
    current = pagerun.translation_hash(path, 1)
    old = "page3:" + current.split(":", 1)[1]
    assert (
        pagerun._translation_moved(path, 1, {"hashes": {"translation": old}})[0]
        == "unverified-digest"
    )


def test_unrelated_prose_and_diagnostic_changes_do_not_reopen_page(tmp_path):
    book, path = fixture(tmp_path)
    before = pagerun.translation_hash(path, 1)
    book["blocks"][1].update(target="متن جدید صفحهٔ دیگر", type="heading", level=1)
    book["blocks"][0]["diagnostic_note"] = "not published"
    book["blocks"][0]["_anchor"] = "transient-builder-cache"
    ir.save_book(book, path)
    assert pagerun.translation_hash(path, 1) == before
    again = copy.deepcopy(book)
    again["blocks"][0] = dict(reversed(list(again["blocks"][0].items())))
    ir.save_book(again, path)
    assert pagerun.translation_hash(path, 1) == before


@pytest.mark.parametrize("mutation", ["align", "tab", "field", "different_first_page"])
def test_running_structure_without_changed_words_invalidates_page(tmp_path, mutation):
    book, path = fixture(tmp_path)
    book["sections"] = [
        {
            "start_block": "b00001",
            "headers": {
                "default": {
                    "paragraphs": [
                        {
                            "align": "left",
                            "pieces": [
                                {"id": "rh0001", "text": "Source", "target": "متن"}
                            ],
                        }
                    ]
                }
            },
        }
    ]
    ir.save_book(book, path)
    before = pagerun.translation_hash(path, 1)
    record = book["sections"][0]
    line = record["headers"]["default"]["paragraphs"][0]
    if mutation == "align":
        line["align"] = "right"
    elif mutation == "tab":
        line["pieces"].append({"tab": True})
    elif mutation == "field":
        line["pieces"].append({"field": "PAGE"})
    else:
        record["different_first_page"] = True
    ir.save_book(book, path)
    assert pagerun.translation_hash(path, 1) != before

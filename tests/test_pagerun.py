"""Resumable page scheduling and recorded lifecycle state."""

from __future__ import annotations

import bookir as ir
import pagerun
from tests_support import reply_text
import runstate
from page_fixtures import _book, _built, _job, _mark, _prose, _save

def test_the_next_page_is_the_first_one_not_accepted(tmp_path):
    book = _book([_prose(page, f"Page{page}") for page in range(1, 6)])
    _, pages = _built(book, tmp_path)

    assert pagerun.status(pages)["next"] == 1
    for page in (1, 2, 3):
        _mark(tmp_path, page, "accepted")
    _mark(tmp_path, 4, "failed", error="render QA found a missing picture")

    progress = pagerun.status(pages)
    assert progress["next"] == 4
    assert progress["accepted"] == 3
    assert progress["failed"] == [4]
    assert progress["by_state"] == {"accepted": 3, "extracted": 1, "failed": 1}
    assert pagerun.next_page(pages)["attempts"] == 1
    assert "missing picture" in pagerun.next_page(pages)["last_error"]


def test_building_a_page_is_itself_the_first_step_of_its_lifecycle(tmp_path):
    """Cutting the page out of the book is a thing that happened to it."""
    book = _book([_prose(page, f"Page{page}") for page in range(1, 4)])
    book_path = _save(book, tmp_path)
    assert runstate.RunState(tmp_path).pages() == {}

    manifest = pagerun.build(book_path, tmp_path / "pages")
    assert pagerun.status(tmp_path / "pages")["by_state"] == {"extracted": 3}
    assert [entry["status"] for entry in manifest["chunks"]] == ["extracted"] * 3


def test_resuming_after_a_failure_re_runs_only_the_page_that_failed(tmp_path):
    book = _book([_prose(page, f"Page{page}") for page in range(1, 6)])
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)

    for page in (1, 2, 3):
        _mark(tmp_path, page, "accepted")
    _mark(tmp_path, 4, "failed", error="the page came out blank")

    # Rebuilding an unchanged book disturbs nothing.
    assert pagerun.build(book_path, pages)["invalidated"] == []
    assert pagerun.status(pages)["by_state"] == {"accepted": 3, "extracted": 1,
                                                 "failed": 1}

    # Correcting page 4 invalidates page 4 and nothing else — not the pages
    # whose only change is what they now see as neighbouring context.
    corrected = ir.load_book(book_path)
    corrected["blocks"][3]["text"] += " A sentence the scan had swallowed."
    ir.save_book(corrected, book_path)

    assert pagerun.build(book_path, pages)["invalidated"] == [4]
    after = pagerun.status(pages)
    assert after["next"] == 4
    assert after["by_state"] == {"accepted": 3, "extracted": 2}
    assert runstate.RunState(tmp_path).page(4)["attempts"] == 0
    assert runstate.RunState(tmp_path).page(1)["state"] == "accepted"


def test_an_accepted_page_keeps_its_answer_when_the_run_is_rebuilt(tmp_path):
    book = _book([_prose(page, f"Page{page}") for page in range(1, 4)])
    book_path = _save(book, tmp_path)
    pages = tmp_path / "pages"
    manifest = pagerun.build(book_path, pages)

    job = _job(manifest, 1)
    answer = pages / job["output"]
    ir.write_text(answer, reply_text(pages / job["file"],
                                     "@@ b00001 para\nمتن فارسی\n"))
    _mark(tmp_path, 1, "accepted")

    pagerun.build(book_path, pages)
    assert answer.read_text(encoding="utf-8").strip().endswith("فارسی")
    assert pagerun.status(pages)["pages"][0]["answered"] is True


def test_every_page_accepted_leaves_nothing_to_do(tmp_path):
    book = _book([_prose(page, f"Page{page}") for page in range(1, 4)])
    _, pages = _built(book, tmp_path)
    for page in (1, 2, 3):
        _mark(tmp_path, page, "accepted")

    assert pagerun.status(pages)["next"] is None
    assert pagerun.next_page(pages) is None

"""Acceptance reads current request dependencies, not only printed page identity."""
from __future__ import annotations

import json

import pytest

import bookir as ir
import glossary as gl
import pagerun
import renderqa
import review
import runstate
from tests_support import png_bytes, reply_text


@pytest.fixture
def checked_page(tmp_path):
    book = ir.new_book(source_format="epub")
    book["blocks"] = [ir.make_block("paragraph", 1, page=1, text="Alice read a source sentence."),
                      ir.make_block("paragraph", 2, page=2, text="The neighbouring sentence.")]
    glossary = gl.new_glossary()
    name = gl.make_entry(1, "Alice", category="person")
    name.update(target="آلیس", aliases=["Al"], alias_targets=["آل"], locked=True)
    glossary["entries"] = [name]
    glossary["policy"]["book_voice"] = "restrained"
    glossary_path = tmp_path / "glossary.json"
    ir.write_text(glossary_path, json.dumps(glossary))
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    pages = tmp_path / "pages"
    manifest = pagerun.build(path, pages, glossary_path=glossary_path)
    entry = manifest["chunks"][0]
    ir.write_text(pages / entry["output"], reply_text(
        pages / entry["file"], "@@ b00001 para\nآلیس جمله‌ای از کتاب خواند.\n"))
    assert pagerun.merge_page(path, pages, 1)["ok"]
    image = tmp_path / "target.png"
    image.write_bytes(png_bytes(8, 8))
    renders = {"target": image.name, "target_sheets": [image.name]}
    ir.write_text(pagerun.qa_report_path(tmp_path, 1), json.dumps(
        {"ok": True, "verified": True, "renders": renders}))
    state = runstate.RunState(tmp_path)
    state.set_page(1, "qa_passed", hashes={"translation": pagerun.translation_hash(path, 1)})
    evidence = renderqa.evidence(tmp_path, renders)
    review.record(tmp_path, 1, {name: True for name in review.QUESTIONS}, render=evidence)
    return path, pages, glossary_path, glossary, state


def test_changed_voice_refuses_acceptance_without_mutating_state(checked_page):
    path, pages, glossary_path, glossary, state = checked_page
    original = state.path.read_bytes()
    glossary["policy"]["book_voice"] = "comic"
    ir.write_text(glossary_path, json.dumps(glossary))
    assert pagerun.translation_hash(path, 1) == state.page(1)["hashes"]["translation"]
    result = pagerun.accept(path, pages, 1)
    assert result["ok"] is False, result
    assert result["refused"] == "stale-source", result
    assert state.path.read_bytes() == original


@pytest.mark.parametrize("change", ["alias", "context", "missing-glossary", "malformed-glossary"])
def test_other_live_dependency_changes_refuse_without_acceptance_mutation(checked_page, change):
    path, pages, glossary_path, glossary, state = checked_page
    original = state.path.read_bytes()
    if change == "alias":
        glossary["entries"][0]["alias_targets"] = ["الی"]
        ir.write_text(glossary_path, json.dumps(glossary))
    elif change == "context":
        book = ir.load_book(path)
        book["blocks"][1]["text"] = "Changed neighbouring context."
        ir.save_book(book, path)
    elif change == "missing-glossary":
        glossary_path.unlink()
    else:
        ir.write_text(glossary_path, "{")
    assert pagerun.translation_hash(path, 1) == state.page(1)["hashes"]["translation"]
    result = pagerun.accept(path, pages, 1)
    assert result["ok"] is False, result
    assert result["refused"] == ("unverified" if "glossary" in change else "stale-source")
    assert state.path.read_bytes() == original


def test_unchanged_live_dependencies_allow_acceptance(checked_page):
    path, pages, _, _, state = checked_page
    assert pagerun.accept(path, pages, 1)["ok"]
    assert runstate.RunState(path.parent).page(1)["state"] == "accepted"

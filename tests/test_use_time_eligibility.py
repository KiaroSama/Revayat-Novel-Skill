"""Both routes reject stale or unknowable dependencies at the point of use."""

from __future__ import annotations

import json

import pytest

import bookir as ir
import chunk
import eligible
import glossary as gl
import merge
import pagerun
import worksheet
from test_status_merge_agreement import _run, _answer
from test_footnote_graph import _heads
from tests_support import reply_text


def page_run(tmp_path):
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    book["blocks"] = [ir.make_block("paragraph", i, text=f"Alice entered room {i}.", page=i)
                      for i in (1, 2)]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    glossary = tmp_path / "glossary.json"
    data = gl.new_glossary()
    data["entries"] = [gl.make_entry(1, "Alice")]
    data["entries"][0]["target"] = "آلیس"
    gl.save(data, glossary)
    pages = tmp_path / "pages"
    manifest = pagerun.build(path, pages, glossary_path=glossary)
    entry = manifest["chunks"][0]
    units = worksheet.read_worksheet((pages / entry["file"]).read_text(encoding="utf-8"))
    ir.write_text(pages / entry["output"], reply_text(pages / entry["file"],
        "\n".join(f"@@ {unit['id']} {unit['kind']}\nآلیس وارد اتاق شد.\n" for unit in units)))
    return path, pages, glossary, entry


@pytest.mark.parametrize("change", ["source", "geometry", "glossary", "voice", "context", "missing-glossary"])
def test_page_reply_is_rechecked_without_rebuilding(tmp_path, change):
    path, pages, glossary, entry = page_run(tmp_path)
    if change in ("source", "geometry", "context"):
        book = ir.load_book(path)
        if change == "geometry":
            book["page"]["width_pt"] += 20
        else:
            book["blocks"][0 if change == "source" else 1]["text"] = "Nobody entered the room."
        ir.save_book(book, path)
    elif change == "missing-glossary":
        glossary.unlink()
    else:
        data = gl.load(glossary)
        if change == "voice":
            data["policy"]["book_voice"] = "Formal narration."
        else:
            data["entries"][0]["target"] = "الیس"
        gl.save(data, glossary)
    before = path.read_bytes()
    proof = eligible.every(pages)[0]
    assert proof["usable"] is False and proof["retryable"] is True
    assert proof["detail"]
    assert pagerun.next_page(pages)["reason"]
    assert pagerun.merge_page(path, pages, entry["page"], glossary_path=glossary)["ok"] is False
    assert path.read_bytes() == before


def test_unchanged_page_reply_is_usable_and_merges(tmp_path):
    path, pages, glossary, entry = page_run(tmp_path)
    assert eligible.every(pages)[0]["usable"] is True
    assert pagerun.merge_page(path, pages, entry["page"], glossary_path=glossary)["ok"] is True
    assert ir.load_book(path)["blocks"][0]["target"] == "آلیس وارد اتاق شد."


@pytest.mark.parametrize("digest", ["", "future:123", "page:123", "units:123"])
def test_unknown_freshness_is_never_usable_or_unschedulable(tmp_path, digest):
    path, out, entry = _run(tmp_path)
    _answer(out, entry)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["chunks"][0]["source_sha256"] = digest
    ir.write_text(manifest_path, json.dumps(manifest))
    before = path.read_bytes()
    proof = eligible.every(out)[0]
    assert not proof["usable"] and proof["retryable"]
    assert chunk.status(out)["next"] == entry["id"]
    assert merge.merge(path, out)["ok"] is False
    assert path.read_bytes() == before


def test_worksheet_cannot_override_the_manifest_request(tmp_path):
    path, out, entry = _run(tmp_path)
    sheet = out / entry["file"]
    text = sheet.read_text(encoding="utf-8").replace(entry["request"], "req1:0000000000000000")
    ir.write_text(sheet, text)
    _answer(out, entry)
    assert eligible.every(out)[0]["usable"] is False
    assert merge.merge(path, out)["ok"] is False


def test_a_missing_book_keeps_the_job_schedulable(tmp_path):
    path, out, entry = _run(tmp_path)
    _answer(out, entry)
    path.rename(path.with_suffix(".saved"))
    proof = eligible.every(out)[0]
    assert proof["usable"] is False and proof["retryable"] is True
    assert chunk.status(out)["next"] == entry["id"]


def test_running_header_note_is_rejected_by_both_consumers(tmp_path):
    path, out, entry = _run(tmp_path)
    _heads(path)
    manifest = chunk.build(path, out, glossary_path=None, budget=4000)
    entry = manifest["chunks"][0]
    running = "rh0001"
    body = "\n".join(f"@@ {unit} {entry['unit_kinds'][unit]}\n"
                      + ("عنوان [[fn:tr-01]]" if unit == running else "متن ترجمه.")
                      for unit in entry["unit_ids"])
    _answer(out, entry, body + "\n@@ tr-01 footnote\nیادداشت مترجم.\n")
    assert eligible.every(out)[0]["usable"] is False
    assert merge.merge(path, out)["ok"] is False


def test_missing_source_page_is_a_retryable_prerequisite_failure(tmp_path, sample_pdf):
    path, pages, glossary, _ = page_run(tmp_path)
    book = ir.load_book(path)
    book["source"].update(format="pdf", path=str(sample_pdf))
    ir.save_book(book, path)
    entry = pagerun.build(path, pages, glossary_path=glossary)["chunks"][0]
    units = worksheet.read_worksheet((pages / entry["file"]).read_text(encoding="utf-8"))
    ir.write_text(pages / entry["output"], reply_text(pages / entry["file"],
        "\n".join(f"@@ {unit['id']} {unit['kind']}\nمتن فارسی.\n" for unit in units)))
    assert eligible.every(pages)[0]["usable"] is True
    (pages / entry["source_pdf"]).unlink()
    proof = eligible.every(pages)[0]
    assert not proof["usable"] and proof["retryable"]
    assert pagerun.merge_page(path, pages, entry["page"], glossary_path=glossary)["ok"] is False


@pytest.mark.parametrize("field,value", [("payload_chars", {}), ("units", "1"), ("page", [])])
def test_wrong_job_field_types_refuse_status_without_a_traceback(tmp_path, field, value):
    path, pages, _, _ = page_run(tmp_path)
    manifest_path = pages / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["chunks"][0][field] = value
    ir.write_text(manifest_path, json.dumps(manifest))
    assert pagerun.status(pages, path)["ok"] is False


def test_segmented_translator_notes_remain_usable_after_replay(tmp_path):
    book = ir.new_book(source_path="original.epub", source_format="epub")
    book["blocks"] = [ir.make_block("paragraph", 1, text="A short original sentence. " * 240)]
    path, out = tmp_path / "book.json", tmp_path / "chunks"
    ir.save_book(book, path)
    manifest = chunk.build(path, out, glossary_path=None, budget=3000)
    assert len(manifest["chunks"]) > 1
    for entry in manifest["chunks"]:
        units = entry["unit_ids"]
        body = "\n".join(f"@@ {unit} {entry['unit_kinds'][unit]}\nمتن فارسی."
                         + ("[[fn:tr-01]]" if unit == units[0] else "") for unit in units)
        _answer(out, entry, body + "\n@@ tr-01 footnote\nیادداشت مترجم.\n")
    assert merge.merge(path, out)["ok"] is True
    before = ir.load_book(path)
    proofs = eligible.every(out)
    assert all(proof["usable"] for proof in proofs), proofs
    assert merge.merge(path, out)["ok"] is True
    after = ir.load_book(path)
    assert after["footnotes"] == before["footnotes"]
    assert after["blocks"] == before["blocks"]

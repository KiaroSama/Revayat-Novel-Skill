"""Glossary, chunking, merge and Persian typography.

The merge protocol is the load-bearing part: if a worksheet id can go missing
without anyone noticing, the book quietly loses paragraphs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bookir as ir
import chunk as chunking
import falint
import glossary as gl
import merge as merging


# --------------------------------------------------------------------------- #
# Glossary
# --------------------------------------------------------------------------- #

def _book_of(*paragraphs: str) -> dict:
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", index, text=text)
        for index, text in enumerate(paragraphs, start=1)
    ]
    return book


def test_scan_finds_repeated_names_and_folds_short_forms():
    book = _book_of(
        "Elizabeth Bennet walked in. Elizabeth Bennet sat down.",
        "Then Elizabeth spoke to Elizabeth Bennet again about Elizabeth.",
        "Elizabeth Bennet left, and Elizabeth returned.",
    )
    entries = gl.scan(book, minimum=2)
    by_source = {e["source"]: e for e in entries}
    assert "Elizabeth Bennet" in by_source
    assert "Elizabeth" in by_source["Elizabeth Bennet"]["aliases"]
    assert "Elizabeth" not in by_source  # absorbed as an alias, not a rival entity


def test_scan_ignores_contractions_and_genitives():
    book = _book_of(
        "I’m sure Alice knew. I’ve seen Alice’s book. I’ll ask Alice about Alice’s cat.",
        "Alice said so. Alice’s friend agreed with Alice.",
    )
    sources = {e["source"] for e in gl.scan(book, minimum=2)}
    assert "Alice" in sources
    assert not any("’" in s for s in sources), sources


def test_scan_rejects_words_that_only_start_sentences():
    book = _book_of(
        "Perhaps not. Perhaps so. Perhaps again.",
        "Perhaps once more. Perhaps finally.",
    )
    assert "Perhaps" not in {e["source"] for e in gl.scan(book, minimum=2)}


def test_term_table_and_compliance_check():
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, "Elizabeth Bennet", category="person", frequency=9,
                          aliases=["Lizzy"])
    entry.update({
        "target": "الیزابت بنت",
        "later_form": "الیزابت بنت",
        "first_form": "الیزابت بنت (Elizabeth Bennet)",
        "locked": True,
    })
    glossary["entries"] = [entry]

    table = gl.render_term_table(
        gl.entries_for_text(glossary, "Elizabeth Bennet arrived"), glossary["policy"]
    )
    assert "الیزابت بنت" in table and "Lizzy" in table

    book = _book_of("Elizabeth Bennet arrived.")
    book["blocks"][0]["target"] = "الیزابت بنت رسید."
    assert gl.check(glossary, book) == []

    book["blocks"][0]["target"] = "الیزابت بِنِت رسید."   # drifted spelling
    violations = gl.check(glossary, book)
    assert len(violations) == 1 and violations[0]["expected"] == "الیزابت بنت"


# --------------------------------------------------------------------------- #
# Chunk / merge
# --------------------------------------------------------------------------- #

def test_chunks_break_on_chapter_headings(tmp_path):
    book = ir.new_book()
    blocks = []
    for chapter in range(3):
        blocks.append(ir.make_block("heading", len(blocks) + 1, level=1,
                                    text=f"Chapter {chapter}"))
        for _ in range(3):
            blocks.append(ir.make_block("paragraph", len(blocks) + 1, text="word " * 60))
    book["blocks"] = blocks
    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)

    manifest = chunking.build(book_path, tmp_path / "chunks", glossary_path=None,
                              budget=100_000)
    assert len(manifest["chunks"]) == 3
    for entry in manifest["chunks"]:
        first = entry["block_ids"][0]
        assert ir.blocks_by_id(book)[first]["type"] == "heading"


def test_worksheet_round_trip_including_image_captions(translated_book, tmp_path):
    book, _assets = translated_book
    for block in ir.iter_text_blocks(book):
        block["target"] = None
    for block in book["blocks"]:
        if block["type"] == "image":
            block["target_alt"] = None
    for note in book["footnotes"]:
        note["target"] = None

    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=400)

    unit_ids = [u for entry in manifest["chunks"] for u in entry["unit_ids"]]
    assert any(u.endswith("#alt") for u in unit_ids), "image captions must be offered"
    assert any(u.startswith("fn") for u in unit_ids), "footnotes must be offered"

    for entry in manifest["chunks"]:
        body = "\n".join(
            f"@@ {unit_id} x\nترجمهٔ {unit_id}\n" for unit_id in entry["unit_ids"]
        )
        (chunks / entry["output"]).write_text(body, encoding="utf-8", newline="")

    report = merging.merge(book_path, chunks)
    assert report["ok"], report
    assert report["units_applied"] == len(unit_ids)

    merged = ir.load_book(book_path)
    assert all((b.get("target") or "").strip() for b in ir.iter_text_blocks(merged))
    assert all((n.get("target") or "").strip() for n in merged["footnotes"])
    assert any((b.get("target_alt") or "").strip()
               for b in merged["blocks"] if b["type"] == "image")


def test_merge_reports_a_dropped_unit_instead_of_losing_it(translated_book, tmp_path):
    book, _assets = translated_book
    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=100_000)

    entry = manifest["chunks"][0]
    kept = entry["unit_ids"][:-1]
    (chunks / entry["output"]).write_text(
        "\n".join(f"@@ {u} x\nمتن\n" for u in kept) + "\n@@ b99999 x\nنامعتبر\n",
        encoding="utf-8", newline="",
    )
    report = merging.merge(book_path, chunks)
    assert not report["ok"]
    assert entry["unit_ids"][-1] in report["missing_units"][entry["id"]]
    assert "b99999" in report["unknown_units"][entry["id"]]


def test_parse_worksheet_ignores_echoed_comments():
    units = merging.parse_worksheet(
        "<!-- header -->\n@@ b00001 para\n<!-- note -->\nمتن فارسی\n\n@@ b00002 para\nدوم\n"
    )
    assert units == {"b00001": "متن فارسی", "b00002": "دوم"}


# --------------------------------------------------------------------------- #
# Persian typography
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "source, expected",
    [
        ("كتاب من", "کتاب من"),                        # Arabic kaf -> Persian keheh
        ("او رفت , و آمد .", "او رفت، و آمد."),        # Latin punctuation in Persian
        ("چه كردی ?", "چه کردی؟"),
        ("كتاب ها", "کتاب‌ها"),                          # ZWNJ for the plural suffix
        ("می روم", "می‌روم"),                            # ZWNJ for the verb prefix
        ("مهم ترین", "مهم‌ترین"),
        ("سال 1984", "سال ۱۹۸۴"),                       # Persian digits
        ("گفت \"سلام\"", "گفت «سلام»"),                  # Persian guillemets
    ],
)
def test_typography_fixes(source, expected):
    assert falint.fix_text(source) == expected


@pytest.mark.parametrize(
    "text",
    [
        "برو به https://example.com/a,b?x=1 و ببین",   # URL punctuation untouched
        "شماره ISBN 978-0-19-953556-9 است",             # identifier digits untouched
        "کد `x = a,b` را ببین",                          # verbatim span untouched
        "پانویس [[fn:fn0001]] اینجاست",                  # protected token untouched
    ],
)
def test_typography_leaves_protected_regions_alone(text):
    fixed = falint.fix_text(text)
    for fragment in ("https://example.com/a,b?x=1", "978-0-19-953556-9",
                     "`x = a,b`", "[[fn:fn0001]]"):
        if fragment in text:
            assert fragment in fixed


def test_typography_preserves_emphasis_markers():
    source = "**پررنگ** و *كج* با , نقطه ."
    fixed = falint.fix_text(source)
    assert ir.emphasis_signature(fixed) == ir.emphasis_signature(source)
    assert "**پررنگ**" in fixed and "*کج*" in fixed


def test_lint_flags_untranslated_and_arabic_forms():
    codes = {f["code"] for f in falint.lint_text(
        "He walked slowly across the room and said nothing at all to her."
    )}
    assert "untranslated" in codes
    assert "arabic-forms" in {f["code"] for f in falint.lint_text("كتاب من")}
    assert falint.lint_text("او رفت و در را بست.") == []

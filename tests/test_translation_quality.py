"""The gates, run against a real translation instead of filler.

Placeholder Persian passes every check in this suite, which is exactly why the
checks needed this file. A gate that only ever sees "ترجمهٔ این بند که به
اندازهٔ کافی بلند است" has never been asked whether it would accept real prose
or reject it — and both failures are invisible until a real book arrives.

So the same gates run here over `translation_sample.PASSAGE`, and the
assertions are about the properties a reader would care about: the name is
introduced once and shortened afterwards, the emphasis survives on both sides,
the translator's own note keeps its anchor, the length ratios sit inside the
band, and the Persian typography is already correct rather than being corrected.
"""

from __future__ import annotations

import argparse
import zipfile

import pytest

import bookir as ir
import falint
import glossary as gl
import qa
from translation_sample import FOOTNOTE, GLOSSARY_ENTRY, PASSAGE


@pytest.fixture(scope="module")
def translated():
    """The passage as a finished book: source, target, footnote and glossary."""
    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    blocks = []
    for index, (kind, level, source, target) in enumerate(PASSAGE, start=1):
        fields = {"page": 1, "text": source}
        if kind == "heading":
            fields["level"] = level
        block = ir.make_block(kind, index, **fields)
        block["target"] = target
        blocks.append(block)
    book["blocks"] = blocks

    anchor = next(b for b in blocks if "[[fn:fn0001]]" in b["target"])
    note = ir.make_footnote(1, anchor_block=anchor["id"], text=FOOTNOTE["source"],
                            origin=FOOTNOTE["origin"])
    note["target"] = FOOTNOTE["target"]
    # The source side is empty on purpose: the translator wrote this note, the
    # book never had one. `text` is filled so the body-empty gate is satisfied
    # by the same string a reader will see.
    note["text"] = FOOTNOTE["target"]
    book["footnotes"] = [note]
    book["meta"]["title_target"] = "خانهٔ سرِ تپه"
    return book


@pytest.fixture(scope="module")
def locked_glossary(translated):
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, GLOSSARY_ENTRY["source"],
                          category=GLOSSARY_ENTRY["category"], frequency=3,
                          aliases=GLOSSARY_ENTRY["aliases"])
    entry.update({k: v for k, v in GLOSSARY_ENTRY.items() if k != "aliases"})
    first = next(b for b in translated["blocks"]
                 if GLOSSARY_ENTRY["first_form"] in b["target"])
    entry["first_block_id"] = first["id"]
    glossary["entries"] = [entry]
    return glossary


# --------------------------------------------------------------------------- #
# The book itself
# --------------------------------------------------------------------------- #

def test_the_sample_is_a_valid_book(translated):
    assert ir.validate_book(translated) == []
    assert len(translated["blocks"]) == len(PASSAGE)


def test_every_block_is_actually_translated(translated):
    for block in ir.iter_text_blocks(translated):
        assert block["target"].strip(), block["id"]
        persian, latin = ir.script_ratio(ir.plain_text(block["target"]))
        assert persian > latin, (
            f"{block['id']} is not predominantly Persian: {block['target'][:60]!r}"
        )


def test_no_block_drifted_outside_the_length_band(translated):
    """Real prose has to sit inside the same band placeholder text does.

    A translation that is systematically short is dropping clauses; one that is
    systematically long is padding. Persian runs a little longer than English,
    so the band is asymmetric and this is a genuine measurement of the sample.
    """
    for block in ir.iter_text_blocks(translated):
        source = ir.plain_text(block["text"])
        target = ir.plain_text(block["target"])
        if len(source) < qa.RATIO_MIN_CHARS:
            continue
        ratio = len(target) / len(source)
        assert qa.LENGTH_RATIO_MIN < ratio < qa.LENGTH_RATIO_MAX, (
            f"{block['id']} ratio {ratio:.2f}: {target[:60]!r}"
        )


# --------------------------------------------------------------------------- #
# The properties a reader would check
# --------------------------------------------------------------------------- #

def test_the_name_is_introduced_once_and_shortened_afterwards(translated):
    joined = "\n".join(b["target"] for b in translated["blocks"])
    assert joined.count(GLOSSARY_ENTRY["first_form"]) == 1

    # After the introduction the name recurs in a *shorter* form, which is the
    # point of having one. Counting `later_form` alone would demand the full
    # two-word name every time it appears, and prose that did that would read
    # like a legal document rather than a novel.
    without_introduction = joined.replace(GLOSSARY_ENTRY["first_form"], "", 1)
    shortened = sum(without_introduction.count(form)
                    for form in GLOSSARY_ENTRY["alias_targets"])
    assert shortened >= 2, (
        "the name never recurs, so nothing proves the short forms are used"
    )
    assert GLOSSARY_ENTRY["source"] not in without_introduction, (
        "the original spelling appears somewhere other than the introduction"
    )


def test_emphasis_is_carried_across_not_invented(translated):
    for block in ir.iter_text_blocks(translated):
        assert ir.emphasis_signature(block["text"]) == \
            ir.emphasis_signature(block["target"]), block["id"]


def test_the_translators_note_keeps_its_marker_and_its_anchor(translated):
    note = translated["footnotes"][0]
    assert note["origin"] == "translator"
    anchor = next(b for b in translated["blocks"] if b["id"] == note["anchor_block"])
    assert note["id"] in ir.footnote_refs(anchor["target"])
    assert note["target"].rstrip().endswith("— م."), (
        "a translator's note should be signed so a reader knows who is speaking"
    )


def test_dialogue_uses_persian_guillemets_not_ascii_quotes(translated):
    spoken = [b["target"] for b in translated["blocks"] if "«" in b["target"]]
    assert len(spoken) >= 3, "the sample lost its dialogue"
    for line in spoken:
        assert '"' not in line and "”" not in line, line[:60]


def test_the_typography_pass_finds_nothing_left_to_fix(translated):
    """Written correctly the first time, not corrected afterwards.

    falint exists to repair what a translator got wrong. If it changes this
    sample, the sample is the thing that is wrong.
    """
    for block in ir.iter_text_blocks(translated):
        assert falint.fix_text(block["target"]) == block["target"], (
            f"{block['id']} needed a typography fix: "
            f"{falint.fix_text(block['target'])[:70]!r}"
        )


# --------------------------------------------------------------------------- #
# And through the gates
# --------------------------------------------------------------------------- #

def test_the_book_gates_accept_the_real_translation(translated, locked_glossary):
    summary = qa.check_book(translated, glossary=locked_glossary).summary()
    assert summary["ok"], summary["findings"]
    assert summary["warnings"] == 0, summary["findings"]


def test_strict_mode_accepts_it_too(translated, locked_glossary):
    """Publication mode promotes the fidelity findings to blocking."""
    summary = qa.check_book(translated, glossary=locked_glossary,
                            strict=True).summary()
    assert summary["ok"], summary["findings"]


def test_the_enforcement_pass_leaves_a_correct_sample_alone(translated,
                                                            locked_glossary):
    """It has to be a no-op on a translation that was already right."""
    import copy

    book = copy.deepcopy(translated)
    before = [b["target"] for b in book["blocks"]]
    gl.enforce_first_mentions(locked_glossary, book)
    assert [b["target"] for b in book["blocks"]] == before


def test_it_builds_into_a_document_that_verifies(translated, tmp_path):
    from build_docx import Builder, add_arguments

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma"])

    destination = tmp_path / "sample.fa.docx"
    report = Builder(translated, tmp_path, options).build(destination)
    assert report["warning_count"] == 0, report["warnings"]
    assert report["footnotes"] == 1
    assert report["headings"] == 1

    package = qa.check_docx(destination, translated).summary()
    assert package["ok"], package["findings"]

    with zipfile.ZipFile(destination) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
    assert "<w:bidi" in document
    # The Latin name inside the Persian sentence must not be marked RTL.
    assert "Margaret Ashcroft" in document

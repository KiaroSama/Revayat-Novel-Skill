"""Mixed Persian/Latin text, checked in the built document rather than by eye.

Every line here is a shape that goes wrong in real Persian typesetting: a Latin
name inside a sentence, a URL with a comma in it, a version number, an ellipsis,
a parenthetical, a sentence-final full stop. The failure mode is not a crash —
it is a document that looks plausible and is subtly wrong, so these assert on
the OOXML that Word will actually read.

Two properties matter and are checked for every line:

* **nothing is lost or reordered** — the concatenated run text equals the input,
  character for character. This is what proves no code path ever "helps" by
  reversing a string to fake right-to-left.
* **direction is stated per run** — the paragraph carries ``w:bidi``, Persian
  runs carry ``w:rtl``, and Latin runs do not, so Word's bidi algorithm gets the
  character direction it needs instead of guessing.
"""

from __future__ import annotations

import argparse
import re
import zipfile

import pytest

import bookir as ir
import falint
from build_docx import Builder, add_arguments, split_by_script

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

#: Each entry is (label, Persian text as a translator would write it).
CORPUS = [
    ("latin name and western digits",
     "او در سال ۱۹۸۴ با John Smith دیدار کرد."),
    ("first-mention parenthetical",
     "الیزابت بنت (Elizabeth Bennet) گفت: «واقعاً؟»"),
    ("filename with a hyphen and an extension",
     "فایل `chapter-01.pdf` را باز کرد؛ بعد برگشت."),
    ("version number and a url containing a comma",
     "نسخهٔ 2.4.1 در https://example.com/a,b?x=1 منتشر شد."),
    ("ellipsis, exclamation and guillemets",
     "«نمی‌دانم… شاید فردا!» گفت."),
    ("latin abbreviation mid-sentence",
     "این کتاب (ویرایش دوم، cf. فصل سوم) منتشر شد."),
    ("brackets around a latin editorial note",
     "او رفت [see Smith 1984] و بازنگشت."),
    ("em dash between two persian clauses",
     "او ماند — و هیچ نگفت."),
    ("persian and western digits in one sentence",
     "صفحهٔ ۱۲ از ISBN 978-0-19-953556-9 نقل شده است."),
    ("colon, semicolon and a trailing full stop",
     "سه چیز: نان؛ آب؛ و کتاب."),
    ("email address inside persian prose",
     "به info@example.com بنویسید تا پاسخ بگیرید."),
    ("two latin runs separated by persian",
     "Alice در سرزمین Wonderland گم شد."),
]


def _build_paragraph(text: str, tmp_path):
    """Put one line through the real builder and return its ``w:p`` element."""
    from lxml import etree

    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, text="placeholder")]
    book["blocks"][0]["target"] = text
    book["meta"]["title_target"] = None

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(
        ["--book", "x", "--out", "y", "--font", "Tahoma", "--no-toc"]
    )
    destination = tmp_path / "line.docx"
    Builder(book, tmp_path, options).build(destination)

    with zipfile.ZipFile(destination) as archive:
        document = etree.fromstring(archive.read("word/document.xml"))
    body = document.find(f"{{{W}}}body")
    for paragraph in body.findall(f"{{{W}}}p"):
        if paragraph.findall(f".//{{{W}}}t"):
            return paragraph
    raise AssertionError("the builder produced no paragraph with text")


def _runs(paragraph):
    """``(text, is_rtl)`` for each run, in document order."""
    from lxml import etree

    out = []
    for run in paragraph.findall(f"{{{W}}}r"):
        pieces = [t.text or "" for t in run.findall(f"{{{W}}}t")]
        if not pieces:
            continue
        properties = run.find(f"{{{W}}}rPr")
        rtl = properties is not None and properties.find(f"{{{W}}}rtl") is not None
        out.append(("".join(pieces), rtl))
    return out


@pytest.mark.parametrize("label,text", CORPUS, ids=[c[0] for c in CORPUS])
def test_no_character_is_lost_or_reordered(label, text, tmp_path):
    """The single most important property: the document says what we wrote."""
    paragraph = _build_paragraph(text, tmp_path)
    rebuilt = "".join(chunk for chunk, _ in _runs(paragraph))
    assert rebuilt == ir.plain_text(text), (
        f"{label}: the built runs do not reconstruct the source line"
    )


@pytest.mark.parametrize("label,text", CORPUS, ids=[c[0] for c in CORPUS])
def test_paragraph_declares_right_to_left(label, text, tmp_path):
    paragraph = _build_paragraph(text, tmp_path)
    properties = paragraph.find(f"{{{W}}}pPr")
    assert properties is not None and properties.find(f"{{{W}}}bidi") is not None, (
        f"{label}: paragraph has no w:bidi, so Word has no base direction"
    )


@pytest.mark.parametrize("label,text", CORPUS, ids=[c[0] for c in CORPUS])
def test_latin_runs_are_not_marked_right_to_left(label, text, tmp_path):
    """A Latin name inside a Persian sentence must stay left-to-right."""
    paragraph = _build_paragraph(text, tmp_path)
    for chunk, rtl in _runs(paragraph):
        stripped = chunk.strip()
        if not stripped:
            continue
        persian, latin = ir.script_ratio(stripped)
        if latin > 0.9 and persian == 0.0:
            assert not rtl, f"{label}: Latin run {stripped!r} was marked w:rtl"
        elif persian > 0.9 and latin == 0.0:
            assert rtl, f"{label}: Persian run {stripped!r} was not marked w:rtl"


@pytest.mark.parametrize("label,text", CORPUS, ids=[c[0] for c in CORPUS])
def test_typography_pass_does_not_damage_the_line(label, text, tmp_path):
    """falint must be safe on every one of these, and stable on a second run."""
    once = falint.fix_text(text)
    assert falint.fix_text(once) == once, f"{label}: typography pass is not idempotent"
    # Emphasis and footnote structure must survive untouched.
    assert ir.emphasis_signature(once) == ir.emphasis_signature(text)
    assert ir.footnote_refs(once) == ir.footnote_refs(text)


def test_protected_regions_reach_the_document_intact(tmp_path):
    """URLs, emails, versions and identifiers must not be re-punctuated."""
    line = ("نسخهٔ 2.4.1 در https://example.com/a,b?x=1 و info@example.com "
            "با ISBN 978-0-19-953556-9 منتشر شد.")
    fixed = falint.fix_text(line)
    paragraph = _build_paragraph(fixed, tmp_path)
    rebuilt = "".join(chunk for chunk, _ in _runs(paragraph))

    for fragment in ("https://example.com/a,b?x=1", "info@example.com",
                     "978-0-19-953556-9", "2.4.1"):
        assert fragment in rebuilt, f"{fragment!r} was altered on the way in"


def test_a_sentence_final_stop_is_never_moved():
    """Persian keeps the ASCII full stop; placement is bidi's job, not ours.

    Moving or mirroring it manually is the classic way to produce text that
    looks right in one viewer and is broken in every other.
    """
    line = "او در سال ۱۹۸۴ با John Smith دیدار کرد."
    fixed = falint.fix_text(line)
    assert fixed.endswith("."), "the full stop was moved or replaced"
    assert fixed.index("John Smith") == fixed.index("John Smith")
    # Order is preserved exactly: no reversal anywhere in the pipeline.
    assert fixed.index("۱۹۸۴") < fixed.index("John Smith") < fixed.index(".")


def test_split_by_script_is_a_partition_over_the_whole_corpus():
    for _, text in CORPUS:
        plain = ir.plain_text(text)
        assert "".join(chunk for chunk, _ in split_by_script(plain)) == plain

"""The two questions a rendered page cannot answer, asked of the .docx itself.

Everything else about a laid-out page is measured from the render, because the
render is what the reader gets. These two are the exceptions, and both are
exceptions for the same measured reason: **PyMuPDF's Arabic-script readback is
not faithful.**

* **Is the text there?** It drops the zero-width non-joiner and transposes
  letters, so probing a Persian paragraph against the rendered page reports
  prose missing that is plainly on the sheet.
* **Is the paragraph right-to-left?** Its block boxes do not report alignment at
  all. Measured on a document whose every paragraph carries `w:bidi`, it called
  all of them left-to-right — so the check that exists to catch a book built the
  wrong way round would have failed every correct book.

The document's own OOXML answers both exactly, and cannot be wrong about its own
settings. So this module unzips the package and reads `word/document.xml` and
`word/styles.xml` directly: no renderer, no PyMuPDF, no tolerances, and nothing
here can be affected by upgrading the PDF library.

`pagecheck` re-exports these three names, because `pagecheck.document_text` is
how every caller and every test already reaches them.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

import qa


def document_text(docx: Path) -> str:
    """Every paragraph of a .docx as one string, in document order.

    Read from the file rather than from the render, for the same reason the
    direction check is: PyMuPDF's Arabic-script readback is not faithful.
    Measured on a correct Word render of a correct book, the zero-width
    non-joiner was dropped and ``بالا`` came back with its
    letters transposed. Probing that for the book's own sentences reports every
    Persian paragraph missing from a page that is perfectly set - a check that
    fails on correct output, which is worse than no check at all.

    The file says what Word will draw. Geometry still comes from the render,
    because that is the question a file cannot answer.
    """
    with zipfile.ZipFile(docx) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
    paragraphs = []
    for block in re.findall(r"<w:p[ >].*?</w:p>", body, re.S):
        pieces = re.findall(r"<w:t[^>]*>(.*?)</w:t>", block, re.S)
        if pieces:
            paragraphs.append("".join(pieces))
    return " ".join(paragraphs)


def requested_fonts(docx: Path) -> dict[str, str]:
    """Which fonts the *document* asked for: ``{"complex": …, "ascii": …}``.

    Asked of `word/styles.xml` rather than of the caller, for the same reason
    the text and the direction are: the caller's idea of the options is not
    what ended up in the file, and the file is what the renderer obeyed. The
    builder writes the Persian font as `w:cs` in `w:docDefaults`
    (`ooxml.set_document_defaults`), so docDefaults is the one place that
    always carries it.

    Empty strings when the document does not say - a state, not a failure: a
    .docx from somewhere else need not carry any of this.
    """
    try:
        with zipfile.ZipFile(docx) as archive:
            styles = archive.read("word/styles.xml").decode("utf-8")
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeDecodeError):
        return {"complex": "", "ascii": ""}

    defaults = re.search(r"<w:docDefaults>.*?</w:docDefaults>", styles, re.S)
    if not defaults:
        return {"complex": "", "ascii": ""}
    element = re.search(r"<w:rFonts\b[^>]*/>", defaults.group(0))
    if not element:
        return {"complex": "", "ascii": ""}
    found = {}
    for key, attribute in (("complex", "w:cs"), ("ascii", "w:ascii")):
        match = re.search(rf'{attribute}="([^"]*)"', element.group(0))
        found[key] = match.group(1) if match else ""
    return found


def check_direction_in_document(docx: Path) -> list[dict[str, Any]]:
    """Is the built document right-to-left? Asked of the file, not the render.

    The one direction check that cannot lie. A rendered page tells you where
    ink landed, and for Arabic script PyMuPDF's block boxes do not report that
    faithfully — so a correct document comes back looking flush-left. The
    `w:bidi` on a paragraph, and on the style it inherits from, is the setting
    Word actually obeys.
    """
    findings: list[dict[str, Any]] = []
    with zipfile.ZipFile(docx) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
        styles = archive.read("word/styles.xml").decode("utf-8")

    normal = re.search(r'<w:style [^>]*w:styleId="Normal".*?</w:style>',
                       styles, re.S)
    inherits = bool(normal and "<w:bidi" in normal.group(0))

    paragraphs = [p for p in re.findall(r"<w:p.*?</w:p>", document, re.S)
                  if "<w:t" in p]
    without = [p for p in paragraphs if "<w:bidi" not in p]
    if without and not inherits:
        findings.append({
            "severity": qa.ERROR, "code": "document-not-rtl", "unit": "document",
            "detail": f"{len(without)} of {len(paragraphs)} paragraphs carry no "
                      f"w:bidi and the Normal style does not supply one, so "
                      f"Word will set them left-to-right",
        })
    return findings


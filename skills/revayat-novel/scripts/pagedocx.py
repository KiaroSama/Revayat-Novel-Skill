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

import zipfile
from pathlib import Path
from typing import Any

import qa
import opc
from docxproperties import Styles, Unresolved


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
        body = opc.Package(archive).xml("word/document.xml")
    paragraphs = []
    parents = {child: parent for parent in body.iter() for child in parent}
    for block in body.iter(opc.qname("w", "p")):
        ancestor = parents.get(block)
        nested = False
        while ancestor is not None:
            if ancestor.tag == opc.qname('w', 'p'):
                nested = True
                break
            ancestor = parents.get(ancestor)
        if nested:
            continue
        style = block.find("w:pPr/w:pStyle", opc.NS)
        if style is not None and style.get(opc.qname("w", "val"), "").startswith("TOC"):
            continue
        if any((part.text or "").strip().startswith("TOC ")
               for part in block.iter(opc.qname("w", "instrText"))):
            continue
        text = opc.text_of(block)
        if text:
            paragraphs.append(text)
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
            styles = opc.Package(archive).xml('word/styles.xml')
    except (OSError, zipfile.BadZipFile, opc.Damaged):
        # Optional font discovery must not prevent the authoritative package
        # and direction gates from reporting unreadable native evidence.
        return {'complex': '', 'ascii': ''}
    element = styles.find('w:docDefaults/w:rPrDefault/w:rPr/w:rFonts', opc.NS)
    return {key: element.get(opc.qname('w', attribute), '') if element is not None else ''
            for key, attribute in [('complex', 'cs'), ('ascii', 'ascii')]}


def check_direction_in_document(docx: Path) -> list[dict[str, Any]]:
    """Is the built document right-to-left? Asked of the file, not the render.

    The one direction check that cannot lie. A rendered page tells you where
    ink landed, and for Arabic script PyMuPDF's block boxes do not report that
    faithfully — so a correct document comes back looking flush-left. The
    `w:bidi` on a paragraph, and on the style it inherits from, is the setting
    Word actually obeys.
    """
    findings: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(docx) as archive:
            package = opc.Package(archive)
            document = package.xml('word/document.xml')
            styles = Styles(package.xml('word/styles.xml'))
        paragraphs = [p for p in document.iter(opc.qname('w', 'p')) if opc.text_of(p).strip()]
        without = sum(not styles.paragraph_bidi(p) for p in paragraphs)
        if without:
            findings.append({'severity': qa.ERROR, 'code': 'document-not-rtl', 'unit': 'document',
                             'detail': f'{without} of {len(paragraphs)} text paragraphs have effective left-to-right direction'})
    except (OSError, zipfile.BadZipFile, opc.Damaged, Unresolved) as error:
        findings.append({'severity': qa.ERROR, 'code': 'document-direction-unverified', 'unit': 'document',
                         'detail': f'native direction evidence is invalid or unresolved ({type(error).__name__})'})
    return findings

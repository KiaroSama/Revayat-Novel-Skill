"""Read native note bodies without flattening their content or hiding failures."""
from __future__ import annotations

import logging
import re
from typing import Callable

from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run
from lxml import etree

import bookir as ir

LOG = logging.getLogger(__name__)
NOTE_PARTS = (("footnote", RT.FOOTNOTES, "w:footnote", "w:footnoteReference"),
              ("endnote", RT.ENDNOTES, "w:endnote", "w:endnoteReference"))
_METADATA = {qn("w:" + name) for name in (
    "pPr", "bookmarkStart", "bookmarkEnd", "proofErr", "commentRangeStart", "commentRangeEnd")}
_RUN_METADATA = {qn("w:" + name) for name in (
    "rPr", "footnoteRef", "endnoteRef", "commentReference", "lastRenderedPageBreak")}


def note_key(kind: str, identity: str | None) -> str:
    if not isinstance(identity, str) or not re.fullmatch(r"[+-]?[0-9]+", identity):
        raise ValueError(f"invalid DOCX {kind} note identifier")
    return f"{kind}:{int(identity)}"


def _body(node, document, run_style: Callable) -> str:
    paragraphs = []
    for child in node:
        if child.tag != qn("w:p"):
            raise ValueError("DOCX note contains unsupported structured content")
        paragraph = Paragraph(child, document)
        spans = []

        def visit(container):
            for item in container:
                if item.tag in _METADATA:
                    continue
                if item.tag in {qn("w:hyperlink"), qn("w:fldSimple")}:
                    visit(item)
                    continue
                if item.tag != qn("w:r"):
                    raise ValueError("DOCX note contains unsupported paragraph content")
                bold, italic = run_style(Run(item, paragraph))
                for part in item:
                    if part.tag in _RUN_METADATA:
                        continue
                    if part.tag == qn("w:t"):
                        text = part.text or ""
                    elif part.tag == qn("w:tab"):
                        text = "\t"
                    elif part.tag == qn("w:cr") or (part.tag == qn("w:br") and
                            part.get(qn("w:type"), "textWrapping") == "textWrapping"):
                        text = "\n"
                    elif part.tag == qn("w:noBreakHyphen"):
                        text = "\u2011"
                    elif part.tag == qn("w:softHyphen"):
                        text = "\u00ad"
                    else:
                        raise ValueError("DOCX note contains unsupported run content")
                    spans.append((text, bold, italic))
        visit(child)
        paragraphs.append(ir.render_markup(spans))
    return "\n".join(paragraphs)


def read_notes(document, run_style: Callable) -> dict[str, str]:
    """Resolve typed note IDs; malformed parts never mean an absence of notes."""
    notes = {}
    for kind, relationship, tag, _ in NOTE_PARTS:
        try:
            part = document.part.part_related_by(relationship)
        except KeyError:
            continue
        try:
            # python-docx's parser disables entity resolution. Reject DTDs as
            # well, before walking text, rather than treating entities as prose.
            root = parse_xml(part.blob)
        except (etree.XMLSyntaxError, ValueError) as error:
            LOG.error("Refused malformed DOCX %s note part", kind)
            raise ValueError(f"malformed DOCX {kind} note part") from error
        if root.getroottree().docinfo.doctype or root.tag != qn(tag + "s"):
            raise ValueError(f"invalid DOCX {kind} note root or DTD")
        seen = set()
        for node in root:
            if node.tag != qn(tag):
                raise ValueError(f"unsupported DOCX {kind} note entry")
            key = note_key(kind, node.get(qn("w:id")))
            if key in seen:
                raise ValueError(f"duplicate DOCX note identifier {key}")
            seen.add(key)
            if node.get(qn("w:type")) in {"separator", "continuationSeparator", "continuationNotice"}:
                continue
            notes[key] = _body(node, document, run_style)
        LOG.debug("Read %d DOCX %s note entries", len(seen), kind)
    return notes

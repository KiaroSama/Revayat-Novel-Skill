"""Read DOCX section geometry and ordered running heads and feet."""
from __future__ import annotations

from typing import Any, Iterator

from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.text.run import Run

import bookir as ir
from docxproperties import emphasis_spans, run_style as _run_style  # noqa: F401


def _pt(length) -> float | None:
    """Points, rounded like the rest of the page setup; ``None`` when unset."""
    return None if length is None else round(length.pt, 2)


def section_geometry(section) -> dict[str, Any]:
    """One ``w:sectPr`` as page setup, in the field names ``book["page"]`` uses.

    The names match deliberately: the first section *is* ``book["page"]``, so
    everything that already reads that key keeps working while the rest of the
    sections travel beside it in the same shape.

    ``start_type`` is kept as the raw XML token rather than an enum member so
    the value survives a JSON round trip and goes back into the built document
    unchanged.
    """
    start = section._sectPr.find(qn("w:type"))
    token = start.get(qn("w:val")) if start is not None else None
    return {
        "start_type": token or "nextPage",   # Word's default when w:type is absent
        "orientation": ("landscape" if section.orientation == WD_ORIENT.LANDSCAPE
                        else "portrait"),
        "width_pt": _pt(section.page_width),
        "height_pt": _pt(section.page_height),
        "margin_top_pt": _pt(section.top_margin),
        "margin_bottom_pt": _pt(section.bottom_margin),
        "margin_inner_pt": _pt(section.left_margin),
        "margin_outer_pt": _pt(section.right_margin),
        "gutter_pt": _pt(section.gutter),
        "header_distance_pt": _pt(section.header_distance),
        "footer_distance_pt": _pt(section.footer_distance),
    }


def _column_count(section) -> int:
    columns = section._sectPr.find(qn("w:cols"))
    try:
        return max(1, int(columns.get(qn("w:num"))))
    except (AttributeError, TypeError, ValueError):
        return 1


def _alignment(paragraph: Paragraph) -> str | None:
    """The paragraph's ``w:jc`` token, kept raw like ``start_type``.

    Raw because Word reads ``left`` as *start* in a right-to-left paragraph, so
    a running head the author pushed to the outside edge stays on the outside
    edge in Persian without this having to know which edge that is.
    """
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return None
    node = properties.find(qn("w:jc"))
    return None if node is None else node.get(qn("w:val"))


def _running_runs(paragraph: Paragraph, note) -> Iterator[Any]:
    """Every ``w:r`` and ``w:fldSimple`` of one header paragraph, in order.

    Descends into ``w:hyperlink`` for the same reason :func:`iter_runs` does —
    the words inside one would otherwise vanish without a sound — but the target
    cannot be rebuilt here, so each is named.
    """
    for node in paragraph._p:
        if node.tag == qn("w:hyperlink"):
            note("running-head-link-dropped",
                 "the words are carried; a running head is not clickable in "
                 "print and the target has nowhere on the page to live")
            yield from (child for child in node
                        if child.tag in (qn("w:r"), qn("w:fldSimple")))
        elif node.tag in (qn("w:r"), qn("w:fldSimple")):
            yield node


def _running_pieces(paragraph: Paragraph, allocate, note) -> list[dict[str, Any]]:
    """One header paragraph as prose, tabs and fields, in reading order.

    Order is the whole content of a running head: "Chapter One → 7" and
    "7 → Chapter One" are different pages. So a field cannot be collected into a
    list beside the text and put back at a guess; it keeps its place in the line.

    A field is carried as its instruction alone. Its *cached* result is whatever
    Word last computed — for a ``STYLEREF`` that is the English chapter title,
    which is precisely the thing that must not reach a Persian page — and the
    built document already asks Word to recompute fields when it opens.
    """
    pieces: list[dict[str, Any]] = []
    spans: list[tuple[str, bool, bool]] = []
    depth = 0                 # nested fields: only the outermost is carried
    in_result = False
    instruction: list[str] = []

    def flush() -> None:
        text = ir.render_markup(spans)
        spans.clear()
        if text.strip():
            pieces.append({"id": allocate(), "text": text, "target": None})
        elif text:
            pieces.append({'controls': ir.plain_text(text)})

    for node in _running_runs(paragraph, note):
        if node.tag == qn("w:fldSimple"):
            flush()
            pieces.append({"field": node.get(qn("w:instr")) or ""})
            continue

        run = Run(node, paragraph)
        if node.find(f".//{qn('w:drawing')}") is not None or \
                node.find(f".//{qn('w:pict')}") is not None:
            note("running-head-picture-dropped",
                 "a picture in a running head is not carried; the words beside "
                 "it are")

        for child in node:
            tag = child.tag
            if tag == qn("w:fldChar"):
                marker = child.get(qn("w:fldCharType"))
                if marker == "begin":
                    if depth == 0:
                        flush()
                        instruction, in_result = [], False
                    depth += 1
                elif marker == "separate":
                    in_result = True
                elif marker == "end":
                    depth = max(0, depth - 1)
                    if depth == 0:
                        pieces.append({"field": "".join(instruction)})
                        in_result = False
            elif tag == qn("w:instrText") and depth and not in_result:
                instruction.append(child.text or "")
            elif tag == qn("w:t") and not depth:
                spans.extend(emphasis_spans(run, child.text or ""))
            elif tag in {qn("w:tab"), qn("w:ptab")} and not depth:
                flush()
                pieces.append({"tab": True})
            elif not depth and tag in {qn("w:br"), qn("w:cr"),
                                       qn("w:noBreakHyphen"), qn("w:softHyphen")}:
                if tag == qn("w:br") and child.get(qn("w:type"), "textWrapping") != "textWrapping":
                    raise ValueError("unsupported DOCX running break type")
                value = {qn("w:br"): "\n", qn("w:cr"): "\n",
                         qn("w:noBreakHyphen"): "‑", qn("w:softHyphen"): "­"}[tag]
                spans.extend(emphasis_spans(run, value))
    flush()
    return pieces


def running_of(section, index: int, even_and_odd: bool, allocate, warnings) -> dict[str, Any]:
    """This section's own headers and feet, ``{"headers": …, "footers": …}``.

    Only the slots Word actually *shows*: a first-page header the section
    never turns on, or an even-page one the document does not ask for, is a
    definition nobody sees, and carrying it would put a running head on the
    Persian edition that the source never printed.
    """
    slots = ["default"]
    if section.different_first_page_header_footer:
        slots.append("first")
    if even_and_odd:
        slots.append("even")

    # Carried rather than inferred from the slots below: a section may say
    # "my first page is different" and still inherit the header that page
    # shows, so the switch and the definition are two separate facts.
    carried: dict[str, Any] = {
        "different_first_page": bool(section.different_first_page_header_footer)}
    for part, key in ir.RUNNING_PARTS:
        found: dict[str, Any] = {}
        for slot in slots:
            container = getattr(
                section, ir.RUNNING_ACCESSOR[slot].format(part=part))
            # Settled before the paragraphs are asked for: python-docx
            # *creates* a definition for an inherited header the moment its
            # content is read, which would turn "this section inherits" into
            # "this section has an empty one of its own".
            if container.is_linked_to_previous:
                continue
            where = f"section {index} {part} ({slot})"
            if container.tables:
                warnings.append({
                    "kind": "running-head-table-dropped", "where": where,
                    "detail": "a table in a running head is not carried; "
                              "its cells are laid out by the header itself "
                              "and the Persian edition sets one line",
                })
            found[slot] = {"paragraphs": [
                {"align": _alignment(paragraph),
                 "pieces": _running_pieces(
                     paragraph, allocate,
                     lambda kind, detail, where=where: warnings.append(
                         {"kind": kind, "where": where, "detail": detail}))}
                for paragraph in container.paragraphs
            ]}
        carried[key] = found
    return carried

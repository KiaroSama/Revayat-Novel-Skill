"""DOCX → Book IR via python-docx, plus raw OOXML for what it cannot reach.

python-docx exposes paragraphs, runs and styles, but has no API for footnotes
(verified against python-docx 1.2.0), so ``word/footnotes.xml`` is read
straight off the package. Inline pictures are pulled from their relationship
so the original bytes and the author's ``wp:extent`` both survive.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docx.text.run import Run

import bookir as ir

EMU_PER_PT = ir.EMU_PER_PT

_HEADING_STYLE = re.compile(r"^\s*heading\s*(\d)\s*$", re.I)
_LIST_STYLE = re.compile(r"list\s*(bullet|number|paragraph)", re.I)


def column_span(tc) -> int:
    """How many grid columns this ``w:tc`` covers, from ``w:gridSpan``."""
    properties = tc.find(qn("w:tcPr"))
    if properties is None:
        return 1
    grid = properties.find(qn("w:gridSpan"))
    try:
        return max(1, int(grid.get(qn("w:val"))))
    except (AttributeError, TypeError, ValueError):
        return 1


def _vertical_merge(tc) -> str | None:
    """``"restart"``, ``"continue"``, or ``None`` when the cell is not merged."""
    properties = tc.find(qn("w:tcPr"))
    if properties is None:
        return None
    merge = properties.find(qn("w:vMerge"))
    if merge is None:
        return None
    # A bare <w:vMerge/> means continue; only "restart" opens a new span.
    return "restart" if merge.get(qn("w:val")) == "restart" else "continue"


def table_cells(table) -> list[dict[str, Any]]:
    """Every distinct cell of a table, once, with its position and span.

    Walks ``w:tr``/``w:tc`` rather than ``row.cells``. Two reasons, and the
    second one is the reason this is not shorter:

    ``row.cells`` *expands* merges — a cell spanning two columns comes back
    twice, and a vertically merged one comes back on every row it covers. Since
    every cell here becomes its own worksheet unit, that made a merged cell's
    sentence get translated twice and printed twice.

    And de-duplicating that expansion by object identity does not work.
    `cell._tc` hands back a fresh lxml proxy on each access, and CPython reuses
    the `id()` of a freed one — measured on a 3x3 grid, three different cells
    reported the same id and a fourth reported one that had never been seen.
    Walking the XML gives each cell exactly once by construction, so there is
    nothing to de-duplicate.

    Returns ``{"tc", "row", "cell", "row_span", "col_span"}`` with 1-based
    ``row``/``cell`` counted in grid columns.
    """
    from docx.table import _Cell

    found: list[dict[str, Any]] = []
    # Grid column -> the record of the cell currently open there, so a
    # `continue` row extends the span of the cell that started it.
    open_at: dict[int, dict[str, Any]] = {}

    for row_number, tr in enumerate(table._tbl.findall(qn("w:tr")), start=1):
        column = 0
        for tc in tr.findall(qn("w:tc")):
            width = column_span(tc)
            if _vertical_merge(tc) == "continue" and column in open_at:
                open_at[column]["row_span"] += 1
            else:
                record = {"tc": _Cell(tc, table), "row": row_number,
                          "cell": column + 1, "row_span": 1, "col_span": width}
                found.append(record)
                open_at[column] = record
            column += width
    return found


def iter_runs(paragraph: Paragraph) -> Iterator[Run]:
    """Every run in the paragraph, including the ones inside a hyperlink.

    ``paragraph.runs`` omits hyperlink content entirely. A sentence with a
    linked phrase in the middle of it therefore came through with the phrase
    missing — not flagged, not empty, just quietly shorter than the source.
    """
    for item in paragraph.iter_inner_content():
        if isinstance(item, Run):
            yield item
        elif isinstance(item, Hyperlink):
            yield from item.runs


def has_page_break(run: Run) -> bool:
    return any(node.get(qn("w:type")) == "page"
               for node in run._r.findall(qn("w:br")))


def _ilvl(element) -> int | None:
    """The 0-based ``w:ilvl`` under this element, as a 1-based depth."""
    if element is None:
        return None
    numbering = element.find(f".//{qn('w:numPr')}")
    if numbering is None:
        return None
    level = numbering.find(qn("w:ilvl"))
    try:
        return int(level.get(qn("w:val"))) + 1
    except (AttributeError, TypeError, ValueError):
        return 1


def list_level(paragraph: Paragraph) -> int | None:
    """The nesting depth of a list item, 1-based, or ``None`` if not a list.

    Word records the depth in one of two places and it is genuinely either:
    a paragraph numbered directly carries ``w:numPr/w:ilvl`` itself, while one
    numbered through a style — which is what "List Bullet 2" is — carries
    nothing, and the depth lives in the style definition.

    When the style says a paragraph is a list but neither place gives a level,
    the trailing digit of the style name is used. That is a reading of Word's
    own naming convention rather than of the file's data, so it is last, and it
    is only ever reached for a paragraph already known to be a list item.
    """
    depth = _ilvl(paragraph._p.find(qn("w:pPr")))
    if depth is not None:
        return depth

    style = getattr(paragraph, "style", None)
    element = getattr(style, "element", None)
    depth = _ilvl(element)
    if depth is not None and depth > 1:
        return depth

    name = (getattr(style, "name", "") or "").strip()
    if not _LIST_STYLE.search(name):
        return None
    trailing = re.search(r"(\d+)\s*$", name)
    return int(trailing.group(1)) if trailing else 1


def _heading_level(style_name: str) -> int | None:
    name = (style_name or "").strip()
    if name.lower() in {"title", "subtitle"}:
        return 1 if name.lower() == "title" else 2
    match = _HEADING_STYLE.match(name)
    if match:
        return min(6, max(1, int(match.group(1))))
    return None


def _run_style(run) -> tuple[bool, bool]:
    """Effective bold/italic, falling back to the run's style definition."""
    bold, italic = run.bold, run.italic
    if bold is None or italic is None:
        style = getattr(run, "style", None)
        font = getattr(style, "font", None) if style is not None else None
        if font is not None:
            bold = font.bold if bold is None else bold
            italic = font.italic if italic is None else italic
    return bool(bold), bool(italic)


def _read_footnotes(document) -> dict[str, str]:
    """``footnote id -> plain text`` from ``word/footnotes.xml``.

    Word reserves ids -1 and 0 for the separator marks; they carry no content.
    """
    try:
        part = document.part.part_related_by(RT.FOOTNOTES)
    except KeyError:
        return {}
    try:
        from lxml import etree
        root = etree.fromstring(part.blob)
    except Exception:
        return {}

    notes: dict[str, str] = {}
    for node in root.findall(qn("w:footnote")):
        note_id = node.get(qn("w:id"))
        if note_id is None or int(note_id) <= 0:
            continue
        pieces = [t.text or "" for t in node.iter(qn("w:t"))]
        text = re.sub(r"\s+", " ", "".join(pieces)).strip()
        if text:
            notes[note_id] = text
    return notes


def _picture(run, document) -> dict[str, Any] | None:
    """Image bytes and the author's rendered size, from an inline drawing."""
    blips = run._r.findall(f".//{qn('a:blip')}")
    if not blips:
        return None
    rel_id = blips[0].get(qn("r:embed"))
    if not rel_id:
        return None
    try:
        image_part = document.part.related_parts[rel_id]
    except KeyError:
        return None

    width_pt = height_pt = None
    extent = run._r.find(f".//{qn('wp:extent')}")
    if extent is not None:
        try:
            width_pt = round(int(extent.get("cx")) / EMU_PER_PT, 2)
            height_pt = round(int(extent.get("cy")) / EMU_PER_PT, 2)
        except (TypeError, ValueError):
            pass

    blob = image_part.blob
    return {
        "blob": blob,
        "name": Path(str(image_part.partname)).name,
        "sha256": ir.sha256_bytes(blob),
        "width_pt": width_pt,
        "height_pt": height_pt,
    }


def read_docx(
    path: str,
    asset_dir: Path,
    *,
    lang_source: str = "en",
    lang_target: str = "fa-IR",
) -> dict[str, Any]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    document = Document(path)
    notes = _read_footnotes(document)
    core = document.core_properties

    book = ir.new_book(
        source_path=str(path),
        source_format="docx",
        source_sha256=ir.sha256_file(path),
        pages=0,
        title=(core.title or Path(path).stem).strip(),
        author=(core.author or "").strip(),
        lang_source=lang_source,
        lang_target=lang_target,
    )

    section = document.sections[0] if document.sections else None
    if section is not None and section.page_width and section.page_height:
        book["page"].update({
            "width_pt": round(section.page_width.pt, 2),
            "height_pt": round(section.page_height.pt, 2),
            "margin_top_pt": round(section.top_margin.pt, 2),
            "margin_bottom_pt": round(section.bottom_margin.pt, 2),
            "margin_inner_pt": round(section.left_margin.pt, 2),
            "margin_outer_pt": round(section.right_margin.pt, 2),
        })

    blocks: list[dict[str, Any]] = []
    footnotes: list[dict[str, Any]] = []
    seen_assets: dict[str, str] = {}
    used_notes: set[str] = set()
    counter = 0

    def add(block_type: str, **fields: Any) -> dict[str, Any]:
        nonlocal counter
        counter += 1
        block = ir.make_block(block_type, counter, **fields)
        blocks.append(block)
        return block

    warnings: list[dict[str, Any]] = []

    def read_paragraph(paragraph, **extra: Any) -> None:
        spans: list[tuple[str, bool, bool]] = []
        pending_notes: list[str] = []

        for run in iter_runs(paragraph):
            picture = _picture(run, document)
            if picture is not None:
                _flush(spans, pending_notes, paragraph, add, footnotes,
                       used_notes, notes, extra)
                spans, pending_notes = [], []
                digest = picture["sha256"]
                if digest in seen_assets:
                    asset_name = seen_assets[digest]
                else:
                    asset_name = f"d{len(seen_assets) + 1:04d}-{picture['name']}"
                    (asset_dir / asset_name).write_bytes(picture["blob"])
                    seen_assets[digest] = asset_name
                add("image", page=0, asset=asset_name, sha256=digest, bbox=None,
                    width_pt=picture["width_pt"], height_pt=picture["height_pt"],
                    pixel_width=None, pixel_height=None, alt="", target_alt=None,
                    **extra)
                continue

            for ref in run._r.findall(qn("w:footnoteReference")):
                note_id = ref.get(qn("w:id"))
                if note_id in notes:
                    pending_notes.append(note_id)

            text = run.text
            if text:
                bold, italic = _run_style(run)
                spans.append((text, bold, italic))

            if has_page_break(run):
                _flush(spans, pending_notes, paragraph, add, footnotes,
                       used_notes, notes, extra)
                spans, pending_notes = [], []
                add("pagebreak", page=0, soft=False)

        _flush(spans, pending_notes, paragraph, add, footnotes, used_notes,
               notes, extra)

    def read_table(table, table_id: str, depth: int = 0) -> int:
        """One table's cells, each read exactly once. Returns the row count.

        Two traps, both of which used to be live.

        ``row.cells`` *expands* merges: a cell spanning two columns comes back
        twice, and a vertically merged one comes back on every row it covers —
        the same `w:tc` object each time. Since every cell becomes its own
        worksheet unit, that meant a merged cell's sentence was translated
        twice and printed twice. Cells are therefore keyed by the identity of
        the underlying element, and the span is recorded instead.

        And a table nested in a cell is not in ``document.iter_inner_content``
        at all, so its text vanished exactly as every table's did before.
        """
        rows = 0
        merges = 0
        for record in table_cells(table):
            rows = max(rows, record["row"] + record["row_span"] - 1)
            span = {name: record[name] for name in ("row_span", "col_span")
                    if record[name] > 1}
            merges += bool(span)
            cell = record["tc"]
            for paragraph in cell.paragraphs:
                read_paragraph(paragraph, table=table_id, row=record["row"],
                               cell=record["cell"], **span)
            for inner_number, inner in enumerate(cell.tables, start=1):
                read_table(inner, f"{table_id}-{record['row']}"
                                  f"{record['cell']}n{inner_number}", depth + 1)
        if merges:
            warnings.append({
                "kind": "table-merged-cells", "table": table_id,
                "detail": f"{merges} merged cell position(s); the text is read "
                          f"once and its span recorded, and the builder "
                          f"reproduces the merge",
            })
        return rows

    # The body in document order. `document.paragraphs` walks only top-level
    # paragraphs, so every table in the book — every cell of it — was dropped
    # without a word: not flagged, not empty, simply absent from the IR and
    # therefore from the translation and from the finished file.
    for index, item in enumerate(document.iter_inner_content()):
        if isinstance(item, Paragraph):
            read_paragraph(item)
        elif isinstance(item, Table):
            read_table(item, f"t{index:04d}")

    book["blocks"] = blocks
    book["footnotes"] = footnotes
    if len(document.sections) > 1:
        warnings.append({
            "kind": "sections-collapsed", "count": len(document.sections),
            "detail": "only the first section's page geometry is carried over",
        })
    if warnings:
        book["source"]["docx_warnings"] = warnings
    return book


def _flush(spans, pending_notes, paragraph, add, footnotes, used_notes, notes,
           extra: dict[str, Any] | None = None) -> None:
    """Emit the accumulated runs of one paragraph as a block."""
    if not spans:
        return
    text = ir.render_markup(spans).strip()
    if not text:
        return
    extra = dict(extra or {})

    for note_id in pending_notes:
        if note_id in used_notes:
            continue
        used_notes.add(note_id)
        note = ir.make_footnote(len(footnotes) + 1, anchor_block="",
                                text=notes[note_id], origin="source")
        footnotes.append(note)
        text = f"{text}[[fn:{note['id']}]]"

    style_name = getattr(paragraph.style, "name", "") or ""
    # The style records the paragraph's role; the numbering properties record
    # how deep it sits. A nested list needs both.
    depth = list_level(paragraph)
    level = _heading_level(style_name)
    if level is not None:
        block = add("heading", page=0, level=level, text=text, **extra)
    elif style_name.lower().startswith("quote") or style_name.lower() == "intense quote":
        block = add("blockquote", page=0, text=text, **extra)
    elif depth is not None or _LIST_STYLE.search(style_name):
        block = add("listitem", page=0, level=depth or 1,
                    ordered="number" in style_name.lower(), text=text, **extra)
    elif style_name.lower() == "caption":
        block = add("caption", page=0, text=text, **extra)
    else:
        block = add("paragraph", page=0, text=text, **extra)

    for note in footnotes:
        if not note["anchor_block"] and note["id"] in ir.footnote_refs(text):
            note["anchor_block"] = block["id"]

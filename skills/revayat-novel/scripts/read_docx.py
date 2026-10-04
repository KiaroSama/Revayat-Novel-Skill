"""DOCX → Book IR via python-docx, plus raw OOXML for what it cannot reach.

python-docx exposes paragraphs, runs and styles, but has no API for footnotes
(verified against python-docx 1.2.0), so ``word/footnotes.xml`` is read
straight off the package. Inline pictures are pulled from their relationship
so the original bytes and the author's ``wp:extent`` both survive.

Section breaks travel as ``book["sections"]``, each entry naming the block it
opens at. ``book["page"]`` still reports the first section's geometry, exactly
as it did when that was the only geometry the reader kept.

Running heads and feet travel on the same records, as prose to be translated
plus the fields and tabs laid out beside it. The author's own running head is
what a Persian edition should show — in Persian; copying it across untranslated
was never the alternative to dropping it.
"""

from __future__ import annotations

import re
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph
from docx.text.run import Run

import bookir as ir
from docxnotes import NOTE_PARTS, note_key, read_notes
from docxfurniture import running_of
from docxfurniture import (  # noqa: F401
    _pt, section_geometry, _column_count, _alignment, _running_runs,
    _running_pieces, _run_style,
)

LOG = logging.getLogger(__name__)

EMU_PER_PT = ir.EMU_PER_PT

_HEADING_STYLE = re.compile(r"^\s*heading\s*(\d)\s*$", re.I)
_LIST_STYLE = re.compile(r"list\s*(bullet|number|paragraph)", re.I)


def column_span(tc) -> int:
    """How many grid columns this ``w:tc`` covers, from ``w:gridSpan``."""
    properties = tc.find(qn("w:tcPr"))
    if properties is None:
        return 1
    grid = properties.find(qn("w:gridSpan"))
    if grid is None:
        return 1
    try:
        width = int(grid.get(qn("w:val")))
    except (TypeError, ValueError) as error:
        raise ValueError("invalid DOCX table grid span") from error
    if width < 1:
        raise ValueError("invalid DOCX table grid span")
    return width


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


def _continuation_is_empty(tc) -> bool:
    """Allow generated empty paragraphs, never discard authored live content."""
    for child in tc:
        if child.tag == qn("w:tcPr"):
            continue
        if child.tag != qn("w:p"):
            return False
        for item in child:
            if item.tag == qn("w:pPr"):
                continue
            if item.tag != qn("w:r"):
                return False
            for part in item:
                if part.tag == qn("w:rPr"):
                    continue
                if part.tag != qn("w:t") or part.text:
                    return False
    return True


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
        properties = tr.find(qn("w:trPr"))
        before = properties.find(qn("w:gridBefore")) if properties is not None else None
        try:
            column = int(before.get(qn("w:val"))) if before is not None else 0
        except (TypeError, ValueError) as error:
            raise ValueError("invalid DOCX table leading grid offset") from error
        if column < 0:
            raise ValueError("invalid DOCX table leading grid offset")
        following: dict[int, dict[str, Any]] = {}
        for tc in tr.findall(qn("w:tc")):
            width = column_span(tc)
            merge = _vertical_merge(tc)
            if merge == "continue":
                record = open_at.get(column)
                if record is None or record["col_span"] != width:
                    raise ValueError("invalid DOCX table vertical merge continuation")
                if not _continuation_is_empty(tc):
                    raise ValueError("populated DOCX table vertical merge continuation")
                record["row_span"] += 1
                following[column] = record
            else:
                record = {"tc": _Cell(tc, table), "row": row_number,
                          "cell": column + 1, "row_span": 1, "col_span": width}
                found.append(record)
                if merge == "restart":
                    following[column] = record
            column += width
        # Only a restarted/continued span in the immediately preceding row is
        # eligible. Ordinary or absent cells must never seed a later merge.
        open_at = following
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


def hyperlinks(paragraph: Paragraph) -> list[dict[str, str]]:
    """The links in this paragraph, as ``{"text", "href"}``.

    ``iter_runs`` keeps a link's *words* - that was the loss worth fixing first,
    because a missing phrase reads as a sentence the author wrote that way. The
    target is the other half: it survives here, on the block, rather than in the
    markup the translator answers. A URL is not text to translate, and a paired
    inline marker for something a printed Persian book cannot click would be a
    contract every worksheet has to honour for no reader's benefit.
    """
    found = []
    for item in paragraph.iter_inner_content():
        if not isinstance(item, Hyperlink):
            continue
        # `address` is the external target; `fragment` the in-document anchor.
        # A link may have either or both, and one without the other is normal.
        target = item.address or ""
        if item.fragment:
            target = f"{target}#{item.fragment}" if target else f"#{item.fragment}"
        if item.text and target:
            found.append({"text": item.text, "href": target})
    return found


def has_page_break(run: Run) -> bool:
    return any(node.get(qn("w:type")) == "page"
               for node in run._r.findall(qn("w:br")))


def ends_section(paragraph: Paragraph) -> bool:
    """True when this paragraph carries the ``w:sectPr`` that closes a section.

    A section's properties sit at its *end*, not its start: on the last
    paragraph of the section for all but the last one, and on ``w:body`` for
    that. So the block after this paragraph is the first of the next section.
    """
    properties = paragraph._p.find(qn("w:pPr"))
    return properties is not None and properties.find(qn("w:sectPr")) is not None


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


def _read_notes(document) -> dict[str, str]:
    """Native note markup, keyed by note kind and normalized numeric ID."""
    return read_notes(document, _run_style)


def _picture_element(element, document) -> dict[str, Any]:
    """Image bytes and the author's rendered size, from an inline drawing."""
    blips = element.findall(f".//{qn('a:blip')}")
    if len(blips) != 1:
        raise ValueError("DOCX drawing is not one supported embedded picture")
    rel_id = blips[0].get(qn("r:embed"))
    if not rel_id:
        raise ValueError("DOCX picture has no embedded image relationship")
    try:
        image_part = document.part.related_parts[rel_id]
    except KeyError as error:
        raise ValueError("DOCX picture relationship is missing") from error

    width_pt = height_pt = None
    extent = element.find(f".//{qn('wp:extent')}")
    if extent is not None:
        try:
            width_pt = round(int(extent.get("cx")) / EMU_PER_PT, 2)
            height_pt = round(int(extent.get("cy")) / EMU_PER_PT, 2)
        except (TypeError, ValueError) as error:
            raise ValueError("DOCX picture has invalid source dimensions") from error
        if width_pt <= 0 or height_pt <= 0:
            raise ValueError("DOCX picture has nonpositive source dimensions")
    else:
        raise ValueError("DOCX picture has no source dimensions")

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
    # A DOCX is a zip, and python-docx unpacks whatever the central directory
    # declares. Refused here first, before any member is inflated.
    ir.check_archive_limits(path)
    document = Document(path)
    # python-docx's paragraph/run iterators do not expose these source nodes.
    # Refuse their presence rather than certify an apparently complete book
    # with the hidden subtree removed. Resolve revisions/unsupported objects in
    # a separate faithful source copy before importing again.
    unsupported = {qn(name) for name in (
        "w:sdt", "w:ins", "w:del", "w:moveFrom", "w:moveTo", "w:altChunk",
        "w:fldSimple", "w:customXml", "m:oMath", "m:oMathPara")}
    for element in document.element.body.iter():
        if element.tag in unsupported:
            LOG.error("Refused unsupported DOCX source node %s", element.tag)
            raise ValueError(f"unsupported DOCX source content: {element.tag}")
    notes = _read_notes(document)
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
    counter = 0

    # Only the bookmarks an in-document link actually points at are carried.
    # A Word file is full of `_GoBack` and `_Toc…` names nothing refers to, and
    # every one of them would become a bookmark in the Persian edition for no
    # reader's benefit — while an anchor whose destination was dropped is a
    # dead link the package gate rightly refuses.
    anchored = {node.get(qn("w:anchor"))
                for node in document.element.body.iter(qn("w:hyperlink"))
                if node.get(qn("w:anchor"))}
    placed_anchors: set[str] = set()

    def add(block_type: str, **fields: Any) -> dict[str, Any]:
        nonlocal counter
        counter += 1
        block = ir.make_block(block_type, counter, **fields)
        blocks.append(block)
        return block

    warnings: list[dict[str, Any]] = []
    running_count = 0

    def allocate_running() -> str:
        nonlocal running_count
        running_count += 1
        return f"rh{running_count:04d}"

    def keep_anchors(paragraph, first: int) -> None:
        """File the paragraph's linked-to bookmarks on the block it became.

        On the *first* block only. A paragraph split by a picture or a page
        break becomes several blocks, and repeating the name on each one would
        open the same bookmark two or three times — which Word resolves by
        sending every link to the first, silently.
        """
        names = [name for node in paragraph._p.iter(qn("w:bookmarkStart"))
                 if (name := node.get(qn("w:name"))) in anchored
                 and name not in placed_anchors]
        if not names:
            return
        block = next((b for b in blocks[first:] if b["type"] in ir.TEXT_TYPES), None)
        if block is None:
            return
        block["bookmarks"] = names
        placed_anchors.update(names)

    def read_paragraph(paragraph, **extra: Any) -> None:
        first_block = len(blocks)
        spans: list[tuple[str, bool, bool]] = []
        links: dict[Any, dict[str, str]] = {}
        active_link = None

        def append(text, bold, italic):
            spans.append((text, bold, italic))
            if active_link is not None:
                identity, target = active_link
                links.setdefault(identity, {"text": "", "href": target})["text"] += text

        def flush():
            carried = [{**link, "text": link["text"].strip(" ")} for link in links.values()
                       if link["text"].strip()]
            prose = {**extra, "links": carried} if carried else extra
            _flush(spans, paragraph, add, footnotes, prose)
            spans.clear()
            links.clear()

        def linked_runs():
            for item in paragraph.iter_inner_content():
                if isinstance(item, Run):
                    yield item, None
                elif isinstance(item, Hyperlink):
                    target = item.address or ""
                    if item.fragment:
                        target += "#" + item.fragment
                    for run in item.runs:
                        yield run, (item._hyperlink, target) if target else None

        reference_kinds = {qn(reference): kind for kind, _, _, reference in NOTE_PARTS}
        for run, active_link in linked_runs():
            bold, italic = _run_style(run)
            # A run may interleave text, pictures, breaks and note references.
            # Neither aggregate run.text nor a single first-picture lookup can
            # express that sequence; consume each child exactly once instead.
            for child in run._r:
                if child.tag in reference_kinds:
                    kind = reference_kinds[child.tag]
                    key = note_key(kind, child.get(qn("w:id")))
                    if key not in notes or not notes[key].strip():
                        raise ValueError(f"unresolved or empty DOCX note {key}")
                    note = ir.make_footnote(len(footnotes) + 1, anchor_block="",
                                           text=notes[key], origin="source")
                    # These are authored native controls, not extractor noise.
                    note["text"] = notes[key]
                    footnotes.append(note)
                    spans.append((f"[[fn:{note['id']}]]", False, False))
                elif child.tag == qn("w:drawing"):
                    flush()
                    if active_link is not None:
                        warnings.append({"kind": "image-hyperlink-dropped",
                                         "detail": "image bytes and placement are retained; "
                                                   "the image's clickable target is not represented"})
                    placements = [node for node in child if node.tag in
                                  {qn("wp:inline"), qn("wp:anchor")}]
                    if not placements:
                        raise ValueError("DOCX drawing has no supported image placement")
                    for placement in placements:
                        picture = _picture_element(placement, document)
                        digest = picture["sha256"]
                        if digest in seen_assets:
                            asset_name = seen_assets[digest]
                        else:
                            # Content identity, not encounter order: an import that
                            # later refuses must never replace an older book's image.
                            asset_name = f"d-{digest}{Path(picture['name']).suffix.lower()}"
                            destination = asset_dir / asset_name
                            if destination.exists() and destination.read_bytes() != picture["blob"]:
                                raise ValueError("DOCX asset content disagrees with its identity")
                            if not destination.exists():
                                temporary = None
                                try:
                                    with tempfile.NamedTemporaryFile(dir=asset_dir, suffix=".tmp", delete=False) as handle:
                                        temporary = Path(handle.name)
                                        handle.write(picture["blob"])
                                        handle.flush()
                                        os.fsync(handle.fileno())
                                    os.replace(temporary, destination)
                                finally:
                                    if temporary is not None:
                                        temporary.unlink(missing_ok=True)
                            seen_assets[digest] = asset_name
                        add("image", page=0, asset=asset_name, sha256=digest, bbox=None,
                            width_pt=picture["width_pt"], height_pt=picture["height_pt"],
                            pixel_width=None, pixel_height=None, alt="", target_alt=None,
                            **extra)
                elif child.tag == qn("w:br") and child.get(qn("w:type")) == "page":
                    flush()
                    add("pagebreak", page=0, soft=False, **extra)
                elif child.tag == qn("w:t"):
                    append(child.text or "", bold, italic)
                elif child.tag in {qn("w:tab"), qn("w:cr"), qn("w:noBreakHyphen")}:
                    value = {qn("w:tab"): "\t", qn("w:cr"): "\n",
                             qn("w:noBreakHyphen"): "\u2011"}[child.tag]
                    append(value, bold, italic)
                elif child.tag == qn("w:br"):
                    if child.get(qn("w:type"), "textWrapping") != "textWrapping":
                        raise ValueError("unsupported DOCX run break type")
                    append("\n", bold, italic)
                elif child.tag == qn("w:softHyphen"):
                    append("\u00ad", bold, italic)
                elif child.tag not in {qn("w:" + name) for name in (
                        "rPr", "lastRenderedPageBreak", "fldChar", "instrText",
                        "commentReference", "footnoteRef", "endnoteRef")}:
                    raise ValueError(f"unsupported DOCX run content: {child.tag}")
        flush()
        keep_anchors(paragraph, first_block)


    def read_table(table, table_id: str, **parent: Any) -> int:
        """One table's cells, each read exactly once. Returns the row count.

        Raw cells avoid python-docx's expanded merge aliases. Their children
        are read in XML order, with nested tables owned by the immediate cell.
        """
        rows = 0
        merges = 0
        for record in table_cells(table):
            rows = max(rows, record["row"] + record["row_span"] - 1)
            span = {name: record[name] for name in ("row_span", "col_span")
                    if record[name] > 1}
            merges += bool(span)
            cell = record["tc"]
            inner_number = 0
            for element in cell._tc:
                if element.tag == qn("w:p"):
                    read_paragraph(Paragraph(element, cell), table=table_id, row=record["row"],
                                   cell=record["cell"], **span, **parent)
                elif element.tag == qn("w:tbl"):
                    inner_number += 1
                    read_table(Table(element, cell), f"{table_id}-{record['row']}"
                                    f"{record['cell']}n{inner_number}",
                               parent_table=table_id, parent_row=record["row"],
                               parent_cell=record["cell"])
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
    #
    # `section_starts` is where each section begins, as a count of blocks read
    # so far: section 0 opens the book, and every later one opens right after
    # the paragraph carrying the previous section's `w:sectPr`.
    section_starts = [0]
    for index, item in enumerate(document.iter_inner_content()):
        if isinstance(item, Paragraph):
            read_paragraph(item)
            if ends_section(item):
                section_starts.append(len(blocks))
        elif isinstance(item, Table):
            read_table(item, f"t{index:04d}")

    book["blocks"] = blocks
    book["footnotes"] = footnotes

    def start_block(number: int) -> str | None:
        """The id of the first block of section ``number``.

        ``None`` when the section holds no blocks at all — a document that ends
        with a section break has a final section made of nothing, and there is
        nothing in the built file for the break to sit before.
        """
        if number >= len(section_starts):
            return None
        at = section_starts[number]
        return blocks[at]["id"] if at < len(blocks) else None

    # Word only prints an even-page header when the whole document says so, so
    # the switch is read once here and every section is asked in its light.
    even_and_odd = bool(document.settings.odd_and_even_pages_header_footer)
    book["sections"] = [
        {"index": number, "start_block": start_block(number),
         **section_geometry(section),
         **running_of(section, number, even_and_odd, allocate_running, warnings)}
        for number, section in enumerate(document.sections)
    ]
    linked = sum(len(block.get("links") or []) for block in blocks)
    if linked:
        warnings.append({
            "kind": "hyperlinks-kept-as-metadata", "count": linked,
            "detail": "link text is in the prose and each target is on its "
                      "block as `links`; the builder puts a live link back "
                      "only where the translation kept the display phrase "
                      "word for word, and names every one it could not",
        })

    # `running-heads-dropped` used to be reported here, and the reasoning it
    # carried still holds: an English running head on a Persian page is worse
    # than none. Translating them answers it without throwing them away, so the
    # warning is gone and what genuinely cannot be carried — a picture, a table,
    # a link target — is named on its own above.
    #
    # `sections-collapsed` used to be reported here. It said the truth at the
    # time — only the first section's geometry was kept — and became a lie the
    # moment `book["sections"]` started carrying every one of them. What is
    # still genuinely dropped is named on its own below.
    for record, section in zip(book["sections"], document.sections):
        columns = _column_count(section)
        if columns > 1:
            warnings.append({
                "kind": "section-columns-dropped", "section": record["index"],
                "count": columns,
                "detail": f"section {record['index']} is set in {columns} "
                          f"columns; page size, orientation and margins are "
                          f"carried but the Persian edition is set in one",
            })

    if warnings:
        book["source"]["docx_warnings"] = warnings
    LOG.info("DOCX extracted: %d blocks, %d native notes", len(blocks), len(footnotes))
    return book


def _flush(spans, paragraph, add, footnotes,
           extra: dict[str, Any] | None = None) -> None:
    """Emit the accumulated runs of one paragraph as a block."""
    if not spans:
        return
    text = ir.render_markup(spans).strip(" ")
    if not text.strip():
        return
    extra = dict(extra or {})

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

    block["text"] = text
    for note in footnotes:
        if not note["anchor_block"] and note["id"] in ir.footnote_refs(text):
            note["anchor_block"] = block["id"]

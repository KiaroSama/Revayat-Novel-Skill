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

import bookir as ir

EMU_PER_PT = ir.EMU_PER_PT

_HEADING_STYLE = re.compile(r"^\s*heading\s*(\d)\s*$", re.I)
_LIST_STYLE = re.compile(r"list\s*(bullet|number|paragraph)", re.I)


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

    for paragraph in document.paragraphs:
        spans: list[tuple[str, bool, bool]] = []
        pending_notes: list[str] = []

        for run in paragraph.runs:
            picture = _picture(run, document)
            if picture is not None:
                _flush(spans, pending_notes, paragraph, add, footnotes, used_notes, notes)
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
                    pixel_width=None, pixel_height=None, alt="", target_alt=None)
                continue

            for ref in run._r.findall(qn("w:footnoteReference")):
                note_id = ref.get(qn("w:id"))
                if note_id in notes:
                    pending_notes.append(note_id)

            text = run.text
            if text:
                bold, italic = _run_style(run)
                spans.append((text, bold, italic))

        _flush(spans, pending_notes, paragraph, add, footnotes, used_notes, notes)

    book["blocks"] = blocks
    book["footnotes"] = footnotes
    return book


def _flush(spans, pending_notes, paragraph, add, footnotes, used_notes, notes) -> None:
    """Emit the accumulated runs of one paragraph as a block."""
    if not spans:
        return
    text = ir.render_markup(spans).strip()
    if not text:
        return

    for note_id in pending_notes:
        if note_id in used_notes:
            continue
        used_notes.add(note_id)
        note = ir.make_footnote(len(footnotes) + 1, anchor_block="",
                                text=notes[note_id], origin="source")
        footnotes.append(note)
        text = f"{text}[[fn:{note['id']}]]"

    style_name = getattr(paragraph.style, "name", "") or ""
    level = _heading_level(style_name)
    if level is not None:
        block = add("heading", page=0, level=level, text=text)
    elif style_name.lower().startswith("quote") or style_name.lower() == "intense quote":
        block = add("blockquote", page=0, text=text)
    elif _LIST_STYLE.search(style_name):
        block = add("listitem", page=0, level=1,
                    ordered="number" in style_name.lower(), text=text)
    elif style_name.lower() == "caption":
        block = add("caption", page=0, text=text)
    else:
        block = add("paragraph", page=0, text=text)

    for note in footnotes:
        if not note["anchor_block"] and note["id"] in ir.footnote_refs(text):
            note["anchor_block"] = block["id"]

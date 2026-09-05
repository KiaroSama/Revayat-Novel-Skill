"""EPUB → Book IR.

EPUB is the friendliest source: it already *is* structured text, so headings,
emphasis, block quotes, lists and image references survive without guessing at
font sizes. Spine order is authoritative; the reader never sorts by filename.

Footnote handling covers the two shapes that account for nearly every ebook:
an ``epub:type="noteref"`` link, and a bare ``<sup><a href="#id">`` — with the
note body pulled from the element the link points at, in this file or another.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urldefrag
from xml.etree import ElementTree

from bs4 import BeautifulSoup, NavigableString, Tag

import bookir as ir

HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
BOLD_TAGS = {"b", "strong"}
ITALIC_TAGS = {"i", "em", "cite", "dfn", "var"}
VERBATIM_TAGS = {"code", "kbd", "samp", "tt", "pre"}
SKIP_TAGS = {"script", "style", "head", "nav", "template"}
BLOCK_TAGS = {"p", "div", "blockquote", "li", "figcaption", "h1", "h2", "h3",
              "h4", "h5", "h6", "section", "article", "figure", "aside", "hr",
              "ul", "ol", "table", "tr", "td", "th", "dl", "dt", "dd", "img",
              "body", "main", "header", "footer"}

_OPF_NS = {"opf": "http://www.idpf.org/2007/opf",
           "cnt": "urn:oasis:names:tc:opendocument:xmlns:container"}
_DC_NS = {"dc": "http://purl.org/dc/elements/1.1/"}


def _opf_path(archive: zipfile.ZipFile) -> str:
    root = ElementTree.fromstring(archive.read("META-INF/container.xml"))
    node = root.find(".//cnt:rootfile", _OPF_NS)
    if node is None or not node.get("full-path"):
        raise ValueError("EPUB container.xml has no rootfile")
    return node.get("full-path")  # type: ignore[return-value]


def _spine_documents(archive: zipfile.ZipFile, opf: str) -> tuple[list[str], dict[str, str]]:
    root = ElementTree.fromstring(archive.read(opf))
    base = posixpath.dirname(opf)
    manifest: dict[str, tuple[str, str]] = {}
    for item in root.iterfind(".//opf:manifest/opf:item", _OPF_NS):
        item_id = item.get("id")
        href = item.get("href")
        if item_id and href:
            full = posixpath.normpath(posixpath.join(base, unquote(href)))
            manifest[item_id] = (full, item.get("media-type", ""))

    documents = [
        manifest[ref.get("idref")][0]
        for ref in root.iterfind(".//opf:spine/opf:itemref", _OPF_NS)
        if ref.get("idref") in manifest
        and ref.get("linear", "yes").lower() != "no"
    ]

    meta: dict[str, str] = {}
    for field in ("title", "creator", "language"):
        node = root.find(f".//dc:{field}", _DC_NS)
        if node is not None and node.text:
            meta[field] = node.text.strip()
    return documents, meta


def _span(text: str, *, bold: bool = False, italic: bool = False,
          verbatim: bool = False) -> dict[str, Any]:
    return {"text": text, "bold": bold, "italic": italic,
            "verbatim": verbatim, "footnote": None}


def _styled(text: str, bold: bool, italic: bool) -> list[dict[str, Any]]:
    """Emit a styled span with surrounding whitespace pushed outside it.

    ``*  word *`` is not emphasis to any parser, so the markers have to hug the
    visible characters.
    """
    if not (bold or italic) or not text.strip():
        return [_span(text)] if text else []
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    spans = []
    if lead:
        spans.append(_span(lead))
    spans.append(_span(text.strip(), bold=bold, italic=italic))
    if trail:
        spans.append(_span(trail))
    return spans


def _inline_spans(node: Tag, bold: bool = False, italic: bool = False
                  ) -> list[dict[str, Any]]:
    """Flatten an element's inline content into span dicts."""
    spans: list[dict[str, Any]] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            text = re.sub(r"\s+", " ", str(child))
            if text:
                spans.extend(_styled(text, bold, italic))
            continue
        if not isinstance(child, Tag) or child.name in SKIP_TAGS:
            continue
        if child.name == "br":
            spans.append(_span(" ", bold=bold, italic=italic))
            continue
        if child.name in VERBATIM_TAGS:
            body = re.sub(r"\s+", " ", child.get_text()).strip()
            if body:
                # Verbatim is a span *kind*, not literal backticks in the text:
                # writing the markers here would only get them escaped again.
                spans.append(_span(body, verbatim=True))
            continue
        spans.extend(_inline_spans(
            child,
            bold or child.name in BOLD_TAGS,
            italic or child.name in ITALIC_TAGS,
        ))
    return spans


def _markup(node: Tag, footnote_marks: dict[int, str]) -> str:
    """Inline markup for a block element, with footnote tokens re-inserted."""
    text = ir.render_spans(_inline_spans(node))
    marker = footnote_marks.get(id(node))
    if marker:
        text = f"{text.rstrip()}[[fn:{marker}]]"
    return text.strip()


def _is_note_link(tag: Tag) -> bool:
    epub_type = (tag.get("epub:type") or tag.get("type") or "").lower()
    if "noteref" in epub_type:
        return True
    href = tag.get("href") or ""
    if not href.startswith("#") and "#" not in href:
        return False
    parent = tag.parent
    return isinstance(parent, Tag) and parent.name in {"sup", "sub"}


def _resolve_note_body(soup: BeautifulSoup, anchor: str) -> str:
    target = soup.find(id=anchor)
    if target is None:
        return ""
    # A note is often a <p id=..> inside an <aside>/<div>; prefer the container
    # when the id sits on a bare backlink anchor with no text of its own.
    if isinstance(target, Tag) and not target.get_text(strip=True) and target.parent:
        target = target.parent
    text = re.sub(r"\s+", " ", target.get_text(" ", strip=True))
    # Strip a leading marker such as "12." or "[3]" that the ebook rendered
    # as literal text; Word will number the footnote itself.
    return re.sub(r"^\s*[\[\(]?\d{1,3}[\]\).:]?\s*", "", text).strip()


def read_epub(
    path: str,
    asset_dir: Path,
    *,
    lang_source: str = "en",
    lang_target: str = "fa-IR",
) -> dict[str, Any]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    archive = zipfile.ZipFile(path)
    try:
        opf = _opf_path(archive)
        documents, meta = _spine_documents(archive, opf)

        book = ir.new_book(
            source_path=str(path),
            source_format="epub",
            source_sha256=ir.sha256_file(path),
            pages=len(documents),
            title=meta.get("title", Path(path).stem),
            author=meta.get("creator", ""),
            lang_source=lang_source or meta.get("language", "en"),
            lang_target=lang_target,
        )

        blocks: list[dict[str, Any]] = []
        footnotes: list[dict[str, Any]] = []
        seen_assets: dict[str, str] = {}
        counter = 0

        def add(block_type: str, **fields: Any) -> dict[str, Any]:
            nonlocal counter
            counter += 1
            block = ir.make_block(block_type, counter, **fields)
            blocks.append(block)
            return block

        for doc_index, doc_path in enumerate(documents, start=1):
            try:
                raw = archive.read(doc_path)
            except KeyError:
                continue
            soup = BeautifulSoup(raw, "html.parser")
            for junk in soup.find_all(list(SKIP_TAGS)):
                junk.decompose()

            footnote_marks = _harvest_footnotes(soup, footnotes, len(footnotes))
            body = soup.body or soup
            if doc_index > 1:
                add("pagebreak", page=doc_index, soft=False)
            _walk(body, add, archive, doc_path, asset_dir, seen_assets,
                  footnote_marks, doc_index)

        book["blocks"] = [b for b in blocks if _keep(b)]
        book["footnotes"] = [f for f in footnotes if f["text"]]
        _drop_orphan_footnote_tokens(book)
        return book
    finally:
        archive.close()


def _harvest_footnotes(soup: BeautifulSoup, footnotes: list[dict[str, Any]],
                       start: int) -> dict[int, str]:
    """Replace note links with tokens; return ``id(block_tag) -> footnote id``.

    Returns a mapping keyed by the *inline* anchor's nearest block ancestor so
    the token can be appended when that block is rendered.
    """
    marks: dict[int, str] = {}
    index = start
    for link in soup.find_all("a"):
        if not _is_note_link(link):
            continue
        anchor = urldefrag(link.get("href") or "").fragment
        if not anchor:
            continue
        body = _resolve_note_body(soup, anchor)
        if not body:
            continue
        index += 1
        note = ir.make_footnote(index, anchor_block="", text=body, origin="source")
        footnotes.append(note)
        holder = link.parent if link.parent and link.parent.name in {"sup", "sub"} else link
        block = holder.find_parent(lambda t: isinstance(t, Tag) and t.name in BLOCK_TAGS)
        if block is not None:
            marks[id(block)] = note["id"]
        holder.decompose()

        # Remove the note body itself so it is not also emitted as a paragraph.
        target = soup.find(id=anchor)
        if isinstance(target, Tag):
            container = target
            if container.parent and container.parent.name in {"aside", "li", "div"}:
                container = container.parent
            container.decompose()
    return marks


def _walk(node: Tag, add, archive: zipfile.ZipFile, doc_path: str,
          asset_dir: Path, seen: dict[str, str], marks: dict[int, str],
          page: int) -> None:
    for child in node.children:
        if not isinstance(child, Tag) or child.name in SKIP_TAGS:
            continue
        name = child.name

        if name == "img" or name == "image":
            _add_image(child, add, archive, doc_path, asset_dir, seen, page)
            continue
        if name == "hr":
            add("separator", page=page)
            continue
        if name in HEADINGS:
            text = _markup(child, marks)
            if text:
                add("heading", page=page, level=HEADINGS[name], text=text)
            continue
        if name in {"p", "figcaption", "dt", "dd", "td", "th"}:
            if child.find("img"):
                _emit_mixed(child, add, archive, doc_path, asset_dir, seen, marks, page)
                continue
            text = _markup(child, marks)
            if text:
                kind = "caption" if name == "figcaption" else "paragraph"
                add(kind, page=page, text=text)
            continue
        if name == "blockquote":
            _walk_quote(child, add, marks, page)
            continue
        if name == "li":
            text = _markup(child, marks)
            if text:
                add("listitem", page=page, level=1, ordered=_ordered(child), text=text)
            continue
        # Container element: recurse.
        _walk(child, add, archive, doc_path, asset_dir, seen, marks, page)


def _emit_mixed(node: Tag, add, archive, doc_path, asset_dir, seen, marks, page) -> None:
    """A paragraph that also holds an image — emit both, in document order."""
    for child in node.children:
        if isinstance(child, Tag) and child.name in {"img", "image"}:
            _add_image(child, add, archive, doc_path, asset_dir, seen, page)
    stripped = BeautifulSoup(str(node), "html.parser")
    for image in stripped.find_all(["img", "image"]):
        image.decompose()
    text = _markup(stripped, marks)
    if text:
        add("paragraph", page=page, text=text)


def _walk_quote(node: Tag, add, marks: dict[int, str], page: int) -> None:
    paragraphs = node.find_all("p", recursive=False) or [node]
    for paragraph in paragraphs:
        text = _markup(paragraph, marks)
        if text:
            add("blockquote", page=page, text=text)


def _ordered(item: Tag) -> bool:
    parent = item.find_parent(["ol", "ul"])
    return bool(parent and parent.name == "ol")


def _add_image(tag: Tag, add, archive: zipfile.ZipFile, doc_path: str,
               asset_dir: Path, seen: dict[str, str], page: int) -> None:
    href = tag.get("src") or tag.get("xlink:href") or tag.get("href")
    if not href:
        return
    target = posixpath.normpath(
        posixpath.join(posixpath.dirname(doc_path), unquote(urldefrag(href).url))
    )
    try:
        data = archive.read(target)
    except KeyError:
        return

    digest = ir.sha256_bytes(data)
    if digest in seen:
        asset_name = seen[digest]
    else:
        asset_name = f"e{page:04d}-{Path(target).name}"
        (asset_dir / asset_name).write_bytes(data)
        seen[digest] = asset_name

    width, height = _pixel_size(data)
    add(
        "image",
        page=page,
        asset=asset_name,
        sha256=digest,
        bbox=None,
        width_pt=None,      # EPUB is reflowable: no authoritative physical size
        height_pt=None,
        pixel_width=width,
        pixel_height=height,
        alt=(tag.get("alt") or "").strip(),
        target_alt=None,
    )


def _pixel_size(data: bytes) -> tuple[int | None, int | None]:
    """Pixel dimensions without pulling in an imaging dependency."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))
    if data[:2] == b"\xff\xd8":  # JPEG: scan the segment chain for SOFn
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return (int.from_bytes(data[offset + 7:offset + 9], "big"),
                        int.from_bytes(data[offset + 5:offset + 7], "big"))
            offset += 2 + int.from_bytes(data[offset + 2:offset + 4], "big")
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return (int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little"))
    return (None, None)


def _keep(block: dict[str, Any]) -> bool:
    if block["type"] in ir.TEXT_TYPES:
        return bool((block.get("text") or "").strip())
    return True


def _drop_orphan_footnote_tokens(book: dict[str, Any]) -> None:
    """Remove tokens whose note body was discarded as empty."""
    live = {note["id"] for note in book["footnotes"]}
    for block in ir.iter_text_blocks(book):
        text = block.get("text") or ""
        if "[[fn:" not in text:
            continue
        block["text"] = ir.FOOTNOTE_TOKEN.sub(
            lambda m: m.group(0) if m.group(1) in live else "", text
        )

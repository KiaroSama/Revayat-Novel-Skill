"""EPUB → Book IR.

EPUB is the friendliest source: it already *is* structured text, so headings,
emphasis, block quotes, lists and image references survive without guessing at
font sizes. Spine order is authoritative; the reader never sorts by filename.

Footnote handling covers the two shapes that account for nearly every ebook:
an ``epub:type="noteref"`` link, and a bare ``<sup><a href="#id">`` — with the
note body pulled from the element the link points at, in this file or another.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import logging
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urldefrag, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Tag

import bookir as ir

LOG = logging.getLogger(__name__)

from epubdom import (  # noqa: F401
    BLOCK_TAGS, BOLD_TAGS, HEADINGS, ITALIC_TAGS, SKIP_TAGS, VERBATIM_TAGS,
    _inline_spans, _link_target, _markup, _span, _styled, walk as _dom_walk,
)

_OPF_NS = {"opf": "http://www.idpf.org/2007/opf",
           "cnt": "urn:oasis:names:tc:opendocument:xmlns:container"}
_DC_NS = {"dc": "http://purl.org/dc/elements/1.1/"}


def _local_member(document: str, href: str) -> str:
    """Resolve an EPUB-local URI without dropping its document component."""
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc or parsed.query:
        raise ValueError("EPUB resource must be an archive-local URI")
    path = unquote(parsed.path)
    if "\\" in path or path.startswith("/"):
        raise ValueError("unsafe EPUB resource path")
    member = posixpath.normpath(posixpath.join(posixpath.dirname(document), path)) if path else document
    if member == ".." or member.startswith("../"):
        raise ValueError("EPUB resource escapes its archive")
    return member


def _opf_path(archive: zipfile.ZipFile) -> str:
    root = ElementTree.fromstring(archive.read("META-INF/container.xml"))
    node = root.find(".//cnt:rootfile", _OPF_NS)
    if node is None or not node.get("full-path"):
        raise ValueError("EPUB container.xml has no rootfile")
    return node.get("full-path")  # type: ignore[return-value]


def _spine_documents(archive: zipfile.ZipFile, opf: str) -> tuple[list[str], dict[str, str]]:
    root = ElementTree.fromstring(archive.read(opf))
    manifest: dict[str, tuple[str, str]] = {}
    for item in root.iterfind(".//opf:manifest/opf:item", _OPF_NS):
        item_id = item.get("id")
        href = item.get("href")
        if not item_id or not href or item_id in manifest:
            raise ValueError("EPUB manifest requires unique ids and nonempty hrefs")
        full = _local_member(opf, href)
        manifest[item_id] = (full, item.get("media-type", ""))

    documents: list[str] = []
    for ref in root.iterfind(".//opf:spine/opf:itemref", _OPF_NS):
        identity = ref.get("idref")
        if identity not in manifest:
            raise ValueError(f"EPUB spine names an unknown document: {identity!r}")
        if ref.get("linear", "yes").lower() == "no":
            continue
        member, media = manifest[identity]
        if media not in ("application/xhtml+xml", "text/html"):
            raise ValueError(f"unsupported EPUB spine document media type: {media!r}")
        if member not in archive.namelist():
            raise ValueError(f"EPUB spine document is missing: {member}")
        if member in documents:
            raise ValueError(f"EPUB spine repeats a document: {member}")
        documents.append(member)
    if not documents:
        raise ValueError("EPUB spine contains no readable document")

    meta: dict[str, str] = {}
    for field in ("title", "creator", "language"):
        node = root.find(f".//dc:{field}", _DC_NS)
        if node is not None and node.text:
            meta[field] = node.text.strip()
    return documents, meta


def _links(node: Tag, warn) -> list[dict[str, str]]:
    """The links in this block, as ``{"text", "href"}``.

    Note links are already footnotes by the time this runs — `_harvest_footnotes`
    replaced them with tokens — so anything still here is an ordinary link.
    """
    found: list[dict[str, str]] = []
    for tag in node.find_all("a"):
        if _is_note_link(tag):
            continue
        text = re.sub(r"\s+", " ", tag.get_text()).strip()
        target = _link_target(tag.get("href") or "", warn)
        if text and target:
            found.append({"text": text, "href": target})
    return found


def _anchors(node: Tag) -> list[str]:
    """Every id this block carries, its own first."""
    names = [node.get("id")] if node.get("id") else []
    names += [tag.get("id") for tag in node.find_all(id=True)]
    return [name for name in dict.fromkeys(names) if name]


def _prose(node: Tag, marks: dict[int, str], warn) -> tuple[str, dict[str, Any]]:
    """A block's markup, plus the link and anchor context that belongs to it.

    One helper because the six places that emit prose must all carry it — a
    call site that forgets is a paragraph whose links are silently gone, which
    is exactly how the targets were lost in the first place.
    """
    text = _markup(node, marks)
    extra: dict[str, Any] = {}
    links = _links(node, warn)
    if links:
        extra["links"] = links
    anchors = _anchors(node)
    if anchors:
        extra["bookmarks"] = anchors
    return text, extra


def _settle_anchors(book: dict[str, Any], wanted: set[str]) -> None:
    """Keep only the anchors something actually links to, each on one block.

    A book is full of ids nothing points at — every generated section wrapper
    has one — and carrying them all would open a bookmark per paragraph in the
    Persian edition for no reader's benefit. Claiming each name once matters
    too: the same id opened twice sends every link to the first, silently.
    """
    claimed: set[str] = set()
    for block in book.get("blocks", []):
        names = [name for name in (block.get("bookmarks") or ())
                 if name in wanted and name not in claimed]
        claimed.update(names)
        if names:
            block["bookmarks"] = names
        else:
            block.pop("bookmarks", None)

def _is_note_link(tag: Tag) -> bool:
    epub_type = (tag.get("epub:type") or tag.get("type") or "").lower()
    role = (tag.get("role") or "").lower()
    if "backlink" in epub_type or role == "doc-backlink":
        return False
    if "noteref" in epub_type.split() or role == "doc-noteref":
        return True
    href = tag.get("href") or ""
    if not href.startswith("#") and "#" not in href:
        return False
    parent = tag.parent
    return isinstance(parent, Tag) and parent.name in {"sup", "sub"}


def _note_body_node(soup: BeautifulSoup, anchor: str) -> Tag | None:
    target = soup.find(id=unquote(anchor))
    if target is None:
        return None
    # A note is often a <p id=..> inside an <aside>/<div>; prefer the container
    # when the id sits on a bare backlink anchor with no text of its own.
    if isinstance(target, Tag) and not target.get_text(strip=True) and target.parent:
        target = target.parent
        if any(_is_note_link(link) for link in target.find_all("a")):
            raise ValueError("an empty note anchor would include note references in its body; identify a dedicated note container")
    return target if isinstance(target, Tag) else None


def _resolve_note_body(soup: BeautifulSoup, anchor: str) -> str:
    target = _note_body_node(soup, anchor)
    if target is None:
        return ""
    return _note_text(target)


def read_epub(
    path: str,
    asset_dir: Path,
    *,
    lang_source: str | None = None,
    lang_target: str = "fa-IR",
) -> dict[str, Any]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    # An EPUB is a zip, and a zip's members declare their own sizes: a few
    # kilobytes can announce gigabytes. Checked from the central directory
    # before anything is read out of it.
    ir.check_archive_limits(path)
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
        warnings: list[dict[str, Any]] = []
        counter = 0

        def warn(kind: str, detail: str) -> None:
            """Say once what could not be carried, with a count."""
            for entry in warnings:
                if entry["kind"] == kind:
                    entry["count"] += 1
                    return
            warnings.append({"kind": kind, "count": 1, "detail": detail})

        def add(block_type: str, **fields: Any) -> dict[str, Any]:
            nonlocal counter
            counter += 1
            block = ir.make_block(block_type, counter, **fields)
            blocks.append(block)
            return block

        soups: dict[str, BeautifulSoup] = {}

        def load_document(member: str) -> BeautifulSoup:
            if member not in soups:
                try:
                    raw = archive.read(member)
                except KeyError as error:
                    raise ValueError(f"EPUB document is missing: {member}") from error
                soup = BeautifulSoup(raw, "html.parser")
                for junk in soup.find_all(list(SKIP_TAGS)):
                    junk.decompose()
                soups[member] = soup
            return soups[member]

        for member in documents:
            load_document(member)
        marks = _book_footnotes(documents, load_document, footnotes)
        _namespace_anchors(documents, soups, warn)
        for doc_index, doc_path in enumerate(documents, start=1):
            soup = soups[doc_path]
            body = soup.body or soup
            if doc_index > 1:
                add("pagebreak", page=doc_index, soft=False)
            _dom_walk(body, add, archive, doc_path, asset_dir, seen_assets,
                  marks.get(doc_path, {}), doc_index, warn, add_image=_add_image)
            LOG.debug("Parsed EPUB spine item %d of %d", doc_index, len(documents))

        book["blocks"] = [b for b in blocks if _keep(b)]
        book["footnotes"] = [f for f in footnotes if f["text"]]
        _validate_footnote_tokens(book)
        by_note = {note["id"]: note for note in book["footnotes"]}
        for block in ir.iter_text_blocks(book):
            for reference in ir.footnote_refs(block.get("text") or ""):
                if reference in by_note:
                    by_note[reference]["anchor_block"] = block["id"]

        # Anchors are settled once every document has been read, because a link
        # in chapter 1 can point into chapter 9 and neither knows about the
        # other while it is being parsed.
        wanted = {link["href"][1:]
                  for block in book["blocks"]
                  for link in (block.get("links") or [])
                  if link["href"].startswith("#")}
        _settle_anchors(book, wanted)

        carried = sum(len(b.get("links") or []) for b in book["blocks"])
        if carried:
            warnings.append({
                "kind": "hyperlinks-kept-as-metadata", "count": carried,
                "detail": "link text is in the prose and each target is on its "
                          "block as `links`; the builder puts a live link back "
                          "only where the translation kept the display phrase "
                          "word for word, and names every one it could not",
            })
        if warnings:
            book["source"]["epub_warnings"] = warnings
        LOG.info("EPUB extracted: %d blocks, %d notes, %d distinct assets",
                 len(book["blocks"]), len(book["footnotes"]), len(seen_assets))
        return book
    except (ValueError, KeyError, zipfile.BadZipFile):
        LOG.error("EPUB extraction refused; source content was not complete")
        raise
    finally:
        archive.close()


def _note_text(target: Tag) -> str:
    """Keep note markup and actual quantities; remove only explicit numbering."""
    copied = deepcopy(target)
    for link in list(copied.find_all("a")):
        role = str(link.get("role") or "")
        kind = str(link.get("epub:type") or "")
        if role == "doc-backlink" or "backlink" in kind.split():
            link.decompose()
        elif _is_note_link(link):
            raise ValueError("EPUB note contains another note reference; resolve its structure before extraction")
    if copied.find(["img", "image", "table", "svg", "math"]):
        raise ValueError("EPUB note contains unsupported structured content; supply a faithful text note")
    body = _markup(copied, {})
    # Bare quantities (12 people), years, and decimals are content, not labels.
    return re.sub(r"^\s*(?:\[\d{1,3}\]|\(\d{1,3}\)|\d{1,3}[.):])(?:\s+|$)", "", body).strip()


def _book_footnotes(documents, load_document, footnotes):
    """Resolve all references before removing any body, across document files."""
    marks: dict[str, dict[int, str]] = {}
    bodies: dict[tuple[str, str], str] = {}
    targets: dict[int, Tag] = {}
    for document in documents:
        soup = load_document(document)
        own: dict[int, str] = {}
        for link in list(soup.find_all("a")):
            if not _is_note_link(link):
                continue
            href = str(link.get("href") or "")
            try:
                member = _local_member(document, href)
                fragment = unquote(urldefrag(href).fragment)
                if not fragment:
                    raise ValueError("note has no fragment")
                target_soup = load_document(member)
                if len(target_soup.find_all(id=fragment)) != 1:
                    raise ValueError("note target is missing or ambiguous")
                key = (member, fragment)
                target = _note_body_node(target_soup, fragment)
                if target is None:
                    raise ValueError("note body is missing")
                if key not in bodies:
                    bodies[key] = _note_text(target)
                if not bodies[key]:
                    raise ValueError("note body is empty")
            except (ValueError, KeyError) as error:
                raise ValueError(f"unresolved EPUB note in {document}: {error}") from error
            note = ir.make_footnote(len(footnotes) + 1, anchor_block="", text=bodies[key], origin="source")
            footnotes.append(note)
            own[id(link)] = note["id"]
            targets[id(target)] = target
        marks[document] = own
    # Keep all target nodes alive while other references still need them.
    for target in targets.values():
        if target.parent is not None:
            target.decompose()
    return marks


def _namespace_anchors(documents, soups, warn):
    """A fragment is file-scoped in EPUB and book-scoped after assembly."""
    identities: dict[tuple[str, str], Tag] = {}
    for document in documents:
        for tag in soups[document].find_all(id=True):
            key = (document, str(tag["id"]))
            if key in identities:
                raise ValueError(f"duplicate EPUB anchor in {document}: {tag['id']}")
            identities[key] = tag
    counts = Counter(fragment for _, fragment in identities)
    used = {fragment for _, fragment in identities}
    resolved: dict[tuple[str, str], str] = {}
    for (document, fragment), tag in identities.items():
        name = fragment
        if counts[fragment] > 1:
            base = "epub-" + ir.sha256_bytes(document.encode("utf-8"))[:12] + "-" + fragment
            name, suffix = base, 1
            while name in used:
                suffix += 1
                name = f"{base}-{suffix}"
            used.add(name)
        resolved[(document, fragment)] = name
        tag["id"] = name
    for document in documents:
        for link in soups[document].find_all("a"):
            if _is_note_link(link):
                continue
            href = str(link.get("href") or "")
            split = urlsplit(href)
            if split.scheme or split.netloc or not split.fragment:
                continue
            try:
                member = _local_member(document, href)
            except ValueError:
                member = ""
            name = resolved.get((member, unquote(split.fragment)))
            if name is None:
                warn("unresolved-internal-link", "an internal target is absent from the imported spine; display words were retained")
                link.attrs.pop("href", None)
            else:
                link["href"] = "#" + name


def _add_image(tag: Tag, add, archive: zipfile.ZipFile, doc_path: str,
               asset_dir: Path, seen: dict[str, str], page: int) -> None:
    href = tag.get("src") or tag.get("xlink:href") or tag.get("href")
    if not href:
        raise ValueError("EPUB image has no source asset")
    target = _local_member(doc_path, href)
    try:
        data = archive.read(target)
    except KeyError as error:
        raise ValueError(f"EPUB image asset is missing: {target}") from error

    digest = ir.sha256_bytes(data)
    if digest in seen:
        asset_name = seen[digest]
    else:
        suffix = Path(target).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"}:
            raise ValueError(f"unsupported EPUB image asset format: {suffix!r}")
        asset_name = f"e-{digest}{suffix}"
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


def _validate_footnote_tokens(book: dict[str, Any]) -> None:
    """Check actual edges; never delete literal examples or unresolved content."""
    live = {note["id"] for note in book["footnotes"]}
    for block in ir.iter_text_blocks(book):
        unknown = set(ir.footnote_refs(block.get("text") or "")) - live
        if unknown:
            raise ValueError(f"unresolved EPUB footnote token in {block['id']}: {sorted(unknown)}")

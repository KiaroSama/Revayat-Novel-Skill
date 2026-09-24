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

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

import bookir as ir

LOG = logging.getLogger(__name__)

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


def _inline_spans(node: Tag, bold: bool = False, italic: bool = False,
                  notes: dict[int, str] | None = None
                  ) -> list[dict[str, Any]]:
    """Flatten an element's inline content into span dicts."""
    spans: list[dict[str, Any]] = []
    for child in node.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            text = re.sub(r"\s+", " ", str(child))
            if text:
                spans.extend(_styled(text, bold, italic))
            continue
        if not isinstance(child, Tag) or child.name in SKIP_TAGS:
            continue
        if notes and id(child) in notes:
            span = _span("")
            span["footnote"] = notes[id(child)]
            spans.append(span)
            continue
        if child.name == "br":
            spans.append(_span(" ", bold=bold, italic=italic))
            continue
        if child.name in VERBATIM_TAGS:
            body = child.get_text()
            if "`" in body:
                raise ValueError("EPUB verbatim content contains an unrepresentable backtick")
            if body:
                # Verbatim is a span *kind*, not literal backticks in the text:
                # writing the markers here would only get them escaped again.
                spans.append(_span(body, verbatim=True))
            continue
        if child.name in BLOCK_TAGS and spans and not spans[-1]["text"].endswith((" ", "\n")):
            spans.append(_span(" "))
        spans.extend(_inline_spans(
            child,
            bold or child.name in BOLD_TAGS,
            italic or child.name in ITALIC_TAGS,
            notes,
        ))
    return spans


def _markup(node: Tag, footnote_marks: dict[int, str]) -> str:
    """Inline markup for a block element, with footnote tokens re-inserted."""
    text = ir.render_spans(_inline_spans(node, notes=footnote_marks))
    return text.strip()


def _link_target(href: str, warn) -> str | None:
    """What an EPUB href means once the book is one Word document, or ``None``.

    The spine is a single book, so a link into another spine document is an
    *internal* link the moment the documents are concatenated: only its fragment
    survives the move, and `#sec2` is the whole of what it meant. An absolute
    URL is carried as it stands. A link to a whole document with no fragment has
    nothing to point at once the file boundaries are gone, so it is dropped —
    said out loud, because a target that vanishes silently is the failure this
    reader keeps being fixed for.
    """
    href = (href or "").strip()
    if not href:
        return None
    if urlsplit(href).scheme:      # http, https, mailto, tel …
        return href
    fragment = urldefrag(href).fragment
    if fragment:
        return f"#{fragment}"
    warn("link-to-a-whole-document", f"{href!r} points at a spine document "
         f"rather than a place in one; there is no such boundary in the "
         f"finished book, so the words stay and the link does not")
    return None


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
            _walk(body, add, archive, doc_path, asset_dir, seen_assets,
                  marks.get(doc_path, {}), doc_index, warn)
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
        if role == "doc-backlink" or "backlink" in kind.split() or link.get_text(strip=True) in {"↩", "↵", "↑"}:
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


def _walk(node: Tag, add, archive: zipfile.ZipFile, doc_path: str,
          asset_dir: Path, seen: dict[str, str], marks: dict[int, str],
          page: int, warn=lambda *a: None) -> None:
    """Emit prose and images in one DOM-order traversal, without flattening a plate."""
    emitted: list[dict[str, Any]] = []

    def emit(kind, **fields):
        block = add(kind, page=page, **fields)
        emitted.append(block)
        return block

    def image(tag):
        def image_add(kind, **fields):
            block = add(kind, **fields)
            emitted.append(block)
            return block
        _add_image(tag, image_add, archive, doc_path, asset_dir, seen, page)

    def flow(container, kind="paragraph", fields=None, bold=False, italic=False, depth=0, inherited_link=None):
        if depth > 200:
            raise ValueError("EPUB document nesting exceeds the supported depth")
        first = len(emitted)
        buffer, anchors, links = [], [], {}
        fields = dict(fields or {})

        def append(spans, active=None):
            buffer.extend(spans)
            if active is not None:
                identity, href = active
                display = "".join(span["text"] for span in spans)
                if identity not in links:
                    links[identity] = {"text": "", "href": href}
                links[identity]["text"] += display

        def flush():
            if any(span["text"].strip() or span.get("footnote") or
                   (span.get("verbatim") and span["text"]) for span in buffer):
                text = ir.render_spans(buffer).strip()
                extra = dict(fields)
                carried = [{"text": item["text"].strip(), "href": item["href"]}
                           for item in links.values() if item["text"].strip()]
                if carried:
                    extra["links"] = carried
                if anchors:
                    extra["bookmarks"] = list(dict.fromkeys(anchors))
                emit(kind, text=text, **extra)
            buffer.clear()
            anchors.clear()
            links.clear()

        def visit(child, strong=bold, emphasis=italic, active=inherited_link):
            if isinstance(child, Comment):
                return
            if isinstance(child, NavigableString):
                append(_styled(re.sub(r"\s+", " ", str(child)), strong, emphasis), active)
                return
            if not isinstance(child, Tag) or child.name in SKIP_TAGS:
                return
            name = child.name
            if id(child) in marks:
                span = _span("", bold=strong, italic=emphasis)
                span["footnote"] = marks[id(child)]
                append([span])
                return
            if name in {"img", "image"}:
                flush()
                image(child)
                if child.get("id"):
                    emitted[-1].setdefault("bookmarks", []).append(child["id"])
                return
            if name == "hr":
                flush()
                emit("separator")
                return
            if name == "br":
                append([_span(" ", bold=strong, italic=emphasis)], active)
                return
            if name in VERBATIM_TAGS:
                if name == "pre":
                    flush()
                if child.get("id"):
                    anchors.append(child["id"])
                literal = child.get_text()
                if "`" in literal:
                    raise ValueError("EPUB verbatim content contains an unrepresentable backtick")
                append([_span(literal, bold=strong, italic=emphasis, verbatim=True)], active)
                if name == "pre":
                    flush()
                return
            if name in BLOCK_TAGS:
                flush()
                new_kind, new_fields = kind, dict(fields)
                if name in HEADINGS:
                    new_kind, new_fields = "heading", {"level": HEADINGS[name]}
                elif name == "li":
                    new_kind, new_fields = "listitem", {
                        "level": 1 + len(child.find_parents("li")), "ordered": _ordered(child)}
                elif name == "blockquote":
                    new_kind, new_fields = "blockquote", {}
                elif name == "figcaption":
                    new_kind, new_fields = "caption", {}
                elif kind not in {"listitem", "blockquote"}:
                    new_kind, new_fields = "paragraph", {}
                flow(child, new_kind, new_fields, strong, emphasis, depth + 1, active)
                return
            if child.get("id"):
                anchors.append(child["id"])
            if name == "a":
                target = _link_target(str(child.get("href") or ""), warn)
                active = (id(child), target) if target else None
            for item in child.children:
                visit(item, strong or name in BOLD_TAGS,
                      emphasis or name in ITALIC_TAGS, active)

        for child in container.children:
            visit(child)
        flush()
        if container.get("id") and len(emitted) > first:
            target = emitted[first].setdefault("bookmarks", [])
            if container["id"] not in target:
                target.insert(0, container["id"])

    flow(node)


def _ordered(item: Tag) -> bool:
    parent = item.find_parent(["ol", "ul"])
    return bool(parent and parent.name == "ol")


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

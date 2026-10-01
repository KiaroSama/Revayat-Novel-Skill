"""EPUB DOM traversal and exact inline content."""

from pathlib import Path
import re
from typing import Any
import zipfile
from urllib.parse import urldefrag, urlsplit

from bs4 import Comment, NavigableString, Tag

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

def literal_text(node: Tag) -> str:
    if node.find(["img", "image", "svg", "math", "table"]):
        raise ValueError("EPUB literal contains unsupported structured content; supply a faithful source")
    return node.get_text()


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
            body = literal_text(child)
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
        if child.name in BLOCK_TAGS and spans and not spans[-1]["text"].endswith((" ", "\n")):
            spans.append(_span(" "))
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


def walk(node: Tag, add, archive: zipfile.ZipFile, doc_path: str,
          asset_dir: Path, seen: dict[str, str], marks: dict[int, str],
          page: int, warn=lambda *a: None, *, add_image) -> None:
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
        add_image(tag, image_add, archive, doc_path, asset_dir, seen, page)

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
                literal = literal_text(child)
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



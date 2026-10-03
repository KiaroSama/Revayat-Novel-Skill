"""Selected web chapter prose normalized for the existing EPUB reader."""

from copy import deepcopy
import logging
from urllib.parse import unquote, urldefrag

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from read_epub import _is_note_link

LOG = logging.getLogger(__name__)
LITERALS = {"pre", "code", "kbd", "samp", "tt"}

CONTAINERS = {"div", "section", "article", "main", "aside", "figure"}
BLOCKS = {"p", "div", "section", "article", "main", "body", "blockquote", "aside",
          "li", "ul", "ol", "figure", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6"}
INLINE = {"b", "strong", "i", "em", "cite", "dfn", "var", "code", "kbd", "samp", "tt", "sup", "sub", "a"}


def chapter_html(raw, selector, title, resolve_asset):
    """Preserve narrative order, including bare text and interleaved images."""
    soup = BeautifulSoup(raw, "html.parser")
    try:
        matches = soup.select(selector)
    except Exception as error:
        raise ValueError("invalid chapter content selector") from error
    if len(matches) != 1:
        raise ValueError("chapter selector must match exactly one narrative container")
    root = deepcopy(matches[0])
    for junk in root.select("script,style,nav,form,button,template,[hidden],[aria-hidden=true]"):
        junk.decompose()
    if root.find(["table", "iframe", "object", "video", "audio", "svg", "canvas"]):
        raise ValueError("chapter contains unsupported structured or embedded content; inspect and supply a faithful saved chapter")
    if not root.get_text(strip=True):
        raise ValueError("chapter narrative is empty")
    for link in root.find_all("a"):
        if _is_note_link(link):
            href = link.get("href") or ""
            anchor = unquote(urldefrag(href).fragment)
            if not href.startswith("#") or len(root.find_all(id=anchor)) != 1:
                raise ValueError("chapter note target is missing, ambiguous or outside the selected content; prepare a complete saved chapter")
    output = BeautifulSoup('<html><head><meta charset="utf-8"></head><body></body></html>', "html.parser")
    output.html["xmlns"] = "http://www.w3.org/1999/xhtml"
    output.html["xmlns:epub"] = "http://www.idpf.org/2007/ops"

    # Normalize ruby once on the source tree, including retained list subtrees.
    # Literal containers are not narrative and keep their exact text instead.
    for annotation in list(root.find_all(["rp", "rt"])):
        if root.name in LITERALS or annotation.find_parent(list(LITERALS)):
            continue
        if annotation.name == "rp":
            annotation.decompose()
        else:
            annotation.replace_with(NavigableString(" (" + annotation.get_text() + ")"))

    # Resolve each original image once, including images in retained list trees.
    # Literal nodes keep their structure for the native reader to validate.
    for image in root.find_all("img"):
        source = image.get("src")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("chapter image has no usable src; resolve lazy-loaded images before import")
        image["src"] = resolve_asset(source)

    # One original node may span a line/image boundary. Its formatting may repeat,
    # but its id must appear only once. Distinct nodes with equal ids remain
    # distinct so the native duplicate-anchor validator still refuses them.
    emitted_ids = set()

    def attributes(node):
        attrs = {key: node[key] for key in ("href", "id", "role", "epub:type") if key in node.attrs}
        if "id" in attrs:
            if id(node) in emitted_ids:
                attrs.pop("id")
            else:
                emitted_ids.add(id(node))
        if "href" in attrs and str(attrs["href"]).lower().startswith(("javascript:", "data:", "file:")):
            attrs.pop("href")
        if node.name == "img":
            attrs.update(src=node["src"], alt=node.get("alt", ""))
        return attrs

    def retained(node):
        copied = deepcopy(node)
        for source, target in zip([node, *node.find_all(True)], [copied, *copied.find_all(True)]):
            target.attrs = attributes(source)
        return copied

    def wrap(copied, wrappers):
        for source in reversed(wrappers):
            name = source.name if source.name in INLINE else "span"
            wrapped = output.new_tag(name, attrs=attributes(source))
            wrapped.append(copied)
            copied = wrapped
        return copied

    def standalone(node, wrappers):
        return wrap(retained(node), wrappers)

    def pieces(node, kind="p"):
        result, buffer = [], []
        wrappers_written = {}
        first = True

        def append(item, wrappers):
            parent = None
            for source in wrappers:
                identity = id(source)
                if identity not in wrappers_written:
                    name = source.name if source.name in INLINE else "span"
                    wrapped = output.new_tag(name, attrs=attributes(source))
                    if parent is None:
                        buffer.append(wrapped)
                    else:
                        parent.append(wrapped)
                    wrappers_written[identity] = wrapped
                parent = wrappers_written[identity]
            if parent is None:
                buffer.append(item)
            else:
                parent.append(item)

        def flush():
            nonlocal first
            if not any(item.get_text(strip=True) if isinstance(item, Tag) else str(item).strip() for item in buffer):
                # Empty destinations still carry meaning for a later link.
                # The native reader binds them to the next concrete block.
                result.extend(item for item in buffer if isinstance(item, Tag)
                              and (item.get("id") or item.find(id=True)))
                buffer.clear()
                wrappers_written.clear()
                return
            block = output.new_tag(kind)
            if first and node.get("id") and node.name not in CONTAINERS:
                block.attrs.update(attributes(node))
            first = False
            for item in buffer:
                block.append(item)
            buffer.clear()
            wrappers_written.clear()
            if kind == "li":
                owner = node.find_parent(["ol", "ul"])
                parent = output.new_tag("ol" if owner and owner.name == "ol" else "ul")
                parent.append(block)
                result.append(parent)
            else:
                result.append(block)

        def visit(child, wrappers=()):
            if isinstance(child, Comment):
                return
            if isinstance(child, NavigableString):
                append(output.new_string(str(child)), wrappers)
                return
            if not isinstance(child, Tag):
                return
            if child.name == "br":
                flush()
            elif child.name == "img":
                flush()
                result.append(standalone(child, wrappers))
            elif child.name == "hr":
                flush()
                result.append(output.new_tag("hr"))
            elif child.name in LITERALS or (child.name == "a" and _is_note_link(child)):
                # These are single semantic units, not one unit per styled leaf.
                # In particular a note reference must not multiply its markers.
                if child.name == "pre":
                    flush()
                    result.append(standalone(child, wrappers))
                else:
                    append(retained(child), wrappers)
            elif child.name in {"ol", "ul"}:
                flush()
                result.append(standalone(child, wrappers))
            elif child.name in BLOCKS:
                flush()
                block_kind = child.name if child.name in {"blockquote", "li", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6"} else "p"
                if block_kind == "p" and kind in {"blockquote", "li"}:
                    block_kind = kind
                result.extend(wrap(element, wrappers) for element in pieces(child, block_kind))
            elif child.name == "rp":
                return
            elif child.name == "rt":
                visit(NavigableString(" (" + child.get_text() + ")"), wrappers)
            else:
                nested = wrappers
                if child.name in INLINE or child.get("id"):
                    nested += (child,)
                if not child.contents and child.get("id"):
                    append(output.new_string(""), nested)
                for item in child.children:
                    visit(item, nested)

        for child in node.children:
            visit(child)
        flush()
        if node.name in CONTAINERS and node.get("id"):
            container = output.new_tag(node.name, attrs=attributes(node))
            for element in result:
                container.append(element)
            return [container]
        return result

    rendered = ([retained(root)] if root.name in LITERALS | {"ol", "ul"}
                else pieces(root, root.name if root.name in {"blockquote", "h1", "h2", "h3", "h4", "h5", "h6"} else "p"))
    if root.name not in {"h1", "h2", "h3", "h4", "h5", "h6"} and not root.find(["h1", "h2", "h3", "h4", "h5", "h6"]):
        heading = output.new_tag("h1")
        heading.string = title
        output.body.append(heading)
    for element in rendered:
        output.body.append(element)
    LOG.debug("Normalized one chapter without duplicating source node identities")
    return str(output).encode("utf-8")

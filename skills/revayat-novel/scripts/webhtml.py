"""Selected web chapter prose normalized for the existing EPUB reader."""

from copy import deepcopy
from urllib.parse import unquote, urldefrag

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from read_epub import _is_note_link

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

    def pieces(node, kind="p"):
        result, buffer = [], []
        first = True

        def flush():
            nonlocal first
            if not any(item.get_text(strip=True) if isinstance(item, Tag) else str(item).strip() for item in buffer):
                buffer.clear()
                return
            block = output.new_tag(kind)
            if first and node.get("id") and node.name not in CONTAINERS:
                block["id"] = node["id"]
            first = False
            for item in buffer:
                block.append(item)
            buffer.clear()
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
                item = output.new_string(str(child))
                for name, attrs in reversed(wrappers):
                    wrapper = output.new_tag(name, attrs=attrs)
                    wrapper.append(item)
                    item = wrapper
                buffer.append(item)
                return
            if not isinstance(child, Tag):
                return
            if child.name == "br":
                flush()
            elif child.name == "img":
                flush()
                source = child.get("src")
                if not isinstance(source, str) or not source.strip():
                    raise ValueError("chapter image has no usable src; resolve lazy-loaded images before import")
                result.append(output.new_tag("img", attrs={"src": resolve_asset(source), "alt": child.get("alt", "")}))
            elif child.name == "hr":
                flush()
                result.append(output.new_tag("hr"))
            elif child.name in BLOCKS:
                flush()
                block_kind = child.name if child.name in {"blockquote", "li", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6"} else "p"
                if block_kind == "p" and kind in {"blockquote", "li"}:
                    block_kind = kind
                result.extend(pieces(child, block_kind))
            elif child.name == "rp":
                return
            elif child.name == "rt":
                visit(NavigableString(" (" + child.get_text() + ")"), wrappers)
            else:
                nested = wrappers
                if child.name in INLINE:
                    attrs = {key: child[key] for key in ("href", "id", "role", "epub:type") if key in child.attrs}
                    if "href" in attrs and str(attrs["href"]).lower().startswith(("javascript:", "data:", "file:")):
                        attrs.pop("href")
                    nested += ((child.name, attrs),)
                for item in child.children:
                    visit(item, nested)

        for child in node.children:
            visit(child)
        flush()
        if node.name in CONTAINERS and node.get("id"):
            container = output.new_tag(node.name, attrs={"id": node["id"]})
            for element in result:
                container.append(element)
            return [container]
        return result

    rendered = pieces(root, root.name if root.name in {"h1", "h2", "h3", "h4", "h5", "h6"} else "p")
    if root.name not in {"h1", "h2", "h3", "h4", "h5", "h6"} and not root.find(["h1", "h2", "h3", "h4", "h5", "h6"]):
        heading = output.new_tag("h1")
        heading.string = title
        output.body.append(heading)
    for element in rendered:
        output.body.append(element)
    return str(output).encode("utf-8")

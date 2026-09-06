"""Word internals python-docx does not expose.

python-docx (verified against 1.2.0) has no API for footnotes, bookmarks, TOC
fields or right-to-left properties, so those are written as raw WordprocessingML
here. The alternative — faking footnotes with superscript numbers and a list at
the end, or faking RTL by reversing strings — produces a document that looks
approximately right and behaves wrong the moment anyone edits it.

Everything in this module is a real Word structure: ``w:bidi`` / ``w:rtl`` for
direction, a genuine ``word/footnotes.xml`` part, ``w:bookmarkStart`` anchors,
and a ``TOC`` field whose cached result is a list of internal hyperlinks.
"""

from __future__ import annotations

from typing import Any, Iterable

from docx.opc.constants import CONTENT_TYPE as CT, RELATIONSHIP_TYPE as RT
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from docx.oxml import parse_xml
from docx.oxml.ns import nsmap, qn

W = nsmap["w"]
_WNS = f'xmlns:w="{W}"'
_RNS = f'xmlns:r="{nsmap["r"]}"'

FOOTNOTE_TEXT_STYLE = "FootnoteText"
FOOTNOTE_REF_STYLE = "FootnoteReference"


def el(tag: str, **attrs: Any):
    """Build a ``w:``-namespaced element with ``w:``-namespaced attributes."""
    rendered = "".join(f' w:{name.replace("_", "-")}="{_escape(value)}"'
                       for name, value in attrs.items())
    return parse_xml(f"<w:{tag} {_WNS}{rendered}/>")


def _escape(value: Any) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def xml_text(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# --------------------------------------------------------------------------- #
# Direction
# --------------------------------------------------------------------------- #

def set_paragraph_rtl(paragraph, rtl: bool = True) -> None:
    """Make a paragraph's base direction right-to-left.

    This is what puts the sentence-final full stop on the left and orders
    mixed Persian/Latin text correctly — Unicode's bidi algorithm needs a base
    direction, and ``w:bidi`` is how Word states it.
    """
    properties = paragraph._p.get_or_add_pPr()
    for existing in properties.findall(qn("w:bidi")):
        properties.remove(existing)
    if rtl:
        properties.append(el("bidi"))


def set_table_rtl(table, rtl: bool = True) -> None:
    """Mirror a table's column order for right-to-left reading.

    A table is not made right-to-left by its paragraphs: those set the direction
    of the text *inside* each cell, while the columns keep marching left to
    right. ``w:bidiVisual`` is the separate switch that puts the first column on
    the right, where a Persian reader looks for it.
    """
    properties = table._tbl.tblPr
    for existing in properties.findall(qn("w:bidiVisual")):
        properties.remove(existing)
    if rtl:
        properties.append(el("bidiVisual"))


def set_run_direction(run, *, rtl: bool, bidi_lang: str = "fa-IR",
                      latin_lang: str = "en-US") -> None:
    """Mark a run as Persian (``w:rtl``) or as isolated Latin.

    A Latin name inside a Persian sentence stays left-to-right *within* the
    right-to-left paragraph — which is exactly what a reader expects, and what
    reversing the string would destroy.
    """
    properties = run._r.get_or_add_rPr()
    for tag in ("w:rtl", "w:lang"):
        for existing in properties.findall(qn(tag)):
            properties.remove(existing)
    if rtl:
        properties.append(el("rtl"))
        properties.append(el("lang", bidi=bidi_lang))
    else:
        properties.append(el("lang", val=latin_lang))


def set_section_rtl(section, rtl: bool = True) -> None:
    properties = section._sectPr
    for existing in properties.findall(qn("w:bidi")):
        properties.remove(existing)
    if rtl:
        properties.insert(0, el("bidi"))


def set_document_defaults(document, *, persian_font: str, latin_font: str,
                          size_pt: float) -> None:
    """Default fonts and direction for every run that does not override them."""
    styles = document.styles.element
    defaults = styles.find(qn("w:docDefaults"))
    if defaults is None:
        defaults = parse_xml(f"<w:docDefaults {_WNS}/>")
        styles.insert(0, defaults)

    run_defaults = defaults.find(qn("w:rPrDefault"))
    if run_defaults is None:
        run_defaults = parse_xml(f"<w:rPrDefault {_WNS}/>")
        defaults.insert(0, run_defaults)
    run_properties = run_defaults.find(qn("w:rPr"))
    if run_properties is None:
        run_properties = parse_xml(f"<w:rPr {_WNS}/>")
        run_defaults.append(run_properties)

    for tag in ("w:rFonts", "w:sz", "w:szCs", "w:lang"):
        for existing in run_properties.findall(qn(tag)):
            run_properties.remove(existing)

    half_points = str(int(round(size_pt * 2)))
    run_properties.append(el("rFonts", ascii=latin_font, hAnsi=latin_font,
                             cs=persian_font))
    run_properties.append(el("sz", val=half_points))
    run_properties.append(el("szCs", val=half_points))
    run_properties.append(el("lang", val="en-US", bidi="fa-IR"))


def style_rtl(document, style_name: str, *, persian_font: str | None = None,
              size_pt: float | None = None) -> None:
    """Give a named style an RTL paragraph direction and a complex-script font."""
    try:
        style = document.styles[style_name]
    except KeyError:
        return
    element = style.element

    properties = element.find(qn("w:pPr"))
    if properties is None:
        properties = parse_xml(f"<w:pPr {_WNS}/>")
        element.append(properties)
    if properties.find(qn("w:bidi")) is None:
        properties.append(el("bidi"))

    run_properties = element.find(qn("w:rPr"))
    if run_properties is None:
        run_properties = parse_xml(f"<w:rPr {_WNS}/>")
        element.append(run_properties)
    if run_properties.find(qn("w:rtl")) is None:
        run_properties.append(el("rtl"))
    if persian_font:
        for existing in run_properties.findall(qn("w:rFonts")):
            existing.set(qn("w:cs"), persian_font)
        if run_properties.find(qn("w:rFonts")) is None:
            run_properties.append(el("rFonts", cs=persian_font))
    if size_pt:
        for tag in ("w:sz", "w:szCs"):
            for existing in run_properties.findall(qn(tag)):
                run_properties.remove(existing)
        half = str(int(round(size_pt * 2)))
        run_properties.append(el("sz", val=half))
        run_properties.append(el("szCs", val=half))


# --------------------------------------------------------------------------- #
# Bookmarks, hyperlinks, TOC
# --------------------------------------------------------------------------- #

class Bookmarks:
    """Allocates the document-unique ids Word requires for bookmark pairs."""

    def __init__(self) -> None:
        self._next = 1
        self.names: list[tuple[str, str]] = []   # (name, display text)

    def wrap(self, paragraph, name: str, display: str = "") -> None:
        identifier = self._next
        self._next += 1
        paragraph._p.insert(
            _content_start(paragraph._p),
            el("bookmarkStart", id=str(identifier), name=name),
        )
        paragraph._p.append(el("bookmarkEnd", id=str(identifier)))
        self.names.append((name, display))


def _content_start(paragraph_element) -> int:
    """Index just after ``w:pPr``, where a bookmark may legally open."""
    properties = paragraph_element.find(qn("w:pPr"))
    return 1 if properties is not None else 0


def add_internal_link(paragraph, anchor: str, text: str, *, rtl: bool = True,
                      style: str = "Hyperlink") -> None:
    """A clickable in-document link — ``w:hyperlink`` with ``w:anchor``."""
    direction = "<w:rtl/><w:lang w:bidi=\"fa-IR\"/>" if rtl else ""
    paragraph._p.append(parse_xml(
        f'<w:hyperlink {_WNS} w:anchor="{_escape(anchor)}" w:history="1">'
        f'<w:r><w:rPr><w:rStyle w:val="{style}"/>{direction}</w:rPr>'
        f'<w:t xml:space="preserve">{xml_text(text)}</w:t></w:r>'
        f"</w:hyperlink>"
    ))


def hyperlink_from(paragraph, first: int, href: str, *,
                   style: str = "Hyperlink") -> None:
    """Move the runs added after position ``first`` into a real ``w:hyperlink``.

    python-docx 1.2.0 can read a hyperlink but not create one. Writing the runs
    a second time inside the element would mean repeating every direction, font
    and emphasis decision the caller has already made — and a Persian link is
    exactly the case where that matters, because the runs inside ``w:hyperlink``
    need the same ``w:rtl`` treatment as the prose around them. So the caller
    writes them normally and this moves them.

    ``href`` beginning with ``#`` is an in-document anchor and needs no
    relationship; anything else is external and gets one. The target is written
    through unchanged.
    """
    moving = list(paragraph._p)[first:]
    if not moving:
        return      # an empty w:hyperlink is a link with nothing to click

    if href.startswith("#"):
        link = el("hyperlink", anchor=href[1:], history="1")
    else:
        rel_id = paragraph.part.relate_to(href, RT.HYPERLINK, is_external=True)
        link = parse_xml(f'<w:hyperlink {_WNS} {_RNS} r:id="{rel_id}" '
                         f'w:history="1"/>')

    for node in moving:
        paragraph._p.remove(node)
        if style and node.tag == qn("w:r"):
            # w:rStyle is the first child of w:rPr; the schema fixes that order,
            # and Word rejects the part outright when it is wrong.
            node.get_or_add_rPr().insert(0, el("rStyle", val=style))
        link.append(node)
    paragraph._p.append(link)


def add_toc_field(paragraph, entries: Iterable[tuple[str, str, int]], *,
                  depth: int = 3, rtl: bool = True) -> None:
    """A real Word TOC field whose *cached result* is already clickable.

    Word rebuilds the field (adding page numbers) when fields update; a viewer
    that never updates fields still shows the cached hyperlink list rather than
    an empty page. Both halves come from the same construct, so there is no
    second copy of the TOC to drift.
    """
    body = paragraph._p
    body.append(parse_xml(
        f'<w:r {_WNS}><w:fldChar w:fldCharType="begin"/></w:r>'
    ))
    body.append(parse_xml(
        f'<w:r {_WNS}><w:instrText xml:space="preserve"> '
        f'TOC \\o "1-{depth}" \\h \\z \\u </w:instrText></w:r>'
    ))
    body.append(parse_xml(
        f'<w:r {_WNS}><w:fldChar w:fldCharType="separate"/></w:r>'
    ))
    for anchor, text, level in entries:
        add_internal_link(paragraph, anchor, ("    " * (level - 1)) + text, rtl=rtl)
        body.append(parse_xml(f'<w:r {_WNS}><w:br/></w:r>'))
    body.append(parse_xml(
        f'<w:r {_WNS}><w:fldChar w:fldCharType="end"/></w:r>'
    ))


def request_field_update(document) -> None:
    """Ask Word to refresh fields on open, so the TOC gains page numbers."""
    settings = document.settings.element
    for existing in settings.findall(qn("w:updateFields")):
        settings.remove(existing)
    settings.append(el("updateFields", val="true"))


# --------------------------------------------------------------------------- #
# Footnotes
# --------------------------------------------------------------------------- #

_SEPARATOR_FOOTNOTES = (
    '<w:footnote w:type="separator" w:id="-1"><w:p><w:pPr>'
    '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
    "<w:r><w:separator/></w:r></w:p></w:footnote>"
    '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:pPr>'
    '<w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
    "<w:r><w:continuationSeparator/></w:r></w:p></w:footnote>"
)


class Footnotes:
    """Collects real Word footnotes and writes ``word/footnotes.xml``.

    Word reserves ids -1 and 0 for the separator marks, so content starts at 1.
    """

    def __init__(self, document) -> None:
        self.document = document
        self._next = 1
        self._bodies: list[str] = []

    def __len__(self) -> int:
        return len(self._bodies)

    def add(self, paragraph, spans: list[dict[str, Any]], *,
            persian_font: str, rtl: bool = True) -> int:
        """Append a reference mark to ``paragraph`` and store the note body."""
        identifier = self._next
        self._next += 1

        paragraph._p.append(parse_xml(
            f'<w:r {_WNS}><w:rPr><w:rStyle w:val="{FOOTNOTE_REF_STYLE}"/></w:rPr>'
            f'<w:footnoteReference w:id="{identifier}"/></w:r>'
        ))

        runs = [
            f'<w:r><w:rPr><w:rStyle w:val="{FOOTNOTE_REF_STYLE}"/></w:rPr>'
            f"<w:footnoteRef/></w:r>",
            '<w:r><w:rPr><w:rtl/></w:rPr><w:t xml:space="preserve"> </w:t></w:r>',
        ]
        for span in spans:
            runs.append(_span_xml(span, persian_font=persian_font, rtl=rtl))

        direction = "<w:bidi/>" if rtl else ""
        self._bodies.append(
            f'<w:footnote w:id="{identifier}"><w:p><w:pPr>'
            f'<w:pStyle w:val="{FOOTNOTE_TEXT_STYLE}"/>{direction}</w:pPr>'
            f'{"".join(runs)}</w:p></w:footnote>'
        )
        return identifier

    def finalise(self) -> None:
        """Attach the footnotes part. No-op when the book has no footnotes."""
        if not self._bodies:
            return
        xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f"<w:footnotes {_WNS}>{_SEPARATOR_FOOTNOTES}{''.join(self._bodies)}"
            "</w:footnotes>"
        )
        package = self.document.part.package
        part = Part(PackURI("/word/footnotes.xml"), CT.WML_FOOTNOTES,
                    xml.encode("utf-8"), package)
        self.document.part.relate_to(part, RT.FOOTNOTES)
        self._declare_separators()

    def _declare_separators(self) -> None:
        settings = self.document.settings.element
        for existing in settings.findall(qn("w:footnotePr")):
            settings.remove(existing)
        settings.append(parse_xml(
            f'<w:footnotePr {_WNS}><w:footnote w:id="-1"/>'
            f'<w:footnote w:id="0"/></w:footnotePr>'
        ))


def _span_xml(span: dict[str, Any], *, persian_font: str, rtl: bool) -> str:
    """One ``w:r`` for a parsed markup span, with direction chosen per run."""
    properties: list[str] = []
    if span.get("bold"):
        properties.append("<w:b/><w:bCs/>")
    if span.get("italic"):
        properties.append("<w:i/><w:iCs/>")
    if span.get("verbatim"):
        properties.append('<w:lang w:val="en-US"/>')
    elif rtl:
        properties.append(f'<w:rFonts w:cs="{_escape(persian_font)}"/><w:rtl/>'
                          f'<w:lang w:bidi="fa-IR"/>')
    prefix = f"<w:rPr>{''.join(properties)}</w:rPr>" if properties else ""
    return f'<w:r>{prefix}<w:t xml:space="preserve">{xml_text(span["text"])}</w:t></w:r>'


def ensure_footnote_styles(document, *, persian_font: str, size_pt: float) -> None:
    """Define the footnote styles — Word's default template omits both."""
    styles = document.styles.element
    existing = {
        node.get(qn("w:styleId")) for node in styles.findall(qn("w:style"))
    }
    half = str(int(round(size_pt * 2)))

    if FOOTNOTE_TEXT_STYLE not in existing:
        styles.append(parse_xml(
            f'<w:style {_WNS} w:type="paragraph" w:styleId="{FOOTNOTE_TEXT_STYLE}">'
            '<w:name w:val="footnote text"/><w:basedOn w:val="Normal"/>'
            '<w:uiPriority w:val="99"/><w:semiHidden/><w:unhideWhenUsed/>'
            '<w:pPr><w:bidi/><w:spacing w:after="0" w:line="240" '
            'w:lineRule="auto"/></w:pPr>'
            f'<w:rPr><w:rFonts w:cs="{_escape(persian_font)}"/>'
            f'<w:sz w:val="{half}"/><w:szCs w:val="{half}"/><w:rtl/></w:rPr>'
            "</w:style>"
        ))

    if FOOTNOTE_REF_STYLE not in existing:
        styles.append(parse_xml(
            f'<w:style {_WNS} w:type="character" w:styleId="{FOOTNOTE_REF_STYLE}">'
            '<w:name w:val="footnote reference"/><w:uiPriority w:val="99"/>'
            '<w:semiHidden/><w:unhideWhenUsed/>'
            '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'
            "</w:style>"
        ))

    if "Hyperlink" not in existing:
        styles.append(parse_xml(
            f'<w:style {_WNS} w:type="character" w:styleId="Hyperlink">'
            '<w:name w:val="Hyperlink"/><w:uiPriority w:val="99"/>'
            '<w:unhideWhenUsed/><w:rPr><w:color w:val="0563C1"/>'
            '<w:u w:val="single"/></w:rPr></w:style>'
        ))


# --------------------------------------------------------------------------- #
# Misc paragraph properties
# --------------------------------------------------------------------------- #

def page_break_before(paragraph) -> None:
    properties = paragraph._p.get_or_add_pPr()
    if properties.find(qn("w:pageBreakBefore")) is None:
        properties.append(el("pageBreakBefore"))


def keep_with_next(paragraph) -> None:
    properties = paragraph._p.get_or_add_pPr()
    if properties.find(qn("w:keepNext")) is None:
        properties.append(el("keepNext"))

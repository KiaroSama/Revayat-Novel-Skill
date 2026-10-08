"""Stage 6 — build the Persian Word document from the translated Book IR.

Deliberately no Markdown and no Pandoc in this path. The IR already holds
everything a Word file needs — heading levels, emphasis spans, image bytes with
their physical size, footnote bodies — so routing it through Markdown would only
throw geometry away and then try to guess it back.

What the reader gets: real ``Heading N`` styles with bookmarks, a TOC field
whose entries are clickable, Word-native footnotes at the foot of the page,
pictures at their original size and aspect, the source's own section breaks and
page geometry, right-to-left paragraphs with Latin names left-to-right inside
them, and selectable, editable Persian text.

The honest limit: Word reflows. A Persian paragraph is rarely the same length as
its English original, so page-for-page identity with the source PDF is not
achievable in an editable document, and this builder does not pretend to it.
Everything else on that list is exact.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION_START
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

import bookir as ir
import runstate
import layout
import ooxml
from docxbody import BodyWriter

#: EPUB and other reflowable sources give no physical size; assume screen DPI.
ASSUMED_DPI = 96.0

_LATIN_SEGMENT = re.compile(r"[A-Za-z](?:[A-Za-z0-9 .,'’&/@:_-]*[A-Za-z0-9])?")

_STYLE_BY_TYPE = {
    "paragraph": "Normal",
    "blockquote": "Quote",
    "caption": "Caption",
    "verse": "Normal",
}


def _cut_links(text: str, pending: dict[str, str]) -> list[tuple[str, str | None]]:
    """Split ``text`` into ``(fragment, href_or_None)`` at each surviving phrase.

    A phrase is consumed the first time it is found, so a link is placed once
    and ``pending`` is left holding exactly the links that could not be placed.
    """
    if not pending:
        return [(text, None)]

    found = sorted((at, at + len(display), display)
                   for display in pending
                   if (at := text.find(display)) >= 0)

    pieces: list[tuple[str, str | None]] = []
    cursor = 0
    for start, end, display in found:
        if start < cursor:      # two phrases overlap; the earlier one wins
            continue
        if start > cursor:
            pieces.append((text[cursor:start], None))
        pieces.append((text[start:end], pending.pop(display)))
        cursor = end
    if cursor < len(text):
        pieces.append((text[cursor:], None))
    return pieces


def split_by_script(text: str) -> list[tuple[str, bool]]:
    """Split prose into ``(chunk, is_latin)`` segments.

    Persian runs are marked ``w:rtl``; Latin ones are not. Word then applies the
    Unicode bidi algorithm with the correct per-run character direction, which
    is why "الیزابت بنت (Elizabeth Bennet) رفت" comes out in the right order —
    and why nothing anywhere in this project reverses a string.

    Note that a run boundary is *not* a bidi boundary: brackets and other
    neutral characters around a Latin run resolve against the paragraph
    direction no matter which ``w:r`` holds them. Moving them between runs
    changes nothing on the page (measured in Word), so this deliberately does
    not try. Controlling that grouping would need Unicode isolates in the text
    itself, which is not worth putting invisible control characters into a
    manuscript people will edit.
    """
    segments: list[tuple[str, bool]] = []
    cursor = 0
    for match in _LATIN_SEGMENT.finditer(text):
        if match.start() > cursor:
            segments.append((text[cursor:match.start()], False))
        segments.append((match.group(0), True))
        cursor = match.end()
    if cursor < len(text):
        segments.append((text[cursor:], False))
    return [(chunk, latin) for chunk, latin in segments if chunk]


class Builder(BodyWriter):
    def __init__(self, book: dict[str, Any], assets: Path, options: argparse.Namespace):
        self.book = book
        self.assets = assets
        self.options = options
        self.document = (
            Document(options.template) if options.template else Document()
        )
        self.footnotes = ooxml.Footnotes(self.document)
        self.bookmarks = ooxml.Bookmarks()
        self.notes_by_id = {n["id"]: n for n in book.get("footnotes", [])}
        self.used_notes: set[str] = set()
        import bookstructure
        problems = bookstructure.validate(book)
        if problems:
            raise ValueError('invalid book structure: ' + '; '.join(problems))
        self.warnings: list[str] = []
        self.toc_entries: list[tuple[str, str, int]] = []
        self.sections = book.get("sections") or []
        # Sections after the first, keyed by the block each one opens at. The
        # first needs no break: it is the document python-docx starts with.
        self.section_at = {record["start_block"]: record
                           for record in self.sections[1:]
                           if record.get("start_block")}
        # Every in-document anchor the book can actually land on. A link to one
        # that was not carried is a dead link, and the package gate refuses it.
        self.anchors = {name for block in book.get("blocks", [])
                        for name in (block.get("bookmarks") or ())}

    # -- document furniture ------------------------------------------------- #

    def setup(self) -> None:
        page = self.book.get("page", ir.default_page_setup())
        section = self.document.sections[0]
        section.page_width = Pt(page["width_pt"])
        section.page_height = Pt(page["height_pt"])
        section.top_margin = Pt(page["margin_top_pt"])
        section.bottom_margin = Pt(page["margin_bottom_pt"])
        section.left_margin = Pt(page["margin_inner_pt"])
        section.right_margin = Pt(page["margin_outer_pt"])
        # The size and margins still come from `book["page"]`, which is the
        # first section's geometry and the knob everything downstream reads.
        # Orientation has nowhere else to live, so it comes from the record.
        if self.sections:
            _set_orientation(section, self.sections[0])
        ooxml.set_section_rtl(section, self.options.rtl)

        ooxml.set_document_defaults(
            self.document,
            persian_font=self.options.font,
            latin_font=self.options.latin_font,
            size_pt=self.options.size,
        )
        ooxml.ensure_footnote_styles(
            self.document, persian_font=self.options.font,
            size_pt=max(7.5, self.options.size - 2),
        )
        if self.options.rtl:
            # `Title` was missing, and it is the one style on the title page.
            # `style_rtl` returns silently for a style a custom --template does
            # not define, so naming one that may be absent is safe.
            for name in ("Normal", "Title", "Subtitle", "Quote", "Caption", "List Bullet",
                         "List Number", *(f"Heading {n}" for n in range(1, 7))):
                ooxml.style_rtl(self.document, name, persian_font=self.options.font)

        # Book layout goes into the styles, not onto each paragraph, so the
        # whole document can be restyled from one place afterwards.
        profile = layout.Profile.from_options(self.options)
        # The generated page number exists because the source had no foot of its
        # own. A section that brought one keeps it — writing both would put two
        # things where the author put one, and the author's may well be a page
        # number already.
        first = self.sections[0] if self.sections else {}
        profile.page_numbers = profile.page_numbers and not first.get("footers")
        self.layout = layout.apply(self.document, section, profile,
                                   rtl=self.options.rtl)
        self.running_heads(section, first)
        ooxml.request_field_update(self.document)

    @property
    def text_width_pt(self) -> float:
        page = self.book.get("page", ir.default_page_setup())
        return max(
            72.0,
            page["width_pt"] - page["margin_inner_pt"] - page["margin_outer_pt"],
        )

    # -- writing ------------------------------------------------------------ #

    def paragraph(self, style: str = "Normal", *, align=None, container=None):
        owner = self.document if container is None else container
        paragraph = owner.add_paragraph(style=style)
        ooxml.set_paragraph_rtl(paragraph, self.options.rtl)
        if align is not None:
            paragraph.alignment = align
        return paragraph

    def write_markup(self, paragraph, markup: str,
                     links: list[dict[str, str]] | None = None) -> None:
        """Emit one marked-up string as correctly-directioned Word runs."""
        pending = self._placeable_links(markup, links)
        for span in ir.parse_markup(markup):
            note_id = span.get("footnote")
            if note_id:
                self._write_footnote(paragraph, note_id)
                continue
            if span["verbatim"]:
                self._run(paragraph, span["text"], span, latin=True)
                continue
            for fragment, href in _cut_links(span["text"], pending):
                first = len(paragraph._p)
                for chunk, is_latin in split_by_script(fragment):
                    self._run(paragraph, chunk, span, latin=is_latin)
                if href:
                    ooxml.hyperlink_from(paragraph, first, href)

        # Whatever is left was findable in the block's prose but not inside any
        # one span of it, so the phrase straddles an emphasis run or a footnote
        # marker and there is no single stretch of text to put the link on.
        for display, href in pending.items():
            self.warnings.append(
                f"link {display!r} -> {href} left as plain text: the phrase is "
                f"split across emphasis or a footnote marker"
            )

    def _placeable_links(self, markup: str,
                         links: list[dict[str, str]] | None) -> dict[str, str]:
        """The links that can be put back, and a warning naming each that cannot.

        A translator is never shown a URL, so a link can only be re-attached
        where its display phrase survived translation *word for word* — a name,
        a title, a number, an address written out as text. Ordinary prose does
        not survive that way, and is not supposed to: the words a link sat on
        stop existing the moment the sentence becomes Persian. So the hit rate
        here is low by design, and every miss is named rather than counted.
        """
        if not links:
            return {}
        prose = ir.plain_text(markup)
        shared = Counter((link.get("text") or "") for link in links)
        placeable: dict[str, str] = {}

        for link in links:
            display = (link.get("text") or "")
            href = (link.get("href") or "").strip()
            occurrences = prose.count(display) if display else 0
            if not display.strip() or not href:
                reason = "the link has no display text or no target"
            elif shared[display] > 1:
                reason = "two links carry these same words and nothing tells them apart"
            elif occurrences == 0:
                reason = "the translation does not contain the phrase"
            elif occurrences > 1:
                reason = "the phrase appears more than once in the translation"
            elif href.startswith("#") and href[1:] not in self.anchors:
                reason = "the anchor it points at was not carried into the book"
            else:
                placeable[display] = href
                continue
            self.warnings.append(
                f"link {display!r} -> {href} left as plain text: {reason}"
            )
        return placeable

    def _place_bookmarks(self, paragraph, block: dict[str, Any]) -> None:
        """Re-open the source's own anchors, so a preserved link lands somewhere."""
        for name in block.get("bookmarks") or ():
            self.bookmarks.wrap(paragraph, name)

    def _run(self, paragraph, text: str, span: dict[str, Any], *, latin: bool):
        if not text:
            return
        run = paragraph.add_run(text)
        run.bold = bool(span.get("bold"))
        run.italic = bool(span.get("italic"))
        run.font.cs_bold = bool(span.get('bold'))
        run.font.cs_italic = bool(span.get('italic'))
        if latin:
            run.font.name = self.options.latin_font
        ooxml.set_run_direction(run, rtl=self.options.rtl and not latin)
        return run

    def _write_footnote(self, paragraph, note_id: str) -> None:
        note = self.notes_by_id.get(note_id)
        if note is None:
            self.warnings.append(f"footnote {note_id} referenced but not defined")
            return
        body = (note.get("target") or note.get("text") or "")
        if not body.strip():
            self.warnings.append(f"footnote {note_id} has no text")
            return
        spans: list[dict[str, Any]] = []
        for span in ir.parse_markup(body):
            if span.get("footnote"):
                continue  # nested footnotes are not a Word concept
            if span["verbatim"]:
                spans.append({**span, "verbatim": True})
                continue
            for chunk, is_latin in split_by_script(span["text"]):
                spans.append({**span, "text": chunk, "verbatim": is_latin})
        self.footnotes.add(paragraph, spans, persian_font=self.options.font,
                           rtl=self.options.rtl)
        self.used_notes.add(note_id)

    # -- blocks ------------------------------------------------------------- #

    def front_matter(self) -> None:
        meta = self.book.get("meta", {})
        title = (meta.get("title_target") or meta.get("title") or "")
        author = (meta.get("author_target") or meta.get("author") or "")
        if title.strip():
            paragraph = self.paragraph("Title", align=WD_ALIGN_PARAGRAPH.CENTER)
            self.write_markup(paragraph, title)
        if author.strip():
            byline = self.paragraph("Subtitle" if _has_style(self.document, "Subtitle")
                                    else "Normal", align=WD_ALIGN_PARAGRAPH.CENTER)
            self.write_markup(byline, author)

    def table_of_contents(self) -> None:
        if not self.options.toc or not self.toc_entries:
            return
        heading = self.paragraph("Heading 1", align=WD_ALIGN_PARAGRAPH.CENTER)
        ooxml.page_break_before(heading)
        self.write_markup(heading, self.options.toc_title)
        holder = self.paragraph("Normal")
        ooxml.add_toc_field(holder, self.toc_entries, depth=self.options.toc_depth,
                            rtl=self.options.rtl)

    def collect_toc(self) -> None:
        """Anchor names are assigned before writing so the TOC can be first."""
        index = 0
        for block in self.book.get("blocks", []):
            if block["type"] != "heading":
                continue
            level = int(block.get("level", 1))
            index += 1
            anchor = f"rv_{index:04d}"
            block["_anchor"] = anchor
            text = ir.plain_text(block.get("target") or block.get("text") or "")
            if level <= self.options.toc_depth:
                self.toc_entries.append((anchor, text.strip(), level))

    def start_section(self, record: dict[str, Any]) -> None:
        """Open a Word section here, so the source's own page setup resumes.

        The layout profile is re-applied rather than copied from the source:
        gutter, mirrored margins and header/footer distances are house style
        set from the command line, exactly as they are for the first section,
        and a book whose sections disagreed about them would otherwise change
        binding halfway through. The record keeps the source's values either
        way, so nothing is lost by not using them here.
        """
        try:
            start = WD_SECTION_START.from_xml(record.get("start_type") or "nextPage")
        except ValueError:      # a hand-edited book.json; a new page is the safe read
            start = WD_SECTION_START.NEW_PAGE
        section = self.document.add_section(start)

        for field, attribute in (("width_pt", "page_width"),
                                 ("height_pt", "page_height"),
                                 ("margin_top_pt", "top_margin"),
                                 ("margin_bottom_pt", "bottom_margin"),
                                 ("margin_inner_pt", "left_margin"),
                                 ("margin_outer_pt", "right_margin")):
            value = record.get(field)
            if value:
                setattr(section, attribute, Pt(float(value)))
        _set_orientation(section, record)

        ooxml.set_section_rtl(section, self.options.rtl)
        layout.apply_section(section, layout.Profile.from_options(self.options))
        self.running_heads(section, record)

    # -- running heads and feet --------------------------------------------- #

    def running_heads(self, section, record: dict[str, Any]) -> None:
        """Put back the running heads and feet this section defined, in Persian.

        Only the slots the source defined: one it inherited is left linked, so
        the Persian edition inherits it from the same place rather than
        repeating it. Nothing here can put a running head on a page the source
        left bare — the reader carried a slot only where Word was showing it.
        """
        # Measured off the section as built, so a landscape plate's running head
        # spreads across the landscape page rather than across the prose pages'.
        sizes = [section.page_width, section.left_margin, section.right_margin]
        width = (max(72.0, sizes[0].pt - sizes[1].pt - sizes[2].pt)
                 if all(sizes) else self.text_width_pt)

        # Set rather than merely turned on, and only for a book that carries the
        # answer: `add_section` clones the section before it, so a leftover
        # `w:titlePg` would put the first section's opening head on the first
        # page of every section after it. A book without sections leaves the
        # switch alone, because a --template may have set it deliberately.
        if "different_first_page" in record:
            section.different_first_page_header_footer = bool(
                record["different_first_page"])

        for part, key in ir.RUNNING_PARTS:
            defined = record.get(key) or {}
            for slot in ir.RUNNING_SLOTS:
                body = defined.get(slot)
                if not body:
                    continue
                container = getattr(
                    section, ir.RUNNING_ACCESSOR[slot].format(part=part), None)
                if container is None:
                    continue
                container.is_linked_to_previous = False
                if slot == "even":
                    self.document.settings.odd_and_even_pages_header_footer = True
                self._running_body(container, body, part, width)

    def _running_body(self, container, body: dict[str, Any], part: str,
                      width_pt: float) -> None:
        """One header or footer, written over the empty one Word just made.

        The property order is the schema's, not a preference: ``w:tabs`` has to
        precede the ``w:bidi`` that :func:`ooxml.set_paragraph_rtl` appends, and
        ``w:jc`` has to follow it, so direction is set before either.
        """
        style = "Header" if part == "header" else "Footer"
        style = style if _has_style(self.document, style) else "Normal"
        existing = list(container.paragraphs)

        for index, line in enumerate(body.get("paragraphs") or []):
            paragraph = (existing[index] if index < len(existing)
                         else container.add_paragraph())
            for run in list(paragraph.runs):
                run._r.getparent().remove(run._r)

            paragraph.style = style
            ooxml.set_paragraph_rtl(paragraph, self.options.rtl)
            pieces = line.get("pieces") or []
            if any(piece.get("tab") for piece in pieces):
                ooxml.set_running_tab_stops(paragraph, width_pt)
            for piece in pieces:
                if piece.get("tab"):
                    ooxml.add_tab(paragraph, rtl=self.options.rtl)
                elif piece.get("field"):
                    ooxml.add_field(paragraph, piece["field"], rtl=self.options.rtl)
                elif 'controls' in piece:
                    self.write_markup(paragraph, ir.escape_markup(piece['controls']))
                else:
                    self._running_text(paragraph, piece, part)
            if line.get("align"):
                ooxml.set_paragraph_alignment(paragraph, line["align"])

    def _running_text(self, paragraph, piece: dict[str, Any], part: str) -> None:
        """The author's own words, or nothing at all.

        Never the source's. A running head reading the English title across a
        Persian page is worse than no running head — that judgement is why these
        used to be dropped wholesale, and it still decides what happens when one
        of them comes back untranslated.
        """
        target = (piece.get("target") or "")
        if not target.strip():
            self.warnings.append(
                f"running {part} {piece['id']} is untranslated and was left "
                f"out: {ir.plain_text(piece.get('text') or '')[:60]!r}"
            )
            return
        self.write_markup(paragraph, target)


def _set_orientation(section, record: dict[str, Any]) -> None:
    """``w:orient`` for a section, after its size has been set.

    python-docx does not swap the page dimensions when the orientation is set,
    which is what makes setting both safe: the width and height already say
    which way round the page is, and this says which way the printer is told.
    """
    section.orientation = (WD_ORIENT.LANDSCAPE
                           if record.get("orientation") == "landscape"
                           else WD_ORIENT.PORTRAIT)


def _has_style(document, name: str) -> bool:
    try:
        document.styles[name]
        return True
    except KeyError:
        return False


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--book", required=True)
    parser.add_argument("--assets", default=None,
                        help="asset directory (default: <book dir>/assets)")
    parser.add_argument("--out", required=True, help="output .docx path")
    parser.add_argument("--template", default=None,
                        help="reference .docx supplying styles and page setup")
    parser.add_argument("--font", default="Vazir",
                        help="Persian (complex-script) font; e.g. 'B Nazanin', 'Tahoma'")
    parser.add_argument("--latin-font", default="Times New Roman")
    parser.add_argument("--size", type=float, default=11.5, help="body size in pt")
    parser.add_argument("--toc", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--toc-title", default="فهرست مطالب")
    parser.add_argument("--toc-depth", type=int, default=2)
    parser.add_argument("--justify", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--captions", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--rtl", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--page-breaks", choices=["chapter", "source", "none"],
                        default="chapter")
    parser.add_argument("--line-spacing", type=float, default=1.5,
                        help="body line spacing as a multiple (1.0 = single)")
    parser.add_argument("--first-line-indent", type=float, default=18.0, metavar="PT",
                        help="first-line indent for body paragraphs; 0 disables it")
    parser.add_argument("--mirror-margins", action=argparse.BooleanOptionalAction,
                        default=False,
                        help="mirrored inner/outer margins, for a printed book")
    parser.add_argument("--gutter", type=float, default=0.0, metavar="PT",
                        help="extra binding margin")
    parser.add_argument("--page-numbers", action=argparse.BooleanOptionalAction,
                        default=True, help="centred page number in the footer")
    parser.add_argument("--widow-control", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="stop a single line of a paragraph stranded on a page")
    parser.add_argument("--heading-size", choices=["style", "source"], default="style",
                        help="'style' uses Word's Heading N sizes; 'source' reproduces "
                             "the point size measured in the original book")


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel build", description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)

    book_path = Path(args.book)
    book = ir.load_book(book_path)
    assets = Path(args.assets) if args.assets else book_path.parent / "assets"

    report = Builder(book, assets, args).build(Path(args.out))
    runstate.RunState(book_path.parent).record("build", {
        "book": runstate.source_digest(book),
        "font": str(args.font),
    }, {"document": runstate.file_hash(args.out)})
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

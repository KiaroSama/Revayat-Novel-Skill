"""Book IR — the structured source of truth for a Revayat Novel translation.

Markdown is deliberately *not* the source of truth. A book is a list of typed
blocks that keep page geometry, image bytes and footnote identity, while only
inline emphasis travels as lightweight markup (which an LLM handles far more
reliably than a bespoke XML dialect, and which QA can verify by counting).

Schema (``book.json``)::

    {
      "schema": "revayat-novel/bookir@1",
      "source": {"path", "sha256", "format", "pages"},
      "meta":   {"title", "author", "lang_source", "lang_target", "title_target"},
      "page":   {"width_pt", "height_pt", "margin_*_pt"},
      "sections": [ ... ],   # DOCX only; "page" is section 0's geometry
      "blocks": [ ... ],
      "footnotes": [ ... ],
      "stats":  {...}
    }

Every block has ``id`` and ``type``. Text-bearing blocks carry ``text`` (source,
with inline markup) and ``target`` (translation, ``None`` until translated).

A section additionally carries the running heads and feet the author wrote::

    {"headers": {"default": {"paragraphs": [
        {"align": "center", "pieces": [
            {"id": "rh0001", "text": "Pride and Prejudice", "target": null},
            {"tab": true},
            {"field": " PAGE "}]}]}},
     "footers": {}}

Only the slots a section *defines* appear; a missing one is inherited from the
section before, in the IR exactly as it is in Word.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = "revayat-novel/bookir@1"

PT_PER_INCH = 72.0
EMU_PER_PT = 12700

#: Block types that carry translatable prose.
TEXT_TYPES = frozenset(
    {"heading", "paragraph", "blockquote", "listitem", "caption", "verse"}
)
#: Every block type the builder knows how to render.
BLOCK_TYPES = TEXT_TYPES | frozenset({"image", "pagebreak", "separator"})

#: The three running-head slots Word keeps per section, in the order it lists
#: them. A section names only the ones it defines and inherits the rest.
RUNNING_SLOTS = ("default", "first", "even")
#: ``(worksheet kind, section key)`` for the two ends of the page. The kind is
#: what a translator sees beside the id, so it says which end this line sits at.
RUNNING_PARTS = (("header", "headers"), ("footer", "footers"))
#: How python-docx names each slot, given the part. The reader and the builder
#: resolve a slot through this one table, so a book cannot be read out of a
#: header the builder would then write somewhere else.
RUNNING_ACCESSOR = {"default": "{part}", "first": "first_page_{part}",
                    "even": "even_page_{part}"}


# --------------------------------------------------------------------------- #
# Console / IO
# --------------------------------------------------------------------------- #

def use_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr.

    Windows consoles default to a legacy code page (cp1252), which raises
    UnicodeEncodeError the moment a Persian character is printed. Every CLI
    entry point calls this first.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if isinstance(stream, io.TextIOWrapper):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):  # pragma: no cover - exotic stream
                pass


def read_text(path: str | os.PathLike[str]) -> str:
    return Path(path).read_text(encoding="utf-8")


def write_text(path: str | os.PathLike[str], text: str) -> None:
    """Write UTF-8 text atomically, with LF endings preserved verbatim.

    ``newline=""`` stops Python translating ``\\n`` to ``os.linesep``, which
    would otherwise make every file CRLF on Windows and churn diffs.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Book construction
# --------------------------------------------------------------------------- #

def new_book(
    *,
    source_path: str = "",
    source_format: str = "",
    source_sha256: str = "",
    pages: int = 0,
    title: str = "",
    author: str = "",
    lang_source: str = "en",
    lang_target: str = "fa-IR",
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "source": {
            "path": source_path,
            "sha256": source_sha256,
            "format": source_format,
            "pages": pages,
        },
        "meta": {
            "title": title,
            "title_target": None,
            "author": author,
            "author_target": None,
            "lang_source": lang_source,
            "lang_target": lang_target,
        },
        "page": default_page_setup(),
        "blocks": [],
        "footnotes": [],
        "stats": {},
    }


#: One centimetre in points. Persian trims are quoted in centimetres, and
#: converting through inches loses a point or two on every edge.
PT_PER_CM = 72.0 / 2.54


def default_page_setup() -> dict[str, float]:
    """وزیری — 16.5 x 23.5 cm, the ordinary Iranian book trim.

    Only ever reached when the source has no page size of its own: a PDF or a
    DOCX brings its own geometry and that is used instead. So this is the size
    an EPUB or a Markdown import becomes, and it should be the size Persian
    books are actually printed at rather than a US trade paperback.

    The earlier default was 5.5 x 8.5in, which is a real size but a foreign
    one, and noticeably small for a novel set in Persian — the script needs
    more room per line than Latin at the same point size.
    """
    return {
        "width_pt": 16.5 * PT_PER_CM,
        "height_pt": 23.5 * PT_PER_CM,
        "margin_top_pt": 62.0,
        "margin_bottom_pt": 62.0,
        "margin_inner_pt": 62.0,
        "margin_outer_pt": 50.0,
    }


def make_block(block_type: str, index: int, **fields: Any) -> dict[str, Any]:
    if block_type not in BLOCK_TYPES:
        raise ValueError(f"unknown block type: {block_type!r}")
    block: dict[str, Any] = {"id": f"b{index:05d}", "type": block_type}
    block.update(fields)
    # Every reader and adapter routes through here, so source normalisation
    # happens once instead of three times, slightly differently.
    for field in ("text", "alt"):
        if isinstance(block.get(field), str):
            block[field] = normalise_source(block[field])
    if block_type in TEXT_TYPES:
        block.setdefault("text", "")
        block.setdefault("target", None)
    return block


def make_footnote(index: int, *, anchor_block: str, text: str,
                  origin: str = "source") -> dict[str, Any]:
    """``origin`` is ``source`` (present in the book) or ``translator`` (added)."""
    return {
        "id": f"fn{index:04d}",
        "anchor_block": anchor_block,
        "origin": origin,
        "text": normalise_source(text),
        "target": None,
    }


def load_book(path: str | os.PathLike[str]) -> dict[str, Any]:
    book = json.loads(read_text(path))
    schema = book.get("schema")
    if schema != SCHEMA:
        raise ValueError(f"unsupported book schema {schema!r}; expected {SCHEMA!r}")
    return book


def save_book(book: dict[str, Any], path: str | os.PathLike[str]) -> None:
    book["stats"] = book_stats(book)
    write_text(path, json.dumps(book, ensure_ascii=False, indent=1) + "\n")


def book_stats(book: dict[str, Any]) -> dict[str, Any]:
    blocks = book.get("blocks", [])
    text_blocks = [b for b in blocks if b["type"] in TEXT_TYPES]
    return {
        "blocks": len(blocks),
        "text_blocks": len(text_blocks),
        "translated_blocks": sum(1 for b in text_blocks if (b.get("target") or "").strip()),
        "images": sum(1 for b in blocks if b["type"] == "image"),
        "headings": sum(1 for b in blocks if b["type"] == "heading"),
        "footnotes": len(book.get("footnotes", [])),
        "source_chars": sum(len(b.get("text") or "") for b in text_blocks),
        "target_chars": sum(len(b.get("target") or "") for b in text_blocks),
    }


def iter_text_blocks(book: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for block in book.get("blocks", []):
        if block["type"] in TEXT_TYPES:
            yield block


def blocks_by_id(book: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {b["id"]: b for b in book.get("blocks", [])}


def iter_running_pieces(
    book: dict[str, Any],
) -> Iterator[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    """``(unit id, kind, piece, section)`` for each translatable running head.

    Only the pieces that carry an id, which are the ones with prose in them: a
    page-number field and the tab in front of it are laid out with the words,
    not translated with them, so a translator is never shown either.
    """
    for section in book.get("sections") or []:
        for kind, key in RUNNING_PARTS:
            part_of_section = section.get(key) or {}
            for slot in RUNNING_SLOTS:
                for paragraph in (part_of_section.get(slot) or {}).get("paragraphs") or []:
                    for piece in paragraph.get("pieces") or []:
                        if piece.get("id"):
                            yield piece["id"], kind, piece, section


def running_heads(book: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``unit id -> piece`` — the lookup merge writes a translation into.

    The counterpart of :func:`blocks_by_id` for the two lines the reader meets
    before and after the prose on every page.
    """
    return {unit_id: piece for unit_id, _, piece, _ in iter_running_pieces(book)}


def validate_book(book: dict[str, Any]) -> list[str]:
    """Structural self-check. Returns human-readable problems (empty == good)."""
    problems: list[str] = []
    seen: set[str] = set()

    for position, block in enumerate(book.get("blocks", [])):
        where = block.get("id") or f"#{position}"
        if not block.get("id"):
            problems.append(f"block {where}: missing id")
        elif block["id"] in seen:
            problems.append(f"block {where}: duplicate id")
        else:
            seen.add(block["id"])

        if block.get("type") not in BLOCK_TYPES:
            problems.append(f"block {where}: unknown type {block.get('type')!r}")
        if block.get("type") == "image" and not block.get("asset"):
            problems.append(f"block {where}: image without asset path")
        if block.get("type") == "heading":
            level = block.get("level")
            if not isinstance(level, int) or not 1 <= level <= 6:
                problems.append(f"block {where}: heading level must be 1..6, got {level!r}")

    footnote_ids: set[str] = set()
    for note in book.get("footnotes", []):
        note_id = note.get("id", "?")
        if note_id in footnote_ids:
            problems.append(f"footnote {note_id}: duplicate id")
        footnote_ids.add(note_id)
        if note.get("anchor_block") and note["anchor_block"] not in seen:
            problems.append(f"footnote {note_id}: anchor block {note['anchor_block']} not found")

    # A repeated running-head id is the "silently disappeared" shape: merge
    # resolves an id to one piece, so the second one's translation lands on the
    # first and the head it was written for prints in the source language.
    running: set[str] = set()
    for unit_id, _, _, section in iter_running_pieces(book):
        if unit_id in running:
            problems.append(f"running head {unit_id}: duplicate id "
                            f"(section {section.get('index')})")
        running.add(unit_id)

    for block in iter_text_blocks(book):
        for side in ("text", "target"):
            value = block.get(side)
            if not value:
                continue
            for ref in footnote_refs(value):
                if ref not in footnote_ids:
                    problems.append(f"block {block['id']} ({side}): unknown footnote ref {ref}")
    return problems


# --------------------------------------------------------------------------- #
# Inline markup
# --------------------------------------------------------------------------- #
#
# The only inline syntax a translator ever sees:
#
#   **bold**            ***bold italic***       *italic*
#   `verbatim`          kept byte-for-byte, forced LTR in the DOCX
#   [[fn:fn0007]]       footnote marker; must survive translation
#
# Anything else is literal. A lone ``*`` in prose is escaped as ``\*``.

FOOTNOTE_TOKEN = re.compile(r"\[\[fn:(fn\d{4})\]\]")
#: Any footnote token, including the ``tr-NN`` form a translator writes before
#: merge allocates it a book-wide id. Kept separate from the canonical pattern
#: on purpose: a ``tr-NN`` left in a finished book is a defect, and
#: :func:`validate_book` should still catch it.
ANY_FOOTNOTE_TOKEN = re.compile(r"\[\[fn:([A-Za-z0-9_-]+)\]\]")
_INLINE = re.compile(
    r"(?P<token>\[\[fn:fn\d{4}\]\])"
    r"|(?P<code>(?<!\\)`(?P<code_body>[^`]*)`)"
    r"|(?P<bi>(?<!\\)\*\*\*(?P<bi_body>(?:[^*\\]|\\.)+?)\*\*\*)"
    r"|(?P<bold>(?<!\\)\*\*(?P<bold_body>(?:[^*\\]|\\.)+?)\*\*)"
    r"|(?P<italic>(?<!\\)\*(?P<italic_body>(?:[^*\\]|\\.)+?)\*)"
)


#: Invisible characters that carry no meaning in prose. U+200C (ZWNJ) is
#: deliberately absent: in Persian it is a real orthographic character.
_INVISIBLE = str.maketrans({
    "﻿": "",   # BOM / zero-width no-break space, common as a word joiner
    "​": "",   # zero-width space
    "‍": "",   # zero-width joiner
    "­": "",   # soft hyphen
    "ـ": "",   # Arabic tatweel
    " ": " ",  # no-break space
})


def normalise_source(text: str) -> str:
    """Strip invisible noise that extractors leave in prose."""
    return re.sub(r"[ \t]{2,}", " ", (text or "").translate(_INVISIBLE)).strip()


def escape_markup(text: str) -> str:
    """Escape characters that would otherwise be read as markup."""
    return text.replace("\\", "\\\\").replace("*", "\\*").replace("`", "\\`")


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def render_markup(spans: Iterable[tuple[str, bool, bool]]) -> str:
    """Join ``(text, bold, italic)`` spans into inline markup.

    Adjacent spans sharing a style are merged first so that a sentence split
    across three same-styled PDF spans does not become ``*a**b**c*``.
    """
    merged: list[list[Any]] = []
    for text, bold, italic in spans:
        if not text:
            continue
        if merged and merged[-1][1] == bold and merged[-1][2] == italic:
            merged[-1][0] += text
            continue
        merged.append([text, bold, italic])

    out: list[str] = []
    for text, bold, italic in merged:
        # Emphasis must wrap visible characters, not the surrounding spaces:
        # "*text *" renders literally in most parsers.
        stripped = text.strip()
        if not stripped or not (bold or italic):
            out.append(escape_markup(text))
            continue
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        marker = "***" if (bold and italic) else ("**" if bold else "*")
        out.append(f"{lead}{marker}{escape_markup(stripped)}{marker}{trail}")
    return "".join(out)


def parse_markup(text: str) -> list[dict[str, Any]]:
    """Inverse of :func:`render_markup`.

    Returns a list of ``{"text", "bold", "italic", "verbatim", "footnote"}``
    spans. A footnote span has ``footnote`` set and empty ``text``.
    """
    spans: list[dict[str, Any]] = []

    def push(chunk: str, *, bold: bool = False, italic: bool = False,
             verbatim: bool = False, footnote: str | None = None) -> None:
        if footnote is None and not chunk:
            return
        spans.append({
            "text": chunk,
            "bold": bold,
            "italic": italic,
            "verbatim": verbatim,
            "footnote": footnote,
        })

    cursor = 0
    for match in _INLINE.finditer(text):
        push(_unescape(text[cursor:match.start()]))
        if match.group("token"):
            push("", footnote=FOOTNOTE_TOKEN.match(match.group("token")).group(1))
        elif match.group("code") is not None:
            push(match.group("code_body"), verbatim=True)
        elif match.group("bi") is not None:
            push(_unescape(match.group("bi_body")), bold=True, italic=True)
        elif match.group("bold") is not None:
            push(_unescape(match.group("bold_body")), bold=True)
        else:
            push(_unescape(match.group("italic_body")), italic=True)
        cursor = match.end()
    push(_unescape(text[cursor:]))
    return spans


def render_spans(spans: Iterable[dict[str, Any]]) -> str:
    """Exact inverse of :func:`parse_markup`.

    Lets a text transform (the Persian typography pass) rewrite only the prose
    spans while emphasis, verbatim runs and footnote tokens are carried through
    untouched — the markup cannot be damaged by a regex that never sees it.
    """
    out: list[str] = []
    for span in spans:
        if span.get("footnote"):
            out.append(f"[[fn:{span['footnote']}]]")
        elif span.get("verbatim"):
            out.append(f"`{span['text']}`")
        elif span.get("bold") and span.get("italic"):
            out.append(f"***{escape_markup(span['text'])}***")
        elif span.get("bold"):
            out.append(f"**{escape_markup(span['text'])}**")
        elif span.get("italic"):
            out.append(f"*{escape_markup(span['text'])}*")
        else:
            out.append(escape_markup(span["text"]))
    return "".join(out)


def plain_text(text: str) -> str:
    """Markup stripped — what a reader would actually see."""
    return "".join(span["text"] for span in parse_markup(text))


def footnote_refs(text: str) -> list[str]:
    return FOOTNOTE_TOKEN.findall(text or "")


def emphasis_signature(text: str) -> tuple[int, int, int]:
    """``(bold, italic, verbatim)`` span counts — compared source vs. target."""
    bold = italic = verbatim = 0
    for span in parse_markup(text or ""):
        if span["footnote"]:
            continue
        if span["verbatim"]:
            verbatim += 1
        elif span["bold"] and span["italic"]:
            bold += 1
            italic += 1
        elif span["bold"]:
            bold += 1
        elif span["italic"]:
            italic += 1
    return bold, italic, verbatim


# --------------------------------------------------------------------------- #
# Script / language helpers
# --------------------------------------------------------------------------- #

PERSIAN_RANGE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
LATIN_LETTER = re.compile(r"[A-Za-z]")
#: A word made only of Latin letters, 2+ chars — the shape of untranslated prose.
LATIN_WORD = re.compile(r"\b[A-Za-z][A-Za-z'’-]{1,}\b")


def script_ratio(text: str) -> tuple[float, float]:
    """``(persian_fraction, latin_fraction)`` over letter characters only."""
    persian = len(PERSIAN_RANGE.findall(text or ""))
    latin = len(LATIN_LETTER.findall(text or ""))
    total = persian + latin
    if total == 0:
        return 0.0, 0.0
    return persian / total, latin / total


def chapter_key(block: dict[str, Any]) -> bool:
    """True when a block starts a new chapter (heading level 1 or 2)."""
    return block.get("type") == "heading" and int(block.get("level", 9)) <= 2

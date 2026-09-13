"""Everything this book will print, typed — the one inventory of published prose.

Four stages each had their own answer to "what text does a reader actually see":
the worksheet cut, the bilingual review, the blind Persian pass and the
typography fixer. They agreed about body paragraphs, which is why nothing
noticed that they disagreed about everything else.

Measured before this existed, on a book with a translated title, a translated
byline and a translator's own footnote:

    inventory units: ['b00001', 'b00002']
    meta.title_target        revision moved: False   verdict still ok: True
    meta.author_target       revision moved: False   verdict still ok: True
    translator note target   revision moved: False   verdict still ok: True

The title page is the first thing a reader meets. It was reviewed by nobody,
covered by no revision, and `falint fix` never touched its typography — so a
Latin quotation mark or an English digit in it survived to print, and an edit to
it left both semantic approvals reading `ok`. A translator's own footnote is the
same shape: published prose that no inventory listed, because every inventory was
built by walking the *source* and asking what to translate. Text a translator
added has no source to walk.

So the question is asked once, here, the other way round: what will be printed,
and where does each piece live? Each record is typed — ``part`` says which
surface it prints on, ``origin`` says whether it came from the book or from the
translator, ``source`` is ``None`` rather than ``""`` when there is genuinely no
source side — so a reader can be selective without any of them re-deriving the
set. A translator's addition is classified as one instead of being given an
invented English original to be compared against.

The literal-only exemption is part of the type, not a special case bolted to each
caller: a unit whose every span is verbatim (a code sample, a bare URL, a column
of figures) is *meant* to survive byte for byte, so it is published, not pending,
and not something to send to a translator.

Depends on `bookir` and nothing else, on purpose: `falint` is reached from
`glossary`, which `merge` and `chunk` import, so an inventory that imported any
of those could not be the one the typography fixer uses.
"""

from __future__ import annotations

from typing import Any

import bookir as ir

#: Stable ids for the two metadata units. They are not block ids and never can
#: be, so they are spelled with the same ``#`` convention as an image's alt text:
#: one namespace, no collision with anything the extractor produces.
TITLE_ID = "meta#title"
AUTHOR_ID = "meta#author"

#: ``part`` — which surface the reader meets this on. The gate, the review and
#: the delivery check all report per part, because "one unit is untranslated"
#: means something very different for a body paragraph and for the title.
PARTS = ("metadata", "running", "body", "caption", "figure", "note")

#: ``origin`` — where the text came from. A ``translator`` unit has no source
#: side and must never be handed a fabricated one.
ORIGINS = ("source", "translator")

_KIND_BY_TYPE = {
    "paragraph": "para",
    "blockquote": "quote",
    "listitem": "list",
    "caption": "caption",
    "verse": "verse",
}


def kind_of(block: dict[str, Any]) -> str:
    """The worksheet kind of one block, with a heading's level in it.

    One definition, because the kind is part of what a translator is asked and
    part of what the request digest covers: a heading answered as a paragraph is
    how a chapter title becomes body text.
    """
    if block["type"] == "heading":
        return f"heading{int(block.get('level', 1))}"
    return _KIND_BY_TYPE.get(block["type"], block["type"])


def prose(text: str) -> str:
    """Everything a translator was meant to rewrite.

    ``verbatim`` spans come out on both sides: `` `literal_token` `` is *supposed*
    to survive byte for byte, so a unit made only of them has nothing to
    translate and reporting it as untranslated is reporting the feature working.
    """
    return "".join(span["text"] for span in ir.parse_markup(text or "")
                   if not span["verbatim"])


def _record(unit_id: str, *, kind: str, part: str, origin: str,
            source: str | None, container: dict[str, Any], field: str,
            ) -> dict[str, Any]:
    target = container.get(field)
    return {
        "id": unit_id,
        "kind": kind,
        "part": part,
        "origin": origin,
        # `None` and `""` are different answers: nothing to compare against
        # versus a source that is there and empty. A reviewer shown `""` reads it
        # as text that was dropped.
        "source": source,
        "target": str(target) if target else "",
        # Where the translation lives, so a writer and a reader cannot disagree
        # about which field is the published one.
        "container": container,
        "field": field,
        # A source that is entirely verbatim: published, exempt, never pending.
        "literal": bool(source and not prose(source).strip()),
    }


def _everything(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Every addressable slot in the book, published or not, in a stable order.

    The order is the digest order, so it has to be a property of the book rather
    than of dictionary iteration: metadata, running heads by section, then the
    blocks in book order with each image's alt text beside it, then the
    footnotes. Adding a unit type appends to the end of its group, which moves
    every revision — correctly, because it means something new is being printed.
    """
    found: list[dict[str, Any]] = []
    meta = book.get("meta") or {}
    for unit_id, kind, side, field in ((TITLE_ID, "title", "title", "title_target"),
                                       (AUTHOR_ID, "author", "author",
                                        "author_target")):
        found.append(_record(unit_id, kind=kind, part="metadata",
                             origin="source" if (meta.get(side) or "").strip()
                             else "translator",
                             source=meta.get(side) or None,
                             container=meta, field=field))

    for unit_id, kind, piece, _section in ir.iter_running_pieces(book):
        found.append(_record(unit_id, kind=kind, part="running",
                             origin="source" if (piece.get("text") or "").strip()
                             else "translator",
                             source=piece.get("text") or None,
                             container=piece, field="target"))

    for block in book.get("blocks") or []:
        if block["type"] in ir.TEXT_TYPES:
            found.append(_record(
                block["id"], kind=kind_of(block),
                part="caption" if block["type"] == "caption" else "body",
                origin="source" if (block.get("text") or "").strip()
                else "translator",
                source=block.get("text") or None,
                container=block, field="target"))
        elif block["type"] == "image":
            found.append(_record(
                f"{block['id']}#alt", kind="alt", part="figure",
                origin="source" if (block.get("alt") or "").strip()
                else "translator",
                source=block.get("alt") or None,
                container=block, field="target_alt"))

    for note in book.get("footnotes") or []:
        found.append(_record(
            note["id"], kind="footnote", part="note",
            # The note says so itself, and it is the one place the distinction is
            # already recorded: `origin: translator` is a note this translation
            # added, which has no English original by definition.
            origin=str(note.get("origin") or "source"),
            source=note.get("text") or None,
            container=note, field="target"))
    return found


def units(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Every unit this book **publishes** — one side of it is filled.

    A slot empty on both sides prints nothing and is not published: an
    undeclared running head, a title the extractor never found. Hashing it would
    make every book's digest depend on slots nobody uses.
    """
    return [unit for unit in _everything(book)
            if (unit["source"] or "").strip() or unit["target"].strip()]


def slots(book: dict[str, Any]) -> dict[str, tuple[dict[str, Any], str]]:
    """``unit id -> (container, field)`` for everything that can be written.

    What `merge.addressing` resolves through, so "where does this translation
    live" has one answer. A reader with its own copy of that rule drifts in one
    direction: towards reading a field nothing writes, which compares emptiness
    with emptiness and passes.

    Built from every slot rather than from the published ones: a translation
    arriving for a unit whose source is empty still has somewhere to go, and
    refusing it here would report it as a unit this book does not have.
    """
    return {unit["id"]: (unit["container"], unit["field"])
            for unit in _everything(book)}


def pending(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Published units that still owe a translation, exemptions honoured.

    A unit that is pending must never vanish from a required coverage list — the
    blind pass legitimately keeps it off a sheet (a reviewer with no source
    cannot translate it) and that is exactly how one disappeared from the set
    anything asked about.
    """
    return [unit for unit in units(book)
            if unit["source"] and not unit["literal"] and not unit["target"].strip()]


def digest_of(records: list[dict[str, Any]], *,
              sides: tuple[str, ...] = ("source", "target"),
              tag: str = "pub1") -> str:
    """One digest over an inventory. ``sides`` picks which halves it covers.

    The bilingual review is a statement about pairs, so it hashes both sides; the
    blind pass is a statement about the Persian alone, so it hashes the target.
    Both cover **every** published unit, which is the point: the first thing a
    reader sees used to be outside both.

    The type travels into the digest with the text. Moving a unit between parts —
    a paragraph becoming a caption, a source note reclassified as a translator's
    — changes what a reader is looking at even when the words do not, and a
    review of it as the other thing is not a review of this.
    """
    lines = ["\x00".join([record["id"], record["kind"], record["part"],
                          record["origin"]]
                         + [str(record.get(side) or "") for side in sides])
             for record in records]
    return f"{tag}:" + ir.sha256_bytes("\x1e".join(lines).encode("utf-8"))


def digest(book: dict[str, Any], *, sides: tuple[str, ...] = ("source", "target"),
           tag: str = "pub1") -> str:
    """:func:`digest_of` over this book's published inventory."""
    return digest_of(units(book), sides=sides, tag=tag)

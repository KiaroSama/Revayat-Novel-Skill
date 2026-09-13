"""Every footnote edge in the book, checked once, before anything is written.

`worksheet.validate_note_graph` checks a *reply*: the local `tr-NN` notes it
offers against the markers in the units it answers, before any id is allocated.
That is the right check in the right place, and it is not the whole graph — it
cannot be, because it never sees the book.

What it could not see, measured on real merges:

* a translator note asked for **from a running header** merged with
  ``anchor_block: ''``. Word cannot place a footnote in a header, so the note
  would have printed nowhere and the marker in the header would have printed
  literally. Nothing refused it; `qa` reported `footnote-orphaned` afterwards,
  about a book already on disk.
* a note body containing ``[[fn:fn4321]]`` — a canonical id the book does not
  define — merged clean, with `validate_book` and `qa` both silent. The local
  check only ever looked at `tr-NN`, and `validate_book` only ever scanned block
  text and targets, so a canonical edge *inside a note* was checked by nothing.
* a note body referring to ``[[fn:fn0001]]``, which is the id that body is about
  to be allocated: a self-cycle, merged clean.

So this module asks the question once, over every place a marker can appear, and
is called from the one place the book is written (`bookwrite.transaction`) and
from the complete gate (`qa check`). Both use the same list, so a defect cannot
be an error in one and invisible in the other.

**Where a note may be anchored is decided by what the builder and the anchor
record actually do, not by what seems reasonable.** `build_docx` writes running
heads, the title page and an image's caption through `write_markup`, which would
try to place a footnote in a header — Word has no such thing — and
`merge._anchor_notes` walks text blocks only, so a note referred to from any of
those gets no anchor at all. Those placements are therefore refused **by name**
before the commit, rather than fabricating an anchor or silently dropping the
note. A caption *block* is a text block: a note anchored there works end to end,
and is allowed.

Markers are read with `bookir.footnote_refs`, which parses the markup rather than
scanning it, so `` `[[fn:tr-example]]` `` in backticks is an example of the
notation and not an edge.
"""

from __future__ import annotations

import re
from typing import Any

import bookir as ir
import published

#: ``tr-01`` — a note a translator invented while translating. Legitimate in a
#: reply, and a defect in the book: merge allocates it a real id.
LOCAL = re.compile(r"^tr-\d+$")

#: ``fn0001`` — a note the book defines.
CANONICAL = re.compile(r"^fn\d+$")

#: Where a marker may sit, because the builder places it there and the anchor
#: record can name it. Everything else is refused rather than invented.
ANCHORABLE = "body"


def _containers(book: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """``(unit id, where, side, text)`` for every place a marker can appear.

    ``where`` is the *typed* part from `published`, narrowed to what this
    question needs: ``body`` for a text block (a caption block included), and the
    part's own name for everything else, because the refusal has to say which
    surface it is refusing.
    """
    found: list[tuple[str, str, str, str]] = []
    blocks = {block["id"] for block in ir.iter_text_blocks(book)}
    for unit in published.units(book):
        where = ANCHORABLE if unit["id"] in blocks else unit["part"]
        for side in ("source", "target"):
            text = unit.get(side) or ""
            if text:
                found.append((unit["id"], where, side, str(text)))
    for note in book.get("footnotes") or []:
        for side, field in (("source", "text"), ("target", "target")):
            text = note.get(field) or ""
            if text:
                found.append((note["id"], "note", side, str(text)))
    return found


def problems(book: dict[str, Any]) -> list[str]:
    """Why this book's footnote graph does not resolve — empty when it does."""
    notes = {note["id"]: note for note in book.get("footnotes") or []}
    found: list[str] = []
    #: Which units refer to each note, so "the same marker twice" is answerable.
    referrers: dict[str, list[str]] = {}

    for unit_id, where, side, text in _containers(book):
        for ref in ir.footnote_refs(text, include_local=True):
            if where != ANCHORABLE and where != "note":
                found.append(
                    f"{unit_id} ({side}): a footnote marker in a {where} unit. "
                    f"Word cannot place a note there and nothing can anchor it, "
                    f"so `[[fn:{ref}]]` would print as itself — move the note "
                    f"into the body text it belongs to")
                continue
            if where == "note":
                if ref == unit_id:
                    found.append(
                        f"{unit_id} ({side}): this note refers to itself. There "
                        f"is no anchor inside a note, so the marker prints")
                else:
                    found.append(
                        f"{unit_id} ({side}): this note refers to `[[fn:{ref}]]`. "
                        f"A footnote inside a footnote has nowhere to be "
                        f"anchored, so the marker prints in the note's own text")
                continue
            if LOCAL.match(ref):
                found.append(
                    f"{unit_id} ({side}): `[[fn:{ref}]]` is a translator's local "
                    f"id and never a book id. Merge allocates one; a local id in "
                    f"the book means the reply was written in past it")
                continue
            if ref not in notes:
                found.append(
                    f"{unit_id} ({side}): `[[fn:{ref}]]` names no note this book "
                    f"defines, so the marker prints as itself")
                continue
            if side == "target":
                referrers.setdefault(ref, []).append(unit_id)

    for note_id, note in notes.items():
        pointing = referrers.get(note_id) or []
        if len(set(pointing)) > 1:
            found.append(
                f"{note_id}: referred to from {sorted(set(pointing))}. One note "
                f"cannot belong to two places, and picking one drops the other")
        if pointing and not (str(note.get("text") or "").strip()
                            or str(note.get("target") or "").strip()):
            found.append(
                f"{note_id}: a marker points at it and it has no body on either "
                f"side, so an empty note would print")
        if str(note.get("origin") or "source") != "translator":
            # A source note the translation has not reached yet is ordinary: its
            # source marker is there and its target is not. `qa` reports the
            # rest, with codes that say more than this one could.
            continue
        if not pointing:
            found.append(
                f"{note_id}: a translator's note with no marker pointing at it. "
                f"It exists because a reply asked for one, so a note nothing "
                f"refers to means the marker was dropped")
        elif str(note.get("anchor_block") or "") not in set(pointing):
            found.append(
                f"{note_id}: anchored to "
                f"{str(note.get('anchor_block') or '(nothing)')!r} and referred "
                f"to from {sorted(set(pointing))}. A note prints under the "
                f"paragraph its anchor names, so it would print away from the "
                f"sentence that points at it")
    return found

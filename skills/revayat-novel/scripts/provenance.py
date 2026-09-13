"""What identifies a question, and the source it was cut from.

Two values answer two different questions about a worksheet, and the difference
matters enough that conflating them has cost this project real defects twice:

* **the request token** — over the worksheet *as it will be sent*, so it says
  which **version** of a job a given answer was written for. Filenames cannot: a
  rebuild puts the new question at the path the old answer already occupies, with
  the same ids and the same count.
* **the source fingerprint** — over the units *as cut*, recomputable later from
  the spans the manifest records, plus the live kind and everything else the
  worker was told. It says whether the book has moved since the build, which the
  token cannot, because the token is fixed at the moment the question was asked.

Both are **tagged with the formula that produced them**, and every reader refuses
a tag it cannot recompute rather than guessing which side is stale — invariant 9
in AGENTS.md. Guessing is how a stale reply passes.

This lives on its own because three modules need it and none of them should own
it: `chunk` records these values, `merge` re-checks them, and `chunkstatus` has to
reach the same verdict as `merge` or the two disagree about what is finished. A
second copy of any formula here drifts in one direction — towards believing a
stale answer is fresh.
"""

from __future__ import annotations

from typing import Any

import bookir as ir
import glossary as gl
import published
import segments
from worksheet import SCAFFOLD_COMMENT, request_line

#: Characters of neighbouring source a worker is shown, and therefore the width
#: of the window the dependency facet has to hash.
CONTEXT_CHARS = 450

#: The worksheet's name for each block type, and the kind a heading's level is
#: built into. Defined in `published`, because the same name has to appear on the
#: worksheet, in the request digest and in the published inventory — three places
#: that would otherwise each have an opinion about what a block is called.
_KIND_BY_TYPE = published._KIND_BY_TYPE
kind_of = published.kind_of


def translatable_units(book: dict[str, Any], ids: list[str]) -> list[tuple[str, str, str]]:
    """``(unit_id, kind, source_text)`` for everything in the chunk to translate."""
    lookup = ir.blocks_by_id(book)
    units: list[tuple[str, str, str]] = []
    for block_id in ids:
        block = lookup.get(block_id)
        if block is None:
            continue
        if block["type"] in ir.TEXT_TYPES and (block.get("text") or "").strip():
            units.append((block_id, kind_of(block), block["text"]))
        elif block["type"] == "image" and (block.get("alt") or "").strip():
            units.append((f"{block_id}#alt", "alt", block["alt"]))

    referenced = {
        ref
        for _, _, text in units
        for ref in ir.footnote_refs(text)
    }
    for note in book.get("footnotes", []):
        if note["id"] in referenced and (note.get("text") or "").strip():
            units.append((note["id"], "footnote", note["text"]))

    # A section's running heads ride with the chunk that opens the section, so
    # the translator settles the head of a chapter while looking at the chapter.
    # A section holding no blocks of its own has nowhere else to go than the
    # first chunk, and an unreachable unit is one nobody can ever translate.
    opening = book["blocks"][0]["id"] if book.get("blocks") else None
    for unit_id, kind, piece, section in ir.iter_running_pieces(book):
        anchor = section.get("start_block") or opening
        if anchor in ids and (piece.get("text") or "").strip():
            units.append((unit_id, kind, piece["text"]))
    return units


def unit_fingerprint(book: dict[str, Any], ids: list[str]) -> str:
    """Identity of the source a worksheet for ``ids`` is cut from.

    One definition, recorded by :func:`build` and re-checked by ``merge``. Two
    copies of this formula would drift, and the direction it drifts in is
    "merge believes a stale reply is fresh" — so it lives here, once, and both
    sides call it.

    Source text only. A translation, a translator's footnote or an accepted page
    must not change it, or every successful merge would report the worksheets it
    came from as stale.

    Tagged with the formula that produced it, because the page route writes a
    *different* digest under the same manifest key — its own covers the page
    raster and the page geometry as well, which merge cannot recompute from
    block ids. Untagged, merge recomputed this formula against that value and
    declared every page-route worksheet stale.
    """
    units = translatable_units(book, ids)
    return "units:" + ir.sha256_bytes(
        "\n".join(f"{unit_id}\x00{text}" for unit_id, _, text in units)
        .encode("utf-8"))


#: Version tag on a request token, so a reader that cannot recompute a form says
#: so instead of guessing. See invariant 9 in AGENTS.md.
REQUEST_VERSION = "req1"

#: How many hex characters of the request digest travel in the worksheet. 64 bits
#: is far more than enough to tell two generations of one job apart, and a token a
#: person has to copy should fit on the line with the rest of the comment.
REQUEST_CHARS = 16

#: What the request line costs a worksheet, reserved out of the budget before the
#: splitter measures anything. A constant, because the token is a fixed-width
#: digest behind a fixed tag.
REQUEST_LINE_CHARS = len(request_line(f"{REQUEST_VERSION}:{'0' * REQUEST_CHARS}")) + 1


def unit_records(units: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    """Per unit: its id, kind, owner, and the slice of the owner it covers.

    This is what makes a cut *recomputable* later. `split_text` guarantees the
    pieces of a unit rejoin exactly, so their offsets are the running sum of their
    lengths — and with the offsets written down, merge can take the owner's live
    text, slice it the same way and ask whether the book still says what the
    worksheet asked about. Without them it could only hash the parent block, which
    is identical for every cut of it, so a recut of one paragraph was invisible.

    **Offsets are relative to the owner, so this must be given every segment of
    that owner — never one worksheet's share of them.** Called per worksheet, the
    running sum restarts at zero, and a paragraph cut across three worksheets
    recorded `offset: 0` three times. Measured on a 2700-character paragraph at
    budget 1400: three segments, all `offset: 0`, so the recorded spans covered
    characters 0-1008 three times over and **1692 characters were checked by
    nothing**. A same-length edit anywhere past the first segment — `word0290`
    for `NEVER290` — left every recorded slice identical, and the stale reply
    merged. :func:`build` computes these once per block group, before the units
    are grouped into jobs, and each job takes its own records from that.

    An image's alt text (`b00042#alt`) is its own owner, not a segment of the
    block: `segments.SEGMENT` is numeric on purpose.
    """
    seen: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    for unit_id, kind, text in units:
        owner = segments.base_of(unit_id)
        start = seen.get(owner, 0)
        records.append({"id": unit_id, "kind": kind, "owner": owner,
                        "offset": start, "length": len(text)})
        seen[owner] = start + len(text)
    return records


def span_problems(records: list[dict[str, Any]],
                  whole: dict[str, str]) -> list[str]:
    """Why these spans do not describe their owners — empty when they do.

    The spans are only worth recording if they are a *partition*: each owner
    covered from its first character to its last, in order, with no gap and no
    overlap. Before this was checked, they were none of those things and nothing
    said so — the digest computed happily over slices that skipped two thirds of a
    paragraph, which is the quiet half of the defect. A verifier that accepts an
    incoherent cut cannot tell a changed source from a mis-recorded one.
    """
    problems: list[str] = []
    by_owner: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_owner.setdefault(record["owner"], []).append(record)

    for owner, spans in sorted(by_owner.items()):
        ordered = sorted(spans, key=lambda s: (s["offset"], s["length"]))
        cursor = 0
        for span in ordered:
            if span["offset"] < cursor:
                problems.append(
                    f"{span['id']} starts at {span['offset']}, inside the span "
                    f"that ends at {cursor}: two units claim the same text")
            elif span["offset"] > cursor:
                problems.append(
                    f"{span['id']} starts at {span['offset']} but the previous "
                    f"span ends at {cursor}: {span['offset'] - cursor} "
                    f"character(s) of {owner} belong to no unit")
            cursor = max(cursor, span["offset"] + span["length"])
        text = whole.get(owner)
        if text is None:
            problems.append(f"{owner} is not in the book any more")
        elif cursor != len(text):
            problems.append(
                f"the spans of {owner} cover {cursor} of its {len(text)} "
                f"characters, so the rest is checked by nothing")
    return problems


def dependency_facet(book: dict[str, Any], units: list[tuple[str, str, str]],
                     glossary: dict[str, Any] | None,
                     neighbours: dict[str, list[str]] | None) -> list[str]:
    """What decided the worker's instructions, besides the units' own text.

    A worksheet is not only its prose. It carries the term table the units called
    for, the voice cards for the characters that speak in them, and a bounded
    window of the surrounding text so a pronoun can be resolved. Change any of
    those *after the worksheet was written* and the question the translator was
    asked no longer matches the question the book would ask today — while the
    units' own bytes, the ids, the kinds and the recorded manifest all still agree.

    That is why this is hashed from the **live** book and the **live** glossary
    rather than compared against anything stored: approving an alias, adding a
    voice card, or editing the paragraph next door leaves a stored manifest
    identical to itself. Measured before this existed: an approved alias mapping
    changed after the build, and every old answer merged clean.

    The rendering functions are the same ones `render_worksheet` calls, so the
    facet is what the worker actually saw and not a second description of it.
    """
    parts: list[str] = ["dependencies"]
    blob = "\n".join(text for _, _, text in units)

    if glossary is None:
        # Distinguished from an empty glossary on purpose: "no glossary was in
        # play" and "the glossary said nothing about these units" are different
        # questions, and collapsing them makes adding the first entry invisible.
        parts.append("glossary\x00none")
    else:
        parts.append("glossary\x00" + gl.render_term_table(
            gl.entries_for_text(glossary, blob), glossary.get("policy", {}),
            block_ids=[unit_id for unit_id, _, _ in units]))
        parts.append("voices\x00" + gl.render_voice_cards(glossary, blob))

    lookup = ir.blocks_by_id(book)

    def neighbour_blob(ids: list[str]) -> str:
        return " ".join(
            ir.plain_text(lookup[i].get("text") or "")
            for i in ids if i in lookup and lookup[i]["type"] in ir.TEXT_TYPES
        ).strip()

    before = (neighbours or {}).get("before") or []
    after = (neighbours or {}).get("after") or []
    # Truncated exactly as `neighbour_context` truncates, so the facet moves when
    # what the worker was shown moves — and not when a distant edit changes text
    # that was always outside the window.
    parts.append("before\x00" + (neighbour_blob(before)[-CONTEXT_CHARS:]
                                 if before else ""))
    parts.append("after\x00" + (neighbour_blob(after)[:CONTEXT_CHARS]
                                if after else ""))
    return parts


def source_fingerprint(book: dict[str, Any], block_ids: list[str],
                       records: list[dict[str, Any]], *,
                       glossary: dict[str, Any] | None = None,
                       neighbours: dict[str, list[str]] | None = None) -> str:
    """Identity of the source these exact units were cut from, recomputable.

    Replaces a digest over the parent blocks' whole text. That one could not tell
    two cuts of one paragraph apart — every segment of a block shared it — so a
    rebuild at a different budget left merge comparing a value against itself.

    The trailing length record catches the case the slices cannot: an owner whose
    text grew after the cut still matches every recorded slice.
    """
    live = {unit_id: (kind, text)
            for unit_id, kind, text in translatable_units(book, block_ids)}
    whole = {unit_id: text for unit_id, (_, text) in live.items()}
    parts: list[str] = []
    for record in records:
        text = whole.get(record["owner"])
        piece = ("\x00absent" if text is None
                 else text[record["offset"]:record["offset"] + record["length"]])
        # The kind comes from the **book as it stands now**, never from the
        # record. Reading the stored kind compared the manifest with itself: turn
        # a translated paragraph into a heading, or move a heading from level 2 to
        # level 3, and the digest did not move — so the old answer merged and a
        # chapter title kept a paragraph's translation. `kind_of` encodes the
        # level, so one lookup covers both.
        kind = live.get(record["owner"], (record["kind"], ""))[0]
        parts.append(f"{record['id']}\x00{kind}\x00{piece}")
    covered: dict[str, int] = {}
    for record in records:
        end = record["offset"] + record["length"]
        covered[record["owner"]] = max(covered.get(record["owner"], 0), end)
    for owner, end in sorted(covered.items()):
        parts.append(f"{owner}\x00span\x00{end}\x00{len(whole.get(owner) or '')}")

    # Everything else the worker was told. Without it, freshness answered only
    # "did these units' own characters change" — so an approved alias, a new voice
    # card or an edit to the paragraph next door all left the digest still.
    ours = {record["owner"] for record in records}
    parts += dependency_facet(
        book, [(unit_id, kind, text) for unit_id, kind, text in
               translatable_units(book, block_ids) if unit_id in ours],
        glossary, neighbours)

    # `units3` because the formula grew: a value written by `units2` covered
    # neither the live kind nor the dependencies, so comparing the two forms
    # would report every older worksheet as changed. Merge routes an unknown or
    # superseded tag to `unverified_freshness` instead of guessing either way.
    return "units3:" + ir.sha256_bytes("\n".join(parts).encode("utf-8"))


def request_token(worksheet: str) -> str:
    """The identity of one question, over the worksheet as it will be sent.

    Everything that decides what a translator is being asked is in that text: the
    ordered headers and kinds, the exact segment boundaries, the neighbouring
    context, the term table this chunk's own units called for and the voice cards
    that came with them. Hashing the rendered worksheet covers all of it without a
    second formula that could drift from what was actually sent.

    Our own scaffolding comments are left out, and that is not a detail: one of
    them counts the jobs ("worksheet 0001/0006"). Hashing it meant that raising
    the budget invalidated the answer to a unit whose question had not changed by
    a character, because the *other* jobs renumbered — and "valid unchanged
    replies remain reusable" is half of what this identity is for. What stays in
    the hash is the question: the ordered headers and kinds, the exact segment
    texts, the neighbouring context, the term table and the voice cards.

    Computed before the request line exists, so the value does not contain itself.
    """
    question = "\n".join(line for line in worksheet.splitlines()
                         if not SCAFFOLD_COMMENT.match(line.strip()))
    return (f"{REQUEST_VERSION}:"
            + ir.sha256_bytes(question.encode("utf-8"))[:REQUEST_CHARS])


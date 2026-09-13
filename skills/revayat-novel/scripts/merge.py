"""Stage 4 — fold translated worksheets back into ``book.json``.

Every unit the worksheet asked for must come back with the same id, the same
kind and in the same order. That is the whole point of the ``@@`` protocol: a
dropped paragraph, a merged pair of paragraphs or an invented id is a hard,
named error here rather than a quietly shorter book discovered after the DOCX is
built.

Two properties this stage owes the rest of the pipeline:

* **A merge that reports failure changed nothing.** The book on disk is either
  the book from before or the book with the whole validated transaction in it.
  Reporting ``ok=false`` *and* writing half the chunk is the worst of both
  worlds: the operator is told to fix the worksheet, and the "before" they would
  fix against no longer exists.
* **Matching ids are not freshness.** ``chunk build`` records what each
  worksheet was cut from, and this stage re-checks it. A translation of "Ali
  arrived." must not be accepted for a source that now says "Ali never
  arrived." — the ids line up perfectly and the meaning is inverted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

import bookir as ir
import glossary as gl
import segments
from chunk import unit_fingerprint
from worksheet import (  # noqa: F401  (this module's published surface)
    ESCAPED_HEADER, FENCE, HEADER, TRANSLATOR_NOTE, parse_worksheet,
    read_reply, read_worksheet, validate_reply,
)

# --------------------------------------------------------------------------- #
# Translator notes
# --------------------------------------------------------------------------- #

def adopt_translator_notes(
    book: dict[str, Any],
    entries: list[dict[str, str]],
    *,
    reply: str,
    expected: set[str],
    allocated: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str], set[str]]:
    """This reply's own ``tr-NN`` notes as book footnotes — the same ones twice.

    The translation policy invites a translator to add a note for a cultural
    reference or a pun that cannot survive. Those ids cannot exist in the
    worksheet manifest — the translator invents them while translating — so they
    are allocated a book-wide id here.

    **Provenance is the point.** Each note records the reply it came from and the
    local id it had there, so replaying a worksheet reuses the same footnote
    instead of allocating a new one and leaving the old one in the book,
    referenced by nothing, still printing. And because the scope is
    ``(reply, local id)`` rather than the local id alone, two segments of one
    block answered in two worksheets may each have their own ``tr-01`` meaning
    different things.

    Nothing here is appended to the book: the caller commits, so a reply that
    turns out to be malformed does not consume a footnote number.

    Returns ``(new notes, local id -> footnote id, ids to retire)``.
    """
    offered: dict[str, str] = {}
    for item in entries:
        if item["id"] in expected or not TRANSLATOR_NOTE.match(item["id"]):
            continue
        body = item["text"].strip()
        if body:
            offered[item["id"]] = body

    owned = {note["local_id"]: note for note in book.get("footnotes", [])
             if (note.get("reply") or "") == reply and note.get("local_id")}

    used = {note["id"] for note in book.get("footnotes", [])}
    used |= {note["id"] for note in allocated}
    next_index = 1 + max(
        (int(note_id[2:]) for note_id in used
         if note_id.startswith("fn") and note_id[2:].isdigit()),
        default=0,
    )

    new_notes: list[dict[str, Any]] = []
    mapping: dict[str, str] = {}
    for local_id, body in offered.items():
        existing = owned.get(local_id)
        if existing is not None:
            # A corrected reply rewrites its own note rather than adding one.
            existing["text"] = ir.normalise_source(body)
            existing["target"] = body
            mapping[local_id] = existing["id"]
            continue
        note = ir.make_footnote(next_index, anchor_block="", text=body,
                               origin="translator")
        note["target"] = body
        note["reply"] = reply
        note["local_id"] = local_id
        new_notes.append(note)
        mapping[local_id] = note["id"]
        next_index += 1

    # Only notes this same reply owns and no longer offers. A source note, or a
    # note belonging to another worksheet, is none of this reply's business.
    retired = {note["id"] for local_id, note in owned.items()
               if local_id not in offered}
    return new_notes, mapping, retired


def _resolve_local_tokens(texts: dict[str, str],
                          mapping: dict[str, str]) -> dict[str, str]:
    """Point this reply's inline tokens at the footnote ids they were given.

    Done on the worksheet's own unit texts, **before** the segments of a block
    are concatenated. Rewriting the rejoined block instead cannot tell the two
    scopes apart: one mapping would claim every ``[[fn:tr-01]]`` in the
    paragraph, including the one that belongs to the other worksheet.
    """
    if not mapping:
        return texts
    return {
        unit_id: ir.ANY_FOOTNOTE_TOKEN.sub(
            lambda match: f"[[fn:{mapping.get(match.group(1), match.group(1))}]]",
            text)
        for unit_id, text in texts.items()
    }


#: What a translator may call their own note. ``footnote`` is what `SKILL.md` and
#: the translation policy actually ask for; ``note`` is the obvious near-miss of
#: that word and is accepted rather than refused, because the intent is
#: unambiguous and the hazard this check exists for is elsewhere — a note answered
#: as ``heading1``, ``para`` or ``alt`` would be filed as structure.
NOTE_KINDS = frozenset(("footnote", "note"))


def validate_note_graph(
    texts: dict[str, str],
    offered: dict[str, str],
    kinds: dict[str, str],
) -> list[str]:
    """Does this reply's footnote graph resolve? Checked before anything is written.

    Four shapes used to reach the book unchallenged, and each one prints: a marker
    with no body left a literal ``[[fn:tr-01]]`` in the finished prose, a body with
    no marker became a note nothing refers to, the same marker twice made ownership
    unguessable, and ``@@ tr-01 heading1`` was adopted as a footnote on the strength
    of its id alone.

    They share an omission rather than a cause: notes were committed one reply at a
    time and nobody asked whether the result made sense as a graph.
    """
    problems: list[str] = []
    used: list[str] = []
    for text in texts.values():
        used += [ref for ref in ir.ANY_FOOTNOTE_TOKEN.findall(text or "")
                 if TRANSLATOR_NOTE.match(ref)]

    for local_id in dict.fromkeys(used):
        if local_id not in offered:
            problems.append(
                f"{local_id}: the translation refers to this note and the reply "
                f"carries no `@@ {local_id} footnote` body for it — merging would "
                f"leave the marker itself in the book")
        if used.count(local_id) > 1:
            problems.append(
                f"{local_id}: referred to {used.count(local_id)} times. One note "
                f"cannot belong to two places, and picking one silently drops the "
                f"other")

    for local_id in offered:
        if local_id not in used:
            problems.append(
                f"{local_id}: a note body no translation refers to. It would print "
                f"at the foot of a page with no number pointing at it")
        kind = kinds.get(local_id)
        if kind is not None and kind not in NOTE_KINDS:
            problems.append(
                f"{local_id}: answered as {kind!r}, which is not a note kind "
                f"({' or '.join(sorted(NOTE_KINDS))}). Adopting it would file a "
                f"heading, a paragraph or an alt text as a footnote")
    return problems


def _anchor_notes(book: dict[str, Any], notes: list[dict[str, Any]]) -> None:
    """Bind each note this merge touched to the block that now refers to it.

    Every note handed over is re-anchored, not only the ones without an anchor.
    Skipping the anchored ones is what left a **reused** note claiming the
    paragraph its marker had moved away from: the note was correctly updated in
    place, the caller passed only the newly allocated notes, and the stale anchor
    was never revisited — so the note printed under a paragraph that does not
    refer to it.
    """
    wanted = {note["id"]: note for note in notes}
    if not wanted:
        return
    for block in ir.iter_text_blocks(book):
        for ref in ir.footnote_refs(block.get("target") or ""):
            note = wanted.pop(ref, None)
            if note is not None:
                note["anchor_block"] = block["id"]


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

def addressing(book: dict[str, Any]) -> Callable[[str], tuple[dict[str, Any], str] | None]:
    """A resolver from unit id to ``(container, field)``, or ``None`` if it has none.

    One addressing rule, built once per book. :func:`apply_units` writes through
    it and `meaning.py` reads through it, because a reader with its own copy of
    "where does the translation of this unit live" drifts in one direction:
    towards reading a field nothing writes. A check against a field nothing
    writes compares emptiness with emptiness and passes — this repository has
    shipped that twice, and both times the check looked right.

    Built once rather than resolved per call: the indexes are O(book), and a
    review walks every unit.
    """
    blocks = ir.blocks_by_id(book)
    notes = {note["id"]: note for note in book.get("footnotes", [])}
    running = ir.running_heads(book)

    def resolve(unit_id: str) -> tuple[dict[str, Any], str] | None:
        if unit_id.endswith("#alt"):
            block = blocks.get(unit_id[: -len("#alt")])
            if block is None or block["type"] != "image":
                return None
            return block, "target_alt"
        if unit_id in notes:
            return notes[unit_id], "target"
        if unit_id in running:
            return running[unit_id], "target"
        block = blocks.get(unit_id)
        if block is not None and block["type"] in ir.TEXT_TYPES:
            return block, "target"
        return None

    return resolve


def apply_units(book: dict[str, Any], units: dict[str, str]) -> dict[str, Any]:
    """Write translations onto the book. Returns a report of what landed.

    ``unknown`` is load-bearing: a manifest id that resolves to nothing in this
    book is not a merge, and the caller must not count the chunk as applied.
    """
    resolve = addressing(book)
    applied, unknown, blank = [], [], []

    for unit_id, value in units.items():
        text = value.strip()
        if not text:
            blank.append(unit_id)
            continue

        slot = resolve(unit_id)
        if slot is None:
            unknown.append(unit_id)
            continue
        container, field = slot
        container[field] = text
        applied.append(unit_id)

    return {"applied": applied, "unknown": unknown, "blank": blank}


def merge(
    book_path: Path,
    chunks_dir: Path,
    *,
    only: list[str] | None = None,
    strict: bool = True,
    glossary_path: Path | None = None,
) -> dict[str, Any]:
    """Fold the selected worksheets in, or change nothing and say why.

    ``strict`` is all-or-nothing across the whole selected transaction. Without
    it, every worksheet that validates is applied and every worksheet with a
    problem contributes nothing — the reply is the unit of trust, so "land what
    is good" means "land the good replies", never "land the good half of a reply
    whose structure cannot be trusted". Either way ``ok`` reports honestly, and
    lenient is an exit code, not permission to corrupt.
    """
    book = ir.load_book(book_path)
    manifest = json.loads((chunks_dir / "manifest.json").read_text(encoding="utf-8"))
    chunks = manifest.get("chunks") or []

    report: dict[str, Any] = {
        "chunks_merged": 0,
        "units_applied": 0,
        "missing_outputs": [],
        "missing_units": {},
        "unknown_units": {},
        "blank_units": {},
        "malformed": {},
        "coverage": [],
        "stale": [],
        "unverified_freshness": [],
        "unverified_kinds": [],
    }

    # Every segment the manifest knows of, from *all* chunks and not only the
    # selected ones: a block split between two worksheets with one of them
    # selected has nothing missing from the selected set.
    known_segments = segments.segments_by_owner(
        unit_id for entry in chunks for unit_id in entry.get("unit_ids") or [])

    accepted: dict[str, str] = {}
    new_notes: list[dict[str, Any]] = []
    retired: set[str] = set()
    #: Notes this merge created *or* reused, so re-anchoring can see both.
    touched: list[dict[str, Any]] = []

    for entry in chunks:
        if only and entry["id"] not in only:
            continue
        output = chunks_dir / entry["output"]
        if not output.exists():
            report["missing_outputs"].append(entry["id"])
            continue

        # `read_reply`, not `read_worksheet`: the second drops the transport's own
        # verdict, and this is the only door a translation walks through. An
        # unclosed fence — the shape of a truncated answer — was reported by
        # `payload`, discarded here and merged, while `classify` read the same
        # reply and called it `invalid`. So `status` said the job was unfinished
        # and `merge` wrote it in anyway: the two sides of the grammar disagreeing
        # about one file, which is the thing worksheet.py exists to prevent.
        entries, transport = read_reply(output.read_text(encoding="utf-8"))
        expected = list(entry.get("unit_ids") or [])
        expected_set = set(expected)
        kinds = entry.get("unit_kinds") or {}
        if not kinds:
            report["unverified_kinds"].append(entry["id"])

        problems, rejected = validate_reply(entries, expected, kinds)
        problems = transport + problems

        # The manifest says which formula produced its digest, because the two
        # routes record different ones under the same key.
        recorded = str(entry.get("source_sha256") or "")
        form = recorded.partition(":")[0]
        if not recorded:
            report["unverified_freshness"].append(entry["id"])
        elif form == "page":
            # The page run checks this itself, at build time, against a digest
            # that also covers the page raster and the page geometry — richer
            # than anything recomputable here from block ids — and invalidates
            # the page when it moves. Re-deriving it here would mean copying a
            # formula this module cannot see, which is how the two came to
            # disagree in the first place.
            pass
        elif recorded != unit_fingerprint(book, entry.get("block_ids") or []):
            report["stale"].append(entry["id"])
            problems.append(
                "the source these units were cut from has changed since the "
                "worksheet was written, so this reply answers text the book no "
                "longer contains")

        answered = {item["id"]: item["text"] for item in entries
                    if item["id"] in expected_set}
        missing = [u for u in expected if not (answered.get(u) or "").strip()]
        notes_offered = {item["id"] for item in entries
                         if TRANSLATOR_NOTE.match(item["id"])}
        extra = sorted({item["id"] for item in entries}
                       - expected_set - notes_offered)
        blank = [u for u, v in answered.items() if not v.strip()]

        report["chunks_merged"] += 1
        if missing:
            report["missing_units"][entry["id"]] = missing
        if extra:
            report["unknown_units"][entry["id"]] = extra
        if blank:
            report["blank_units"][entry["id"]] = blank
        if rejected:
            problems.append(f"units not written: {sorted(rejected)}")
        if problems or missing or extra:
            report["malformed"][entry["id"]] = problems or ["incomplete reply"]
            # Contributes nothing, in either mode. Notably it consumes no
            # footnote number either, so a corrected reply gets the same one.
            continue

        # The graph is checked against the candidate translation, before any note
        # is allocated or any text is accepted. A reply whose notes do not resolve
        # contributes nothing and consumes no footnote number, so a corrected
        # reply still gets the same one.
        offered = {item["id"]: item["text"].strip() for item in entries
                   if item["id"] not in expected_set
                   and TRANSLATOR_NOTE.match(item["id"])
                   and item["text"].strip()}
        note_kinds = {item["id"]: item["kind"] for item in entries
                      if item["id"] not in expected_set
                      and TRANSLATOR_NOTE.match(item["id"])}
        graph = validate_note_graph(answered, offered, note_kinds)
        if graph:
            report["malformed"][entry["id"]] = graph
            continue

        chunk_notes, mapping, gone = adopt_translator_notes(
            book, entries, reply=entry["id"], expected=expected_set,
            allocated=new_notes)
        new_notes += chunk_notes
        retired |= gone
        # Every note this reply touched, reused ones included, so `_anchor_notes`
        # can move an anchor that followed its marker to another paragraph.
        touched.extend(
            note for note in book.get("footnotes", [])
            if note.get("id") in set(mapping.values()))
        if mapping:
            report.setdefault("translator_notes", {})[entry["id"]] = mapping
        accepted.update(_resolve_local_tokens(answered, mapping))

    report["coverage"] = segments.coverage_problems(known_segments, accepted)

    problems_found = bool(
        report["missing_outputs"] or report["missing_units"]
        or report["unknown_units"] or report["malformed"]
        or report["coverage"] or report["stale"]
    )
    report["ok"] = not problems_found

    # A truncated block is never acceptable, in either mode: writing part of a
    # paragraph over the whole of it loses the rest with nothing to recover from.
    if report["coverage"] or (strict and problems_found):
        accepted, new_notes, retired, touched = {}, [], set(), []

    if accepted or new_notes or retired:
        book["footnotes"] = [note for note in book.get("footnotes", [])
                             if note["id"] not in retired] + new_notes
        outcome = apply_units(book, segments.rejoin(accepted))
        report["units_applied"] = len(outcome["applied"])
        if outcome["unknown"]:
            # The manifest named a unit this book does not have. Nothing landed
            # for it, and counting the chunk as merged would be a lie.
            report["unresolved_units"] = sorted(outcome["unknown"])
            report["ok"] = False
        _anchor_notes(book, new_notes + touched)

        # Asking each chunk to introduce a name only where the worksheet says so
        # is a request, and parallel agents that cannot see each other all answer
        # the same way. This is the pass that settles it from the outside, once
        # every chunk is back, so the result does not depend on any of them
        # complying.
        if glossary_path is not None and Path(glossary_path).exists():
            report["first_mentions"] = gl.enforce_first_mentions(
                gl.load(Path(glossary_path)), book)

        if report["ok"] or not strict:
            ir.save_book(book, book_path)

    report["stats"] = book.get("stats", {})
    if not report["ok"] and strict:
        report["hint"] = (
            "Nothing was written. Re-run only the affected worksheets: every @@ "
            "header from the source worksheet must reappear exactly once, in "
            "the same order, with the same kind, with Persian text under it."
        )
    return report


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel merge", description=__doc__)
    parser.add_argument("--book", required=True)
    parser.add_argument("--chunks", required=True)
    parser.add_argument("--only", nargs="*", default=None,
                        help="merge just these chunk ids")
    parser.add_argument("--glossary", default=None,
                        help="enforce one first mention per locked name")
    parser.add_argument("--lenient", action="store_true",
                        help="apply every worksheet that validates and exit 0, "
                             "instead of all-or-nothing")
    args = parser.parse_args(argv)

    report = merge(Path(args.book), Path(args.chunks),
                   only=args.only, strict=not args.lenient,
                   glossary_path=Path(args.glossary) if args.glossary else None)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if (report["ok"] or args.lenient) else 1


if __name__ == "__main__":
    sys.exit(main())

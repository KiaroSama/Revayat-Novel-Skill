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
import bookwrite
import eligible
import reviewstate
import glossary as gl
import published
import segments
from chunk import source_fingerprint  # noqa: F401 (public re-export)
from worksheet import (  # noqa: F401  (this module's published surface)
    ESCAPED_HEADER, FENCE, HEADER, NOTE_KINDS, TRANSLATOR_NOTE,
    parse_worksheet, read_reply, read_worksheet, request_of,
    validate_note_graph, validate_reply, verdict,
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
            existing.setdefault("submitted", existing.get("text") or body)
            existing["target"] = body
            mapping[local_id] = existing["id"]
            continue
        note = ir.make_footnote(next_index, anchor_block="", text=body,
                               origin="translator")
        note["target"] = body
        note["submitted"] = body
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
    # Markup-aware: a worksheet's own example, a literal `[[fn:tr-example]]` in
    # backticks, was rewritten into a real allocated id the moment some reply
    # offered a body for that name.
    return {unit_id: ir.rewrite_footnote_refs(text, mapping)
            for unit_id, text in texts.items()}


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

    The map itself comes from `published.slots`, which is also what the review
    stages, the typography fixer and the delivery gate enumerate. This module
    used to build its own — body blocks, alt text, notes and running heads — and
    it was right about all four, which is exactly why nothing noticed that the
    title page was in none of them.
    """
    addressable = published.slots(book)

    def resolve(unit_id: str) -> tuple[dict[str, Any], str] | None:
        return addressable.get(unit_id)

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


@reviewstate.guarded
def merge(
    book_path: Path,
    chunks_dir: Path,
    *,
    only: list[str] | None = None,
    strict: bool = True,
    glossary_path: Path | None = None,
    revalidate_unbound: bool = False,
) -> dict[str, Any]:
    """Fold the selected worksheets in, or change nothing and say why.

    ``strict`` is all-or-nothing across the whole selected transaction. Without
    it, every worksheet that validates is applied and every worksheet with a
    problem contributes nothing — the reply is the unit of trust, so "land what
    is good" means "land the good replies", never "land the good half of a reply
    whose structure cannot be trusted". Either way ``ok`` reports honestly, and
    lenient is an exit code, not permission to corrupt.
    """
    # The digest of the file this merge is about to reason from. Everything below
    # is computed against this content, so committing over a *different* file
    # would discard whatever the other writer put there — silently, because the
    # book it wrote would be perfectly well-formed.
    before = bookwrite.file_digest(Path(book_path))
    book = ir.load_book(book_path)
    manifest = eligible.read_manifest(chunks_dir)
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

    if glossary_path is None and manifest.get("glossary"):
        glossary_path = eligible._book_and_glossary(chunks_dir, manifest)[1]
        if glossary_path is None:
            glossary_path = Path(manifest["glossary"])
    glossary_for_freshness = None
    if glossary_path is not None and Path(glossary_path).is_file():
        try:
            glossary_for_freshness = gl.load(Path(glossary_path))
        except (OSError, ValueError, UnicodeError):
            pass
    proofs = {proof["id"]: proof for proof in eligible.every(
        chunks_dir, manifest, book_path=book_path, book=book,
        glossary=glossary_for_freshness, glossary_path=glossary_path,
        revalidate_unbound=revalidate_unbound)}
    if glossary_path is not None and glossary_for_freshness is None:
        report.update({"ok": False, "refused": "named-glossary-unusable",
                       "detail": "the named glossary is missing or unreadable; restore it or rebuild"})
        return report

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
        expected = list(entry.get("unit_ids") or [])
        kinds = entry.get("unit_kinds") or {}
        proof = proofs[entry["id"]]

        # Zero-unit completion, defined once in `worksheet.verdict`: a job that
        # asks for nothing — an image-only page, a blank verso — is finished the
        # moment it is cut, and no reply file is expected. Demanding one here
        # while `status` counted it translated left a run with nothing to offer
        # and a merge that could never pass.
        if not expected and proof["usable"]:
            report["chunks_merged"] += 1
            continue
        if proof["state"] == "missing":
            report["missing_outputs"].append(entry["id"])
            continue

        # `read_reply`, not `read_worksheet`: the second drops the transport's own
        # verdict, and this is the only door a translation walks through. An
        # unclosed fence — the shape of a truncated answer — was reported by
        # `payload`, discarded here and merged, while `classify` read the same
        # reply and called it `invalid`. So `status` said the job was unfinished
        # and `merge` wrote it in anyway: the two sides of the grammar disagreeing
        # about one file, which is the thing worksheet.py exists to prevent.
        if not kinds:
            report["unverified_kinds"].append(entry["id"])

        # One verdict, the same one `status` and `next` read. Transport, ordered
        # ids and kinds, completeness and the note graph are all decided in
        # `worksheet.verdict`; freshness is appended below because it is a
        # question about the book, which that module cannot see.
        say = proof.get("transport", verdict(None, expected, kinds))
        entries = say["entries"]
        expected_set = set(expected)
        problems = list(say["problems"])

        if not proof["usable"]:
            problems.append(proof["detail"] or f"reply state {proof['state']}")
        if proof["state"] == "unverified":
            report["unverified_freshness"].append(entry["id"])
        if proof["state"] == "stale-source":
            report["stale"].append(entry["id"])
        if proof.get("revalidated") and proof["usable"]:
            report.setdefault("revalidated", []).append(entry["id"])
        if proof.get("candidate_problems"):
            report["refused"] = "invalid-book"
            report["detail"] = "; ".join(proof["candidate_problems"])
            report.setdefault("invalid_ir", []).extend(proof["candidate_problems"])

        answered = say["answered"]
        missing, extra = say["missing"], say["extra"]

        report["chunks_merged"] += 1
        if missing:
            report["missing_units"][entry["id"]] = missing
        if extra:
            report["unknown_units"][entry["id"]] = extra
        if say["blank"]:
            report["blank_units"][entry["id"]] = say["blank"]
        if say["rejected"]:
            problems.append(f"units not written: {sorted(say['rejected'])}")
        if problems or missing or extra:
            report["malformed"][entry["id"]] = problems or ["incomplete reply"]
            # Contributes nothing, in either mode. Notably it consumes no
            # footnote number either, so a corrected reply gets the same one.
            continue

        # The note graph was checked inside the verdict, against the candidate
        # translation and before any note is allocated — so a reply whose notes do
        # not resolve has already been refused above, consuming no footnote
        # number, and `status` refused it for the same reason.
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

        # The candidate book, before anything is written. `validate_book` already
        # refuses a reference to a footnote the book does not have and a duplicate
        # block id — and merge wrote books it rejected, because nothing asked it.
        # A reply that would produce an invalid book is a malformed reply, whatever
        # its own structure looked like.
        invalid = ir.validate_book(book)
        if invalid:
            report["invalid_ir"] = invalid[:20]
            report["ok"] = False

        # Asking each chunk to introduce a name only where the worksheet says so
        # is a request, and parallel agents that cannot see each other all answer
        # the same way. This is the pass that settles it from the outside, once
        # every chunk is back, so the result does not depend on any of them
        # complying.
        if glossary_path is not None:
            report["first_mentions"] = gl.enforce_first_mentions(
                glossary_for_freshness, book)

        # Lenient means "land the replies that validate", never "write a book the
        # validator rejects": an invalid IR blocks the write in both modes.
        if report["ok"] or (not strict and not invalid):
            try:
                bookwrite.replace(book_path, book, actor="merge", expect=before)
            except bookwrite.Refused as stopped:
                report["ok"] = False
                report["refused"] = stopped.reason
                report["detail"] = stopped.detail

    report["stats"] = book.get("stats", {})
    if not report["ok"] and strict:
        report["hint"] = (
            "Nothing was written. Re-run only the affected worksheets: every @@ "
            "header from the source worksheet must reappear exactly once, in "
            "the same order, with the same kind, with Persian text under it."
        )
    return report


@reviewstate.cli
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
    parser.add_argument("--revalidate-unbound", action="store_true",
                        help="accept a reply that carries no request line, on the "
                             "strength of the source digest alone. For replies "
                             "written before worksheets carried one; a reply whose "
                             "token disagrees is never accepted this way")
    args = parser.parse_args(argv)

    report = merge(Path(args.book), Path(args.chunks),
                   only=args.only, strict=not args.lenient,
                   glossary_path=Path(args.glossary) if args.glossary else None,
                   revalidate_unbound=args.revalidate_unbound)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if (report["ok"] or args.lenient) else 1


if __name__ == "__main__":
    sys.exit(main())

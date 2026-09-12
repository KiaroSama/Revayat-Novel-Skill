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
import re
import sys
from pathlib import Path
from typing import Any

import bookir as ir
import glossary as gl
import segments
from chunk import HEADER, TRANSLATOR_NOTE, unit_fingerprint

#: A fenced reply. Models wrap their output in one, and the closing fence is not
#: part of the novel. Taking the slice *between* fences also discards whatever
#: pleasantry follows the closing one, which is the other half of the same
#: problem: trailing prose lands inside the last unit.
FENCE = re.compile(r"^\s*(?:```|~~~)\s*[A-Za-z0-9_+-]*\s*$")

#: :func:`chunk.escape_payload`'s mark on a source line that would otherwise
#: parse as a header, removed on the way back in.
ESCAPED_HEADER = re.compile(r"^(\s*)\\(@@\s)")


# --------------------------------------------------------------------------- #
# Reading a reply
# --------------------------------------------------------------------------- #

def _payload(text: str) -> list[str]:
    """The lines that are the answer, with any outer code fence removed."""
    lines = text.splitlines()
    opening = next((i for i, line in enumerate(lines) if FENCE.match(line)), None)
    if opening is None:
        return lines
    closing = next((i for i in range(opening + 1, len(lines))
                    if FENCE.match(lines[i])), len(lines))
    return lines[opening + 1:closing]


def read_worksheet(text: str) -> list[dict[str, str]]:
    """Ordered ``{id, kind, text}``, one entry per ``@@`` header.

    Ordered and typed on purpose. Every check worth making — answered twice,
    answered as the wrong kind, answered out of order — is a question about the
    sequence of headers, and a dict keyed by id cannot answer any of them: by
    the time the dict exists the duplicate has already overwritten its twin and
    the order is gone.
    """
    entries: list[dict[str, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        if entries:
            entries[-1]["text"] = "\n".join(buffer).strip()

    for line in _payload(text):
        match = HEADER.match(line.strip())
        if match:
            flush()
            entries.append({"id": match.group("id"),
                            "kind": match.group("kind"), "text": ""})
            buffer = []
            continue
        if not entries:
            # Scaffolding the model echoed before the first header. It cannot
            # corrupt a unit, so it is dropped rather than refused — a reply
            # that restates the instructions is still a good reply.
            continue
        stripped = line.strip()
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        buffer.append(ESCAPED_HEADER.sub(r"\1\2", line))
    flush()
    return entries


def parse_worksheet(text: str) -> dict[str, str]:
    """``unit id -> translated text``.

    The flat view, for callers that only want the mapping. A duplicate id
    resolves to its last occurrence here; :func:`merge` reads the ordered form
    and refuses it by name instead.
    """
    return {entry["id"]: entry["text"] for entry in read_worksheet(text)}


def validate_reply(
    entries: list[dict[str, str]],
    expected: list[str],
    kinds: dict[str, str],
) -> tuple[list[str], set[str]]:
    """``(problems, unit ids that must not be written)``.

    ``kinds`` may be empty for a manifest written before kinds were recorded;
    the check is then skipped and the caller says so in its report rather than
    inventing a kind to compare against.
    """
    expected_set = set(expected)
    problems: list[str] = []
    rejected: set[str] = set()
    seen: list[str] = []

    for item in entries:
        if item["id"] in seen:
            problems.append(
                f"{item['id']}: answered more than once — which of the two "
                f"translations is the paragraph cannot be guessed")
            rejected.add(item["id"])
        seen.append(item["id"])

        wanted = kinds.get(item["id"])
        if wanted and item["kind"] != wanted:
            problems.append(
                f"{item['id']}: answered as {item['kind']!r} but asked as "
                f"{wanted!r}")
            rejected.add(item["id"])

    first_seen = list(dict.fromkeys(seen))
    answered_order = [i for i in first_seen if i in expected_set]
    wanted_order = [i for i in expected if i in set(answered_order)]
    if answered_order != wanted_order:
        problems.append(
            f"headers came back in a different order: {answered_order} "
            f"against the {wanted_order} the worksheet asked for")
        rejected |= set(seen)

    return problems, rejected


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


def _anchor_notes(book: dict[str, Any], notes: list[dict[str, Any]]) -> None:
    """Bind each new note to the block whose translation actually refers to it."""
    wanted = {note["id"]: note for note in notes if not note.get("anchor_block")}
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

def apply_units(book: dict[str, Any], units: dict[str, str]) -> dict[str, Any]:
    """Write translations onto the book. Returns a report of what landed.

    ``unknown`` is load-bearing: a manifest id that resolves to nothing in this
    book is not a merge, and the caller must not count the chunk as applied.
    """
    blocks = ir.blocks_by_id(book)
    notes = {note["id"]: note for note in book.get("footnotes", [])}
    running = ir.running_heads(book)
    applied, unknown, blank = [], [], []

    for unit_id, value in units.items():
        text = value.strip()
        if not text:
            blank.append(unit_id)
            continue

        if unit_id.endswith("#alt"):
            block = blocks.get(unit_id[: -len("#alt")])
            if block is None or block["type"] != "image":
                unknown.append(unit_id)
                continue
            block["target_alt"] = text
        elif unit_id in notes:
            notes[unit_id]["target"] = text
        elif unit_id in running:
            running[unit_id]["target"] = text
        elif unit_id in blocks and blocks[unit_id]["type"] in ir.TEXT_TYPES:
            blocks[unit_id]["target"] = text
        else:
            unknown.append(unit_id)
            continue
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

    for entry in chunks:
        if only and entry["id"] not in only:
            continue
        output = chunks_dir / entry["output"]
        if not output.exists():
            report["missing_outputs"].append(entry["id"])
            continue

        entries = read_worksheet(output.read_text(encoding="utf-8"))
        expected = list(entry.get("unit_ids") or [])
        expected_set = set(expected)
        kinds = entry.get("unit_kinds") or {}
        if not kinds:
            report["unverified_kinds"].append(entry["id"])

        problems, rejected = validate_reply(entries, expected, kinds)

        recorded = entry.get("source_sha256")
        if not recorded:
            report["unverified_freshness"].append(entry["id"])
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

        chunk_notes, mapping, gone = adopt_translator_notes(
            book, entries, reply=entry["id"], expected=expected_set,
            allocated=new_notes)
        new_notes += chunk_notes
        retired |= gone
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
        accepted, new_notes, retired = {}, [], set()

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
        _anchor_notes(book, new_notes)

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

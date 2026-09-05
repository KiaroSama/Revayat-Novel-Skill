"""Stage 4 — fold translated worksheets back into ``book.json``.

Every unit the worksheet asked for must come back with the same id. That is the
whole point of the ``@@`` protocol: a dropped paragraph, a merged pair of
paragraphs or an invented id is a hard, named error here rather than a quietly
shorter book discovered after the DOCX is built.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import bookir as ir
from chunk import HEADER, TRANSLATOR_NOTE


def adopt_translator_notes(
    book: dict[str, Any],
    units: dict[str, str],
    expected: set[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Turn the translator's own ``tr-NN`` notes into real book footnotes.

    The translation policy invites a translator to add a note for a cultural
    reference or a pun that cannot survive. Those ids cannot exist in the
    worksheet manifest — the translator invents them while translating — so
    they are allocated a book-wide id here and the inline token is rewritten to
    match. Numbering is per chunk, so this must be applied one chunk at a time.

    Returns ``(units_without_notes, tr_id -> fn_id)``.
    """
    existing = {note["id"] for note in book.setdefault("footnotes", [])}
    next_index = 1 + max(
        (int(note_id[2:]) for note_id in existing if note_id.startswith("fn")),
        default=0,
    )

    remaining: dict[str, str] = {}
    mapping: dict[str, str] = {}
    for unit_id, text in units.items():
        if unit_id in expected or not TRANSLATOR_NOTE.match(unit_id):
            remaining[unit_id] = text
            continue
        body = text.strip()
        if not body:
            continue
        note = ir.make_footnote(next_index, anchor_block="", text=body,
                                origin="translator")
        note["target"] = body
        book["footnotes"].append(note)
        mapping[unit_id] = note["id"]
        next_index += 1
    return remaining, mapping


def _rewrite_tokens(book: dict[str, Any], block_ids: list[str],
                    mapping: dict[str, str]) -> None:
    """Point the translator's inline tokens at their allocated footnote ids."""
    if not mapping:
        return
    blocks = ir.blocks_by_id(book)
    for block_id in block_ids:
        block = blocks.get(block_id)
        if block is None or not (block.get("target") or ""):
            continue
        block["target"] = ir.ANY_FOOTNOTE_TOKEN.sub(
            lambda match: f"[[fn:{mapping.get(match.group(1), match.group(1))}]]",
            block["target"],
        )
        for note in book.get("footnotes", []):
            if not note["anchor_block"] and note["id"] in ir.footnote_refs(block["target"]):
                note["anchor_block"] = block_id


def parse_worksheet(text: str) -> dict[str, str]:
    """``unit id -> translated text`` from a filled-in worksheet."""
    units: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current is not None:
            units[current] = "\n".join(buffer).strip()

    for line in text.splitlines():
        match = HEADER.match(line.strip())
        if match:
            flush()
            current = match.group("id")
            buffer = []
            continue
        if current is not None:
            # Worksheet scaffolding the model may have echoed back.
            stripped = line.strip()
            if stripped.startswith("<!--") and stripped.endswith("-->"):
                continue
            buffer.append(line)
    flush()
    return units


def apply_units(book: dict[str, Any], units: dict[str, str]) -> dict[str, Any]:
    """Write translations onto the book. Returns a report of what landed."""
    blocks = ir.blocks_by_id(book)
    notes = {note["id"]: note for note in book.get("footnotes", [])}
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
) -> dict[str, Any]:
    book = ir.load_book(book_path)
    manifest = json.loads((chunks_dir / "manifest.json").read_text(encoding="utf-8"))

    report: dict[str, Any] = {
        "chunks_merged": 0,
        "units_applied": 0,
        "missing_outputs": [],
        "missing_units": {},
        "unknown_units": {},
        "blank_units": {},
    }

    for entry in manifest["chunks"]:
        if only and entry["id"] not in only:
            continue
        output = chunks_dir / entry["output"]
        if not output.exists():
            report["missing_outputs"].append(entry["id"])
            continue

        units = parse_worksheet(output.read_text(encoding="utf-8"))
        expected = set(entry["unit_ids"])
        units, note_ids = adopt_translator_notes(book, units, expected)
        missing = [u for u in entry["unit_ids"] if u not in units or not units[u].strip()]
        extra = sorted(set(units) - expected)

        outcome = apply_units(book, {k: v for k, v in units.items() if k in expected})
        _rewrite_tokens(book, entry["block_ids"], note_ids)
        report["chunks_merged"] += 1
        report["units_applied"] += len(outcome["applied"])
        if note_ids:
            report.setdefault("translator_notes", {})[entry["id"]] = note_ids
        if missing:
            report["missing_units"][entry["id"]] = missing
        if extra:
            report["unknown_units"][entry["id"]] = extra
        if outcome["blank"]:
            report["blank_units"][entry["id"]] = outcome["blank"]

    ir.save_book(book, book_path)
    report["stats"] = book["stats"]

    problems = (
        report["missing_outputs"]
        or report["missing_units"]
        or report["unknown_units"]
    )
    report["ok"] = not problems
    if problems and strict:
        report["hint"] = (
            "Re-run only the affected worksheets: every @@ header from the "
            "source worksheet must reappear exactly once in the output, in "
            "the same order, with Persian text under it."
        )
    return report


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat merge", description=__doc__)
    parser.add_argument("--book", required=True)
    parser.add_argument("--chunks", required=True)
    parser.add_argument("--only", nargs="*", default=None,
                        help="merge just these chunk ids")
    parser.add_argument("--lenient", action="store_true",
                        help="exit 0 even when units are missing")
    args = parser.parse_args(argv)

    report = merge(Path(args.book), Path(args.chunks),
                   only=args.only, strict=not args.lenient)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if (report["ok"] or args.lenient) else 1


if __name__ == "__main__":
    sys.exit(main())

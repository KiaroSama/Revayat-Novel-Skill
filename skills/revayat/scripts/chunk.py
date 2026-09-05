"""Stage 2 — cut the book into chapter-aware translation worksheets.

A worksheet is plain text with one ``@@ <id> <kind>`` header per translatable
unit. That shape is deliberate: models are far more reliable editing delimited
prose than editing JSON, and a missing or invented id is caught deterministically
at merge time instead of silently corrupting the book.

Chunks break on chapter headings first and on a character budget second, so a
translator almost always sees a whole scene rather than a sentence cut in half.
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

#: Target source characters per chunk. Small enough for one focused context,
#: large enough that a scene is not shredded across three agents.
DEFAULT_BUDGET = 6000
#: A chunk may overshoot the budget by this much to finish the current scene.
OVERSHOOT = 0.35
#: Characters of neighbouring source shown for pronoun/entity resolution.
CONTEXT_CHARS = 450

#: The worksheet unit header. The id character set has to cover every shape an
#: id can take: ``b00042`` blocks, ``b00075#alt`` image captions, ``fn0007``
#: source footnotes and ``tr-01`` notes the translator adds. Omitting the
#: hyphen did not reject a translator's note — it stopped the header being
#: recognised at all, so the note's body was silently swallowed into the
#: previous paragraph and merge still reported success.
HEADER = re.compile(r"^@@\s+(?P<id>[A-Za-z0-9_#-]+)\s+(?P<kind>[a-z0-9]+)\s*$")

#: A footnote the translator introduced, numbered per chunk. Merge allocates it
#: a real book-wide id.
TRANSLATOR_NOTE = re.compile(r"^tr-[A-Za-z0-9_-]+$")

_KIND_BY_TYPE = {
    "paragraph": "para",
    "blockquote": "quote",
    "listitem": "list",
    "caption": "caption",
    "verse": "verse",
}


def kind_of(block: dict[str, Any]) -> str:
    if block["type"] == "heading":
        return f"heading{int(block.get('level', 1))}"
    return _KIND_BY_TYPE.get(block["type"], block["type"])


# --------------------------------------------------------------------------- #
# Splitting
# --------------------------------------------------------------------------- #

def split_blocks(book: dict[str, Any], budget: int = DEFAULT_BUDGET) -> list[list[str]]:
    """Group block ids into chunks, preferring chapter boundaries."""
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    hard_cap = int(budget * (1 + OVERSHOOT))

    for block in book.get("blocks", []):
        text = block.get("text") or ""
        cost = len(text)

        starts_chapter = ir.chapter_key(block)
        if current and (starts_chapter or size + cost > hard_cap):
            # Only break early on a chapter when the chunk has real content;
            # a title page followed immediately by "Chapter One" should not
            # produce a two-line chunk.
            if starts_chapter and size < budget * 0.25 and not _has_prose(book, current):
                pass
            else:
                chunks.append(current)
                current, size = [], 0

        current.append(block["id"])
        size += cost

        if size >= budget and not starts_chapter:
            # Prefer to close on a paragraph boundary rather than mid-scene.
            chunks.append(current)
            current, size = [], 0

    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def _has_prose(book: dict[str, Any], ids: list[str]) -> bool:
    lookup = ir.blocks_by_id(book)
    return any(
        lookup[i]["type"] in ("paragraph", "blockquote", "verse")
        and len(lookup[i].get("text") or "") > 120
        for i in ids if i in lookup
    )


# --------------------------------------------------------------------------- #
# Worksheet rendering
# --------------------------------------------------------------------------- #

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
    return units


def render_worksheet(
    book: dict[str, Any],
    glossary: dict[str, Any],
    ids: list[str],
    *,
    index: int,
    total: int,
    previous_tail: str,
    next_head: str,
) -> str:
    lookup = ir.blocks_by_id(book)
    units = translatable_units(book, ids)
    source_blob = "\n".join(text for _, _, text in units)

    lines: list[str] = [
        f"<!-- revayat worksheet {index:04d}/{total:04d} "
        f"| units {len(units)} | {len(source_blob)} source chars -->",
        "<!-- Reply with the same @@ headers, in the same order, Persian text "
        "underneath each. Do not add, drop, merge or reorder headers. -->",
        "",
    ]

    table = gl.render_term_table(
        gl.entries_for_text(glossary, source_blob), glossary.get("policy", {}),
        block_ids=ids,
    )
    if table:
        lines += ["## Names — use these exact forms", "", table, ""]

    cards = gl.render_voice_cards(glossary, source_blob)
    if cards:
        lines += ["## Character voices", "", cards, ""]

    if previous_tail or next_head:
        lines += ["## Surrounding text — context only, do not translate or output", ""]
        if previous_tail:
            lines += [f"Before: …{previous_tail}", ""]
        if next_head:
            lines += [f"After: {next_head}…", ""]

    lines += ["## Translate", ""]
    for unit_id, kind, text in units:
        block = lookup.get(unit_id.split("#")[0])
        if block is not None and block["type"] == "image":
            lines.append(
                f"<!-- illustration {block['asset']} is anchored here; the picture "
                f"itself needs nothing from you. The alt header below is its "
                f"caption text and does need translating. -->"
            )
        lines.append(f"@@ {unit_id} {kind}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def neighbour_context(book: dict[str, Any], chunks: list[list[str]], index: int) -> tuple[str, str]:
    def blob(ids: list[str]) -> str:
        lookup = ir.blocks_by_id(book)
        return " ".join(
            ir.plain_text(lookup[i].get("text") or "")
            for i in ids if i in lookup and lookup[i]["type"] in ir.TEXT_TYPES
        ).strip()

    previous_tail = blob(chunks[index - 1])[-CONTEXT_CHARS:] if index > 0 else ""
    next_head = blob(chunks[index + 1])[:CONTEXT_CHARS] if index + 1 < len(chunks) else ""
    return previous_tail, next_head


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def build(
    book_path: Path,
    out_dir: Path,
    *,
    glossary_path: Path | None,
    budget: int = DEFAULT_BUDGET,
) -> dict[str, Any]:
    book = ir.load_book(book_path)
    glossary = gl.load(glossary_path) if glossary_path else gl.new_glossary()
    chunks = split_blocks(book, budget)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "schema": "revayat/chunks@1",
        "book": str(book_path),
        "book_sha256": book["source"].get("sha256", ""),
        "budget": budget,
        "chunks": [],
    }

    for index, ids in enumerate(chunks, start=1):
        previous_tail, next_head = neighbour_context(book, chunks, index - 1)
        worksheet = render_worksheet(
            book, glossary, ids,
            index=index, total=len(chunks),
            previous_tail=previous_tail, next_head=next_head,
        )
        name = f"chunk{index:04d}.md"
        ir.write_text(out_dir / name, worksheet)

        units = translatable_units(book, ids)
        manifest["chunks"].append({
            "id": f"chunk{index:04d}",
            "file": name,
            "output": f"out_chunk{index:04d}.md",
            "block_ids": ids,
            "unit_ids": [unit_id for unit_id, _, _ in units],
            "units": len(units),
            "source_chars": sum(len(text) for _, _, text in units),
            # Identity of the source this worksheet was built from, so a later
            # run can tell "already translated" from "source changed".
            "source_sha256": ir.sha256_bytes(
                "\n".join(f"{i}\x00{t}" for i, _, t in units).encode("utf-8")
            ),
        })

    ir.write_text(out_dir / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    return manifest


def status(out_dir: Path) -> dict[str, Any]:
    """Which worksheets still need translating — the resume view."""
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    pending, done, empty = [], [], []
    for entry in manifest["chunks"]:
        output = out_dir / entry["output"]
        if not output.exists():
            pending.append(entry["id"])
        elif not output.read_text(encoding="utf-8").strip():
            empty.append(entry["id"])
        else:
            done.append(entry["id"])
    return {
        "total": len(manifest["chunks"]),
        "translated": len(done),
        "pending": pending,
        "empty": empty,
        "next": pending[0] if pending else None,
    }


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat chunk")
    sub = parser.add_subparsers(dest="action", required=True)

    p_build = sub.add_parser("build", help="write worksheets and a manifest")
    p_build.add_argument("--book", required=True)
    p_build.add_argument("--out", required=True, help="chunks directory")
    p_build.add_argument("--glossary", default=None)
    p_build.add_argument("--budget", type=int, default=DEFAULT_BUDGET)

    p_status = sub.add_parser("status", help="report which chunks still need work")
    p_status.add_argument("--chunks", required=True)

    args = parser.parse_args(argv)

    if args.action == "build":
        manifest = build(
            Path(args.book), Path(args.out),
            glossary_path=Path(args.glossary) if args.glossary else None,
            budget=args.budget,
        )
        print(json.dumps({
            "chunks": len(manifest["chunks"]),
            "units": sum(c["units"] for c in manifest["chunks"]),
            "source_chars": sum(c["source_chars"] for c in manifest["chunks"]),
            "dir": args.out,
        }, ensure_ascii=False, indent=1))
        return 0

    print(json.dumps(status(Path(args.chunks)), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Stage 2b — one translation task per source page.

A novel must never be handed to one model context, and a worksheet cut by
character budget alone breaks wherever the budget happens to run out. A page is
a boundary the book already has: it is stable between runs, it is the unit a
reviewer looks at, and it is the only unit a *rendered* page can be compared
against. This module turns ``book.json`` into one independent, resumable job
per source page.

The defect the whole feature exists to prevent is a block translated twice.
``read_pdf._merge_split_paragraphs`` already rejoins a paragraph that a page
break cut in half, into a single block whose ``page`` is the page it *started*
on — so ownership follows the block's own page number and a spanning paragraph
belongs to exactly one job. The page it ran onto sees it only inside a bounded
neighbour-context section headed "do not translate".

Continuity between pages is carried as compact state, not as more prose: the
glossary rows that apply here, the voices that speak here, the words OCR was
not sure of here. All of it bounded, because an unbounded "just add context"
is how a page job silently becomes a book job again.

The manifest's job list is called ``chunks`` on purpose: a page job *is* a
chunk of exactly one page, and ``merge.py`` reads that key, so a page run folds
back into the book with the same command a chunk run does.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import bookir as ir
import chunk as chunking
import glossary as gl
import runstate

SCHEMA = "revayat-novel/pagerun@1"

#: Rendered worksheet characters one page job may carry. A page of a trade
#: novel is ~2000 source characters; the rest is the glossary, the voices and
#: the neighbour context. A job over this is *reported*, never trimmed — a
#: silently shortened page is a page translated wrong, which is exactly the
#: class of failure the per-page split exists to make visible.
DEFAULT_BUDGET = 12000

#: Hard maximum characters of adjacent-page prose shown per side. Neighbour
#: text is for resolving a pronoun, not for translating; without a ceiling the
#: two neighbours of a dense page can outweigh the page itself.
NEIGHBOUR_CHARS = 600

#: Ceilings on the injected continuity state. Every one of these grows with the
#: book rather than with the page, so each needs its own bound.
MAX_TERMS = 24
MAX_VOICE_CARDS = 6
MAX_OCR_NOTES = 8
MAX_LOW_WORDS = 6


# --------------------------------------------------------------------------- #
# Ownership
# --------------------------------------------------------------------------- #

def page_of(block: dict[str, Any], fallback: int) -> int:
    """The page a block belongs to, or the last page seen.

    EPUB and DOCX have no pages at all, and a damaged PDF read can leave the
    key off one block. Carrying the previous page forward keeps the partition
    total and deterministic instead of inventing a page 0 that owns strays.
    """
    page = block.get("page")
    try:
        number = int(page)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def owners(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Partition every block, image and footnote across pages, exactly once.

    Returns one record per page in ascending page order. The invariant this
    function exists to hold: the union of every ``block_ids`` is every block in
    the book, and no id appears in two of them.
    """
    by_page: dict[int, dict[str, Any]] = {}
    owner_of: dict[str, int] = {}
    current = 1

    for block in book.get("blocks", []):
        current = page_of(block, current)
        job = by_page.get(current)
        if job is None:
            job = by_page[current] = {
                "page": current, "block_ids": [], "image_ids": [],
                "footnote_ids": [],
            }
        job["block_ids"].append(block["id"])
        owner_of[block["id"]] = current
        if block["type"] == "image":
            job["image_ids"].append(block["id"])

    # A footnote belongs to the page where the reader meets its marker, which
    # is not always the page ``anchor_block`` names — a note anchored to a
    # block on one page can be referenced from a block on another, and the
    # reference is the half a reader actually sees. First claim in book order
    # wins, so a note referenced twice is still owned once.
    note_page: dict[str, int] = {}
    for block in book.get("blocks", []):
        for ref in ir.footnote_refs(block.get("text") or ""):
            note_page.setdefault(ref, owner_of[block["id"]])
    for note in book.get("footnotes", []):
        anchor = note.get("anchor_block")
        if anchor in owner_of:
            note_page.setdefault(note["id"], owner_of[anchor])

    known = {note["id"] for note in book.get("footnotes", [])}
    for note_id, page in note_page.items():
        if note_id in known and page in by_page:
            by_page[page]["footnote_ids"].append(note_id)

    return [by_page[page] for page in sorted(by_page)]


def _union(boxes: list[list[float]]) -> list[float]:
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[2] for b in boxes)
    bottom = max(b[3] for b in boxes)
    return [round(v, 2) for v in (left, top, right, bottom)]


def geometry(book: dict[str, Any], block_ids: list[str],
             lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Page setup plus the box the page's own content actually occupies.

    The setup is the book's; ``text_bbox`` is measured, and it is what the
    render check compares the translated page against.
    """
    setup: dict[str, Any] = dict(book.get("page") or ir.default_page_setup())
    boxes = [lookup[i]["bbox"] for i in block_ids
             if i in lookup and lookup[i].get("bbox")]
    if boxes:
        setup["text_bbox"] = _union(boxes)
    return setup


def ocr_state(block_ids: list[str],
              lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """OCR provenance for the page, and the words it was not sure of.

    Empty when the page came from a text layer: a page with nothing to report
    should not carry an "OCR: fine" section into every worksheet.
    """
    grades: Counter[str] = Counter()
    worst: float | None = None
    uncertain: list[dict[str, Any]] = []

    for block_id in block_ids:
        evidence = (lookup.get(block_id) or {}).get("ocr")
        if not isinstance(evidence, dict):
            continue
        grades[str(evidence.get("grade", "unknown"))] += 1
        confidence = evidence.get("confidence")
        if isinstance(confidence, (int, float)):
            worst = confidence if worst is None else min(worst, float(confidence))
        words = evidence.get("low_words") or []
        if evidence.get("grade") in ("low", "medium") and words:
            uncertain.append({"block": block_id,
                              "words": list(words)[:MAX_LOW_WORDS]})

    if not grades:
        return {}
    return {
        "by_grade": dict(sorted(grades.items())),
        "min_confidence": worst,
        "uncertain": uncertain[:MAX_OCR_NOTES],
        "truncated": max(0, len(uncertain) - MAX_OCR_NOTES),
    }


# --------------------------------------------------------------------------- #
# Worksheet
# --------------------------------------------------------------------------- #

def neighbour_context(book: dict[str, Any], jobs: list[dict[str, Any]],
                      index: int, limit: int = NEIGHBOUR_CHARS) -> tuple[str, str]:
    """``(before, after)`` prose from the adjacent pages, hard-capped.

    Read-only by contract: whatever comes back is rendered under a header that
    says not to translate it, and the blocks it came from are owned by *their*
    page, never by this one.
    """
    lookup = ir.blocks_by_id(book)

    def blob(ids: list[str]) -> str:
        return " ".join(
            ir.plain_text(lookup[i].get("text") or "")
            for i in ids
            if i in lookup and lookup[i]["type"] in ir.TEXT_TYPES
        ).strip()

    before = blob(jobs[index - 1]["block_ids"])[-limit:] if index > 0 else ""
    after = (blob(jobs[index + 1]["block_ids"])[:limit]
             if index + 1 < len(jobs) else "")
    return before, after


def render_worksheet(
    book: dict[str, Any],
    glossary: dict[str, Any],
    job: dict[str, Any],
    units: list[tuple[str, str, str]],
    *,
    total: int,
    previous_tail: str,
    next_head: str,
) -> str:
    lookup = ir.blocks_by_id(book)
    source_blob = "\n".join(text for _, _, text in units)
    page = job["page"]

    lines: list[str] = [
        f"<!-- revayat-novel page worksheet {page:04d}/{total:04d} "
        f"| page {page} | units {len(units)} | {len(source_blob)} source chars -->",
        "<!-- Reply with the same @@ headers, in the same order, Persian text "
        "underneath each. Do not add, drop, merge or reorder headers. -->",
        "",
    ]

    entries = gl.entries_for_text(glossary, source_blob)[:MAX_TERMS]
    table = gl.render_term_table(entries, glossary.get("policy", {}),
                                 block_ids=job["block_ids"])
    if table:
        lines += ["## Names — use these exact forms", "", table, ""]

    cards = gl.render_voice_cards(glossary, source_blob).splitlines()[:MAX_VOICE_CARDS]
    if cards:
        lines += ["## Character voices", "", *cards, ""]

    uncertain = (job.get("ocr") or {}).get("uncertain") or []
    if uncertain:
        lines += ["## Read poorly by OCR — translate the sense, flag the word, "
                  "do not invent one", ""]
        lines += [f"- {note['block']}: " + "، ".join(f"«{w}»" for w in note["words"])
                  for note in uncertain]
        lines.append("")

    if previous_tail or next_head:
        lines += ["## Surrounding pages — context only, do not translate or output",
                  ""]
        if previous_tail:
            lines += [f"Before (page {page - 1}): …{previous_tail}", ""]
        if next_head:
            lines += [f"After (page {page + 1}): {next_head}…", ""]

    lines += [f"## Translate — page {page}", ""]
    for unit_id, kind, text in units:
        block = lookup.get(unit_id.split("#")[0])
        if block is not None and block["type"] == "image":
            lines.append(
                f"<!-- illustration {block['asset']} is anchored here; the "
                f"picture itself needs nothing from you. The alt header below "
                f"is its caption text and does need translating. -->"
            )
        lines.append(f"@@ {unit_id} {kind}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def source_digest(units: list[tuple[str, str, str]]) -> str:
    """Identity of the source side of one page.

    The source only, for the same reason ``chunk.source_digest`` is: merge
    writes the Persian back into the same book, and a page whose translation
    landed must not read as a page whose source moved.
    """
    return ir.sha256_bytes(
        "\n".join(f"{unit_id}\x00{text}" for unit_id, _, text in units)
        .encode("utf-8")
    )


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def build(
    book_path: Path,
    out_dir: Path,
    *,
    glossary_path: Path | None = None,
    budget: int = DEFAULT_BUDGET,
    neighbour_chars: int = NEIGHBOUR_CHARS,
) -> dict[str, Any]:
    """Write one worksheet per source page, plus the manifest.

    Rebuilding is safe at any point: worksheet bodies are a pure function of
    the book, translated ``out_page*.md`` replies are never touched, and only
    a page whose *own* source moved has its recorded progress reset.
    """
    book = ir.load_book(book_path)
    glossary = gl.load(glossary_path) if glossary_path else gl.new_glossary()
    lookup = ir.blocks_by_id(book)
    state = runstate.RunState(out_dir.parent)

    jobs = owners(book)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "book": str(book_path),
        "book_sha256": book["source"].get("sha256", ""),
        "glossary": str(glossary_path) if glossary_path else "",
        "budget": budget,
        "neighbour_chars": neighbour_chars,
        "pages": len(jobs),
        "chunks": [],
        "over_budget": [],
        "invalidated": [],
    }

    for index, job in enumerate(jobs):
        page = job["page"]
        # ``translatable_units`` pulls in every footnote the page's blocks
        # refer to, and two pages can refer to the same note. Only the page
        # that owns it may put it on a worksheet — otherwise the note goes out
        # to be translated twice and merge applies whichever answer lands last.
        owned_notes = set(job["footnote_ids"])
        units = [
            unit for unit in chunking.translatable_units(book, job["block_ids"])
            if unit[1] != "footnote" or unit[0] in owned_notes
        ]
        job["geometry"] = geometry(book, job["block_ids"], lookup)
        job["ocr"] = ocr_state(job["block_ids"], lookup)

        previous_tail, next_head = neighbour_context(book, jobs, index,
                                                     neighbour_chars)
        worksheet = render_worksheet(
            book, glossary, job, units,
            total=len(jobs), previous_tail=previous_tail, next_head=next_head,
        )
        name = f"page{page:04d}.md"
        ir.write_text(out_dir / name, worksheet)

        digest = source_digest(units)
        if state.note_page_source(page, digest):
            manifest["invalidated"].append(page)

        entry = {
            "id": f"page{page:04d}",
            "page": page,
            "file": name,
            "output": f"out_page{page:04d}.md",
            "block_ids": job["block_ids"],
            "unit_ids": [unit_id for unit_id, _, _ in units],
            "image_ids": job["image_ids"],
            "footnote_ids": job["footnote_ids"],
            "geometry": job["geometry"],
            "ocr": job["ocr"],
            "units": len(units),
            "source_chars": sum(len(text) for _, _, text in units),
            "payload_chars": len(worksheet),
            "over_budget": len(worksheet) > budget,
            "source_sha256": digest,
            "status": (state.page(page) or {}).get("state", "pending"),
        }
        if entry["over_budget"]:
            manifest["over_budget"].append(page)
        manifest["chunks"].append(entry)

    ir.write_text(out_dir / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    # Deliberately no stage-level record: a page run's identity is the per-page
    # source hashes above, and writing a `chunk` entry here would make the
    # chunk stage's own staleness answer about a run it did not do.
    return manifest


# --------------------------------------------------------------------------- #
# Resume
# --------------------------------------------------------------------------- #

def load_manifest(out_dir: Path) -> dict[str, Any]:
    return json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))


def status(out_dir: Path) -> dict[str, Any]:
    """Where every page stands, and which one to work on next.

    ``next`` is the first page that is not accepted, in page order. A page is
    finished when the run state says ``accepted`` and not before: a worksheet
    with an answer in it is translated, which is three states short of done.
    """
    manifest = load_manifest(out_dir)
    state = runstate.RunState(out_dir.parent)

    pages: list[dict[str, Any]] = []
    for entry in manifest["chunks"]:
        record = state.page(entry["page"]) or {}
        output = out_dir / entry["output"]
        answered = output.exists() and bool(output.read_text(encoding="utf-8").strip())
        pages.append({
            "page": entry["page"],
            "state": record.get("state", "pending"),
            "attempts": int(record.get("attempts", 0)),
            "last_error": record.get("last_error", ""),
            "answered": answered,
            "over_budget": entry["over_budget"],
            "payload_chars": entry["payload_chars"],
        })

    unfinished = [p for p in pages if p["state"] != "accepted"]
    return {
        "total": len(pages),
        "accepted": len(pages) - len(unfinished),
        "next": unfinished[0]["page"] if unfinished else None,
        "by_state": dict(sorted(Counter(p["state"] for p in pages).items())),
        "failed": [p["page"] for p in pages if p["state"] == "failed"],
        "over_budget": manifest.get("over_budget", []),
        "pages": pages,
    }


def next_page(out_dir: Path) -> dict[str, Any] | None:
    """The page to work on next, with the paths its worksheet lives at."""
    progress = status(out_dir)
    if progress["next"] is None:
        return None
    entry = next(e for e in load_manifest(out_dir)["chunks"]
                 if e["page"] == progress["next"])
    record = next(p for p in progress["pages"] if p["page"] == entry["page"])
    return {
        "page": entry["page"],
        "id": entry["id"],
        "worksheet": str(out_dir / entry["file"]),
        "output": str(out_dir / entry["output"]),
        "units": entry["units"],
        "payload_chars": entry["payload_chars"],
        "over_budget": entry["over_budget"],
        "state": record["state"],
        "attempts": record["attempts"],
        "last_error": record["last_error"],
        "remaining": progress["total"] - progress["accepted"],
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)

    p_build = sub.add_parser("build", help="one worksheet per source page")
    p_build.add_argument("--book", required=True)
    p_build.add_argument("--out", required=True, help="page worksheet directory")
    p_build.add_argument("--glossary", default=None)
    p_build.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                         help="worksheet characters a page job may carry; a "
                              "job over it is reported, never truncated")
    p_build.add_argument("--neighbour-chars", type=int, default=NEIGHBOUR_CHARS,
                         help="hard maximum context characters per side")

    p_status = sub.add_parser("status", help="where every page stands")
    p_status.add_argument("--pages", required=True)

    p_next = sub.add_parser("next", help="the first page that is not accepted")
    p_next.add_argument("--pages", required=True)


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel pages",
                                     description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)

    if args.action == "build":
        manifest = build(
            Path(args.book), Path(args.out),
            glossary_path=Path(args.glossary) if args.glossary else None,
            budget=args.budget,
            neighbour_chars=args.neighbour_chars,
        )
        print(json.dumps({
            "pages": manifest["pages"],
            "units": sum(c["units"] for c in manifest["chunks"]),
            "source_chars": sum(c["source_chars"] for c in manifest["chunks"]),
            "over_budget": manifest["over_budget"],
            "invalidated": manifest["invalidated"],
            "dir": args.out,
        }, ensure_ascii=False, indent=1))
        return 0

    if args.action == "status":
        progress = status(Path(args.pages))
        print(json.dumps({k: v for k, v in progress.items() if k != "pages"},
                         ensure_ascii=False, indent=1))
        return 0

    upcoming = next_page(Path(args.pages))
    print(json.dumps(upcoming or {"next": None, "detail": "every page accepted"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

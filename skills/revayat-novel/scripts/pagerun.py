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

The budget is a ceiling and not a note in the margin. A page whose worksheet
would not fit is cut into consecutive sub-jobs that do, all of them still owned
by that one source page; prose is never shortened to make room. A page nothing
can bring under the ceiling stops the run instead of going out oversized,
because emitting the job the budget just rejected is the context-limit failure
the budget exists to prevent.

The manifest's job list is called ``chunks`` on purpose: a page job *is* a
chunk of exactly one page, and ``merge.py`` reads that key, so a page run folds
back into the book with the same command a chunk run does. Sub-jobs need
nothing new from it: each one is simply a smaller chunk of the same page, and
they fold back before the page is looked at.

A page also gets its own single-page PDF, copied out of the artefact the book
was actually read from, so the thing a reviewer compares a translated page
against is the printed page rather than a description of it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import bookir as ir
import chunk as chunking
import glossary as gl
import merge as merging
import review as page_review
import runstate

SCHEMA = "revayat-novel/pagerun@1"

#: Rendered worksheet characters one page job may carry — a hard maximum, not
#: a report. A page of a trade novel is ~2000 source characters; the rest is the
#: glossary, the voices and the neighbour context. A page over this is split
#: into sub-jobs that each fit; prose is never trimmed, because a silently
#: shortened page is a page translated wrong.
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

#: The single-page source PDFs, under the worksheet directory.
SOURCE_DIR = "source"


class OverBudget(RuntimeError):
    """One worksheet cannot be brought under the budget without cutting prose."""


class SourceCollision(RuntimeError):
    """Files this build did not write are sitting in the page-PDF directory."""


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
# The source page itself
# --------------------------------------------------------------------------- #

def reference_pdf(book: dict[str, Any], book_path: Path) -> Path | None:
    """The PDF this book was actually read from, or ``None`` for a non-PDF.

    Not ``$WORK/ocr.pdf`` by convention: a born-digital book never has that
    file, and a mixed one has both an original and an OCR-normalised copy, so
    naming the artefact instead of recording it picks the wrong one about half
    the time. ``read_pdf`` writes down the file it opened — whichever of the
    three that was — and that is the only answer right for all of them.
    """
    source = book.get("source") or {}
    if source.get("format") != "pdf" or not source.get("path"):
        return None
    direct = Path(str(source["path"]))
    if direct.exists():
        return direct
    # The path is stored as it was typed and a page run is routinely resumed
    # from somewhere else, so look beside the book before giving up.
    beside = Path(book_path).parent / direct.name
    return beside if beside.exists() else None


def split_source_pages(reference: Path, pages: list[int],
                       out_dir: Path) -> dict[int, dict[str, Any]]:
    """One real PDF per source page, written beside the worksheets.

    Copied, never rasterised: ``insert_pdf`` carries the page's boxes, its
    rotation, its resources and its embedded image streams across untouched, so
    what a reviewer opens is the printed page rather than a photograph of one,
    and render QA can rasterise it later at whatever resolution it likes.
    """
    import pymupdf  # noqa: PLC0415  (only a PDF book ever reaches here)

    directory = out_dir / SOURCE_DIR
    wanted = {page: f"page-{page:04d}.pdf" for page in pages}

    # Another stage packages this tree. Writing over a file this build did not
    # produce would destroy someone's work, and leaving one here would smuggle
    # it into their package, so a stranger stops the run by name.
    if directory.exists():
        mine = set(wanted.values())
        strangers = sorted(item.name for item in directory.iterdir()
                           if item.name not in mine)
        if strangers:
            raise SourceCollision(
                f"{directory} already holds "
                f"{', '.join(strangers[:5])}{'…' if len(strangers) > 5 else ''}"
                f", which this build did not write. Move or delete them; "
                f"nothing here was overwritten."
            )
    directory.mkdir(parents=True, exist_ok=True)

    written: dict[int, dict[str, Any]] = {}
    source = pymupdf.open(str(reference))
    try:
        for page, name in wanted.items():
            # A book read with --max-pages, or one whose blocks were renumbered,
            # can name a page the PDF does not have. Skipping leaves the entry's
            # source_pdf empty, which reads as "there is none" rather than as a
            # path to a file nobody wrote.
            if not 1 <= page <= source.page_count:
                continue
            single = pymupdf.open()
            try:
                single.insert_pdf(source, from_page=page - 1, to_page=page - 1)
                single.save(str(directory / name))
            finally:
                single.close()
            written[page] = {
                "file": f"{SOURCE_DIR}/{name}",
                "source_page": page,
                "sha256": ir.sha256_file(directory / name),
            }
    finally:
        source.close()
    return written


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def fit_jobs(
    render: Callable[[list[tuple[str, str, str]]], str],
    units: list[tuple[str, str, str]],
    budget: int,
) -> list[tuple[list[tuple[str, str, str]], str]]:
    """``(units, worksheet)`` for each sub-job, in reading order.

    Consecutive runs, never a reshuffle: the units of a page are read in order
    and a translator handed part two must be able to follow part one. A page
    that fits comes back as the single job it was, so the ordinary book is
    unchanged down to the byte.

    Measured by rendering rather than by estimating a per-unit cost, because
    the overhead is not constant — the glossary rows and the voice cards a job
    carries are the ones its own units call for. One render per unit, each
    bounded by the budget, so a long page costs its length and not its square.
    """
    groups: list[list[tuple[str, str, str]]] = []
    current: list[tuple[str, str, str]] = []
    for unit in units:
        if current and len(render(current + [unit])) > budget:
            groups.append(current)
            current = []
        current.append(unit)
    # Appended even when empty: a page with nothing to translate — a plate, a
    # blank verso — still gets a worksheet, and merge still expects one.
    groups.append(current)
    return [(group, render(group)) for group in groups]


def build(
    book_path: Path,
    out_dir: Path,
    *,
    glossary_path: Path | None = None,
    budget: int = DEFAULT_BUDGET,
    neighbour_chars: int = NEIGHBOUR_CHARS,
) -> dict[str, Any]:
    """Write the worksheets one source page needs, plus the manifest.

    Rebuilding is safe at any point: worksheet bodies are a pure function of
    the book, translated ``out_page*.md`` replies are never touched, and only
    a page whose *own* source moved has its recorded progress reset.

    Every page is planned before any of it is written, so a page nothing can
    bring under the budget refuses while the worksheets already on disk still
    match the manifest beside them.
    """
    book = ir.load_book(book_path)
    glossary = gl.load(glossary_path) if glossary_path else gl.new_glossary()
    lookup = ir.blocks_by_id(book)
    state = runstate.RunState(out_dir.parent)

    jobs = owners(book)
    plan: list[tuple[dict[str, Any], list[tuple[str, str, str]],
                     list[tuple[list[tuple[str, str, str]], str]]]] = []

    for index, job in enumerate(jobs):
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

        def render(subset: list[tuple[str, str, str]], job: dict[str, Any] = job,
                   tail: str = previous_tail, head: str = next_head) -> str:
            return render_worksheet(book, glossary, job, subset,
                                    total=len(jobs), previous_tail=tail,
                                    next_head=head)

        fitted = fit_jobs(render, units, budget)
        for group, worksheet in fitted:
            if len(worksheet) <= budget:
                continue
            prose = sum(len(text) for _, _, text in group)
            raise OverBudget(
                f"page {job['page']}: a worksheet carrying {len(group)} unit(s) "
                f"renders {len(worksheet)} characters, over the {budget} "
                f"budget — {prose} of them prose and the rest context and "
                f"scaffolding. Splitting further would cut prose, so nothing "
                f"was written. Raise --budget to at least {len(worksheet)}, or "
                f"--neighbour-chars to carry less of the pages either side."
            )
        plan.append((job, units, fitted))

    out_dir.mkdir(parents=True, exist_ok=True)
    reference = reference_pdf(book, book_path)
    source_pdfs = (split_source_pages(reference, [j["page"] for j in jobs], out_dir)
                   if reference is not None else {})

    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "book": str(book_path),
        "book_sha256": book["source"].get("sha256", ""),
        # Recorded rather than assumed, so a consumer never has to guess which
        # of the original, the cleaned copy and the OCR copy a page came from.
        "reference_pdf": str(reference) if reference is not None else "",
        "glossary": str(glossary_path) if glossary_path else "",
        "budget": budget,
        "neighbour_chars": neighbour_chars,
        "pages": len(jobs),
        "chunks": [],
        "split": [],
        "invalidated": [],
    }

    for job, units, fitted in plan:
        page = job["page"]
        digest = source_digest(units)
        if state.note_page_source(page, digest):
            manifest["invalidated"].append(page)
        # The worksheet is on disk, so the page has been cut out of the book.
        # Only a page that has got no further is moved: a rebuild must not walk
        # an accepted page backwards.
        if (state.page(page) or {}).get("state") == "pending":
            state.set_page(page, "extracted")
        if len(fitted) > 1:
            manifest["split"].append(page)

        source_pdf = source_pdfs.get(page, {})
        for part, (group, worksheet) in enumerate(fitted, start=1):
            job_id = (f"page{page:04d}" if len(fitted) == 1
                      else f"page{page:04d}-{part:02d}")
            name = f"{job_id}.md"
            ir.write_text(out_dir / name, worksheet)
            manifest["chunks"].append({
                "id": job_id,
                "page": page,
                "part": part,
                "parts": len(fitted),
                "file": name,
                "output": f"out_{job_id}.md",
                # Page-level, and so repeated across a split page's sub-jobs:
                # these describe the page, not the job. Only ``unit_ids`` is
                # partitioned, because only units are sent out to be translated.
                "block_ids": job["block_ids"],
                "image_ids": job["image_ids"],
                "footnote_ids": job["footnote_ids"],
                "geometry": job["geometry"],
                "ocr": job["ocr"],
                "unit_ids": [unit_id for unit_id, _, _ in group],
                "units": len(group),
                "source_chars": sum(len(text) for _, _, text in group),
                "payload_chars": len(worksheet),
                "source_pdf": source_pdf.get("file", ""),
                "source_pdf_page": source_pdf.get("source_page", 0),
                "source_pdf_sha256": source_pdf.get("sha256", ""),
                "source_sha256": digest,
                "status": (state.page(page) or {}).get("state", "pending"),
            })

    # A rebuild at a different budget cuts a page in different places, so the
    # answers to the old cut are still on disk under names the new manifest
    # does not use. Nothing is deleted — a translator's work is not this
    # function's to throw away — but it is named, because an orphan nobody
    # mentions is prose merge quietly leaves out of the book.
    named = {entry[key] for entry in manifest["chunks"] for key in ("file", "output")}
    manifest["orphaned"] = sorted(item.name for item in out_dir.glob("*page*.md")
                                  if item.name not in named)

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


def jobs_for(manifest: dict[str, Any], page: int) -> list[dict[str, Any]]:
    """Every sub-job of one source page, in reading order."""
    return [entry for entry in manifest["chunks"] if entry["page"] == page]


def answer(out_dir: Path, entry: dict[str, Any]) -> str:
    """What the translator wrote back for one job, or ``""``."""
    output = out_dir / entry["output"]
    if not output.exists():
        return ""
    return output.read_text(encoding="utf-8").strip()


def qa_report_path(work_dir: Path, page: int) -> Path:
    """Where ``renderqa`` files a page's report.

    Spelled out here rather than imported, because ``renderqa`` imports this
    module; ``test_pagerun`` asserts the two agree so the copy cannot drift.
    """
    return work_dir / "qa" / "pages" / f"page-{page:04d}.json"


def status(out_dir: Path) -> dict[str, Any]:
    """Where every page stands, and which one to work on next.

    Reported per source page, not per job: a page split into sub-jobs is still
    one page to accept, and it is answered only when every one of them is.

    ``next`` is the first page that is not accepted, in page order. A page is
    finished when the run state says ``accepted`` and not before: a worksheet
    with an answer in it is translated, which is three states short of done.
    """
    manifest = load_manifest(out_dir)
    state = runstate.RunState(out_dir.parent)

    pages: list[dict[str, Any]] = []
    for number in dict.fromkeys(entry["page"] for entry in manifest["chunks"]):
        entries = jobs_for(manifest, number)
        record = state.page(number) or {}
        pages.append({
            "page": number,
            "state": record.get("state", "pending"),
            "attempts": int(record.get("attempts", 0)),
            "last_error": record.get("last_error", ""),
            "answered": all(answer(out_dir, entry) for entry in entries),
            "jobs": len(entries),
            "payload_chars": max(entry["payload_chars"] for entry in entries),
        })

    unfinished = [p for p in pages if p["state"] != "accepted"]
    return {
        "total": len(pages),
        "accepted": len(pages) - len(unfinished),
        "next": unfinished[0]["page"] if unfinished else None,
        "by_state": dict(sorted(Counter(p["state"] for p in pages).items())),
        "failed": [p["page"] for p in pages if p["state"] == "failed"],
        "split": manifest.get("split", []),
        "orphaned": manifest.get("orphaned", []),
        "reference_pdf": manifest.get("reference_pdf", ""),
        "pages": pages,
    }


def next_page(out_dir: Path) -> dict[str, Any] | None:
    """The next job to do, and the page it belongs to.

    One job at a time even when a page was split: answer it, ask again, and the
    same page comes back with its next part until the page is complete.
    """
    progress = status(out_dir)
    if progress["next"] is None:
        return None
    entries = jobs_for(load_manifest(out_dir), progress["next"])
    entry = next((e for e in entries if not answer(out_dir, e)), entries[0])
    record = next(p for p in progress["pages"] if p["page"] == entry["page"])
    return {
        "page": entry["page"],
        "id": entry["id"],
        "job": entry["part"],
        "jobs": entry["parts"],
        "worksheet": str(out_dir / entry["file"]),
        "output": str(out_dir / entry["output"]),
        "units": entry["units"],
        "payload_chars": entry["payload_chars"],
        # Two different files, and confusing them renders the wrong page:
        # ``page_pdf`` is this one page alone, and ``reference_pdf`` is the
        # whole book, which is what ``render-qa --source-pdf`` indexes into.
        "page_pdf": (str(out_dir / entry["source_pdf"])
                     if entry.get("source_pdf") else ""),
        "reference_pdf": progress["reference_pdf"],
        "state": record["state"],
        "attempts": record["attempts"],
        "last_error": record["last_error"],
        "remaining": progress["total"] - progress["accepted"],
    }


# --------------------------------------------------------------------------- #
# Lifecycle — the record moves because something happened, not because a
# caller said so
# --------------------------------------------------------------------------- #

def untranslated(book: dict[str, Any], unit_ids: list[str]) -> list[str]:
    """The units of a page the book still holds no Persian for."""
    blocks = ir.blocks_by_id(book)
    notes = {note["id"]: note for note in book.get("footnotes", [])}
    missing: list[str] = []
    for unit_id in unit_ids:
        if unit_id.endswith("#alt"):
            block = blocks.get(unit_id[: -len("#alt")]) or {}
            filled = bool((block.get("target_alt") or "").strip())
        elif unit_id in notes:
            filled = bool((notes[unit_id].get("target") or "").strip())
        else:
            block = blocks.get(unit_id) or {}
            filled = bool((block.get("target") or "").strip())
        if not filled:
            missing.append(unit_id)
    return missing


def merge_page(book_path: Path, out_dir: Path, page: int, *,
               glossary_path: Path | None = None) -> dict[str, Any]:
    """Fold one page's answers back into the book, and record that it happened.

    This is what moves a page from *extracted* through *translated* to
    *merged*, so ``pages status`` is right without anybody writing the record
    by hand. A split page merges only once every one of its sub-jobs is
    answered: half a page in the book is not a translated page, and merging the
    half would leave the rest looking like prose the translator dropped.
    """
    state = runstate.RunState(out_dir.parent)
    entries = jobs_for(load_manifest(out_dir), page)
    if not entries:
        return {"ok": False, "page": page, "refused": "unknown-page",
                "detail": f"the manifest in {out_dir} has no page {page}"}

    answers = [answer(out_dir, entry) for entry in entries]
    waiting = [entry["id"] for entry, text in zip(entries, answers) if not text]
    if waiting:
        return {"ok": False, "page": page, "refused": "not-translated",
                "detail": f"page {page} is still waiting on "
                          f"{', '.join(waiting)}; nothing was merged"}

    state.set_page(page, "translated", hashes={
        "translation": ir.sha256_bytes("\n".join(answers).encode("utf-8")),
    })
    report = merging.merge(book_path, out_dir,
                           only=[entry["id"] for entry in entries],
                           glossary_path=glossary_path)
    if report["ok"]:
        state.set_page(page, "merged")
    else:
        state.set_page(page, "failed", error=_merge_problem(report))
    return {"page": page, "jobs": len(entries), **report}


def _merge_problem(report: dict[str, Any]) -> str:
    for name in ("missing_outputs", "missing_units", "unknown_units"):
        if report.get(name):
            return f"{name}: {json.dumps(report[name], ensure_ascii=False)}"
    return "merge failed"


def accept(book_path: Path, out_dir: Path, page: int) -> dict[str, Any]:
    """Finish one page — only once every gate it has to clear actually has.

    Four separate pieces of evidence, because a page can look finished in any
    one of them and not be: the book has to hold Persian for every unit the
    page sent out, the run record has to say render QA passed, the report
    render QA wrote has to still say so itself, and somebody has to have
    actually looked at the page - geometry cannot see a plate three pages from
    the paragraph it belongs to. A gate nobody ran must never read as a gate
    that passed, so a missing report or review refuses rather than defaults — and because the record goes *backwards* when a page is merged
    again, a page re-translated after it passed cannot keep the old pass.
    """
    state = runstate.RunState(out_dir.parent)
    entries = jobs_for(load_manifest(out_dir), page)
    if not entries:
        return {"ok": False, "page": page, "refused": "unknown-page",
                "detail": f"the manifest in {out_dir} has no page {page}"}

    unit_ids = [unit_id for entry in entries for unit_id in entry["unit_ids"]]
    missing = untranslated(ir.load_book(book_path), unit_ids)
    if missing:
        return {"ok": False, "page": page, "refused": "not-merged",
                "detail": f"{len(missing)} of page {page}'s units have no "
                          f"Persian in the book yet: "
                          f"{', '.join(missing[:6])}"}

    record = state.page(page) or {}
    if record.get("state") != "qa_passed":
        return {"ok": False, "page": page, "refused": "not-qa-passed",
                "detail": f"page {page} is {record.get('state', 'pending')!r}; "
                          f"run render-qa on it and accept it once it passes"}

    report_file = qa_report_path(out_dir.parent, page)
    if not report_file.exists():
        return {"ok": False, "page": page, "refused": "no-render-qa",
                "detail": f"there is no {report_file}; a page nobody looked at "
                          f"is unverified, not accepted"}
    report = json.loads(report_file.read_text(encoding="utf-8"))
    if not (report.get("verified") and report.get("ok")):
        return {"ok": False, "page": page, "refused": "render-qa-failed",
                "detail": report.get("detail")
                          or f"{report_file} does not report a page that passed"}

    seen = page_review.verdict(out_dir.parent, page)
    if not seen["ok"]:
        return {"ok": False, "page": page, "refused": seen["refused"],
                "detail": seen["detail"]}

    state.set_page(page, "accepted")
    return {"ok": True, "page": page, "state": "accepted",
            "units": len(unit_ids), "jobs": len(entries),
            "reviewed": seen.get("render_sha256", "")[:12]}


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
                         help="worksheet characters one job may carry; a page "
                              "over it is split into jobs that fit, never "
                              "truncated")
    p_build.add_argument("--neighbour-chars", type=int, default=NEIGHBOUR_CHARS,
                         help="hard maximum context characters per side")

    p_status = sub.add_parser("status", help="where every page stands")
    p_status.add_argument("--pages", required=True)

    p_next = sub.add_parser("next", help="the first page that is not accepted")
    p_next.add_argument("--pages", required=True)

    p_merge = sub.add_parser("merge", help="fold one page's answers into the book")
    p_merge.add_argument("--book", required=True)
    p_merge.add_argument("--pages", required=True)
    p_merge.add_argument("--page", type=int, required=True)
    p_merge.add_argument("--glossary", default=None)

    p_review = sub.add_parser(
        "review", help="file what a reviewer saw on the rendered page")
    p_review.add_argument("--pages", required=True)
    p_review.add_argument("--page", type=int, required=True)
    p_review.add_argument(
        "--answer", action="append", default=[], metavar="ID=yes|no",
        help="one per question: " + ", ".join(sorted(page_review.QUESTIONS)))
    p_review.add_argument("--note", default="",
                          help="what was wrong, in the reviewer's own words")

    p_accept = sub.add_parser(
        "accept", help="finish a page whose gates have all actually passed")
    p_accept.add_argument("--book", required=True)
    p_accept.add_argument("--pages", required=True)
    p_accept.add_argument("--page", type=int, required=True)


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel pages",
                                     description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)

    if args.action == "build":
        try:
            manifest = build(
                Path(args.book), Path(args.out),
                glossary_path=Path(args.glossary) if args.glossary else None,
                budget=args.budget,
                neighbour_chars=args.neighbour_chars,
            )
        except (OverBudget, SourceCollision) as refusal:
            print(json.dumps({
                "ok": False,
                "refused": ("over-budget" if isinstance(refusal, OverBudget)
                            else "source-directory-in-use"),
                "detail": str(refusal),
            }, ensure_ascii=False, indent=1))
            return 2
        print(json.dumps({
            "pages": manifest["pages"],
            "jobs": len(manifest["chunks"]),
            "units": sum(c["units"] for c in manifest["chunks"]),
            "source_chars": sum(c["source_chars"] for c in manifest["chunks"]),
            "split": manifest["split"],
            "orphaned": manifest["orphaned"],
            "invalidated": manifest["invalidated"],
            "reference_pdf": manifest["reference_pdf"],
            "dir": args.out,
        }, ensure_ascii=False, indent=1))
        return 0

    if args.action == "status":
        progress = status(Path(args.pages))
        print(json.dumps({k: v for k, v in progress.items() if k != "pages"},
                         ensure_ascii=False, indent=1))
        return 0

    if args.action == "merge":
        report = merge_page(
            Path(args.book), Path(args.pages), args.page,
            glossary_path=Path(args.glossary) if args.glossary else None,
        )
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report["ok"] else 1

    if args.action == "review":
        try:
            answers = dict(page_review.parse_answer(item) for item in args.answer)
        except ValueError as wrong:
            print(json.dumps({"ok": False, "refused": "bad-answer",
                              "detail": str(wrong),
                              "questions": page_review.QUESTIONS},
                             ensure_ascii=False, indent=1))
            return 2
        filed = page_review.record(Path(args.pages).parent, args.page, answers,
                                   note=args.note)
        print(json.dumps(filed, ensure_ascii=False, indent=1))
        return 0 if filed["ok"] else 2

    if args.action == "accept":
        outcome = accept(Path(args.book), Path(args.pages), args.page)
        print(json.dumps(outcome, ensure_ascii=False, indent=1))
        return 0 if outcome["ok"] else 2

    upcoming = next_page(Path(args.pages))
    print(json.dumps(upcoming or {"next": None, "detail": "every page accepted"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

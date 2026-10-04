"""What a page *is*, and what identifies it.

Two questions the page lifecycle asks constantly and neither of which is part of
it: **which page owns this block** and **has anything the page renders changed**.
They live here so the lifecycle module stays about the lifecycle, and because
several other stages need the same answers — `pagecheck` and `preview` both
partition a book exactly as the page jobs did, and `renderqa` records the same
digest `pages accept` re-checks. Two copies of either would be two answers.

The dependency points one way: this module knows nothing about building, merging
or accepting a page. `pagecheck` is imported inside :func:`translation_hash`
rather than at the top because it imports the lifecycle, which imports this.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

import bookir as ir

REQUEST_DIGEST_VERSION = "page-request1"


def request_fingerprint(book, entry, *, glossary, book_path, neighbour_chars=600,
                        source_prints=None, out_dir=None):
    """Live page source, geometry, OCR, policy and context; never cached across uses."""
    import pagesheet
    import provenance
    import sourcepages

    jobs = owners(book)
    index = next((i for i, job in enumerate(jobs) if job["page"] == entry["page"]), None)
    if index is None:
        raise ValueError("the source page no longer exists; rebuild")
    job = jobs[index]
    if job["block_ids"] != entry["block_ids"]:
        raise ValueError("the page ownership changed; rebuild")
    lookup = ir.blocks_by_id(book)
    visual = ""
    if (book.get("source") or {}).get("format") == "pdf":
        if out_dir is not None:
            problem = sourcepages.page_artifact_problem(out_dir, entry)
            if problem:
                raise ValueError(problem)
        reference = sourcepages.reference_pdf(book, Path(book_path))
        if reference is None:
            raise ValueError("the source PDF is unavailable; restore it or re-extract")
        visual = (source_prints.get(job["page"], "") if source_prints is not None
                  else sourcepages.page_fingerprint(reference, job["page"]))
        if not visual:
            raise ValueError("the source page could not be fingerprinted")
    data = {
        "page": job["page"], "blocks": job["block_ids"],
        "units": provenance.translatable_units(book, job["block_ids"]),
        "cut": provenance.source_fingerprint(book, job["block_ids"], entry["unit_spans"], glossary=glossary),
        "geometry": geometry(book, job["block_ids"], lookup, job["page"]),
        "ocr": ocr_state(job["block_ids"], lookup), "source_pdf": visual,
        "glossary": glossary,
        "context": pagesheet.neighbour_context(book, jobs, index, neighbour_chars),
    }
    return REQUEST_DIGEST_VERSION + ":" + ir.sha256_bytes(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))

#: Ceilings on the OCR-uncertainty payload a page carries. Both grow with the
#: page's worst text rather than with the page, so each needs its own bound: an
#: unbounded "just add the uncertain words" is how a page job becomes a book job.
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
             lookup: dict[str, dict[str, Any]],
             page: int | None = None) -> dict[str, Any]:
    """Page setup plus the box the page's own content actually occupies.

    ``text_bbox`` is measured, and it is what the render check compares the
    translated page against. The setup is the book's *unless this page has its
    own* - a landscape plate or a differently trimmed page is laid out on its
    own paper, not on the book's average.
    """
    setup: dict[str, Any] = dict(book.get("page") or ir.default_page_setup())
    # `book["page"]` is the *dominant* geometry, which is the right default and
    # the wrong answer for the landscape map or the differently trimmed front
    # matter. The census lists every page that disagrees; a preview built from
    # the dominant setup would lay such a page out on paper it was never on.
    own = ((book.get("source") or {}).get("page_geometry") or {}).get("pages") or {}
    setup.update(own.get(str(page)) or {})
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



PAGE_DIGEST_VERSION = "page4"

# Persisted presentation fields consumed by the native builder. Diagnostic
# notes and transient TOC caches are intentionally absent: they do not print.
_VISUAL_BLOCK_FIELDS = (
    "id", "type", "text", "target", "alt", "target_alt", "level", "ordered",
    "table", "row", "cell", "row_span", "col_span", "parent_table", "parent_row", "parent_cell",
    "bookmarks", "links", "font_size_pt", "asset", "width_pt", "height_pt",
    "pixel_width", "pixel_height", "soft",
)


def translation_hash(book_path: Path, page: int) -> str:
    """Identity of this page's source, published text, structure and actual assets.

    page4 replaces delimiter-concatenated strings and target-only prose with a
    typed projection. Moving a table cell, changing a heading/list kind or a
    running-header field is a changed page even when every word is unchanged.
    Old approvals require new rendering, never a version-prefix substitution.
    """
    import pagecheck

    book = ir.load_book(book_path)
    expected = pagecheck.expectations(book, page)
    job = next((j for j in owners(book) if j["page"] == page), None)
    lookup = ir.blocks_by_id(book)
    block_ids = (job or {}).get("block_ids") or []
    ids = set(block_ids)
    blocks = [lookup[identifier] for identifier in block_ids if identifier in lookup]
    note_ids = set((job or {}).get("footnote_ids") or [])
    for block in blocks:
        for field in ("text", "target", "alt", "target_alt"):
            note_ids.update(ir.footnote_refs(block.get(field) or ""))
    assets = Path(book_path).parent / "assets"
    figures = []
    for block in blocks:
        if block.get("type") == "image":
            name = block.get("asset") or ""
            picture = assets / name if name else None
            identity = ir.sha256_file(picture) if picture is not None and picture.is_file() else "absent"
            figures.append({"id": block["id"], "asset": name, "sha256": identity})
    section_fields = ("start_block", "start_type", "orientation", "width_pt", "height_pt",
                      "margin_top_pt", "margin_bottom_pt", "margin_inner_pt", "margin_outer_pt",
                      "different_first_page", "headers", "footers")
    showing = ir.sections_covering(book, ids)
    payload = {
        "page": page,
        "geometry": expected.get("setup") or {},
        "blocks": [{key: block[key] for key in _VISUAL_BLOCK_FIELDS if key in block} for block in blocks],
        "notes": [{key: note.get(key) for key in ("id", "text", "target", "origin", "anchor_block")}
                  for note in book.get("footnotes", []) if note.get("id") in note_ids],
        "figures": figures,
        "sections": [{key: section[key] for key in section_fields if key in section} for section in showing],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return PAGE_DIGEST_VERSION + ":" + ir.sha256_bytes(encoded)


def _translation_moved(book_path: Path, page: int,
                       record: dict[str, Any]) -> tuple[str, str]:
    """``(refusal code, detail)`` for this page's recorded digest, or ``("", "")``.

    Three outcomes, not two. The digest is **versioned**, so a value written by an
    older formula is not evidence either way: comparing it against the current one
    reports every such page as changed, and ignoring it reports every such page as
    current. Both are wrong, and the second is the defect this check exists to
    close. So an incomparable digest refuses as `unverified-digest`, which one
    `render-qa` run clears — migratable, not stranded.
    """
    recorded = (record.get("hashes") or {}).get("translation", "")
    if not recorded or recorded.partition(":")[0] != PAGE_DIGEST_VERSION:
        return ("unverified-digest",
                f"page {page}'s recorded translation digest is "
                f"{recorded[:16] or 'absent'}, which this version cannot "
                f"compare — it predates the digest covering notes, running "
                f"heads, figures and geometry. Run render-qa on the page again "
                f"to record a current one, then accept it.")
    current = translation_hash(book_path, page)
    if current == recorded:
        return ("", "")
    return ("translation-changed",
            f"the page's rendered content now hashes {current[:16]} against the "
            f"{recorded[:16]} that was checked")

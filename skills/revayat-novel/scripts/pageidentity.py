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
from pathlib import Path
from typing import Any

import bookir as ir

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



PAGE_DIGEST_VERSION = "page3"


def translation_hash(book_path: Path, page: int) -> str:
    """Identity of one page's rendered content **and the source it answers**.

    **One definition, for everyone who records or checks it.** The page record
    has a single ``translation`` hash and it used to be written two different
    ways — ``merge_page`` hashed the worksheet *answers* while ``renderqa``
    hashed the page's *texts as laid out*. Two formulas under one key always
    disagree, and the way this pair disagreed was silent: every merge looked
    like a changed translation, which is exactly the signal the retry cap and the
    acceptance check read.

    **Both sides, since `page3`.** Until then this hashed only the Persian, so
    re-extracting the source — a corrected OCR line, a re-read PDF — left every
    page's digest identical while its translation now answered text that had
    changed. `accept` then passed a page whose Persian belonged to a source
    nobody had compared it against. It is the same defect the chunk route fixed
    by moving from `units:` to `units2:`, and it survived here because a digest
    over the target alone *looks* like it covers the page.

    ``pagecheck`` is imported here rather than at the top because it imports this
    module; the dependency points one way at import time, and this is the one
    place that needs to look back along it.
    """
    import pagecheck

    book = ir.load_book(book_path)
    expected = pagecheck.expectations(book, page)
    job = next((j for j in owners(book) if j["page"] == page), None)
    lookup = ir.blocks_by_id(book)
    ids = set(job["block_ids"]) | set(job["image_ids"]) if job else set()

    # Everything that decides what the sheet looks like, not only its paragraphs.
    # `expectations()["texts"]` is the body prose alone, so a retranslated
    # footnote, a corrected running head, a rewritten caption or a changed page
    # width all left this digest identical — and a page accepted on that evidence
    # had passed QA against a sheet that no longer exists.
    parts: list[str] = ["body"] + list(expected["texts"])

    # The English this page's Persian is an answer to. `expectations()["texts"]`
    # is targets only, by design — it is what a rendered page must *show* — so
    # the source has to be taken from the blocks directly, under the same
    # ownership rule the job used.
    parts.append("source")
    for block_id in (job or {}).get("block_ids") or []:
        block = lookup.get(block_id) or {}
        if block.get("type") in ir.TEXT_TYPES:
            parts.append(f"{block_id}\x00{block.get('text') or ''}")

    parts.append("geometry")
    setup = expected.get("setup") or {}
    parts += [f"{key}={setup[key]!r}" for key in sorted(setup)]

    parts.append("notes")
    referenced = {ref for text in expected["texts"]
                  for ref in ir.ANY_FOOTNOTE_TOKEN.findall(text)}
    for note in book.get("footnotes", []):
        if note.get("id") in referenced:
            # Source beside target, for the reason given above: a corrected note
            # in English changes what its Persian has to say.
            parts.append(f"{note['id']}\x00{note.get('text') or ''}"
                         f"\x00{note.get('target') or ''}")

    parts.append("figures")
    # The asset's identity is read from the **file**, not from a field on the
    # block. My first version hashed `block["asset_sha256"]` — a name nothing in
    # this project ever writes, so it contributed the empty string for every
    # figure and replacing a picture under the same filename changed nothing.
    # The test agreed with the bug because it set that same invented key by hand.
    # Assets live beside `book.json`, which is the convention `qa check` uses.
    assets = Path(book_path).parent / "assets"
    for block_id in sorted(ids):
        block = lookup.get(block_id) or {}
        if block.get("type") != "image":
            continue
        name = block.get("asset") or ""
        picture = assets / name if name else None
        if picture is not None and picture.is_file():
            identity = ir.sha256_file(picture)
        else:
            identity = "absent"
        parts.append(f"{block_id}\x00{name}\x00{identity}"
                     f"\x00{block.get('target_alt') or ''}"
                     f"\x00{block.get('width_pt')}x{block.get('height_pt')}")

    # Every head this page actually prints, which is every head of every section
    # the page's blocks fall in — not only a head whose section *opens* on this
    # page. Under the old rule a corrected running head moved the digest of the
    # section's first page and no other, so pages 2..n of a chapter kept an
    # accepted verdict while the header at the top of them had changed.
    parts.append("running")
    showing = ir.sections_covering(book, ids)
    for unit_id, _kind, piece, section in ir.iter_running_pieces(book):
        if not ids or any(section is shown for shown in showing):
            parts.append(f"{unit_id}\x00{piece.get('text') or ''}"
                         f"\x00{piece.get('target') or ''}")

    return f"{PAGE_DIGEST_VERSION}:" + ir.sha256_bytes(
        "\n".join(parts).encode("utf-8"))


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



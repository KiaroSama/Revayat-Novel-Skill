"""The read side of a page run: where every page stands, and which one is next.

`pagerun` owns the lifecycle — it builds worksheets, merges replies, records that
something happened. This module only *reads* what that left behind: the manifest,
the replies on disk, the run state, and the recorded evidence. Nothing here
mutates anything, which is the boundary, and it is worth keeping because the two
halves fail differently: a lifecycle bug writes something wrong, a reporting bug
tells an operator the wrong thing about work that is fine.

**The one judgement this side does make** is whether a page reported as finished
still *is* finished. `status` was a pure label reader, and a label cannot notice
that a page's text moved underneath it — so a page stayed `accepted` after its
source was corrected or its Persian re-merged, and `next_page` skipped it. Given
the book, each finished page's recorded digest is re-compared through
`pageidentity.translation_hash` — the one formula, never a second opinion — and a
page whose content has moved comes back `stale`.

`pagerun` re-exports every name here, because `pagerun.status` is how the CLI,
`renderqa` and the tests already reach them.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import eligible
import reviewstate
import runstate
from pageidentity import _translation_moved


def load_manifest(out_dir: Path) -> dict[str, Any]:
    return eligible.read_manifest(out_dir)


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

    Spelled out here rather than imported, because ``renderqa`` imports the
    lifecycle module; ``test_pagerun`` asserts the two agree so the copy cannot
    drift.
    """
    return work_dir / "qa" / "pages" / f"page-{page:04d}.json"


#: States whose evidence a recorded digest can be compared against. Earlier
#: states have no digest yet, so checking them would report every pending page as
#: stale — a gate that fires on everything is as useless as one that fires on
#: nothing.
_FRESHNESS_CHECKED = frozenset({"merged", "qa_passed", "accepted"})


@reviewstate.guarded
def status(out_dir: Path, book_path: Path | None = None) -> dict[str, Any]:
    """Where every page stands, and which one to work on next.

    Reported per source page, not per job: a page split into sub-jobs is still
    one page to accept, and it is answered only when every one of them is.

    ``next`` is the first page that is not accepted, in page order. A page is
    finished when the run state says ``accepted`` and not before: a worksheet
    with an answer in it is translated, which is three states short of done.

    Resolve the recorded book when no override is supplied. A stored accepted
    label cannot bypass current translation/dependency checks. Changed or unknown
    evidence returns the page to the unfinished queue without modifying its record.
    """
    manifest = load_manifest(out_dir)
    state = runstate.RunState(out_dir.parent)
    proofs = {proof["id"]: proof for proof in eligible.every(out_dir, manifest, book_path=book_path)}
    if book_path is None:
        book_path = eligible._book_and_glossary(out_dir, manifest)[0]

    pages: list[dict[str, Any]] = []
    for number in dict.fromkeys(entry["page"] for entry in manifest["chunks"]):
        entries = jobs_for(manifest, number)
        record = state.page(number) or {}
        reported = record.get("state", "pending")
        stale = ""
        if book_path is not None and reported in _FRESHNESS_CHECKED:
            refusal, detail = _translation_moved(Path(book_path), number, record)
            if refusal:
                stale, reported = refusal, "stale"
        dependencies = [proofs[entry["id"]] for entry in entries
                        if proofs[entry["id"]]["state"] in ("stale-source", "unverified")]
        if dependencies and reported in _FRESHNESS_CHECKED:
            stale, reported = dependencies[0]["state"], "stale"
            detail = dependencies[0]["detail"]
        pages.append({
            "page": number,
            "state": reported,
            "stale": stale,
            "recorded_state": record.get("state", "pending"),
            "attempts": int(record.get("attempts", 0)),
            "last_error": record.get("last_error", ""),
            "answered": all(proofs[entry["id"]]["usable"] for entry in entries),
            "jobs": len(entries),
            "payload_chars": max(entry["payload_chars"] for entry in entries),
        })
        if stale:
            pages[-1]["last_error"] = detail

    unfinished = [p for p in pages if p["state"] != "accepted"]
    return {
        "total": len(pages),
        "accepted": len(pages) - len(unfinished),
        "next": unfinished[0]["page"] if unfinished else None,
        "by_state": dict(sorted(Counter(p["state"] for p in pages).items())),
        "failed": [p["page"] for p in pages if p["state"] == "failed"],
        "stale": [p["page"] for p in pages if p["stale"]],
        "freshness": "checked" if book_path is not None else "unchecked",
        "split": manifest.get("split", []),
        "orphaned": manifest.get("orphaned", []),
        "reference_pdf": manifest.get("reference_pdf", ""),
        "pages": pages,
    }


def _job_to_do(out_dir: Path,
               entries: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """``(the sub-job that still needs work, why)`` — merge's own answer.

    The test used to be ``bool(the reply file)``, which is weaker than the one
    merge applies, so a split page whose *second* part carried a reply merge
    refuses — one bound to the previous cut, one with no request line, one whose
    footnote does not resolve — handed back the **first** part instead. Measured:
    a two-part page with an unbound answer to part 2 refused as
    ``malformed: ['page0001-02']`` while ``next_page`` offered ``page0001-01``,
    so a driver re-translated a part that was already correct and the broken one
    was never named.

    :mod:`eligible` is the same read-only resolver the chunk scheduler and merge
    share. Missing dependencies remain unverified and unusable, so they cannot
    silently disappear from the unfinished queue.
    """
    proofs = {proof["id"]: proof for proof in eligible.every(out_dir)}
    for entry in entries:
        verdict = proofs[entry["id"]]
        if not verdict["usable"]:
            # A reason names what is wrong with the reply **on disk**. "Nobody has
            # answered this yet" is the ordinary state of an unanswered job, and
            # reporting it as a reason would make the field mean nothing.
            if verdict["state"] in ("missing", "empty"):
                return entry, ""
            return entry, verdict["detail"] or f"state {verdict['state']}"
    return entries[0], ""


@reviewstate.guarded
def next_page(out_dir: Path) -> dict[str, Any] | None:
    """The next job to do, and the page it belongs to.

    One job at a time even when a page was split: answer it, ask again, and the
    same page comes back with its next part until the page is complete — and
    when one part's reply is the problem, that part is the one handed back.
    """
    progress = status(out_dir)
    if progress.get("ok") is False:
        return progress
    if progress["next"] is None:
        return None
    entries = jobs_for(load_manifest(out_dir), progress["next"])
    entry, reason = _job_to_do(out_dir, entries)
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
        # What is wrong with the reply already on disk, when that is why this job
        # came back. Empty when the job simply has not been answered yet.
        "reason": reason,
        "remaining": progress["total"] - progress["accepted"],
    }

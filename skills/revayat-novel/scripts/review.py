"""Stage 6c - the page is looked at by something with eyes, and says what it saw.

Everything else in this pipeline is deterministic, and deterministic checks
answer deterministic questions: is this block present once, is that plate the
right shape, is the paragraph right-to-left, is there a hole where a page of
text should be. They are the questions worth automating because they are the
ones a machine answers better than a person, every page, for free.

They are not the whole of "is this page right". A picture can sit inside the
body area, at the correct aspect ratio, present exactly once, and belong to the
paragraph three pages back. Persian can pass every geometric test and still
render as disconnected letters because the font lacks the joining forms. A
heading can be present, in the right place, and look like body text. None of
that is visible in the IR - it is visible on the page, which is why both PNGs
are written and why this module exists to record what was seen in them.

The verdict is bound to the render it was made against. A review that outlives
its render is worse than no review: it reports that someone looked at a page
that no longer exists, and `accept` would believe it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import bookir as ir
import runstate

SCHEMA = "revayat-novel/review@1"

#: The finished book is reviewed too, and it is not a page. Reviews are keyed by
#: subject rather than by number so the two cannot be confused in a directory
#: listing or in a gate that reads one and means the other.
DOCUMENT = "document"

#: What the reviewer is asked, and why a machine is not asked it instead. Each
#: one is a question the deterministic checks provably cannot reach: they read
#: geometry and text, and every question here is about meaning or shape that
#: only survives as pixels.
QUESTIONS: dict[str, str] = {
    "figure-placement":
        "Is each picture beside the text it belongs to? Geometry only proves it "
        "is on the page and the right shape, never that it is in the right place.",
    "script-integrity":
        "Does the Persian render as joined, readable script - no disconnected "
        "letters, no boxes, no dotted circles where a glyph is missing?",
    "no-source-language":
        "Is everything that should be Persian actually Persian? A caption or a "
        "heading left in the source language is invisible to a block-count check.",
    "hierarchy":
        "Do headings still read as headings and dialogue as dialogue - is the "
        "visual hierarchy the source page had still there?",
    "reads-as-a-book":
        "Would a reader accept this as a page of a printed book - even margins, "
        "an even colour of type, no line crushed or stretched to fit?",
}

#: How an answer may be written. Anything else is a typo, and a typo must not
#: quietly become a pass.
YES = {"yes", "y", "true", "ok", "pass"}
NO = {"no", "n", "false", "bad", "fail"}


def review_path(work_dir: Path, page: int | str) -> Path:
    name = page if isinstance(page, str) else f"page-{page:04d}"
    return Path(work_dir) / "qa" / "reviews" / f"{name}.json"


def parse_answer(text: str) -> tuple[str, bool]:
    """``figure-placement=yes`` -> ``("figure-placement", True)``."""
    name, _, value = str(text).partition("=")
    name, value = name.strip(), value.strip().lower()
    if name not in QUESTIONS:
        raise ValueError(f"unknown question {name!r}; expected one of "
                         f"{', '.join(sorted(QUESTIONS))}")
    if value in YES:
        return name, True
    if value in NO:
        return name, False
    raise ValueError(f"{name}: {value!r} is neither yes nor no")


def current_render(work_dir: Path, page: int | str, *, render: str = "") -> str:
    """The hash of what the reviewer would be looking at right now.

    ``render`` is for a subject the run record does not track: the assembled
    book has no page record and never will. Its caller passes the .docx hash
    rather than the rendered PDF's, because Word stamps a PDF with the moment it
    made it — binding there would make every review stale the instant it was
    filed.
    """
    if render:
        return render
    if isinstance(page, str):
        return ""
    return ((runstate.RunState(Path(work_dir)).page(page) or {})
            .get("hashes", {}).get("render", ""))


def record(work_dir: Path, page: int | str, answers: dict[str, bool],
           *, note: str = "", render: str = "") -> dict[str, Any]:
    """File one reviewer's answers against the render they were made from."""
    work_dir = Path(work_dir)
    render = current_render(work_dir, page, render=render)
    if not render:
        subject = "the document" if isinstance(page, str) else f"page {page}"
        return {"ok": False, "page": page, "refused": "not-rendered",
                "detail": f"{subject} has not been rendered, so there is "
                          f"nothing to have reviewed; render it first"}

    unanswered = sorted(set(QUESTIONS) - set(answers))
    if unanswered:
        # A question nobody answered is not a question nobody had a problem
        # with. Defaulting it to yes is how a review passes a page it never
        # looked at properly.
        return {"ok": False, "page": page, "refused": "incomplete",
                "detail": "these were not answered: " + ", ".join(unanswered),
                "questions": {name: QUESTIONS[name] for name in unanswered}}

    failed = sorted(name for name, good in answers.items() if not good)
    written = {
        "schema": SCHEMA,
        "page": page,
        "ok": not failed,
        "render_sha256": render,
        "answers": {name: bool(answers[name]) for name in sorted(QUESTIONS)},
        "failed": failed,
        "note": str(note)[:2000],
    }
    ir.write_text(review_path(work_dir, page),
                  json.dumps(written, ensure_ascii=False, indent=1) + "\n")
    return written


def verdict(work_dir: Path, page: int | str, *,
            render: str = "") -> dict[str, Any]:
    """What a gate should make of this subject's review. Never raises."""
    path = review_path(Path(work_dir), page)
    subject = "the document" if isinstance(page, str) else f"page {page}"
    if not path.exists():
        return {"ok": False, "refused": "not-reviewed",
                "detail": f"nobody has looked at {subject}: there is no "
                          f"{path}. Deterministic checks do not answer "
                          f"{', '.join(sorted(QUESTIONS))}."}
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as failure:
        return {"ok": False, "refused": "unreadable-review",
                "detail": f"{path} could not be read: {failure}"}

    render = current_render(Path(work_dir), page, render=render)
    if render and found.get("render_sha256") != render:
        return {"ok": False, "refused": "stale-review",
                "detail": f"{subject} was reviewed, then rendered again. The "
                          f"review describes something that no longer exists; "
                          f"look at what is there now."}
    if not found.get("ok"):
        return {"ok": False, "refused": "review-rejected",
                "detail": f"the reviewer rejected {subject}: "
                          f"{', '.join(found.get('failed') or ['no reason given'])}"
                          + (f" - {found['note']}" if found.get("note") else "")}
    return {"ok": True, **found}

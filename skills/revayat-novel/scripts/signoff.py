"""The last gate before a book is built: is this the text that was approved?

`qa check` is deterministic and thorough about what it measures — coverage,
parity, footnotes, geometry, names, typography — and it cannot answer the one
question a reader would ask first: has anybody read this translation, and did
they read *this* version of it.

The two semantic stages answer that, and they were not asked. Neither was the
geometric page review, which is why it must never be given the job: `render-qa`
judges whether a page is laid out correctly, and a page can be laid out
perfectly around a sentence whose negation is inverted. Asking the gate with the
eyes to certify meaning is how a semantic approval becomes decorative.

So the semantic verdicts are enforced here, by asking the modules that own them
for the book **as it now stands**:

* `meaning.verdict` — read against the source, at this exact revision;
* `fluency.verdict` — read blind, its edits applied, and the source comparison
  redone afterwards;
* `published.pending` — nothing still owing a translation, because a pass over
  the sheets can be complete while published prose is missing from them.

Two failure modes are deliberately kept apart, following `--glossary` in the same
gate and `text-unverified` in the page report:

* a directory **named** whose verdict is absent, stale or refused is an error —
  the operator pointed at an approval and it does not hold;
* a directory **not named** is reported as unverified rather than as passed. A
  gate nobody ran must never read as a gate that passed, and under ``--strict``
  — which is what publication work uses — it blocks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bookir as ir
import fluency as fluency_pass
import meaning as meaning_review
import published

#: What `qa` files these findings under. One code per condition, so a report can
#: be acted on without reading the detail line.
REJECTED = "semantic-rejected"
UNVERIFIED = "semantic-unverified"
PENDING = "publication-pending"


def _refusal(stage: str, answer: dict[str, Any]) -> str:
    reason = answer.get("refused") or "unknown"
    detail = str(answer.get("detail") or "").strip()
    return f"{stage} review: {reason}" + (f" — {detail}" if detail else "")


def problems(book_path: Path, *, review_dir: Path | None,
             fluency_dir: Path | None) -> list[tuple[str, str, str]]:
    """``(code, unit, detail)`` for everything that blocks a delivery.

    Read-only: the verdicts are recomputed from the book on disk and the
    recorded sidecars, and nothing here writes or repairs anything.
    """
    book_path = Path(book_path)
    book = ir.load_book(book_path)
    found: list[tuple[str, str, str]] = []

    owing = published.pending(book)
    if owing:
        found.append((
            PENDING, "book.json",
            f"{len(owing)} published unit(s) have no Persian: "
            + ", ".join(f"{unit['id']} ({unit['part']})" for unit in owing[:8])
            + ". The title page and a translator's notes are published prose "
              "like any paragraph."))

    if review_dir is None:
        found.append((
            UNVERIFIED, "meaning",
            "no --review directory was given, so nothing says this translation "
            "has been read against its source. That is not a pass: the "
            "deterministic checks in this gate cannot see an inverted negation."))
    else:
        answer = meaning_review.verdict(
            Path(review_dir),
            meaning_review.revision(meaning_review.pairs(book)))
        if not answer.get("ok"):
            found.append((REJECTED, "meaning", _refusal("meaning", answer)))

    if fluency_dir is None:
        found.append((
            UNVERIFIED, "fluency",
            "no --fluency directory was given, so nothing says the Persian has "
            "been read on its own. A bilingual reviewer cannot answer it: they "
            "see the English behind every sentence."))
    elif review_dir is None:
        found.append((
            UNVERIFIED, "fluency",
            "--fluency was given without --review, and the blind pass only "
            "passes once the meaning review has been redone against the book it "
            "edited. Without that directory the claim cannot be checked."))
    else:
        answer = fluency_pass.verdict(Path(fluency_dir), book_path,
                                      Path(review_dir))
        if not answer.get("ok"):
            found.append((REJECTED, "fluency", _refusal("fluency", answer)))

    return found


def approved(book_path: Path, *, review_dir: Path,
             fluency_dir: Path) -> dict[str, Any]:
    """What was approved, for a report or a later comparison.

    The two revision digests are over the published inventory, so quoting them
    beside a built file is how "the shipped content is the content last
    approved" is checked rather than asserted.
    """
    book = ir.load_book(Path(book_path))
    return {
        "meaning_revision": meaning_review.revision(meaning_review.pairs(book)),
        "fluency_revision": fluency_pass.revision(fluency_pass.targets(book)),
        "published_units": len(published.units(book)),
        "pending": [unit["id"] for unit in published.pending(book)],
        "problems": [
            {"code": code, "unit": unit, "detail": detail}
            for code, unit, detail in problems(book_path, review_dir=review_dir,
                                               fluency_dir=fluency_dir)],
    }

"""Stage 9b — the finished book, checked as the artefact the reader receives.

Accepting pages one at a time is necessary and not sufficient. A page that was
right on its own can still be wrong once the book is assembled: the material
ahead of it reflows, so a plate that sat comfortably mid-page ends up split
across a break, and a heading that had room under it ends up the last line on a
page. Nothing in the per-page reports can see that, because each of them looked
at a document that did not exist yet.

Two kinds of question are asked here, and mixing them up is the mistake this
module is shaped to avoid.

**Per page, and only about that page.** Nothing off the trim, nothing outside
the body, no hole where text should be, no text on a plate. Ownership by source
page does not survive assembly — Persian reflows, so the book's page 12 is not
source page 12 — and *any* check that compares final page N with source page N
is wrong here by construction.

**Across the whole render, about content.** Every translated block the IR holds
appears in the finished document exactly once, and every illustration appears,
in order, at its own shape. That is a question the assembled book can answer and
a single page cannot, and it is the one that catches a block assembly dropped.

The rendered pages are kept as PNGs because the last gate is a person looking at
them, and a review is only worth anything while it is bound to what it describes
— the same rule `review` applies per page, applied to the book. Here the binding
is the **.docx**, not the render: Word stamps a PDF with the moment it made it,
so two renders of one unchanged book differ and a review bound there would be
stale the instant it was filed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import bookir as ir
import pagecheck
import qa
import renderqa
import review
import wordrender

#: Where the finished document's own evidence lives, kept apart from the
#: per-page reports so a reader is never in doubt which artefact a finding is
#: about.
FINAL_RENDER_DIR = ("renders", "final")
FINAL_PAGE_DIR = ("renders", "final", "pages")


def report_path(work_dir: Path) -> Path:
    return Path(work_dir) / "qa" / "document.json"


def page_png(work_dir: Path, page: int) -> Path:
    return Path(work_dir).joinpath(*FINAL_PAGE_DIR) / f"page-{page:04d}.png"


# --------------------------------------------------------------------------- #
# What the whole render must contain
# --------------------------------------------------------------------------- #

def document_expectations(book: dict[str, Any]) -> dict[str, Any]:
    """Every translated block and every illustration the book should show.

    Deliberately flat and page-free. This is the one question whose answer must
    not depend on where anything landed.
    """
    texts: list[str] = []
    translatable = 0
    images: list[dict[str, Any]] = []
    for block in book.get("blocks", []):
        if block["type"] == "image":
            width, height = block.get("width_pt"), block.get("height_pt")
            images.append({
                "id": block["id"],
                "aspect": (float(width) / float(height)) if width and height else None,
            })
            continue
        if block.get("type") not in ir.TEXT_TYPES:
            continue
        if not (block.get("text") or "").strip():
            continue
        translatable += 1
        target = (block.get("target") or "").strip()
        if target:
            texts.append(target)
    return {"page": 0, "texts": texts, "images": images,
            "translatable": translatable, "setup": dict(book.get("page") or {})}


def check_completeness(views: list[dict[str, Any]], expected: dict[str, Any],
                       report: qa.Report, *, source: str) -> None:
    """Did every block and picture survive assembly, exactly once?

    Asked of the whole book at once. A paragraph that reflowed onto a later page
    is present; a paragraph that assembly dropped is not; a paragraph written
    twice appears twice. None of those is answerable one page at a time, which
    is why the per-page pass cannot be trusted to have covered it — and why a
    document whose every page is individually well-formed can still be missing a
    page's worth of prose.

    Text is asked of the document, pictures of the render, and the split is not
    arbitrary. Arabic script does not read back faithfully from a PDF, so the
    file is the only honest source for "is this sentence in the book". A picture,
    by contrast, is only *in* the finished book if it was actually drawn, which
    is a question the file cannot answer.
    """
    whole = pagecheck.combine(views)
    pagecheck._check_text_presence(whole, expected, report, "document",
                                   source=source)
    pagecheck._check_images(whole, expected, report, "document")
    report.count("expected_blocks", len(expected["texts"]))
    report.count("expected_images", len(expected["images"]))
    report.count("rendered_images", sum(1 for i in whole["images"]
                                        if pagecheck.is_illustration(i)))


def check_assembled_page(target: dict[str, Any], setup: dict[str, Any],
                         unit: str) -> qa.Report:
    """The checks that still mean something once the book is assembled.

    Ownership by source page does not survive assembly — the built document
    paginates on its own terms, so "these blocks belong on page 7" is no longer
    a question anyone can ask. What remains is everything intrinsic to the page
    in front of you: nothing off the trim, nothing outside the body, no hole
    where a page of text should be, no text over a plate.
    """
    report = qa.Report()
    pagecheck._check_page_size(target, setup, report, unit)
    pagecheck._check_body_area(target, setup, report, unit, margins=False)
    pagecheck._check_blank_regions(target, setup, report, unit)
    pagecheck._check_overlap(target, report, unit, setup)
    # Direction is deliberately not judged here. `_check_direction` reads the
    # alignment of PyMuPDF's block boxes, and for Arabic script those do not
    # reliably reflect what is on the page: measured on a document whose every
    # paragraph carries `w:bidi`, it reported all of them as left-to-right.
    # The document's own XML answers the question exactly, so
    # `check_direction_in_document` asks it there instead.
    return report


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

def check_document(work_dir: Path, book_path: Path, docx: Path, *,
                   dpi: int = pagecheck.DEFAULT_DPI,
                   timeout: float = wordrender.DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Render the finished document, check every page and the whole of it."""
    work_dir, book_path, docx = Path(work_dir), Path(book_path), Path(docx)
    book = ir.load_book(book_path)

    try:
        rendered = renderqa.render_docx(
            docx, work_dir.joinpath(*FINAL_RENDER_DIR), timeout=timeout)
    except renderqa.RenderError as error:
        return _write(work_dir, {
            "ok": False, "verified": False, "unverified": str(error),
            "detail": f"the assembled document was not checked: {error}. It is "
                      f"unverified, not passed.",
        })

    total = pagecheck.page_count(rendered)
    if not total:
        return _write(work_dir, {
            "ok": False, "verified": False,
            "unverified": f"{rendered} came back with no pages to read; "
                          f"PyMuPDF may not be installed",
        })

    setup = book.get("page", ir.default_page_setup())
    views: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    sheets: list[str] = []

    for index in range(total):
        view = pagecheck.page_view(rendered, index)
        views.append(view)
        summary = check_assembled_page(view, setup, f"page{index + 1:04d}").summary()
        pages.append({"page": index + 1, "ok": summary["ok"],
                      "errors": summary["errors"], "warnings": summary["warnings"]})
        for finding in summary["findings"]:
            findings.append({"page": index + 1, **finding})
        # Persisted, not thrown away: the last gate is a person looking at these,
        # and a review of pages nobody kept cannot be re-read or re-checked.
        png = page_png(work_dir, index + 1)
        if pagecheck.render_png(rendered, index, png, dpi) is not None:
            sheets.append(str(png.relative_to(work_dir).as_posix()))

    whole = qa.Report()
    check_completeness(views, document_expectations(book), whole,
                       source=pagecheck.document_text(docx))
    global_summary = whole.summary()
    findings += global_summary["findings"]
    findings += pagecheck.check_direction_in_document(docx)

    subject = ir.sha256_file(docx)
    seen = review.verdict(work_dir, review.DOCUMENT, render=subject)
    if not seen["ok"]:
        # A gate nobody ran must never read as a gate that passed. The document
        # is not failed for want of a review — it is *unverified*, which is a
        # different and honest thing to report.
        return _write(work_dir, {
            "ok": False,
            "verified": False,
            "unverified": seen["detail"],
            "laid_out_by": wordrender.backend(),
            "pages": total,
            "findings": findings[:60],
            "render": str(rendered),
            "renders": sheets,
            "per_page": pages,
            "counts": global_summary.get("counts", {}),
            "document_sha256": subject,
            "detail": f"the assembled document rendered and was measured, but "
                      f"nobody has looked at it: {seen['detail']}",
        })

    return _write(work_dir, {
        "ok": not findings,
        "verified": True,
        "laid_out_by": wordrender.backend(),
        "pages": total,
        "findings": findings[:60],
        "render": str(rendered),
        "renders": sheets,
        "per_page": pages,
        "counts": global_summary.get("counts", {}),
        "document_sha256": subject,
        "reviewed": seen.get("render_sha256", "")[:12],
    })


def document_hash(work_dir: Path) -> str:
    """The identity a final review is bound to: the .docx it was made from.

    The *document*, not the render. Word stamps a PDF with the moment it made
    it, so laying one unchanged book out twice produces two different files —
    binding the review to that would make every review stale the instant it was
    filed, and a gate nobody can ever satisfy is a gate that gets removed.

    Read back from the report so the order is the documented one: check, look
    at the pages it kept, then review what you looked at.
    """
    report = report_path(Path(work_dir))
    if not report.exists():
        return ""
    try:
        return json.loads(report.read_text(encoding="utf-8")).get(
            "document_sha256", "")
    except (OSError, json.JSONDecodeError):
        return ""


def _write(work_dir: Path, body: dict[str, Any]) -> dict[str, Any]:
    written = {"schema": pagecheck.SCHEMA, "scope": "document", **body}
    ir.write_text(report_path(work_dir),
                  json.dumps(written, ensure_ascii=False, indent=1))
    return written


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)

    p_check = sub.add_parser("check", help="render the finished book and check it")
    p_check.add_argument("--book", required=True)
    p_check.add_argument("--work", required=True, help="the working directory")
    p_check.add_argument("--docx", required=True, help="the assembled document")
    p_check.add_argument("--dpi", type=int, default=pagecheck.DEFAULT_DPI)

    p_review = sub.add_parser(
        "review", help="file what a reviewer saw in the finished book")
    p_review.add_argument("--work", required=True)
    p_review.add_argument(
        "--answer", action="append", default=[], metavar="ID=yes|no",
        help="one per question: " + ", ".join(sorted(review.QUESTIONS)))
    p_review.add_argument("--note", default="",
                          help="what was wrong, in the reviewer's own words")


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel doc-qa",
                                     description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)

    if args.action == "review":
        work = Path(args.work)
        try:
            answers = dict(review.parse_answer(item) for item in args.answer)
        except ValueError as wrong:
            print(json.dumps({"ok": False, "refused": "bad-answer",
                              "detail": str(wrong),
                              "questions": review.QUESTIONS},
                             ensure_ascii=False, indent=1))
            return 2
        filed = review.record(work, review.DOCUMENT, answers, note=args.note,
                              render=document_hash(work))
        print(json.dumps(filed, ensure_ascii=False, indent=1))
        return 0 if filed["ok"] else 2

    written = check_document(Path(args.work), Path(args.book), Path(args.docx),
                             dpi=args.dpi)
    print(json.dumps(written, ensure_ascii=False, indent=1))
    return 0 if written["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

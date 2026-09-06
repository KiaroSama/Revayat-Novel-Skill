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
is wrong here by construction. The same reflow moves every section boundary
with it, so a rendered page is judged against the declared geometry it is
nearest to rather than one the book only holds for its opening section; see
`setup_for` for what that catches and what it does not.

**Section order, twice, because one reading cannot prove it.** Sizes in the
wrong order are each individually legal, so page-by-page matching sees three
runs of correct pages. `check_section_package` compares the built `w:sectPr`
sequence with `book["sections"]` — count, ordered size, orientation, start type
— and `check_section_order` requires the rendered shapes to be a walk *forward*
through the declared sequence. The first can see a section duplicated while
another is dropped, which leaves no trace on paper; the second can see a
renderer that disagreed with its own package. Adjacent sections of one shape,
and many pages inside one section, are ambiguous by nature and pass.

**Across the whole render, about content.** Every translated block the IR holds
appears in the finished document exactly once, and every illustration appears,
in order, at its own shape. That is a question the assembled book can answer and
a single page cannot, and it is the one that catches a block assembly dropped.

The rendered pages are kept as PNGs because the last gate is a person looking at
them, and a review is only worth anything while it is bound to what it describes
— the same rule `review` applies per page, applied to the book. The binding is
those **page images**, in order: they are what the reviewer saw, and one
unchanged .docx lays out differently under a font fallback, a different backend
or a newer renderer. The rendered PDF's own bytes are no use for it — Word
stamps one with the moment it made it, so two renders of one unchanged book
differ and a review bound there would be stale the instant it was filed. The
.docx hash stays in the report as provenance: it says which document these pages
are of, which is a different question from what was looked at.
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
import read_docx
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


def page_setups(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Every page geometry the finished book may legitimately show.

    ``book["page"]`` first, because that is the geometry the builder sets the
    opening section to. Every later section brings its own size, orientation
    and margins, so a book with a landscape plate or a different trim partway
    through has more than one right answer to "how big is a page", and judging
    all of them against the first one reports the correct ones as failures.
    """
    setups = [dict(book.get("page") or ir.default_page_setup())]
    setups += [dict(record) for record in (book.get("sections") or [])]
    return setups


def setup_for(target: dict[str, Any],
              setups: list[dict[str, Any]]) -> dict[str, Any]:
    """The declared geometry to judge this rendered page against.

    A rendered page cannot be traced back to the section that produced it.
    Persian reflows, so the book's page N is not source page N and every
    section boundary moves with it; a mapping by page order would be a guess
    presented as a fact. What *is* known is the set of geometries the book
    declares, so a page is judged against the declared one it is nearest to.

    **What that catches:** a page at a size no section asked for — what a lost,
    duplicated or corrupted section break looks like from the outside.
    **What it does not:** a page of the right size that belongs to the wrong
    section. Two sections sharing a trim are indistinguishable here, and this
    does not pretend otherwise. This function knows an ordered-compatible
    *range*, never which section a page came from.

    Order is therefore not this function's to prove and never was: sizes in the
    wrong order are all individually legal, so nearest-geometry matching passes
    A, C, B. `check_section_package` reads the built `w:sectPr` sequence and
    `check_section_order` walks the rendered shapes forward through the declared
    one; between them the order is checked from the XML and from the paper.

    Nearest rather than matching, so a page that matches nothing is still
    measured against the geometry it most nearly is: `_check_page_size` then
    fires with the closest declared size in its message rather than whichever
    one happens to be first.
    """
    width, height = float(target["width_pt"]), float(target["height_pt"])
    return min(setups, key=lambda setup: (
        abs(width - float(setup.get("width_pt") or 0.0))
        + abs(height - float(setup.get("height_pt") or 0.0))))


#: How close two declared page sizes have to be to count as the same shape.
#: Word stores twips and the reader rounds to points, so an exact comparison
#: fails on arithmetic rather than on a defect.
SECTION_TOLERANCE_PT = 1.0


def _shape(setup: dict[str, Any]) -> tuple[float, float]:
    return (round(float(setup.get("width_pt") or 0.0), 1),
            round(float(setup.get("height_pt") or 0.0), 1))


def _same_shape(one: dict[str, Any], other: dict[str, Any]) -> bool:
    a, b = _shape(one), _shape(other)
    return (abs(a[0] - b[0]) <= SECTION_TOLERANCE_PT
            and abs(a[1] - b[1]) <= SECTION_TOLERANCE_PT)


def check_section_package(docx: Path, book: dict[str, Any],
                          report: qa.Report) -> list[dict[str, Any]]:
    """Compare the built document's ``w:sectPr`` sequence with the book's.

    The rendered pages cannot answer this. Two sections of the same trim are
    indistinguishable on paper, so a document whose sections came out in the
    wrong order, or with one duplicated and another dropped, renders as a
    perfectly ordinary sequence of correctly sized pages. The package knows:
    the sections are in it, in order, with their sizes and start types.

    Returns the built sequence, so the render check below can walk the same one.
    """
    declared = book.get("sections") or []
    try:
        import docx as python_docx  # noqa: PLC0415
        document = python_docx.Document(str(docx))
        built = [read_docx.section_geometry(section)
                 for section in document.sections]
    except Exception as failure:
        report.add(qa.WARNING, "section-package-unread", "document",
                    f"the built document's sections could not be read back "
                    f"({failure}); their order was not checked")
        return []

    if not declared:
        return built
    if len(built) != len(declared):
        report.add(qa.ERROR, "section-count", "document",
                     f"the book declares {len(declared)} sections and the "
                     f"built document has {len(built)}; a section break was "
                     f"lost or duplicated")
        return built

    for index, (want, got) in enumerate(zip(declared, built), start=1):
        if not _same_shape(want, got):
            report.add(qa.ERROR, "section-size", f"section{index:02d}",
                         f"section {index} should be "
                         f"{_shape(want)[0]:.0f}x{_shape(want)[1]:.0f}pt and is "
                         f"{_shape(got)[0]:.0f}x{_shape(got)[1]:.0f}pt — the "
                         f"right sizes in the wrong order look exactly like this")
        for field in ("orientation", "start_type"):
            if want.get(field) and got.get(field) != want.get(field):
                report.add(qa.ERROR, "section-property", f"section{index:02d}",
                             f"section {index} {field} is {got.get(field)!r}, "
                             f"not the {want.get(field)!r} the book declares")
    return built


def declared_sections(book: dict[str, Any]) -> list[dict[str, Any]]:
    """The section sequence the book says the finished document should have."""
    return ([dict(record) for record in book["sections"]]
            if book.get("sections")
            else [dict(book.get("page") or ir.default_page_setup())])


def check_section_order(views: list[dict[str, Any]],
                        declared: list[dict[str, Any]],
                        report: qa.Report) -> None:
    """The rendered sizes must be a walk *forward* through the declared sections.

    Nearest-geometry matching, on its own, judges each page against the set of
    allowed shapes and never against their order — so A, C, B passes when A, B
    and C are all declared. This asks the one question order makes available:
    once the pages have moved on to a later section's shape, they may not go
    back to an earlier, different one.

    Measured against what the **book** declares, deliberately, and not against
    what the package turned out to contain. Walking the built sequence would be
    circular — the pages come from those sections, so they can never disagree
    with them — and would only restate `check_section_package`. Against the
    declared order it is a second, independent reading of the same defect: one
    from the XML, one from the paper.

    Permissive where the evidence is genuinely ambiguous. Adjacent sections of
    the same shape are one run and cannot be told apart on paper; many pages
    inside one section are ordinary. Only a backwards jump to a distinct earlier
    shape is reported, because only that is provable here.
    """
    if len(declared) < 2 or not views:
        return

    reached = 0
    for number, view in enumerate(views, start=1):
        if _same_shape(view, declared[reached]):
            continue
        ahead = next((index for index in range(reached + 1, len(declared))
                      if _same_shape(view, declared[index])), None)
        if ahead is not None:
            reached = ahead
            continue
        behind = next((index for index in range(reached)
                       if _same_shape(view, declared[index])), None)
        if behind is not None:
            report.add(
                qa.ERROR, "section-order", f"page{number:04d}",
                f"page {number} is section {behind + 1}'s shape "
                f"({_shape(view)[0]:.0f}x{_shape(view)[1]:.0f}pt) after the "
                f"document had already reached section {reached + 1}; the "
                f"sections came out in the wrong order")
            reached = behind
        # A shape belonging to no section at all is `_check_page_size`'s to
        # report, and it already does; saying it twice helps nobody.


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

    setups = page_setups(book)
    views: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    sheets: list[str] = []
    # Every sheet the book should have, whether or not one could be written. A
    # page missing from the evidence is a state of its own, not a gap to skip.
    pngs = [page_png(work_dir, index + 1) for index in range(total)]

    for index in range(total):
        view = pagecheck.page_view(rendered, index)
        views.append(view)
        summary = check_assembled_page(view, setup_for(view, setups),
                                       f"page{index + 1:04d}").summary()
        pages.append({"page": index + 1, "ok": summary["ok"],
                      "errors": summary["errors"], "warnings": summary["warnings"]})
        for finding in summary["findings"]:
            findings.append({"page": index + 1, **finding})
        # Persisted, not thrown away: the last gate is a person looking at these,
        # and a review of pages nobody kept cannot be re-read or re-checked.
        if pagecheck.render_png(rendered, index, pngs[index], dpi) is not None:
            sheets.append(str(pngs[index].relative_to(work_dir).as_posix()))

    whole = qa.Report()
    check_section_package(docx, book, whole)
    check_section_order(views, declared_sections(book), whole)
    check_completeness(views, document_expectations(book), whole,
                       source=pagecheck.document_text(docx))
    global_summary = whole.summary()
    findings += global_summary["findings"]
    findings += pagecheck.check_direction_in_document(docx)

    subject = ir.sha256_file(docx)
    # The review is bound to the sheets, not to the .docx. The document's bytes
    # say which book this is; they do not say what the reviewer saw, and the
    # same file lays out differently under a font fallback, another backend or
    # a newer renderer. A sheet that could not be written takes part as absent,
    # so a render that half succeeded is not the evidence one that succeeded is.
    visual = review.evidence_digest(pngs)
    seen = review.verdict(work_dir, review.DOCUMENT, render=visual)
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
            "visual_render_sha256": visual,
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
        "visual_render_sha256": visual,
        "reviewed": seen.get("render_sha256", "")[:12],
    })


def visual_hash(work_dir: Path) -> str:
    """The identity a final review is bound to: the sheets that were looked at.

    Not the .docx, which is provenance and not evidence: one document lays out
    differently under a font fallback, another rendering backend or a newer
    renderer, and a review bound to its bytes would survive a change to
    everything the reviewer saw. Not the rendered PDF either — Word stamps one
    with the moment it made it, so laying one unchanged book out twice produces
    two different files, and a gate nobody can ever satisfy is a gate that gets
    removed. The page images are the one artefact that is stable across a
    re-render of the same document and moves when the layout does.

    Read back from the report so the order is the documented one: check, look
    at the pages it kept, then review what you looked at.
    """
    report = report_path(Path(work_dir))
    if not report.exists():
        return ""
    try:
        return json.loads(report.read_text(encoding="utf-8")).get(
            "visual_render_sha256", "")
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
                              render=visual_hash(work))
        print(json.dumps(filed, ensure_ascii=False, indent=1))
        return 0 if filed["ok"] else 2

    written = check_document(Path(args.work), Path(args.book), Path(args.docx),
                             dpi=args.dpi)
    print(json.dumps(written, ensure_ascii=False, indent=1))
    return 0 if written["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

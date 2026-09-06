"""Stage 6b — a translated page is not accepted until it is looked at.

Every other gate in this pipeline reads the IR. A page can be perfect in the IR
and wrong on the page: a picture that slid onto the wrong side of a break, a
paragraph Word set left-to-right because a style lost its ``w:bidi``, a caption
clipped off the trim, a whole page that came out blank because the build failed
halfway. None of that is visible until the page is rendered and compared.

This is a *structural* comparison, not a pixel one. Persian reflows — it is a
different language set in a different direction, and the line breaks, the line
count and often the page count will not match the source. Demanding pixel
equality would fail every correct page. What must hold is structure and
geometry: the blocks that belong on this page are on it, once each; the
pictures are the same pictures in the same order at the same shape; nothing is
outside the body area or off the trim; Persian prose is set right-to-left;
there is no blank band where a page's worth of text should be.

Expectations come from ``book.json`` rather than from re-reading the source
PDF, because the IR *is* what the source page became and ownership by page is
already decided there — but the source page is still rendered beside the
translated one, because the reviewer this produces evidence for wants to see
both.

Give it ``--docx`` and it lays the document out itself; give it ``--target-pdf``
or ``--target-image`` and it uses what you hand over. The first exists because
a check nobody can run without a manual detour is a check nobody runs.

Word does the laying out on Windows and LibreOffice elsewhere — see
`wordrender`. Word first where it exists because the deliverable is a .docx and
its pagination is the one the reader will see; LibreOffice rather than nothing
elsewhere, because none of the questions asked here ("is this block present
once", "did this paragraph come out left-to-right", "is the plate the right
shape") depend on where a line broke. Which backend ran is recorded in the
report, since the two do not paginate identically.

Artifacts, all under the working directory::

    renders/source/page-NNNN.png
    renders/target/page-NNNN.png
    qa/pages/page-NNNN.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import bookir as ir
import pagecheck
import preview
import review
import qa
import runstate
import wordrender
from pagecheck import (  # noqa: F401  (this module's published surface)
    DEFAULT_DPI, MAX_ATTEMPTS, SCHEMA, body_rect, check_page, check_preview,
    check_direction_in_document, combine, expectations, is_furniture,
    is_illustration, page_count,
    page_view, render_png, views_of,
)


class RenderError(RuntimeError):
    """The page could not be laid out — the message names what to install."""


# --------------------------------------------------------------------------- #
# Run one page
# --------------------------------------------------------------------------- #

def evidence(work_dir: Path, renders: dict[str, Any]) -> str:
    """What the reviewer will actually be shown, as one identity.

    The source render and **every** target sheet, in that order. Binding a
    review to the first target PNG alone was a hole exactly the width of a page
    that reflowed: sheet two could be re-rendered from different text and the
    reviewer's "yes" would still stand, because nothing it was bound to had
    moved. A page that runs to two sheets has two sheets of evidence.
    """
    paths = []
    if renders.get("source"):
        paths.append(Path(work_dir) / renders["source"])
    paths += [Path(work_dir) / name for name in renders.get("target_sheets") or []]
    return review.evidence_digest(paths)


def report_path(work_dir: Path, page: int) -> Path:
    return work_dir / "qa" / "pages" / f"page-{page:04d}.json"


#: Returned as the index when the manifest exists, describes a PDF book, and
#: still names no source page. Distinct from "there is no manifest at all",
#: which is an EPUB or a synthetic fixture and has no source page by nature.
MISSING_MANIFEST_SOURCE = -1


def source_evidence(work_dir: Path, pages_dir: Path | None, page: int,
                    given: Path | None) -> tuple[Path | None, int]:
    """Which file holds this page's source render, and which sheet of it.

    Callers used to have to work this out: hand over the whole reference PDF
    and remember that its page index is one less than the page number. Nobody
    following the documented loop did, so `--source-pdf` was simply omitted and
    the comparison quietly became one-sided. The manifest already knows - it
    records a one-page PDF per page - so this reads it instead of asking.

    ``(path, index)`` when there is one, ``(None, MISSING_MANIFEST_SOURCE)``
    when a PDF book's manifest names none, and ``(None, 0)`` when the book has
    no source pages at all and never should have.
    """
    if given is not None:
        # An explicit file is the whole reference PDF unless it is one page.
        given = Path(given)
        return given, 0 if page_count(given) == 1 else page - 1

    pages_dir = Path(pages_dir) if pages_dir else Path(work_dir) / "pages"
    try:
        manifest = json.loads((pages_dir / "manifest.json")
                              .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, 0          # no page run here: nothing was ever promised

    if not (manifest.get("reference_pdf") or "").strip():
        return None, 0          # an EPUB or DOCX book: there is no source page
    for entry in manifest.get("chunks") or ():
        if entry.get("page") == page and entry.get("source_pdf"):
            # The split one-page PDF, so the index is always its only sheet.
            return pages_dir / entry["source_pdf"], 0
    return None, MISSING_MANIFEST_SOURCE


def check(
    work_dir: Path,
    book_path: Path,
    page: int,
    *,
    target_pdf: Path | None = None,
    target_image: Path | None = None,
    docx: Path | None = None,
    source_pdf: Path | None = None,
    pages_dir: Path | None = None,
    dpi: int = DEFAULT_DPI,
    max_attempts: int = MAX_ATTEMPTS,
    timeout: float = wordrender.DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Render this source page on its own, compare, record.

    Three outcomes, and they are deliberately distinct: passed, failed with
    named findings, or *unverified* because there was nothing to look at. The
    third must never read as the first.

    However the target arrives - a preview built here from the IR, a ``docx``
    laid out here, a PDF handed over - it describes **this source page and
    nothing else**. It is not the assembled book: Persian reflows, so the book's
    page N is not this page, and reading the book's Nth sheet is how a correct
    page comes back reported as missing its own text and carrying its
    neighbour's. The finished book is checked separately, by
    ``docqa.check_document``, against different questions.
    """
    work_dir, book_path = Path(work_dir), Path(book_path)

    # Laying the page out is done here unless the caller has already done it. A
    # converter failure is reported as unverified rather than raised: "we could
    # not look" must not be able to masquerade as "we looked and it was fine",
    # and it must not stop the report being written either.
    conversion_failure = ""
    laid_out_by = ""
    built_preview = ""

    state = runstate.RunState(work_dir)
    record = state.page(page) or {}
    attempts = int(record.get("attempts", 0))

    if record.get("state") == "failed" and attempts >= max_attempts:
        return _write(work_dir, page, {
            "ok": False,
            "verified": False,
            "refused": "retry-exhausted",
            "attempts": attempts,
            "detail": f"page {page} has failed render QA {attempts} times "
                      f"(limit {max_attempts}). Fix the build or raise "
                      f"--max-attempts; nothing was re-rendered.",
        })

    book = ir.load_book(book_path)
    expected = expectations(book, page)

    if target_pdf is None and target_image is None:
        if docx is None:
            # Nobody handed one over, so build it. A check that depends on an
            # artefact the documented workflow never produces is a check nobody
            # ever runs.
            docx = preview.preview_path(work_dir, page)
            made = preview.build(book_path, page, docx,
                                 assets=book_path.parent / "assets")
            if not made["ok"]:
                return _write(work_dir, page, {
                    "ok": False, "verified": False,
                    "unverified": made["detail"], "attempts": attempts,
                    "renders": {},
                    "detail": f"page {page} was not checked: {made['detail']}",
                })
            built_preview = str(docx)
        try:
            target_pdf = render_docx(Path(docx), work_dir / "renders" / "preview",
                                     timeout=timeout)
            laid_out_by = wordrender.backend()
        except RenderError as error:
            conversion_failure = str(error)

    renders: dict[str, Any] = {}
    wanted, source_index = source_evidence(work_dir, pages_dir, page, source_pdf)
    source_missing = ""
    if wanted is not None:
        rendered = (render_png(wanted, source_index,
                               work_dir / "renders" / "source" / f"page-{page:04d}.png",
                               dpi) if wanted.exists() else None)
        if rendered is not None:
            renders["source"] = str(rendered.relative_to(work_dir).as_posix())
        else:
            source_missing = (f"the source page for page {page} should be at "
                              f"{wanted}, and it could not be rendered from "
                              f"there")
    elif source_index == MISSING_MANIFEST_SOURCE:
        source_missing = (f"the page manifest names no source PDF for page "
                          f"{page}, so there is nothing to compare against")

    target_png = work_dir / "renders" / "target" / f"page-{page:04d}.png"
    if target_image is not None and not Path(target_image).exists():
        # Same rule as everywhere else here: failing to produce evidence must
        # not stop the page being reported on, and must never be mistaken for
        # a page that was looked at and found sound.
        conversion_failure = f"{target_image} is not there"
        target_image = None

    sheets: list[str] = []
    if target_image is not None:
        target_png.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(target_image), target_png)
        sheets.append(str(target_png.relative_to(work_dir).as_posix()))
    elif target_pdf is not None:
        # Every sheet the preview came out as is persisted, not only the first.
        # A page whose Persian runs longer than its English is ordinary, and the
        # reviewer has to be able to see the part that ran over.
        for index in range(max(1, page_count(Path(target_pdf)))):
            out = (target_png if index == 0 else
                   target_png.with_name(f"page-{page:04d}-{index + 1}.png"))
            if render_png(Path(target_pdf), index, out, dpi) is not None:
                sheets.append(str(out.relative_to(work_dir).as_posix()))
    if sheets:
        renders["target"] = sheets[0]
        renders["target_sheets"] = sheets
        # The page exists as an image now, whatever the checks below go on to
        # say about it. Recording that here rather than at the end is what makes
        # the state worth having: laying a document out is the slow part, and a
        # run that dies while judging must not come back reading `merged`, as
        # though the render had never happened.
        state.set_page(page, "rendered",
                       hashes={"render": evidence(work_dir, renders)})

    try:
        # A missing source page is "we could not look", exactly like a missing
        # target. The whole gate is *comparison*: with one side absent there is
        # nothing to compare, and the deterministic checks that would still run
        # only ever describe the target. Letting them pass alone is how a page
        # reaches `accepted` having never been set beside the page it came from.
        unverified = source_missing or _why_unverified(target_pdf,
                                                       conversion_failure)
        views = [] if unverified else views_of(Path(target_pdf))
        if not unverified and not views:
            unverified = f"{target_pdf} came back with no pages to read"
    except Exception as failure:  # a damaged PDF is "we could not look", not a pass
        unverified, views = f"{target_pdf} could not be read back: {failure}", []
    if unverified:
        return _write(work_dir, page, {
            "ok": False,
            "verified": False,
            "unverified": unverified,
            "attempts": attempts,
            # Recorded even here, and especially here. A page whose *target*
            # could not be laid out may still have had its source rendered, and
            # a downstream gate reading this report has to be able to tell that
            # apart from a page with no source at all — otherwise a missing
            # converter is reported as a missing source page.
            "source_evidence": renders.get("source", ""),
            "renders": renders,
            "detail": f"page {page} was not checked: {unverified}. It is "
                      f"unverified, not passed.",
        })

    # Text identity comes from the file when there is one; geometry always
    # comes from the render. See `pagecheck.document_text` for why.
    source = None
    if docx is not None and Path(docx).exists():
        try:
            source = pagecheck.document_text(Path(docx))
        except Exception:  # a package we cannot open is not a page we can fail
            source = None

    report = check_preview(views, expected, source=source)
    if docx is not None and Path(docx).exists():
        # Direction is asked of the file for the same reason text is: measured
        # on a document whose every paragraph carries `w:bidi`, PyMuPDF
        # reported every one of them flush-left. The XML says what Word obeys.
        for finding in pagecheck.check_direction_in_document(Path(docx)):
            report.add(finding["severity"], finding["code"],
                       f"page{page:04d}", finding["detail"])
    outcome = report.summary()
    written = _write(work_dir, page, {
        "verified": True,
        "attempts": attempts + (0 if outcome["ok"] else 1),
        # Named rather than inferred from `renders`, so a gate downstream asks
        # one question instead of re-deriving the answer from a file listing.
        "source_evidence": renders.get("source", ""),
        "renders": renders,
        "preview": built_preview or str(docx or target_pdf or ""),
        "sheets": len(views),
        # Word and LibreOffice do not paginate identically, so a reader
        # comparing two reports has to be able to tell which produced each.
        "laid_out_by": laid_out_by or "supplied by the caller",
        **outcome,
    })

    hashes = {
        "translation": ir.sha256_bytes("\n".join(expected["texts"]).encode("utf-8")),
        "qa": ir.sha256_file(report_path(work_dir, page)),
    }
    if sheets:
        hashes["render"] = evidence(work_dir, renders)
    state.set_page(
        page, "qa_passed" if written["ok"] else "failed",
        hashes=hashes,
        error="" if written["ok"] else _first_problem(written),
    )
    return written


def word_available() -> str:
    """Kept as the module's own name for "can a page be laid out here at all".

    Delegates: which backend is used is `wordrender`'s decision, and the answer
    differs per platform. Returns the reason it cannot, or ``""``.
    """
    return wordrender.unavailable_reason()


def render_docx(docx: Path, out_dir: Path,
                *, timeout: float = wordrender.DEFAULT_TIMEOUT) -> Path:
    """Lay the built document out, so there is something to look at.

    Render QA compares a *rendered* page, and the caller used to have to produce
    that PDF by hand — which made the check that exists to be run routinely the
    one nobody could run without a manual detour.

    Word on Windows, LibreOffice elsewhere, and the timeout is real on both:
    `wordrender` puts Word in a child process precisely because COM cannot be
    cancelled. Which one ran is recorded in the report, because the two do not
    paginate identically and a reader comparing two reports deserves to know.
    """
    try:
        produced, _backend = wordrender.render(docx, out_dir, timeout=timeout)
    except wordrender.RenderError as error:
        raise RenderError(str(error)) from error
    return produced


def _why_unverified(target_pdf: Path | None, conversion_failure: str = "") -> str:
    if conversion_failure:
        return conversion_failure
    if pagecheck._pymupdf() is None:
        return "PyMuPDF is not installed, so no page could be read back"
    if target_pdf is None:
        return ("no --target-pdf was given; an image alone shows a reviewer the "
                "page but carries no geometry to check")
    if not Path(target_pdf).exists():
        return f"{target_pdf} is not there"
    return ""


def _first_problem(written: dict[str, Any]) -> str:
    findings = written.get("findings") or []
    if not findings:
        return "render QA failed"
    return f"{findings[0]['code']}: {findings[0]['detail']}"


def _write(work_dir: Path, page: int, body: dict[str, Any]) -> dict[str, Any]:
    written = {"schema": SCHEMA, "page": page, **body}
    ir.write_text(report_path(work_dir, page),
                  json.dumps(written, ensure_ascii=False, indent=1) + "\n")
    return written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--book", required=True)
    parser.add_argument("--work", required=True, help="the working directory")
    parser.add_argument("--page", type=int, required=True,
                        help="1-based source page. This stage checks one page "
                             "against its source page; the finished book is a "
                             "different question, asked by `doc-qa check`")
    parser.add_argument("--target-pdf", default=None,
                        help="the translated document, converted to PDF by "
                             "whatever tool you like — this never runs one")
    parser.add_argument("--target-image", default=None,
                        help="a pre-rendered page image to file as evidence "
                             "instead of rasterising --target-pdf")
    parser.add_argument("--docx", default=None,
                        help="a page-local preview .docx to lay out instead of "
                             "building one. NOT the assembled book: source page "
                             "N is not the book's page N once Persian reflows")
    parser.add_argument("--source-pdf", default=None,
                        help="rendered beside the translation for the reviewer. Leave it off: the page manifest already names this page's "
                             "own one-page source PDF, and resolving it here is what stops the comparison quietly becoming one-sided")
    parser.add_argument("--pages", default=None,
                        help="where `pages build` wrote its manifest (default: <work>/pages)")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS)


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel render-qa",
                                     description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)

    written = check(
        Path(args.work), Path(args.book), args.page,
        target_pdf=Path(args.target_pdf) if args.target_pdf else None,
        target_image=Path(args.target_image) if args.target_image else None,
        docx=Path(args.docx) if args.docx else None,
        source_pdf=Path(args.source_pdf) if args.source_pdf else None,
        pages_dir=Path(args.pages) if args.pages else None,
        dpi=args.dpi,
        max_attempts=args.max_attempts,
    )
    print(json.dumps(written, ensure_ascii=False, indent=1))
    if written.get("refused"):
        return 2
    return 0 if written["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

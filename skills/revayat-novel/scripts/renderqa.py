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

Give it ``--docx`` and it drives **Word** to lay the document out; give it
``--target-pdf`` or ``--target-image`` and it uses what you hand over. The
first exists because a check nobody can run without a manual detour is a check
nobody runs.

Word and not a substitute renderer: the deliverable is a .docx, so Word's
pagination is the real one. A check that reports where things fall on the page
is worth nothing if the pages are not the ones the reader will see. That ties
this path to Windows, and elsewhere a page is reported *unverified* — which is
the honest answer, and not the same as passing.

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
import pagerun
import qa
import runstate


class RenderError(RuntimeError):
    """The page could not be laid out — the message names what to install."""


SCHEMA = "revayat-novel/renderqa@1"

#: Failed attempts a page may make before this refuses to look again. A render
#: that fails the same way four times is not going to pass on the fifth, and a
#: caller driving this in a loop must be able to hit a wall rather than spin.
MAX_ATTEMPTS = 3

DEFAULT_DPI = 110

#: A page size within this of the profile is the same size — a converter that
#: rounds points to millimetres and back lands a fraction of a point out.
SIZE_TOLERANCE_PT = 3.0
#: Content may sit this far outside the body box before it counts as overflow:
#: a hanging quotation mark and an italic's overhang legitimately do.
BODY_TOLERANCE_PT = 6.0
#: Two pictures are the same shape within this relative difference.
ASPECT_TOLERANCE = 0.08
#: A Persian paragraph this far short of the body's right edge, while hugging
#: the left, was set left-to-right.
RTL_TOLERANCE_PT = 24.0
#: …judged only on blocks long enough to have wrapped. A three-word heading is
#: short enough to sit anywhere without meaning anything.
RTL_MIN_CHARS = 40
#: An empty band this share of the body height, *between* two inked bands, is
#: a hole in the page rather than a chapter ending early.
BLANK_BAND_SHARE = 0.35
#: Text over a picture, as a share of the smaller of the two areas.
OVERLAP_SHARE = 0.15
#: Characters of a block's translation used to find it on the rendered page.
#: A prefix rather than the whole string, because the tail of the last block on
#: a page legitimately reflows onto the next one.
PROBE_CHARS = 48
#: Below this a probe is too generic to prove a duplicate — "«بله.»" repeats in
#: any novel — so a short block is checked for presence only.
PROBE_MIN_CHARS = 12

_SPACE = re.compile(r"\s+")


def _flat(text: str) -> str:
    """Markup stripped and whitespace collapsed — what a renderer emits."""
    return _SPACE.sub(" ", ir.plain_text(text or "")).strip()


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _pymupdf():
    """PyMuPDF, or ``None``.

    Imported here rather than at module scope so that a machine without it
    reports a page as *unverified* instead of failing to import the checker at
    all — "we could not look" and "we looked and it was fine" must never be the
    same answer.
    """
    try:
        import pymupdf  # noqa: PLC0415  (deliberate optional import)
        return pymupdf
    except ImportError:
        try:
            import fitz  # noqa: PLC0415
            return fitz
        except ImportError:
            return None


def page_view(pdf_path: Path, index: int) -> dict[str, Any]:
    """One PDF page reduced to the geometry the checks compare.

    ``{"width_pt", "height_pt", "blocks": [{"text", "bbox"}],
       "images": [{"bbox", "width_pt", "height_pt"}]}``
    """
    pymupdf = _pymupdf()
    if pymupdf is None:
        raise RuntimeError("PyMuPDF is not installed")

    doc = pymupdf.open(str(pdf_path))
    try:
        if not 0 <= index < len(doc):
            raise IndexError(f"{pdf_path} has {len(doc)} pages; wanted index {index}")
        page = doc[index]
        blocks = [
            {"text": item[4], "bbox": [round(float(v), 2) for v in item[:4]]}
            for item in page.get_text("blocks")
            if len(item) > 6 and item[6] == 0 and str(item[4]).strip()
        ]
        images = []
        for info in page.get_images(full=True):
            for rect in page.get_image_rects(info[0]):
                images.append({
                    "bbox": [round(float(v), 2) for v in rect],
                    "width_pt": round(float(rect.width), 2),
                    "height_pt": round(float(rect.height), 2),
                })
        return {
            "width_pt": round(float(page.rect.width), 2),
            "height_pt": round(float(page.rect.height), 2),
            "blocks": blocks,
            "images": images,
        }
    finally:
        doc.close()


def render_png(pdf_path: Path, index: int, out_path: Path,
               dpi: int = DEFAULT_DPI) -> Path | None:
    """Rasterise one PDF page, or ``None`` when it cannot be rendered.

    Producing evidence must never be the thing that stops a page being
    reported on: a missing or damaged file leaves the artifact unwritten and
    the report says the page is unverified.
    """
    pymupdf = _pymupdf()
    if pymupdf is None or not Path(pdf_path).exists():
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception:  # PyMuPDF raises its own hierarchy for a damaged file
        return None
    try:
        if not 0 <= index < len(doc):
            return None
        doc[index].get_pixmap(dpi=dpi).save(str(out_path))
    finally:
        doc.close()
    return out_path


# --------------------------------------------------------------------------- #
# Expectations
# --------------------------------------------------------------------------- #

def expectations(book: dict[str, Any], page: int) -> dict[str, Any]:
    """What the translated page must show, taken from the IR.

    Uses the same ownership rule as the page jobs, so what is checked for on a
    page is exactly what was sent out to be translated for it — a block that
    ran on from the previous page is that page's to prove, not this one's.
    """
    lookup = ir.blocks_by_id(book)
    job = next((j for j in pagerun.owners(book) if j["page"] == page), None)
    if job is None:
        return {"page": page, "setup": dict(book.get("page") or {}),
                "texts": [], "images": [], "translatable": 0}

    texts: list[str] = []
    translatable = 0
    for block_id in job["block_ids"]:
        block = lookup.get(block_id) or {}
        if block.get("type") not in ir.TEXT_TYPES:
            continue
        if not (block.get("text") or "").strip():
            continue
        translatable += 1
        target = (block.get("target") or "").strip()
        if target:
            texts.append(target)

    images = []
    for block_id in job["image_ids"]:
        block = lookup.get(block_id) or {}
        width, height = block.get("width_pt"), block.get("height_pt")
        images.append({
            "id": block_id,
            "aspect": (float(width) / float(height)) if width and height else None,
        })

    return {
        "page": page,
        "setup": pagerun.geometry(book, job["block_ids"], lookup),
        "texts": texts,
        "images": images,
        "translatable": translatable,
    }


def body_rect(setup: dict[str, Any]) -> tuple[float, float, float, float]:
    """``(left, top, right, bottom)`` of the body area, in points.

    ponytail: inner/outer are read as left/right. In a mirrored-margin book
    they swap on a verso page; the two differ by 9pt in the default profile,
    which is inside the tolerance the overflow check already allows.
    """
    width = float(setup.get("width_pt") or 0.0)
    height = float(setup.get("height_pt") or 0.0)
    return (
        float(setup.get("margin_inner_pt") or 0.0),
        float(setup.get("margin_top_pt") or 0.0),
        width - float(setup.get("margin_outer_pt") or 0.0),
        height - float(setup.get("margin_bottom_pt") or 0.0),
    )


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: list[float], b: list[float]) -> float:
    return (max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
            * max(0.0, min(a[3], b[3]) - max(a[1], b[1])))


def _same_shape(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return True  # nothing recorded to compare; counted, not judged
    return abs(a - b) <= ASPECT_TOLERANCE * max(a, b)


def _check_page_size(target: dict[str, Any], setup: dict[str, Any],
                     report: qa.Report, unit: str) -> None:
    wanted_w = float(setup.get("width_pt") or 0.0)
    wanted_h = float(setup.get("height_pt") or 0.0)
    if not wanted_w or not wanted_h:
        return
    got_w, got_h = target["width_pt"], target["height_pt"]
    if (abs(got_w - wanted_w) > SIZE_TOLERANCE_PT
            or abs(got_h - wanted_h) > SIZE_TOLERANCE_PT):
        turned = (abs(got_w - wanted_h) <= SIZE_TOLERANCE_PT
                  and abs(got_h - wanted_w) <= SIZE_TOLERANCE_PT)
        report.add(qa.ERROR, "page-size", unit,
                   f"rendered {got_w}×{got_h}pt, book is {wanted_w}×{wanted_h}pt"
                   + (" — the page is on its side" if turned else ""))


def _check_body_area(target: dict[str, Any], setup: dict[str, Any],
                     report: qa.Report, unit: str) -> None:
    """Anything off the trim is clipped; anything past the margins overflows."""
    left, top, right, bottom = body_rect(setup)
    page_w, page_h = target["width_pt"], target["height_pt"]

    for item in target["blocks"] + target["images"]:
        box = item["bbox"]
        what = _flat(item.get("text", ""))[:40] or "a picture"
        if (box[0] < -BODY_TOLERANCE_PT or box[1] < -BODY_TOLERANCE_PT
                or box[2] > page_w + BODY_TOLERANCE_PT
                or box[3] > page_h + BODY_TOLERANCE_PT):
            report.add(qa.ERROR, "text-clipped", unit,
                       f"{what!r} at {box} runs off a {page_w}×{page_h}pt page")
            continue
        if (box[0] < left - BODY_TOLERANCE_PT or box[1] < top - BODY_TOLERANCE_PT
                or box[2] > right + BODY_TOLERANCE_PT
                or box[3] > bottom + BODY_TOLERANCE_PT):
            report.add(qa.ERROR, "text-overflow", unit,
                       f"{what!r} at {box} is outside the body area "
                       f"[{left}, {top}, {right}, {bottom}]")


def _check_text_presence(target: dict[str, Any], expected: dict[str, Any],
                         report: qa.Report, unit: str) -> None:
    page_text = " ".join(_flat(block["text"]) for block in target["blocks"])

    if expected["translatable"] and not expected["texts"]:
        report.add(qa.ERROR, "text-missing", unit,
                   f"{expected['translatable']} blocks belong to this page and "
                   f"none of them is translated yet")
        return

    for text in expected["texts"]:
        probe = _flat(text)[:PROBE_CHARS]
        if not probe:
            continue
        seen = page_text.count(probe)
        if seen == 0:
            report.add(qa.ERROR, "text-missing", unit,
                       f"nothing on the page begins {probe!r}")
        elif seen > 1 and len(probe) >= PROBE_MIN_CHARS:
            report.add(qa.ERROR, "text-duplicated", unit,
                       f"{probe!r} appears {seen} times on one page")


def _check_images(target: dict[str, Any], expected: dict[str, Any],
                  report: qa.Report, unit: str) -> None:
    wanted = [entry["aspect"] for entry in expected["images"]]
    got = [
        (image["width_pt"] / image["height_pt"]) if image["height_pt"] else None
        for image in sorted(target["images"], key=lambda i: (i["bbox"][1], i["bbox"][0]))
    ]

    if len(got) < len(wanted):
        report.add(qa.ERROR, "image-missing", unit,
                   f"{len(wanted)} illustrations belong on this page, {len(got)} "
                   f"are on it")
        return
    if len(got) > len(wanted):
        report.add(qa.ERROR, "image-extra", unit,
                   f"{len(got)} illustrations on a page that owns {len(wanted)}")
        return

    wrong = [i for i, (a, b) in enumerate(zip(got, wanted))
             if not _same_shape(a, b)]
    if not wrong:
        return

    # The pictures can only be told apart here by shape, so a set that matches
    # in some other order is a reordering rather than a resized picture — which
    # is exactly what a swapped pair looks like.
    order = sorted((a for a in got if a is not None))
    same_set = sorted((a for a in wanted if a is not None))
    if len(order) == len(same_set) and all(
        _same_shape(a, b) for a, b in zip(order, same_set)
    ):
        report.add(qa.ERROR, "image-reordered", unit,
                   f"the same {len(got)} illustrations, in a different order "
                   f"(positions {[i + 1 for i in wrong]})")
        return

    for index in wrong:
        report.add(qa.ERROR, "image-aspect", unit,
                   f"illustration {index + 1} is {got[index]:.3f} wide-to-tall, "
                   f"the book's is {wanted[index]:.3f}")


def _check_direction(target: dict[str, Any], setup: dict[str, Any],
                     report: qa.Report, unit: str) -> None:
    """A Persian paragraph set left-to-right anchors to the wrong margin.

    Direction cannot be read back out of a rendered page, but its consequence
    can: a wrapped RTL paragraph is flush to the body's right edge and ragged
    on the left. One flush to the left, short of the right, was set LTR.
    """
    left, _, right, _ = body_rect(setup)
    for block in target["blocks"]:
        text = _flat(block["text"])
        if len(text) < RTL_MIN_CHARS or ir.script_ratio(text)[0] < 0.5:
            continue
        box = block["bbox"]
        if (right - box[2]) > RTL_TOLERANCE_PT and (box[0] - left) <= RTL_TOLERANCE_PT:
            report.add(qa.ERROR, "paragraph-not-rtl", unit,
                       f"{text[:40]!r} is flush to the left margin and "
                       f"{right - box[2]:.0f}pt short of the right one")


def _check_blank_regions(target: dict[str, Any], setup: dict[str, Any],
                         report: qa.Report, unit: str) -> None:
    """A hole in the middle of a page is a build that gave up halfway.

    Measured from the geometry rather than from pixels: a band with no text box
    and no picture in it *is* a blank band, and counting boxes cannot be fooled
    by a light background or an anti-aliased glyph.
    """
    left, top, right, bottom = body_rect(setup)
    height = bottom - top
    if height <= 0:
        return

    spans = sorted(
        (max(top, item["bbox"][1]), min(bottom, item["bbox"][3]))
        for item in target["blocks"] + target["images"]
        if item["bbox"][3] > top and item["bbox"][1] < bottom
    )
    if not spans:
        report.add(qa.ERROR, "blank-region", unit,
                   "nothing at all inside the body area")
        return

    merged: list[list[float]] = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    for before, after in zip(merged, merged[1:]):
        gap = after[0] - before[1]
        if gap > BLANK_BAND_SHARE * height:
            report.add(qa.ERROR, "blank-region", unit,
                       f"{gap:.0f}pt of nothing between {before[1]:.0f} and "
                       f"{after[0]:.0f}, in a {height:.0f}pt body")


def _check_overlap(target: dict[str, Any], report: qa.Report, unit: str) -> None:
    for block in target["blocks"]:
        for image in target["images"]:
            shared = _intersection(block["bbox"], image["bbox"])
            smaller = min(_area(block["bbox"]), _area(image["bbox"]))
            if smaller > 0 and shared / smaller > OVERLAP_SHARE:
                report.add(qa.ERROR, "text-image-overlap", unit,
                           f"{_flat(block['text'])[:40]!r} sits on a picture "
                           f"({shared / smaller:.0%} of it covered)")


def check_page(target: dict[str, Any], expected: dict[str, Any]) -> qa.Report:
    """Every structural check, against one rendered page."""
    report = qa.Report()
    unit = f"page{expected['page']:04d}"
    setup = expected["setup"]

    _check_page_size(target, setup, report, unit)
    _check_body_area(target, setup, report, unit)
    _check_text_presence(target, expected, report, unit)
    _check_images(target, expected, report, unit)
    _check_direction(target, setup, report, unit)
    _check_blank_regions(target, setup, report, unit)
    _check_overlap(target, report, unit)

    report.count("expected_blocks", len(expected["texts"]))
    report.count("rendered_blocks", len(target["blocks"]))
    report.count("expected_images", len(expected["images"]))
    report.count("rendered_images", len(target["images"]))
    return report


# --------------------------------------------------------------------------- #
# Run one page
# --------------------------------------------------------------------------- #

def report_path(work_dir: Path, page: int) -> Path:
    return work_dir / "qa" / "pages" / f"page-{page:04d}.json"


def check(
    work_dir: Path,
    book_path: Path,
    page: int,
    *,
    target_pdf: Path | None = None,
    target_image: Path | None = None,
    docx: Path | None = None,
    source_pdf: Path | None = None,
    dpi: int = DEFAULT_DPI,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict[str, Any]:
    """Render, compare, record. Returns the report that was written.

    Three outcomes, and they are deliberately distinct: passed, failed with
    named findings, or *unverified* because there was nothing to look at. The
    third must never read as the first.
    """
    work_dir, book_path = Path(work_dir), Path(book_path)

    # Laying the document out is the caller's job only when they want it to be.
    # A converter failure is reported as unverified rather than raised: "we
    # could not look" must not be able to masquerade as "we looked and it was
    # fine", and it must not stop the report being written either.
    conversion_failure = ""
    if docx is not None and target_pdf is None and target_image is None:
        try:
            target_pdf = render_docx(Path(docx), work_dir / "renders")
        except RenderError as error:
            conversion_failure = str(error)
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

    renders: dict[str, str] = {}
    if source_pdf is not None:
        rendered = render_png(Path(source_pdf), page - 1,
                              work_dir / "renders" / "source" / f"page-{page:04d}.png",
                              dpi)
        if rendered is not None:
            renders["source"] = str(rendered.relative_to(work_dir).as_posix())

    target_png = work_dir / "renders" / "target" / f"page-{page:04d}.png"
    if target_image is not None and not Path(target_image).exists():
        # Same rule as everywhere else here: failing to produce evidence must
        # not stop the page being reported on, and must never be mistaken for
        # a page that was looked at and found sound.
        conversion_failure = f"{target_image} is not there"
        target_image = None

    if target_image is not None:
        target_png.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(target_image), target_png)
        renders["target"] = str(target_png.relative_to(work_dir).as_posix())
    elif target_pdf is not None:
        rendered = render_png(Path(target_pdf), page - 1, target_png, dpi)
        if rendered is not None:
            renders["target"] = str(target_png.relative_to(work_dir).as_posix())

    try:
        unverified = _why_unverified(target_pdf, conversion_failure)
        view = None if unverified else page_view(Path(target_pdf), page - 1)
    except Exception as failure:  # a damaged PDF is "we could not look", not a pass
        unverified, view = f"{target_pdf} could not be read back: {failure}", None
    if unverified:
        return _write(work_dir, page, {
            "ok": False,
            "verified": False,
            "unverified": unverified,
            "attempts": attempts,
            "renders": renders,
            "detail": f"page {page} was not checked: {unverified}. It is "
                      f"unverified, not passed.",
        })

    outcome = check_page(view, expected).summary()
    written = _write(work_dir, page, {
        "verified": True,
        "attempts": attempts + (0 if outcome["ok"] else 1),
        "renders": renders,
        **outcome,
    })

    hashes = {
        "translation": ir.sha256_bytes("\n".join(expected["texts"]).encode("utf-8")),
        "qa": ir.sha256_file(report_path(work_dir, page)),
    }
    if "target" in renders:
        hashes["render"] = ir.sha256_file(target_png)
    state.set_page(
        page, "qa_passed" if written["ok"] else "failed",
        hashes=hashes,
        error="" if written["ok"] else _first_problem(written),
    )
    return written


#: Word's own "save as PDF" format code.
WORD_PDF_FORMAT = 17


def word_available() -> str:
    """Why Word cannot be driven here, or ``""`` when it can.

    Deliberately Word and not LibreOffice. The two paginate differently, and a
    check that reports where things fall on the page is worth nothing if the
    pages are not the ones the reader will see — the document is a .docx, so
    Word's layout is the real one and anything else is an approximation being
    passed off as the answer.
    """
    if sys.platform != "win32":
        return ("Word can only be driven on Windows; on this platform a page "
                "cannot be laid out, so it is reported unverified rather than "
                "measured against a different renderer's pagination")
    try:
        import win32com.client  # noqa: F401,PLC0415
    except ImportError:
        return ("pywin32 is not installed, so Word cannot be driven "
                "(pip install pywin32)")
    return ""


def render_docx(docx: Path, out_dir: Path, *, timeout: float = 300.0) -> Path:
    """Lay the built document out with Word, so there is something to look at.

    Render QA compares a *rendered* page, and until now the caller had to
    produce that PDF by hand — which meant the check that exists to be run
    routinely was the one nobody could run without a manual detour.

    ``timeout`` is accepted for interface symmetry and not enforced: COM is a
    blocking call with no cancellation, so promising a bound that cannot be
    kept would be worse than saying plainly that this waits for Word.
    """
    reason = word_available()
    if reason:
        raise RenderError(reason + ". Or render it yourself and pass --target-pdf.")

    import pythoncom  # noqa: PLC0415
    import win32com.client  # noqa: PLC0415

    docx = Path(docx).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    produced = (out_dir / (docx.stem + ".pdf")).resolve()

    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = False
        document = word.Documents.Open(str(docx), ReadOnly=True,
                                       AddToRecentFiles=False)
        # Repaginate before exporting: a document built by python-docx carries
        # no layout, and the field results (the TOC, the page numbers) are
        # whatever was cached at build time until Word works them out.
        document.Fields.Update()
        document.Repaginate()
        document.SaveAs2(str(produced), FileFormat=WORD_PDF_FORMAT)
    except Exception as error:  # pywin32 raises com_error, not an OSError
        raise RenderError(f"Word could not lay out {docx.name}: {error}") from error
    finally:
        # Order matters and so does the guard: leaving a hidden WINWORD.EXE
        # running is how a test run ends with a process nobody can see.
        try:
            if document is not None:
                document.Close(SaveChanges=False)
        finally:
            if word is not None:
                word.Quit()
            pythoncom.CoUninitialize()

    if not produced.exists():
        raise RenderError(f"Word reported success but wrote no PDF to {produced}")
    return produced


def _why_unverified(target_pdf: Path | None, conversion_failure: str = "") -> str:
    if conversion_failure:
        return conversion_failure
    if _pymupdf() is None:
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
    parser.add_argument("--page", type=int, required=True, help="1-based")
    parser.add_argument("--target-pdf", default=None,
                        help="the translated document, converted to PDF by "
                             "whatever tool you like — this never runs one")
    parser.add_argument("--target-image", default=None,
                        help="a pre-rendered page image to file as evidence "
                             "instead of rasterising --target-pdf")
    parser.add_argument("--docx", default=None,
                        help="the built document; laid out by Word")
    parser.add_argument("--source-pdf", default=None,
                        help="rendered beside the translation for the reviewer")
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
        source_pdf=Path(args.source_pdf) if args.source_pdf else None,
        dpi=args.dpi,
        max_attempts=args.max_attempts,
    )
    print(json.dumps(written, ensure_ascii=False, indent=1))
    if written.get("refused"):
        return 2
    return 0 if written["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

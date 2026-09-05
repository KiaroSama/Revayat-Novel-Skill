"""Stage 1b — the OCR audit trail: confidence, boxes, and what was preprocessed.

OCRmyPDF hands back a searchable PDF and nothing else. That is enough to read
the book and not nearly enough to *trust* it: once the text layer is embedded, a
word Tesseract was 18% sure of looks exactly like a word it was 99% sure of, and
a translator — human or model — will smooth the wrong one into fluent prose
without ever knowing there was a question.

So this runs Tesseract a second time for the numbers OCRmyPDF throws away and
writes them beside the book as ``source.ocr.json``. Every text block then knows
how confident its own recognition was, which is what lets the worksheet tell a
translator "this line is a guess, check the page image" instead of silently
inviting an invention.

The engine is asked for TSV rather than hOCR: the same data, no XML to parse,
and the level column (1 page, 2 block, 3 paragraph, 4 line, 5 word) gives the
hierarchy for free.

Confidence is character-weighted at every level. A plain mean lets one stray
one-character artefact at 3% drag a clean line down as hard as a real word,
which produces exactly the false alarms that make people switch a check off.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

import bookir as ir

SCHEMA = "revayat-novel/ocr@1"

#: At or above this, recognition is taken at face value.
DEFAULT_HIGH = 85.0
#: Below this, the text is a guess and has to be checked against the page image.
DEFAULT_LOW = 60.0
#: Tesseract's own recommendation; below roughly 200 accuracy falls away.
DEFAULT_DPI = 300

#: Tesseract's TSV levels.
LEVEL_PAGE, LEVEL_BLOCK, LEVEL_PARAGRAPH, LEVEL_LINE, LEVEL_WORD = 1, 2, 3, 4, 5


class SidecarError(RuntimeError):
    pass


def find_tesseract() -> str | None:
    return shutil.which("tesseract")


def grade(confidence: float | None, *, high: float = DEFAULT_HIGH,
          low: float = DEFAULT_LOW) -> str:
    """``high`` accept · ``medium`` read in context · ``low`` check the image."""
    if confidence is None:
        return "unknown"
    if confidence >= high:
        return "high"
    if confidence >= low:
        return "medium"
    return "low"


def _weighted(samples: Iterable[tuple[str, float]]) -> float | None:
    """Character-weighted mean confidence, so long words count for more."""
    total = weight = 0.0
    for text, confidence in samples:
        length = len(text.strip())
        if not length:
            continue
        total += confidence * length
        weight += length
    return round(total / weight, 2) if weight else None


# --------------------------------------------------------------------------- #
# Reading Tesseract
# --------------------------------------------------------------------------- #

def read_tsv(text: str) -> list[dict[str, Any]]:
    """Parse a TSV dump into rows with the numbers already converted.

    ``QUOTE_NONE`` matters: OCR output contains bare quotation marks all the
    time, and letting csv treat them as quoting swallows the rest of the page.
    """
    rows: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
    for raw in reader:
        if not raw.get("level"):
            continue
        try:
            rows.append({
                "level": int(raw["level"]),
                "block": int(raw["block_num"]),
                "paragraph": int(raw["par_num"]),
                "line": int(raw["line_num"]),
                "left": float(raw["left"]),
                "top": float(raw["top"]),
                "width": float(raw["width"]),
                "height": float(raw["height"]),
                "conf": float(raw["conf"]),
                "text": raw.get("text") or "",
            })
        except (TypeError, ValueError, KeyError):
            continue
    return rows


def run_tesseract(image: Path, *, language: str, binary: str,
                  timeout: float = 300.0) -> str:
    """One page in, TSV out. Raises rather than returning half a page."""
    command = [binary, str(image), "stdout", "-l", language, "tsv"]
    try:
        finished = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise SidecarError(
            f"tesseract timed out after {timeout:.0f}s on {image.name}"
        ) from error
    if finished.returncode != 0:
        detail = finished.stderr.decode("utf-8", "replace").strip()[:300]
        raise SidecarError(f"tesseract failed on {image.name}: {detail}")
    return finished.stdout.decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def build_page(tsv: str, page_number: int, *, dpi: int = DEFAULT_DPI,
               high: float = DEFAULT_HIGH,
               low: float = DEFAULT_LOW) -> dict[str, Any]:
    """Fold word rows up into lines and blocks, with the boxes in points."""
    scale = 72.0 / dpi

    def box(row: dict[str, Any]) -> list[float]:
        return [round(row["left"] * scale, 2), round(row["top"] * scale, 2),
                round((row["left"] + row["width"]) * scale, 2),
                round((row["top"] + row["height"]) * scale, 2)]

    containers: dict[tuple[int, ...], dict[str, Any]] = {}
    for row in read_tsv(tsv):
        if row["level"] == LEVEL_BLOCK:
            containers.setdefault((row["block"],), {})["bbox"] = box(row)
        elif row["level"] == LEVEL_LINE:
            key = (row["block"], row["paragraph"], row["line"])
            containers.setdefault(key, {})["bbox"] = box(row)
        elif row["level"] == LEVEL_WORD and row["text"].strip():
            key = (row["block"], row["paragraph"], row["line"])
            line = containers.setdefault(key, {"bbox": box(row)})
            line.setdefault("words", []).append(
                {"text": row["text"], "conf": round(row["conf"], 2), "bbox": box(row)}
            )

    blocks: list[dict[str, Any]] = []
    order = 0
    for key in sorted(k for k in containers if len(k) == 1):
        lines: list[dict[str, Any]] = []
        for line_key in sorted(k for k in containers if len(k) == 3 and k[0] == key[0]):
            words = containers[line_key].get("words") or []
            if not words:
                continue
            confidence = _weighted((w["text"], w["conf"]) for w in words)
            lines.append({
                "bbox": containers[line_key].get("bbox") or words[0]["bbox"],
                "confidence": confidence,
                "grade": grade(confidence, high=high, low=low),
                "text": " ".join(w["text"] for w in words),
                # Only the words a reviewer would actually have to look at are
                # kept. Storing every word turns a 400-page book into a
                # hundred-megabyte sidecar nobody opens.
                "low_words": [w for w in words if w["conf"] < low],
            })
        if not lines:
            continue
        confidence = _weighted((line["text"], line["confidence"]) for line in lines
                               if line["confidence"] is not None)
        blocks.append({
            "id": f"o{page_number:04d}-{order:03d}",
            # The TSV says where a block is, not what it is; naming a semantic
            # type here would be inventing evidence the engine never gave.
            "type": "text",
            "reading_order": order,
            "bbox": containers[key].get("bbox") or lines[0]["bbox"],
            "confidence": confidence,
            "grade": grade(confidence, high=high, low=low),
            "lines": lines,
        })
        order += 1

    page_confidence = _weighted(
        (" ".join(line["text"] for line in block["lines"]), block["confidence"])
        for block in blocks if block["confidence"] is not None
    )
    return {
        "page": page_number,
        "confidence": page_confidence,
        "grade": grade(page_confidence, high=high, low=low),
        "blocks": blocks,
    }


def page_text(page: dict[str, Any]) -> str:
    return "\n".join(
        "\n".join(line["text"] for line in block["lines"])
        for block in page.get("blocks", [])
    )


# --------------------------------------------------------------------------- #
# Building the sidecar
# --------------------------------------------------------------------------- #

def build(
    pdf: Path,
    *,
    language: str,
    dpi: int = DEFAULT_DPI,
    high: float = DEFAULT_HIGH,
    low: float = DEFAULT_LOW,
    max_pages: int | None = None,
    preprocessing: dict[str, Any] | None = None,
    binary: str | None = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Recognise every page and record how sure the engine was about it."""
    import pymupdf

    binary = binary or find_tesseract()
    if not binary:
        raise SidecarError(
            "tesseract was not found on PATH — it is needed to record OCR "
            "confidence (Windows: winget install tesseract-ocr.tesseract · "
            "macOS: brew install tesseract · Debian: apt install tesseract-ocr)"
        )

    document = pymupdf.open(pdf)
    try:
        count = len(document) if max_pages is None else min(len(document), max_pages)
        pages: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="revayat-novel-ocr-") as scratch:
            for index in range(count):
                image = Path(scratch) / f"p{index + 1:05d}.png"
                document[index].get_pixmap(dpi=dpi).save(image)
                page = build_page(
                    run_tesseract(image, language=language, binary=binary),
                    index + 1, dpi=dpi, high=high, low=low,
                )
                page["width_pt"] = round(float(document[index].rect.width), 2)
                page["height_pt"] = round(float(document[index].rect.height), 2)
                pages.append(page)
                image.unlink(missing_ok=True)
                if progress:
                    progress(index + 1, count)
    finally:
        document.close()

    graded = [page for page in pages if page["confidence"] is not None]
    return {
        "schema": SCHEMA,
        "source": {"path": str(pdf), "sha256": ir.sha256_file(pdf),
                   "pages": len(pages)},
        "engine": {"name": "tesseract", "language": language, "dpi": dpi,
                   "version": tesseract_version(binary)},
        "thresholds": {"high": high, "low": low},
        # What was done to the page images before recognition, so a poor score
        # can be traced to a preprocessing decision instead of blamed on OCR.
        "preprocessing": preprocessing or {"applied": False},
        "summary": {
            "confidence": _weighted((page_text(p), p["confidence"]) for p in graded),
            "pages_low": [p["page"] for p in pages if p["grade"] == "low"],
            "pages_medium": [p["page"] for p in pages if p["grade"] == "medium"],
        },
        "pages": pages,
    }


def tesseract_version(binary: str) -> str:
    try:
        finished = subprocess.run([binary, "--version"], capture_output=True,
                                  timeout=30)
        return finished.stdout.decode("utf-8", "replace").splitlines()[0].strip()
    except Exception:
        return "unknown"


def write(sidecar: dict[str, Any], out_dir: Path) -> dict[str, str]:
    """``source.ocr.json`` plus the reconstructed plain text beside it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "source.ocr.json"
    text_path = out_dir / "source.ocr.txt"
    ir.write_text(json_path, json.dumps(sidecar, ensure_ascii=False, indent=1))
    ir.write_text(text_path, "\n\n".join(
        f"[page {page['page']}]\n{page_text(page)}" for page in sidecar["pages"]
    ))
    return {"json": str(json_path), "text": str(text_path)}


# --------------------------------------------------------------------------- #
# Attaching confidence to the book
# --------------------------------------------------------------------------- #

def overlap(first: list[float], second: list[float]) -> float:
    """Intersection area as a share of the smaller of the two boxes."""
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    smaller = min((first[2] - first[0]) * (first[3] - first[1]),
                  (second[2] - second[0]) * (second[3] - second[1]))
    return intersection / smaller if smaller > 0 else 0.0


def attach(book: dict[str, Any], sidecar: dict[str, Any], *,
           minimum_overlap: float = 0.35) -> dict[str, Any]:
    """Stamp each text block with the confidence of the region it came from.

    Matching is by box overlap on the same page rather than by index: the two
    passes segment a page differently often enough that positional pairing
    quietly mislabels blocks, and a wrong confidence is worse than none at all.
    """
    by_page = {int(page["page"]): page.get("blocks", [])
               for page in sidecar.get("pages", [])}
    thresholds = sidecar.get("thresholds", {})

    matched = unmatched = 0
    counts = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
    for block in ir.iter_text_blocks(book):
        box = block.get("bbox")
        candidates = by_page.get(int(block.get("page") or 0), [])
        best = max((c for c in candidates if c.get("bbox")),
                   key=lambda c: overlap(box, c["bbox"]), default=None) if box else None
        if best is None or overlap(box, best["bbox"]) < minimum_overlap:
            unmatched += 1
            continue
        matched += 1
        words = [w for line in best["lines"] for w in line["low_words"]]
        block["ocr"] = {
            "confidence": best["confidence"],
            "grade": best["grade"],
            "source_block": best["id"],
            "source_bbox": best["bbox"],
            "low_words": [w["text"] for w in words][:12],
        }
        counts[best["grade"]] = counts.get(best["grade"], 0) + 1

    summary = {
        "matched": matched,
        "unmatched": unmatched,
        "by_grade": counts,
        "thresholds": {"high": float(thresholds.get("high", DEFAULT_HIGH)),
                       "low": float(thresholds.get("low", DEFAULT_LOW))},
    }
    book.setdefault("source", {})["ocr"] = {
        "sidecar": "source.ocr.json",
        "confidence": sidecar.get("summary", {}).get("confidence"),
        **summary,
    }
    return summary


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pdf", required=True, help="the scanned or OCR'd PDF")
    parser.add_argument("--out", required=True, help="directory for the artifacts")
    parser.add_argument("--lang", default="eng", help="Tesseract language, e.g. fas")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    parser.add_argument("--high", type=float, default=DEFAULT_HIGH,
                        help="at or above this, recognition is accepted as-is")
    parser.add_argument("--low", type=float, default=DEFAULT_LOW,
                        help="below this, the text must be checked against the image")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--book", default=None,
                        help="also stamp the confidence onto this book.json")
    parser.add_argument("--preprocessing", default=None,
                        help="a scan-clean report to record in the sidecar")


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    options = parser.parse_args(argv)

    preprocessing = None
    if options.preprocessing:
        preprocessing = json.loads(
            Path(options.preprocessing).read_text(encoding="utf-8")
        )

    def tick(done: int, total: int) -> None:
        print(f"  ocr {done}/{total}", file=sys.stderr)

    sidecar = build(
        Path(options.pdf), language=options.lang, dpi=options.dpi,
        high=options.high, low=options.low, max_pages=options.max_pages,
        preprocessing=preprocessing, progress=tick,
    )
    report: dict[str, Any] = {
        "artifacts": write(sidecar, Path(options.out)),
        "summary": sidecar["summary"],
    }

    if options.book:
        book = ir.load_book(options.book)
        report["attached"] = attach(book, sidecar)
        ir.write_text(options.book, json.dumps(book, ensure_ascii=False, indent=1))

    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Stage 5/7 — deterministic quality gates.

"Ask the model whether the translation is good" is not a check. Everything here
is mechanical and reproducible: counts, hashes, ratios, parsed XML. A model can
argue with a reviewer; it cannot argue with a missing paragraph id.

``check`` runs against ``book.json`` before the document is built.
``docx`` runs against the built file, because a structure that is correct in the
IR can still be wrong in the package — a picture whose asset went missing, a TOC
link with no matching bookmark, a footnote reference with no body.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

import bookir as ir
import falint
import glossary as gl

#: Persian usually runs a little longer than English. Outside this band a block
#: is suspicious: far too short usually means a dropped clause.
LENGTH_RATIO_MIN = 0.55
LENGTH_RATIO_MAX = 2.4
#: Ratios are meaningless for very short strings ("Yes." -> "بله.").
RATIO_MIN_CHARS = 120

ERROR = "error"
WARNING = "warning"


class Report:
    def __init__(self) -> None:
        self.findings: list[dict[str, Any]] = []

    def add(self, severity: str, code: str, unit: str, detail: str) -> None:
        self.findings.append(
            {"severity": severity, "code": code, "unit": unit, "detail": detail[:200]}
        )

    def summary(self, limit: int = 60) -> dict[str, Any]:
        errors = [f for f in self.findings if f["severity"] == ERROR]
        warnings = [f for f in self.findings if f["severity"] == WARNING]
        by_code: dict[str, int] = {}
        for finding in self.findings:
            by_code[finding["code"]] = by_code.get(finding["code"], 0) + 1
        return {
            "ok": not errors,
            "errors": len(errors),
            "warnings": len(warnings),
            "by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
            "findings": (errors + warnings)[:limit],
            "truncated": max(0, len(self.findings) - limit),
        }


# --------------------------------------------------------------------------- #
# Book-level gates
# --------------------------------------------------------------------------- #

def check_book(
    book: dict[str, Any],
    *,
    assets: Path | None = None,
    glossary: dict[str, Any] | None = None,
    require_complete: bool = True,
) -> Report:
    report = Report()
    _check_coverage(book, report, require_complete)
    _check_structure_parity(book, report)
    _check_lengths(book, report)
    _check_footnotes(book, report)
    if assets is not None:
        _check_assets(book, assets, report)
    _check_typography(book, report)
    if glossary:
        for violation in gl.check(glossary, book):
            report.add(
                WARNING, "glossary-drift", violation["block"],
                f"expected {violation['expected']!r} for "
                f"{violation['source_forms']}: {violation['excerpt']}",
            )
    return report


def _check_coverage(book: dict[str, Any], report: Report, require_complete: bool) -> None:
    severity = ERROR if require_complete else WARNING
    for block in ir.iter_text_blocks(book):
        if not (block.get("text") or "").strip():
            continue
        if not (block.get("target") or "").strip():
            report.add(severity, "untranslated-block", block["id"],
                       ir.plain_text(block["text"])[:120])


def _check_structure_parity(book: dict[str, Any], report: Report) -> None:
    """Emphasis and footnote markers must survive translation intact."""
    for block in ir.iter_text_blocks(book):
        source, target = block.get("text") or "", block.get("target") or ""
        if not target.strip():
            continue

        source_notes = sorted(ir.footnote_refs(source))
        target_notes = sorted(ir.footnote_refs(target))
        if source_notes != target_notes:
            report.add(ERROR, "footnote-marker-lost", block["id"],
                       f"source {source_notes} vs target {target_notes}")

        source_emphasis = ir.emphasis_signature(source)
        target_emphasis = ir.emphasis_signature(target)
        if source_emphasis != target_emphasis:
            report.add(WARNING, "emphasis-parity", block["id"],
                       f"(bold, italic, verbatim) {source_emphasis} -> {target_emphasis}")


def _check_lengths(book: dict[str, Any], report: Report) -> None:
    for block in ir.iter_text_blocks(book):
        source = ir.plain_text(block.get("text") or "")
        target = ir.plain_text(block.get("target") or "")
        if not target.strip() or len(source) < RATIO_MIN_CHARS:
            continue
        ratio = len(target) / len(source)
        if ratio < LENGTH_RATIO_MIN:
            report.add(ERROR, "possible-omission", block["id"],
                       f"target is {ratio:.0%} of source length")
        elif ratio > LENGTH_RATIO_MAX:
            report.add(WARNING, "possible-padding", block["id"],
                       f"target is {ratio:.0%} of source length")


def _check_footnotes(book: dict[str, Any], report: Report) -> None:
    defined = {note["id"] for note in book.get("footnotes", [])}
    referenced: set[str] = set()
    for block in ir.iter_text_blocks(book):
        referenced.update(ir.footnote_refs(block.get("target") or block.get("text") or ""))

    for note_id in sorted(referenced - defined):
        report.add(ERROR, "footnote-undefined", note_id, "referenced but never defined")
    for note_id in sorted(defined - referenced):
        report.add(WARNING, "footnote-unreferenced", note_id,
                   "defined but no marker points at it; it will not appear")
    for note in book.get("footnotes", []):
        if note["id"] in referenced and not (note.get("target") or "").strip():
            report.add(WARNING, "footnote-untranslated", note["id"],
                       ir.plain_text(note.get("text") or "")[:100])


def _check_assets(book: dict[str, Any], assets: Path, report: Report) -> None:
    """Every picture must still be on disk and byte-identical to extraction."""
    for block in book.get("blocks", []):
        if block["type"] != "image":
            continue
        path = assets / block["asset"]
        if not path.exists():
            report.add(ERROR, "asset-missing", block["id"], str(path))
            continue
        expected = block.get("sha256")
        if expected and ir.sha256_file(path) != expected:
            report.add(ERROR, "asset-modified", block["id"],
                       f"{block['asset']} no longer matches its extraction hash")


def _check_typography(book: dict[str, Any], report: Report) -> None:
    severity_by_code = {
        "untranslated": ERROR,
        "arabic-forms": WARNING,
        "guillemets": WARNING,
        "latin-quotes": WARNING,
        "double-punctuation": WARNING,
        "script-collision": WARNING,
    }
    for finding in falint.lint_book(book)["findings"]:
        report.add(
            severity_by_code.get(finding["code"], WARNING),
            finding["code"], finding["unit"], finding["detail"],
        )


# --------------------------------------------------------------------------- #
# Package-level gates
# --------------------------------------------------------------------------- #

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_BOOKMARK = re.compile(r'<w:bookmarkStart[^>]*w:name="([^"]+)"')
_ANCHOR = re.compile(r'<w:hyperlink[^>]*w:anchor="([^"]+)"')
_FOOTNOTE_REF = re.compile(r'<w:footnoteReference[^>]*w:id="(-?\d+)"')
_FOOTNOTE_BODY = re.compile(r'<w:footnote[^>]*w:id="(-?\d+)"')
_EXTENT = re.compile(r"<wp:extent[^>]*cx=\"(\d+)\"[^>]*cy=\"(\d+)\"")


def check_docx(path: Path, book: dict[str, Any] | None = None) -> Report:
    report = Report()
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        report.add(ERROR, "docx-unreadable", str(path), str(error))
        return report

    with archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            report.add(ERROR, "docx-invalid", str(path), "no word/document.xml")
            return report

        document = archive.read("word/document.xml").decode("utf-8", "replace")
        content_types = archive.read("[Content_Types].xml").decode("utf-8", "replace")

        bookmarks = set(_BOOKMARK.findall(document))
        for anchor in sorted(set(_ANCHOR.findall(document))):
            if anchor not in bookmarks:
                report.add(ERROR, "dead-link", anchor,
                           "internal link has no matching bookmark")

        refs = {int(x) for x in _FOOTNOTE_REF.findall(document)}
        if refs:
            if "word/footnotes.xml" not in names:
                report.add(ERROR, "footnotes-part-missing", str(path),
                           f"{len(refs)} references but no word/footnotes.xml")
            else:
                footnotes = archive.read("word/footnotes.xml").decode("utf-8", "replace")
                bodies = {int(x) for x in _FOOTNOTE_BODY.findall(footnotes)}
                for missing in sorted(refs - bodies):
                    report.add(ERROR, "footnote-body-missing", str(missing),
                               "reference with no footnote body")
                if "footnotes+xml" not in content_types:
                    report.add(ERROR, "footnotes-content-type", str(path),
                               "footnotes part is not declared in [Content_Types].xml")

        media = [n for n in names if n.startswith("word/media/")]
        extents = _EXTENT.findall(document)
        if len(extents) < len(media):
            report.add(WARNING, "picture-size-implicit", str(path),
                       f"{len(media)} media parts but only {len(extents)} sized extents")

        if "w:bidi" not in document:
            report.add(WARNING, "no-rtl", str(path),
                       "no w:bidi found — the document is not right-to-left")

        if book is not None:
            expected = sum(1 for b in book["blocks"] if b["type"] == "image")
            unique = len({b["sha256"] for b in book["blocks"]
                          if b["type"] == "image" and b.get("sha256")})
            if media and unique and len(media) < unique:
                report.add(ERROR, "images-lost", str(path),
                           f"{unique} unique images expected, {len(media)} in package")
            elif not media and expected:
                report.add(ERROR, "images-lost", str(path),
                           f"{expected} images expected, none in package")

    return report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat qa", description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)

    p_check = sub.add_parser("check", help="gate book.json before building")
    p_check.add_argument("--book", required=True)
    p_check.add_argument("--assets", default=None)
    p_check.add_argument("--glossary", default=None)
    p_check.add_argument("--allow-incomplete", action="store_true")
    p_check.add_argument("--limit", type=int, default=60)

    p_docx = sub.add_parser("docx", help="gate the built .docx")
    p_docx.add_argument("--file", required=True)
    p_docx.add_argument("--book", default=None)
    p_docx.add_argument("--limit", type=int, default=60)

    args = parser.parse_args(argv)

    if args.action == "check":
        book_path = Path(args.book)
        book = ir.load_book(book_path)
        assets = Path(args.assets) if args.assets else book_path.parent / "assets"
        glossary = gl.load(Path(args.glossary)) if args.glossary else None
        report = check_book(book, assets=assets if assets.exists() else None,
                            glossary=glossary,
                            require_complete=not args.allow_incomplete)
    else:
        book = ir.load_book(args.book) if args.book else None
        report = check_docx(Path(args.file), book)

    summary = report.summary(args.limit)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

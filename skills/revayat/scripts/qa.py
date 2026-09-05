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
from collections import Counter
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
#: Two blocks with the same translation this long did not arrive there by
#: coincidence. A sentence can repeat; a paragraph of 120+ characters matching
#: another one exactly is a pasted worksheet reply.
DUPLICATE_MIN_CHARS = 120
#: A run of source text this long, alive inside the translation, is a clause
#: that was never translated. The longest run that legitimately survives is the
#: first-mention parenthetical — "(Elizabeth Bennet)", 18 characters — and
#: `verbatim` spans are removed before the comparison, so 40 leaves a margin
#: wide enough that no name or quoted token can reach it.
COPIED_RUN_CHARS = 40

ERROR = "error"
WARNING = "warning"


class Report:
    def __init__(self) -> None:
        self.findings: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}

    def add(self, severity: str, code: str, unit: str, detail: str) -> None:
        self.findings.append(
            {"severity": severity, "code": code, "unit": unit, "detail": detail[:200]}
        )

    def count(self, name: str, value: int) -> None:
        """Record a total the findings cannot express.

        ``findings`` is capped at ``limit``, so a list of untranslated blocks
        says nothing about whether one paragraph was missed or a thousand.
        """
        self.counts[name] = value

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
            "counts": dict(sorted(self.counts.items())),
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
    strict: bool = False,
) -> Report:
    """Gate the translated book.

    ``strict`` promotes the fidelity findings — emphasis that did not survive,
    a locked name that drifted — from advice to blocking errors. They are
    warnings by default because Persian legitimately needs a different number
    of emphasised words sometimes; for publication work that latitude is not
    wanted, and this is the switch that removes it.
    """
    report = Report()
    _check_coverage(book, report, require_complete)
    _check_structure_parity(book, report, strict)
    _check_lengths(book, report)
    _check_duplicate_targets(book, report)
    _check_copied_runs(book, report)
    _check_footnotes(book, report)
    if assets is not None:
        _check_assets(book, assets, report)
    _check_typography(book, report)
    _check_ocr_confidence(book, report, strict)
    if glossary:
        _check_first_mentions(book, glossary, report)
        for violation in gl.check(glossary, book):
            report.add(
                ERROR if strict else WARNING, "glossary-drift", violation["block"],
                f"expected {violation['expected']!r} for "
                f"{violation['source_forms']}: {violation['excerpt']}",
            )
    return report


def _check_coverage(book: dict[str, Any], report: Report, require_complete: bool) -> None:
    """Every source block must come back with Persian in it.

    The totals matter as much as the findings. ``findings`` is truncated, so on
    its own it cannot tell "one heading was missed" from "the last four chunks
    were never translated" — the two look identical at the top of the list.
    Headings are counted apart from the rest because a book that lost only its
    headings still reads as 98% complete while its table of contents is empty.
    """
    severity = ERROR if require_complete else WARNING
    totals = {"headings": 0, "headings_translated": 0,
              "paragraphs": 0, "paragraphs_translated": 0}
    for block in ir.iter_text_blocks(book):
        if not (block.get("text") or "").strip():
            continue
        group = "headings" if block["type"] == "heading" else "paragraphs"
        totals[group] += 1
        if (block.get("target") or "").strip():
            totals[f"{group}_translated"] += 1
        else:
            report.add(severity, "untranslated-block", block["id"],
                       ir.plain_text(block["text"])[:120])
    for name, value in totals.items():
        report.count(name, value)


def _prose(text: str) -> str:
    """Everything a translator was meant to rewrite.

    ``verbatim`` spans come out on both sides: `` `literal_token` `` is
    *supposed* to survive byte for byte, so leaving it in would report the
    feature working as a leak.
    """
    return "".join(span["text"] for span in ir.parse_markup(text or "")
                   if not span["verbatim"])


def _copied_run(source: str, target: str, window: int = COPIED_RUN_CHARS) -> str:
    """The first ``window``-character run of ``source`` still verbatim in
    ``target``, or ``""``.

    Cost is O(len(source) x len(target)) per block, which on a real 300k-character
    book is a fraction of a second beside the markup parsing QA already does for
    every block several times over.
    """
    if len(source) < window or not target:
        return ""
    for start in range(len(source) - window + 1):
        run = source[start:start + window]
        # A leader line of dots or a column of figures legitimately survives
        # translation; only letters make a run prose.
        if run in target and any(character.isalpha() for character in run):
            return run
    return ""


def _check_copied_runs(book: dict[str, Any], report: Report) -> None:
    """A clause of the source that reached the translation unchanged.

    ``untranslated`` fires on a block that is *mostly* the source language. This
    catches the commoner shape it cannot see: fluent Persian with one English
    clause left in the middle of it, where the script ratio never trips.
    """
    for block in ir.iter_text_blocks(book):
        target = block.get("target") or ""
        if not target.strip():
            continue
        run = _copied_run(_prose(block.get("text") or ""), _prose(target))
        if run:
            report.add(ERROR, "copied-source-run", block["id"],
                       f"{run!r} was carried over from the source unchanged; "
                       f"re-run this chunk and translate that clause")


def _check_duplicate_targets(book: dict[str, Any], report: Report) -> None:
    """Two different blocks, one identical translation.

    A worksheet answered by pasting the previous reply lands as a run of blocks
    all carrying the same Persian, and every existing gate passes it: nothing is
    missing, the ids all resolve, the length ratios are plausible. Blocks whose
    *sources* are identical are excluded — a refrain or a running head really
    should translate the same way twice.
    """
    first_seen: dict[str, tuple[str, str]] = {}   # target -> (block id, source)
    for block in ir.iter_text_blocks(book):
        target = ir.plain_text(block.get("target") or "").strip()
        if len(target) <= DUPLICATE_MIN_CHARS:
            continue
        source = ir.plain_text(block.get("text") or "").strip()
        previous = first_seen.get(target)
        if previous is None:
            first_seen[target] = (block["id"], source)
        elif previous[1] != source:
            report.add(ERROR, "duplicate-translation", block["id"],
                       f"word for word the translation of {previous[0]}, whose "
                       f"source is different; re-run this chunk")


#: The original spelling a first-mention form carries, e.g. "(Elizabeth Bennet)".
_PARENTHETICAL = re.compile(r"\([^()]{2,}\)")


def _check_first_mentions(book: dict[str, Any], glossary: dict[str, Any],
                          report: Report) -> None:
    """The original spelling belongs in exactly one place.

    Chunks are translated in parallel by agents that cannot see each other, so
    left to their own judgement every one of them answers "yes, this is the
    first mention" and «الیزابت بنت (Elizabeth Bennet)» is repeated through the
    whole book. The worksheet already names the chunk that owns the
    introduction; this is the gate that proves it was obeyed.
    """
    targets = [(block["id"], block.get("target") or "")
               for block in ir.iter_text_blocks(book)]
    for entry in glossary.get("entries", []):
        match = _PARENTHETICAL.search(entry.get("first_form") or "")
        if not match:
            continue
        introduction = match.group(0)
        where = [block_id for block_id, target in targets if introduction in target]
        if len(where) > 1:
            owner = entry.get("first_block_id") or where[0]
            report.add(ERROR, "first-mention-repeated", entry.get("id") or introduction,
                       f"{introduction} is introduced in {len(where)} blocks "
                       f"({', '.join(where[:4])}); keep it in {owner} and re-run "
                       f"the other chunk(s)")


def _check_structure_parity(book: dict[str, Any], report: Report,
                            strict: bool = False) -> None:
    """Emphasis and footnote markers must survive translation intact.

    Footnotes are compared by origin, not by count. Every note that came with
    the book has to still be there; a note the *translator* added is a new
    marker with no counterpart in the source, and that is the feature working,
    not a defect.
    """
    added_by_translator = {
        note["id"] for note in book.get("footnotes", [])
        if note.get("origin") == "translator"
    }

    for block in ir.iter_text_blocks(book):
        source, target = block.get("text") or "", block.get("target") or ""
        if not target.strip():
            continue

        source_notes = set(ir.footnote_refs(source))
        target_notes = set(ir.footnote_refs(target))
        dropped = sorted(source_notes - target_notes)
        invented = sorted(target_notes - source_notes - added_by_translator)
        if dropped:
            report.add(ERROR, "footnote-marker-lost", block["id"],
                       f"the source's note(s) {dropped} are missing from the translation")
        if invented:
            report.add(ERROR, "footnote-marker-invented", block["id"],
                       f"marker(s) {invented} point at notes the book never had")

        source_emphasis = ir.emphasis_signature(source)
        target_emphasis = ir.emphasis_signature(target)
        if source_emphasis != target_emphasis:
            report.add(ERROR if strict else WARNING, "emphasis-parity", block["id"],
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


def _check_ocr_confidence(book: dict[str, Any], report: Report,
                          strict: bool = False) -> None:
    """Surface the blocks the OCR engine itself was not sure about.

    Recognition confidence is the one defect class that is invisible after the
    fact: a misread word is a real Persian word in a fluent sentence, and no
    length ratio, glossary rule or typography pass can see it. The only thing
    that can is the number the engine gave at the time — so a block it graded
    low is reported with its page and the words in question, and accepting it
    means looking at the page image.

    A warning by default, because a low score is a reason to check rather than
    proof of an error; ``--strict`` makes it blocking for publication work.
    """
    for block in ir.iter_text_blocks(book):
        evidence = block.get("ocr")
        if not evidence or evidence.get("grade") != "low":
            continue
        words = evidence.get("low_words") or []
        detail = f"page {block.get('page')}, recognition {evidence.get('confidence')}%"
        if words:
            detail += " — check " + ", ".join(repr(w) for w in words[:5])
        report.add(ERROR if strict else WARNING, "ocr-low-confidence",
                   block["id"], detail)


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
#: One per picture, in the order Word lays them out.
_BLIP = re.compile(r'<a:blip[^>]*r:embed="([^"]+)"')
_RELATIONSHIP = re.compile(r"<Relationship\b[^>]*>")
#: Attributes are pulled by name rather than by position: the order of ``Id``,
#: ``Type`` and ``Target`` inside a relationship is not fixed by the format.
_ATTRIBUTE = re.compile(r'(\w+)="([^"]*)"')


def _package_image_order(archive: zipfile.ZipFile, document: str) -> list[str]:
    """The SHA-256 of every picture, in the order the document shows them.

    python-docx stores one media part per *distinct* image, so two identical
    pictures share a part and ``word/media/`` cannot describe order at all. The
    ``<a:blip r:embed>`` sequence in ``document.xml`` can, once each
    relationship is followed back to the bytes it points at.
    """
    try:
        rels = archive.read("word/_rels/document.xml.rels").decode("utf-8", "replace")
    except KeyError:
        return []

    target_by_id: dict[str, str] = {}
    for tag in _RELATIONSHIP.findall(rels):
        attributes = dict(_ATTRIBUTE.findall(tag))
        if "Id" in attributes and "Target" in attributes:
            target_by_id[attributes["Id"]] = attributes["Target"]

    digests: dict[str, str] = {}
    order: list[str] = []
    for relationship_id in _BLIP.findall(document):
        target = target_by_id.get(relationship_id, "")
        if not target:
            continue
        name = target[1:] if target.startswith("/") else f"word/{target}"
        if name not in digests:
            try:
                digests[name] = ir.sha256_bytes(archive.read(name))
            except KeyError:            # an external or missing part
                digests[name] = ""
        if digests[name]:
            order.append(digests[name])
    return order


def _check_image_order(archive: zipfile.ZipFile, document: str,
                       book: dict[str, Any], report: Report) -> None:
    """The pictures must be where the book puts them, not merely present.

    Counting was the only check, and a count cannot see an illustration that
    moved: the picture is still in the file, the caption underneath it now
    belongs to a different one.
    """
    expected = [block["sha256"] for block in book.get("blocks", [])
                if block["type"] == "image" and block.get("sha256")]
    if not expected:
        return
    actual = _package_image_order(archive, document)
    report.count("pictures_placed", len(actual))
    if not actual:
        return

    # A picture whose asset went missing is already reported by asset-missing.
    # Compare only the ones present on both sides, so a single absent file does
    # not read as a reordering of everything after it.
    shared = set(expected) & set(actual)
    in_book = [digest for digest in expected if digest in shared]
    in_package = [digest for digest in actual if digest in shared]
    if in_book != in_package:
        first = next((position for position, pair in enumerate(zip(in_book, in_package))
                      if pair[0] != pair[1]), min(len(in_book), len(in_package)))
        report.add(ERROR, "image-order", f"picture {first + 1}",
                   "the pictures are not in the book's order — a caption now "
                   "sits under the wrong illustration; rebuild from book.json "
                   "instead of editing the document")


def _check_bookmarks(names: list[str], book: dict[str, Any] | None,
                     report: Report) -> None:
    """Bookmarks are what the table of contents and every internal link land on."""
    report.count("bookmarks", len(names))
    for name, times in sorted(Counter(names).items()):
        if times > 1:
            report.add(ERROR, "bookmark-duplicate", name,
                       f"opened {times} times; Word sends every link to the "
                       f"first, so the contents jump to the wrong chapter — "
                       f"rebuild instead of editing the document")

    if book is None:
        return
    headings = sum(1 for block in book.get("blocks", []) if block["type"] == "heading")
    report.count("headings_in_book", headings)
    if headings and not names:
        report.add(ERROR, "bookmarks-missing", "word/document.xml",
                   f"{headings} headings and no bookmarks; the table of contents "
                   f"has nothing to link to — rebuild")


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

        bookmark_names = _BOOKMARK.findall(document)
        bookmarks = set(bookmark_names)
        for anchor in sorted(set(_ANCHOR.findall(document))):
            if anchor not in bookmarks:
                report.add(ERROR, "dead-link", anchor,
                           "internal link has no matching bookmark")
        _check_bookmarks(bookmark_names, book, report)

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
            _check_image_order(archive, document, book, report)

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
    p_check.add_argument("--strict", action="store_true",
                         help="treat emphasis loss and glossary drift as errors")
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
                            require_complete=not args.allow_incomplete,
                            strict=args.strict)
    else:
        book = ir.load_book(args.book) if args.book else None
        report = check_docx(Path(args.file), book)

    summary = report.summary(args.limit)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

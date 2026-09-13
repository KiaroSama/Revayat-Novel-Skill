"""The finished `.docx`, checked as a package rather than as a promise.

`qa check` reads `book.json`; this reads the file that was actually written. The
two answer different questions, and only this one can catch a build that dropped
a picture, renumbered a footnote or produced a document Word will not open.

Split out of `qa` so both have room: the package checks are a different kind of
work — archive members, relationship graphs, XML namespaces — from the checks over
the IR, and `qa` was over the 800-line ceiling with both inside it.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import bookir as ir
from findings import ERROR, WARNING, Report

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
    # No early return on an empty `actual`. "The document shows no pictures at
    # all" is the *strongest* form of the failure this function exists to catch,
    # and treating it as nothing to compare is how deleting the only `<w:drawing>`
    # — media part left in place, so the file count is unchanged — passed.

    # Three distinct conditions, and the old check could see only the third.
    # Comparing the *intersection* of the two sides excluded exactly the pictures
    # that were wrong: a substituted image's bytes are in neither set, so it
    # dropped out of its own comparison, and a removed placement dropped out of
    # its. Measured: replacing `word/media/image1.png` with a different image of
    # the same length passed, and deleting the `<w:drawing>` while keeping the
    # media part passed.
    placed = set(actual)
    wanted = set(expected)
    for position, digest in enumerate(expected, start=1):
        if digest not in placed:
            report.add(ERROR, "image-missing-placement", f"picture {position}",
                       "the book places this illustration and the document does "
                       "not show it — the media part may still be in the package, "
                       "which is why counting the files cannot see this")
    for position, digest in enumerate(actual, start=1):
        if digest not in wanted:
            report.add(ERROR, "image-unexpected", f"placed picture {position}",
                       "this illustration's bytes are not any picture the book "
                       "expects — it was substituted or edited after the build; "
                       "rebuild from book.json")

    # Order is asked of the pictures present on both sides, because a missing or
    # substituted one is already named above and would otherwise shift every
    # position after it.
    shared = wanted & placed
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
        # Every part this function goes on to read, checked before it reads any of
        # them. `[Content_Types].xml` was read without asking, so a package
        # missing it raised `KeyError` out of a function whose whole job is to
        # report what is wrong with a package — the one failure mode a verifier
        # must never have.
        for required in ("word/document.xml", "[Content_Types].xml",
                         "word/_rels/document.xml.rels"):
            if required not in names:
                report.add(ERROR, "docx-invalid", str(path),
                           f"no {required}: this is not a Word package Word will "
                           f"open, whatever else is in it")
        if report.summary()["errors"]:
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

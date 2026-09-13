"""A `.docx` read as OPC: parsed XML, resolved relationships, bounded archive.

The package checks were lexical — regexes over the raw text of
``word/document.xml``, a substring test for ``footnotes+xml`` in
``[Content_Types].xml``, a count of ``<wp:extent>`` tags. Every one of those
answers a question about the *serialisation* rather than about the document, and
measured on real mutations they answer it wrongly in both directions:

* a package whose relationship elements use a different (and perfectly legal)
  namespace prefix was **rejected**, because the regex looks for one spelling;
* a truncated ``word/document.xml`` **passed**, because a regex over broken XML
  simply finds fewer matches and nothing said the part would not parse;
* removing a footnote's relationship **passed**, because nothing resolved the
  relationship graph at all — only the content-type string was looked for.

So this module does the boring OPC work once, safely: parse with the standard
namespace-aware parser, resolve each part's content type through the Default and
Override rules, resolve relationships to normalised part names, and report a
corrupt archive or a part that will not parse **as a finding** rather than as an
exception out of the verifier.

Bounds are part of the contract. A verifier is exactly the code that gets handed a
hostile file, so an archive is refused before it is read when a member is
implausibly large, when the whole thing expands beyond :data:`MAX_TOTAL`, or when
a member name escapes the package (``..``, an absolute path, a drive letter). The
XML parser is the standard library's, used without any entity resolution or
external DTD fetching: `ElementTree` does not resolve external entities, which is
the property that matters here.
"""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

#: Namespaces, by the short name this project uses for them.
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}

#: Relationship types this project resolves by name rather than by guesswork.
REL = {
    "footnotes": "http://schemas.openxmlformats.org/officeDocument/2006/"
                 "relationships/footnotes",
    "image": "http://schemas.openxmlformats.org/officeDocument/2006/"
             "relationships/image",
    "header": "http://schemas.openxmlformats.org/officeDocument/2006/"
              "relationships/header",
}

#: A single part larger than this, or a package expanding past the total, is not
#: a book this pipeline produced — it is something to refuse rather than read.
MAX_PART = 256 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
#: A member that expands more than this many times its stored size is a bomb.
MAX_RATIO = 200


def qname(prefix: str, tag: str) -> str:
    """``{namespace}tag`` — what a parsed element's ``.tag`` actually is."""
    return f"{{{NS[prefix]}}}{tag}"


class Damaged(Exception):
    """The package cannot be read far enough to be judged.

    Raised instead of letting a `zipfile` or parser error escape, so the caller
    reports "this is not a readable package" rather than crashing inside the
    function whose job is to say what is wrong with it.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _safe_name(name: str) -> bool:
    """Would extracting this member stay inside the package?"""
    if not name or name.startswith("/") or ":" in name.split("/")[0]:
        return False
    return ".." not in posixpath.normpath(name).split("/")


class Package:
    """One opened `.docx`, read lazily and parsed on request."""

    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.archive = archive
        self.names = [info.filename for info in archive.infolist()
                      if not info.is_dir()]
        self._parsed: dict[str, Any] = {}
        self._types: dict[str, str] | None = None

    # -- parts ------------------------------------------------------------- #

    def read(self, name: str) -> bytes:
        try:
            return self.archive.read(name)
        except (KeyError, OSError, zipfile.BadZipFile) as failure:
            raise Damaged("part-unreadable",
                          f"{name} could not be read: {failure}") from None

    def xml(self, name: str) -> ElementTree.Element:
        """The parsed root of one part. Raises :class:`Damaged` if it will not parse.

        A truncated or hand-edited part is the case this exists for: it used to
        pass every check, because a regex over broken XML finds fewer matches and
        nothing noticed that the matches were missing rather than absent.
        """
        if name not in self._parsed:
            try:
                self._parsed[name] = ElementTree.fromstring(self.read(name))
            except ElementTree.ParseError as failure:
                raise Damaged("part-malformed",
                              f"{name} is not well-formed XML: {failure}") from None
        return self._parsed[name]

    # -- content types ------------------------------------------------------ #

    def content_types(self) -> dict[str, str]:
        """``part name -> content type``, resolved through Default and Override.

        The rule the format specifies, not a substring search: a Default maps an
        extension, an Override names one part, and the Override wins. Looking for
        ``footnotes+xml`` anywhere in the file answers neither question — it is
        true of a package that declares the type for the wrong part, and of one
        that mentions it in a comment.
        """
        if self._types is not None:
            return self._types
        root = self.xml("[Content_Types].xml")
        if root.tag != qname("ct", "Types"):
            raise Damaged("content-types-invalid",
                          f"[Content_Types].xml has root {root.tag!r}, not Types")
        defaults: dict[str, str] = {}
        overrides: dict[str, str] = {}
        for element in root:
            if element.tag == qname("ct", "Default"):
                extension = (element.get("Extension") or "").lower()
                if extension:
                    defaults[extension] = element.get("ContentType") or ""
            elif element.tag == qname("ct", "Override"):
                part = (element.get("PartName") or "").lstrip("/")
                if part:
                    overrides[part] = element.get("ContentType") or ""

        resolved: dict[str, str] = {}
        for name in self.names:
            extension = name.rpartition(".")[2].lower()
            resolved[name] = overrides.get(name, defaults.get(extension, ""))
        self._types = resolved
        return resolved

    # -- relationships ------------------------------------------------------ #

    def relationships(self, part: str) -> dict[str, dict[str, str]]:
        """``id -> {type, target, mode, part}`` for one part's relationships.

        ``part`` is the *source* part (``word/document.xml``); its relationships
        live beside it in ``_rels``. Targets are resolved against the source's
        directory and normalised, so ``../media/image1.png`` and
        ``/word/media/image1.png`` come back as the same part name — the
        comparison every caller actually wants to make.
        """
        directory, _, base = part.rpartition("/")
        rels_name = f"{directory}/_rels/{base}.rels" if directory else f"_rels/{base}.rels"
        if rels_name not in self.names:
            return {}
        root = self.xml(rels_name)
        if root.tag != qname("pr", "Relationships"):
            raise Damaged("relationships-invalid",
                          f"{rels_name} has root {root.tag!r}, not Relationships")
        found: dict[str, dict[str, str]] = {}
        for element in root:
            if element.tag != qname("pr", "Relationship"):
                continue
            identifier = element.get("Id") or ""
            target = element.get("Target") or ""
            mode = element.get("TargetMode") or "Internal"
            if not identifier:
                continue
            if mode == "External":
                resolved = ""
            elif target.startswith("/"):
                resolved = target.lstrip("/")
            else:
                resolved = posixpath.normpath(posixpath.join(directory, target))
            found[identifier] = {"type": element.get("Type") or "",
                                 "target": target, "mode": mode,
                                 "part": resolved}
        return found

    def related(self, part: str, kind: str) -> list[str]:
        """Every part ``part`` points at with the ``kind`` relationship type."""
        wanted = REL[kind]
        return [record["part"] for record in self.relationships(part).values()
                if record["type"] == wanted and record["part"]]


def open_package(path: Path) -> Package:
    """Open a `.docx`, refusing an archive that is not safe to read.

    The bounds are checked from the central directory, before a single member is
    decompressed: a verifier is precisely the code that is handed a hostile file.
    """
    try:
        archive = zipfile.ZipFile(Path(path))
    except (OSError, zipfile.BadZipFile) as failure:
        raise Damaged("docx-unreadable", str(failure)) from None

    total = 0
    for info in archive.infolist():
        if not _safe_name(info.filename):
            archive.close()
            raise Damaged("member-escapes",
                          f"{info.filename!r} would be written outside the "
                          f"package")
        if info.file_size > MAX_PART:
            archive.close()
            raise Damaged("member-too-large",
                          f"{info.filename} expands to {info.file_size} bytes")
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            archive.close()
            raise Damaged("member-ratio",
                          f"{info.filename} expands "
                          f"{info.file_size // max(1, info.compress_size)}x, which "
                          f"no document produces")
        total += info.file_size
    if total > MAX_TOTAL:
        archive.close()
        raise Damaged("package-too-large",
                      f"the package expands to {total} bytes in total")
    return Package(archive)


# --------------------------------------------------------------------------- #
# Reading a Word document's own shapes
# --------------------------------------------------------------------------- #

def text_of(element: ElementTree.Element) -> str:
    """Every ``w:t`` under ``element``, joined — the text Word will show.

    Word splits one sentence across runs whenever anything about the formatting
    changes, so the text of a paragraph is never one node's ``.text``.
    """
    return "".join(node.text or ""
                   for node in element.iter(qname("w", "t")))


def footnote_references(document: ElementTree.Element) -> list[str]:
    """Every footnote reference id, in document order, with repeats kept.

    A set loses exactly the defect a duplicate reference is: two markers pointing
    at one note, so the note prints twice and one of the two sentences is footnoted
    by something written for the other.
    """
    return [node.get(qname("w", "id")) or ""
            for node in document.iter(qname("w", "footnoteReference"))]


def footnote_bodies(footnotes: ElementTree.Element) -> dict[str, str]:
    """``id -> the note's text``, for the real notes only.

    Word keeps two housekeeping notes, id ``0`` and ``-1``, for the separator and
    the continuation mark. They carry no text and are not the book's.
    """
    bodies: dict[str, str] = {}
    for note in footnotes.iter(qname("w", "footnote")):
        identifier = note.get(qname("w", "id")) or ""
        if identifier in ("0", "-1", ""):
            continue
        bodies[identifier] = text_of(note).strip()
    return bodies


def drawing_extents(document: ElementTree.Element) -> list[tuple[str, int, int]]:
    """``(relationship id, cx, cy)`` per placed picture, in document order.

    The extent is in EMU and is what Word actually draws, so a distorted picture
    is visible here and nowhere else in the package: the bytes are untouched.
    """
    placed: list[tuple[str, int, int]] = []
    for anchor in document.iter():
        if anchor.tag not in (qname("wp", "inline"), qname("wp", "anchor")):
            continue
        extent = anchor.find(qname("wp", "extent"))
        blip = next((node for node in anchor.iter(qname("a", "blip"))), None)
        if extent is None or blip is None:
            continue
        try:
            cx, cy = int(extent.get("cx") or 0), int(extent.get("cy") or 0)
        except ValueError:
            cx = cy = 0
        placed.append((blip.get(qname("r", "embed")) or "", cx, cy))
    return placed

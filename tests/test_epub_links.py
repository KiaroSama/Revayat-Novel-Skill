"""An EPUB's link targets, which used to leave the book without a word said.

The words of a link always survived — `_inline_spans` walks into `<a>` like any
other inline element — so nothing looked wrong. The *target* was dropped, and a
book of citations imported as a book of unlinked phrases with no warning
anywhere. That is the shape this project keeps finding and keeps closing: a loss
that no count can see.

The DOCX reader was fixed first, and the builder already knows what to do with
`block["links"]`. So the whole of this is teaching the EPUB reader to fill the
same field — and to say what it still cannot carry, rather than carrying
something that would become a dead link in Word.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from read_epub import read_epub

OPF = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>A Linked Book</dc:title><dc:creator>Test Author</dc:creator>
  <dc:language>en</dc:language><dc:identifier id="id">urn:test:2</dc:identifier>
 </metadata>
 <manifest>
  <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
  <item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>
 </manifest>
 <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""

C1 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<body>
 <h1 id="opening">Chapter One</h1>
 <p>The deed named <a href="https://example.com/ashcroft">Ashcroft</a> as owner.</p>
 <p>See <a href="c2.xhtml#later">the second chapter</a>, and also
    <a href="#opening">the opening</a>.</p>
 <p>The whole of <a href="c2.xhtml">chapter two</a> covers it.</p>
 <p>A note<sup><a epub:type="noteref" href="#n1">1</a></sup> follows.</p>
 <aside epub:type="footnote" id="n1"><p>1. A real footnote.</p></aside>
</body></html>"""

C2 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
 <h2 id="later">Chapter Two</h2>
 <p id="unlinked">Nothing points at this paragraph.</p>
</body></html>"""


@pytest.fixture(scope="module")
def linked_epub(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("epub-links") / "linked.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr("OEBPS/content.opf", OPF)
        archive.writestr("OEBPS/c1.xhtml", C1)
        archive.writestr("OEBPS/c2.xhtml", C2)
    return path


@pytest.fixture(scope="module")
def imported(linked_epub, tmp_path_factory):
    return read_epub(str(linked_epub), tmp_path_factory.mktemp("epub-assets"))


def _targets(book) -> dict[str, str]:
    return {link["text"]: link["href"]
            for block in book["blocks"] for link in (block.get("links") or [])}


def _warnings(book) -> dict[str, dict]:
    return {w["kind"]: w for w in (book["source"].get("epub_warnings") or [])}


# --------------------------------------------------------------------------- #
# What is carried
# --------------------------------------------------------------------------- #

def test_an_external_target_is_carried_as_it_stands(imported):
    assert _targets(imported)["Ashcroft"] == "https://example.com/ashcroft"


def test_a_link_into_another_spine_document_becomes_an_anchor(imported):
    """The spine is one book. Once the documents are concatenated the file
    boundary is gone and `c2.xhtml#later` means, exactly, `#later`."""
    assert _targets(imported)["the second chapter"] == "#later"


def test_a_link_inside_one_document_stays_an_anchor(imported):
    assert _targets(imported)["the opening"] == "#opening"


def test_a_link_is_filed_on_the_block_whose_prose_contains_it(imported):
    for block in imported["blocks"]:
        for link in block.get("links") or ():
            assert link["text"] in (block.get("text") or ""), (
                f"{link['text']!r} is filed on a block that does not contain it"
            )


def test_the_anchors_something_points_at_are_kept(imported):
    kept = {name for block in imported["blocks"]
            for name in (block.get("bookmarks") or ())}
    assert kept == {"opening", "later"}, (
        "either a linked-to anchor was dropped, or an unlinked one was carried"
    )


def test_an_anchor_is_opened_on_exactly_one_block(imported):
    """The same name opened twice sends every link to the first, silently."""
    seen: list[str] = []
    for block in imported["blocks"]:
        seen += list(block.get("bookmarks") or ())
    assert len(seen) == len(set(seen))


# --------------------------------------------------------------------------- #
# What is not, and is said out loud
# --------------------------------------------------------------------------- #

def test_a_link_to_a_whole_document_is_dropped_and_named(imported):
    """It points at a file boundary the finished book does not have.

    Writing it as an external relationship would put a link to `c2.xhtml` in a
    Word file — dead the moment it leaves the EPUB. The words stay; the target
    goes, and the warning says so.
    """
    assert "chapter two" not in _targets(imported)
    assert "link-to-a-whole-document" in _warnings(imported)


def test_a_footnote_reference_is_not_reported_as_a_link(imported):
    """It is already a footnote — `_harvest_footnotes` ran first."""
    assert "1" not in _targets(imported)
    assert imported["footnotes"], "the fixture's footnote was lost entirely"


def test_the_carried_links_are_counted_in_the_warnings(imported):
    warned = _warnings(imported)["hyperlinks-kept-as-metadata"]
    assert warned["count"] == len(_targets(imported)) == 3


def test_a_book_with_no_links_warns_about_none(tmp_path_factory):
    """A warning that fires on every file is a warning nobody reads."""
    plain = tmp_path_factory.mktemp("epub-plain") / "plain.epub"
    body = ("""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
 <h1>Only Prose</h1><p>Nothing links anywhere.</p></body></html>""")
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr("OEBPS/content.opf", OPF.replace(
            '<item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>', ""
        ).replace('<itemref idref="c2"/>', ""))
        archive.writestr("OEBPS/c1.xhtml", body)

    book = read_epub(str(plain), tmp_path_factory.mktemp("epub-plain-assets"))
    assert not book["source"].get("epub_warnings")
    assert not any(block.get("links") for block in book["blocks"])
    assert not any(block.get("bookmarks") for block in book["blocks"])


# --------------------------------------------------------------------------- #
# The point of carrying them at all
# --------------------------------------------------------------------------- #

def test_the_builder_puts_a_carried_link_back(imported, tmp_path):
    """The reader's half is only worth having because the builder uses it.

    Same field, same code path as the DOCX reader — that is the whole design:
    two readers, one `block["links"]`, one place that rebuilds them.
    """
    import argparse

    import bookir as ir
    from build_docx import Builder, add_arguments

    book = {**imported, "blocks": [dict(b) for b in imported["blocks"]]}
    for block in book["blocks"]:
        # The display phrase survives translation verbatim here, which is the
        # case a link can be placed in at all.
        if "Ashcroft" in (block.get("text") or ""):
            block["target"] = "سند Ashcroft را مالک نامیده بود."
        elif block.get("text"):
            block["target"] = "متنی فارسی برای این بند."

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(["--book", "x", "--out", "y", "--no-toc"])
    built = tmp_path / "linked.fa.docx"
    Builder(book, tmp_path / "assets", options).build(built)

    with zipfile.ZipFile(built) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
        rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "<w:hyperlink" in document, "no link element reached the document"
    assert "https://example.com/ashcroft" in rels, "the target was not preserved"

"""Shared fixtures.

Fixtures are *generated*, never committed as binaries: the suite stays fast, the
repository stays small, and no third-party book text is vendored in.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))


from tests_support import building, png_bytes  # noqa: E402  (path set above)

#: Where every test's `tmp_path` lives: inside the project, not in the system temp
#: directory. **pytest deletes an explicitly given basetemp at the start of every
#: run**, so the deletable path is one level down and named for what it is —
#: `.pytest-tmp/` is ours to keep, `.pytest-tmp/scratch/` is ours to wipe. Nothing
#: but this suite should ever write either, and both are git-ignored.
BASETEMP = Path(__file__).resolve().parents[1] / ".pytest-tmp" / "scratch"


def _reparse_point(path: Path) -> bool:
    """A symlink *or* a Windows directory junction.

    `Path.is_symlink()` returns **False** for a junction, which is the form this
    project's own notes warn about elsewhere — so checking only that would miss
    the case on the platform it matters on.
    """
    try:
        return bool(os.lstat(path).st_file_attributes
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:      # not Windows: no such attribute
        return path.is_symlink()
    except OSError:             # does not exist yet, which is the normal case
        return False


def pytest_configure(config: pytest.Config) -> None:
    """Keep the suite's scratch files in the project, wherever pytest was started.

    Without this, `tmp_path` lands under the OS temp directory — nine
    `pytest-of-<user>` trees had accumulated there, holding generated PDFs, DOCX
    files and page rasters, none of it visible to anyone looking at the project.

    Set here rather than as `addopts = --basetemp=.pytest-tmp` because that option
    resolves against the **invocation directory**: run pytest from anywhere but the
    repository root and the scratch tree appears there instead. This path comes
    from the conftest file's own location, so it does not matter where you started.

    The one refusal is a reparse point. Wiping *through* a junction or symlink is
    the way a contained delete stops being contained, and it is the only case here
    that could reach something real — an unexpected plain file cannot, because the
    deletable directory is `.pytest-tmp/scratch` and nothing else claims that name.
    """
    if config.option.basetemp:  # an explicit --basetemp wins, as it should
        return
    for path in (BASETEMP, BASETEMP.parent):
        if _reparse_point(path):
            config.issue_config_time_warning(
                UserWarning(f"{path} is a link rather than a directory; leaving "
                            f"temporary files in the system directory rather than "
                            f"deleting through it"),
                stacklevel=1)
            return
    # pytest `mkdir`s the basetemp itself but **not** its parents, so a nested
    # path without this raises `FileNotFoundError` at the first `tmp_path` — 29
    # errors, all at setup, before this line existed.
    BASETEMP.parent.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(BASETEMP)


@pytest.fixture(scope="session")
def sample_png(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("assets") / "fig.png"
    with building("sample_png", path):
        path.write_bytes(png_bytes(120, 80))
    return path


@pytest.fixture(scope="session")
def sample_pdf(tmp_path_factory, sample_png) -> Path:
    """A three-page book with running heads, page numbers and a split paragraph."""
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path_factory.mktemp("pdf") / "sample.pdf"

    width, height = 396, 612
    body, head, sub = 10.5, 20.0, 14.0
    pages = [
        ("Chapter One", [
            ("The morning came slowly over", body, "helv"),
            ("the ridge and Elizabeth stood", body, "helv"),
            ("do not look back", body, "heit"),
            ("Darcy said nothing at all and", body, "helv"),
            ("she turned away from him and", body, "helv"),
        ], True),
        (None, [
            ("betrayed what she had thought.", body, "helv"),
            ("A Note on the Text", sub, "hebo"),
            ("This edition follows the text.", body, "helv"),
        ], False),
        ("Chapter Two", [
            ("Lizzy went down to breakfast.", body, "helv"),
            ("Nobody mentioned the letter.", body, "helv"),
        ], True),
    ]

    doc = pymupdf.open()
    for number, (title, lines, with_image) in enumerate(pages, start=1):
        page = doc.new_page(width=width, height=height)
        page.insert_text((54, 30), "PRIDE AND PREJUDICE", fontsize=8, fontname="helv")
        y = 90.0
        if title:
            page.insert_text((54, y), title, fontsize=head, fontname="hebo")
            y += 40
        for text, size, font in lines:
            page.insert_text((54, y), text, fontsize=size, fontname=font)
            y += size * 1.7
        if with_image:
            page.insert_image(
                pymupdf.Rect(90, y + 20, 270, y + 140), filename=str(sample_png)
            )
        page.insert_text((width / 2, height - 30), str(number), fontsize=8, fontname="helv")

    doc.set_metadata({"title": "Pride and Prejudice", "author": "Jane Austen"})
    with building("sample_pdf", path):
        doc.save(str(path))
    doc.close()
    return path


def _text_page(doc, page_no: int):
    """One typeset page, used as the source for the rasterised fixtures."""
    page = doc.new_page(width=396, height=612)
    y = 100.0
    for text, size, font in [
        ("Chapter One", 20, "hebo"),
        ("The morning came slowly over the ridge and", 12, "helv"),
        ("Elizabeth Bennet stood beside the window,", 12, "helv"),
        ("She had not slept at all that night.", 12, "helv"),
    ]:
        page.insert_text((54, y), text, fontsize=size, fontname=font)
        y += size * 2.0
    page.insert_text((190, 580), str(page_no), fontsize=9, fontname="helv")
    return page


@pytest.fixture(scope="session")
def scanned_pdf(tmp_path_factory) -> Path:
    """Every page a 200-DPI raster with no text layer — a real scan's shape."""
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path_factory.mktemp("scan") / "scanned.pdf"

    source = pymupdf.open()
    for number in (1, 2):
        _text_page(source, number)

    scan = pymupdf.open()
    # Rasterising at 200 DPI is the memory-hungry step in this file, so it is
    # inside the guard along with the write.
    with building("scanned_pdf", path):
        for index in range(len(source)):
            pixmap = source[index].get_pixmap(dpi=200)
            scan.new_page(width=396, height=612).insert_image(
                pymupdf.Rect(0, 0, 396, 612), pixmap=pixmap
            )
        scan.save(str(path))
    scan.close()
    source.close()
    return path


@pytest.fixture(scope="session")
def mixed_pdf(tmp_path_factory) -> Path:
    """Page 1 has a real text layer, page 2 is a scan — the awkward common case."""
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path_factory.mktemp("mixed") / "mixed.pdf"

    source = pymupdf.open()
    _text_page(source, 2)

    mixed = pymupdf.open()
    _text_page(mixed, 1)
    with building("mixed_pdf", path):
        pixmap = source[0].get_pixmap(dpi=200)
        mixed.new_page(width=396, height=612).insert_image(
            pymupdf.Rect(0, 0, 396, 612), pixmap=pixmap
        )
        mixed.save(str(path))
    mixed.close()
    source.close()
    return path


@pytest.fixture(scope="session")
def sample_epub(tmp_path_factory, sample_png) -> Path:
    """A spine of two documents with a heading, emphasis, an image and a footnote."""
    import zipfile

    path = tmp_path_factory.mktemp("epub") / "sample.epub"
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>A Small Book</dc:title><dc:creator>Test Author</dc:creator>
  <dc:language>en</dc:language><dc:identifier id="id">urn:test:1</dc:identifier>
 </metadata>
 <manifest>
  <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
  <item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>
  <item id="img" href="fig.png" media-type="image/png"/>
 </manifest>
 <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""

    c1 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<body>
 <h1>Chapter One</h1>
 <p>He whispered, <em>do not look back</em>, and then he was <strong>gone</strong>.</p>
 <p>A cultural reference<sup><a epub:type="noteref" href="#n1">1</a></sup> follows.</p>
 <img src="fig.png" alt="A red rectangle"/>
 <blockquote><p>A quoted line.</p></blockquote>
 <aside epub:type="footnote" id="n1"><p>1. Thanksgiving is a holiday.</p></aside>
</body></html>"""

    c2 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
 <h2>Chapter Two</h2>
 <p>Plain text with a <code>literal_token</code> inside.</p>
 <ul><li>First item</li><li>Second item</li></ul>
</body></html>"""

    with building("sample_epub", path), zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/c1.xhtml", c1)
        archive.writestr("OEBPS/c2.xhtml", c2)
        archive.writestr("OEBPS/fig.png", sample_png.read_bytes())
    return path


@pytest.fixture
def translated_book(sample_epub, tmp_path):
    """An extracted book with every unit filled in with plausible Persian."""
    import bookir as ir
    from read_epub import read_epub

    book = read_epub(str(sample_epub), tmp_path / "assets")
    for index, block in enumerate(ir.iter_text_blocks(book)):
        refs = "".join(f"[[fn:{r}]]" for r in ir.footnote_refs(block["text"]))
        block["target"] = f"متن فارسی شمارهٔ {index} برای آزمون." + refs
    for block in book["blocks"]:
        if block["type"] == "image" and block.get("alt"):
            block["target_alt"] = "توضیح تصویر"
    for note in book["footnotes"]:
        note["target"] = "یادداشت مترجم برای آزمون."
    book["meta"]["title_target"] = "کتابی کوچک"
    book["meta"]["author_target"] = "نویسندهٔ آزمون"
    return book, tmp_path / "assets"

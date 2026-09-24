"""Native EPUB intake must not silently omit or misroute published content."""
from __future__ import annotations

import logging
from xml.etree import ElementTree as ET
import zipfile

import pytest

import bookir as ir
from read_epub import read_epub
from tests_support import png_bytes

LOG = logging.getLogger(__name__)


def make_epub(tmp_path, documents, *, extras=None, spine=None, language="ja"):
    """Generate owned fixtures without committing book binaries."""
    package = ET.Element("package", {"xmlns": "http://www.idpf.org/2007/opf", "version": "3.0"})
    metadata = ET.SubElement(package, "metadata", {"xmlns:dc": "http://purl.org/dc/elements/1.1/"})
    ET.SubElement(metadata, "dc:title").text = "Integrity fixture"
    ET.SubElement(metadata, "dc:language").text = language
    manifest, reading = ET.SubElement(package, "manifest"), ET.SubElement(package, "spine")
    for index, name in enumerate(documents):
        ET.SubElement(manifest, "item", id=f"c{index}", href=name, attrib={"media-type": "application/xhtml+xml"})
    for identity in (spine if spine is not None else [f"c{i}" for i in range(len(documents))]):
        ET.SubElement(reading, "itemref", idref=identity)
    path = tmp_path / "fixture.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles></container>')
        archive.writestr("OPS/content.opf", ET.tostring(package, encoding="utf-8"))
        for name, body in documents.items():
            if body is not None:
                archive.writestr("OPS/" + name, '<html xmlns:epub="http://www.idpf.org/2007/ops"><meta charset="utf-8"/><body>' + body + '</body></html>')
        for name, raw in (extras or {}).items():
            archive.writestr("OPS/" + name, raw)
    return path


def read(tmp_path, body, **kwargs):
    path = make_epub(tmp_path, {"one.xhtml": body}, **kwargs)
    return read_epub(str(path), tmp_path / "assets")


def visible(book):
    return [ir.plain_text(block.get("text") or "") if block["type"] != "image" else "IMAGE" for block in book["blocks"] if block["type"] != "pagebreak"]


@pytest.mark.parametrize("body", [
    'Bare first.<p>Middle.</p>Bare last.',
    '<div>Bare first.<p>Middle.</p><span>Bare last.</span></div>',
    '<article><span>Bare first.</span><p>Middle.</p>Bare last.</article>',
])
def test_direct_and_container_prose_is_never_lost(tmp_path, body):
    assert visible(read(tmp_path, body)) == ["Bare first.", "Middle.", "Bare last."]


@pytest.mark.parametrize("wrapper,kind", [("p", "paragraph"), ("blockquote", "blockquote"), ("li", "listitem"), ("h2", "heading")])
@pytest.mark.parametrize("wrapped", [False, True])
def test_mixed_images_keep_narrative_order_and_block_kind(tmp_path, wrapper, kind, wrapped):
    image = '<img src="picture.png" alt="A plate"/>'
    if wrapped:
        image = '<a href="https://example.com"><span>' + image + '</span></a>'
    body = f'<{wrapper}>Before.{image}After.</{wrapper}>'
    book = read(tmp_path, body, extras={"picture.png": png_bytes(12, 8)})
    assert visible(book) == ["Before.", "IMAGE", "After."]
    assert [b["type"] for b in book["blocks"]] == [kind, "image", kind]


def test_same_basename_assets_do_not_overwrite_one_another(tmp_path):
    first, second = png_bytes(12, 8), png_bytes(24, 16)
    book = read(tmp_path, '<p>Pictures.</p><img src="a/plate.png"/><img src="b/plate.png"/>', extras={"a/plate.png": first, "b/plate.png": second})
    figures = [b for b in book["blocks"] if b["type"] == "image"]
    assert len({b["asset"] for b in figures}) == 2
    assert [(tmp_path / "assets" / b["asset"]).read_bytes() for b in figures] == [first, second]
    assert all(ir.sha256_file(tmp_path / "assets" / b["asset"]) == b["sha256"] for b in figures)


@pytest.mark.parametrize("body", ['<p>Text.</p><img src="absent.png"/>', '<p>Text.</p><img/>'])
def test_missing_image_is_a_named_failure_not_a_success_without_the_plate(tmp_path, body):
    with pytest.raises(ValueError, match="image|asset"):
        read(tmp_path, body)


@pytest.mark.parametrize("spine,documents", [(["c0", "missing"], {"one.xhtml": "<p>Start.</p>"}), (["c0", "c1"], {"one.xhtml": "<p>Start.</p>", "two.xhtml": None}), ([], {"one.xhtml": "<p>Start.</p>"})])
def test_incomplete_spine_is_refused(tmp_path, spine, documents):
    path = make_epub(tmp_path, documents, spine=spine)
    with pytest.raises(ValueError, match="spine|document|chapter"):
        read_epub(str(path), tmp_path / "assets")


def test_repeated_note_references_each_get_their_own_body_and_anchor(tmp_path):
    book = read(tmp_path, '<p>One<sup><a href="#n">1</a></sup> and two<sup><a href="#n">1</a></sup>.</p><aside id="n"><p>A shared note.</p></aside>')
    assert len(book["footnotes"]) == 2
    assert [n["text"] for n in book["footnotes"]] == ["A shared note."] * 2
    assert ir.footnote_refs(book["blocks"][0]["text"]) == [n["id"] for n in book["footnotes"]]
    assert len({n["id"] for n in book["footnotes"]}) == 2
    assert ir.validate_book(book) == []


def test_cross_document_note_resolves_the_document_not_just_the_fragment(tmp_path):
    path = make_epub(tmp_path, {
        "chapter.xhtml": '<p>Read<sup><a epub:type="noteref" href="notes.xhtml#n">1</a></sup>.</p><p id="n">Unrelated local anchor.</p>',
        "notes.xhtml": '<aside id="n"><p>The real note.</p></aside>'}, spine=["c0"])
    book = read_epub(str(path), tmp_path / "assets")
    assert [n["text"] for n in book["footnotes"]] == ["The real note."]
    assert "Unrelated local anchor." in visible(book)


def test_cross_document_note_is_not_repeated_as_main_prose(tmp_path):
    path = make_epub(tmp_path, {"one.xhtml": '<p>Read<sup><a href="two.xhtml#n">1</a></sup>.</p>', "two.xhtml": '<h2>Notes</h2><aside id="n"><p>Note body.</p></aside>'})
    book = read_epub(str(path), tmp_path / "assets")
    assert [n["text"] for n in book["footnotes"]] == ["Note body."]
    assert "Note body." not in visible(book)


@pytest.mark.parametrize("note", ["12 people waited.", "3.14 was the ratio.", "2026 was the year."])
def test_a_note_does_not_lose_a_real_leading_quantity(tmp_path, note):
    book = read(tmp_path, '<p>Word<sup><a href="#n">1</a></sup>.</p><aside id="n"><p>' + note + '</p></aside>')
    assert book["footnotes"][0]["text"] == note


def test_note_emphasis_and_verbatim_survive_extraction(tmp_path):
    book = read(tmp_path, '<p>Word<sup><a href="#n">1</a></sup>.</p><aside id="n"><p>1. An <em>important</em> <code>A  B</code> note.</p></aside>')
    assert "*important*" in book["footnotes"][0]["text"]
    assert ir.verbatim_spans(book["footnotes"][0]["text"]) == ["A  B"]


@pytest.mark.parametrize("href", ["#missing", "absent.xhtml#n", "https://example.com/n#n"])
def test_unresolved_explicit_notes_are_not_silently_plain_numbers(tmp_path, href):
    with pytest.raises(ValueError, match="note"):
        read(tmp_path, '<p>Word<a epub:type="noteref" href="' + href + '">1</a>.</p>')


def test_literal_whitespace_and_comments(tmp_path):
    book = read(tmp_path, '<p>Keep<!-- hidden prose --> this.</p><pre> a  b\n  c</pre>')
    assert visible(book) == ["Keep this.", " a  b\n  c"]
    assert ir.verbatim_spans(book["blocks"][1]["text"]) == [" a  b\n  c"]


def test_metadata_language_is_used_unless_the_caller_overrides_it(tmp_path):
    path = make_epub(tmp_path, {"one.xhtml": "<p>本文。</p>"}, language="ja")
    assert read_epub(str(path), tmp_path / "a")["meta"]["lang_source"] == "ja"
    assert read_epub(str(path), tmp_path / "b", lang_source="zh")["meta"]["lang_source"] == "zh"


def test_identical_fragment_names_in_different_documents_keep_distinct_destinations(tmp_path):
    path = make_epub(tmp_path, {"one.xhtml": '<h1 id="same">First</h1><p>Read <a href="two.xhtml#same">second</a>.</p>', "two.xhtml": '<h1 id="same">Second</h1><p>Back <a href="one.xhtml#same">first</a>.</p>'})
    book = read_epub(str(path), tmp_path / "assets")
    headings = [b for b in book["blocks"] if b["type"] == "heading"]
    links = {link["text"]: link["href"] for b in book["blocks"] for link in b.get("links", [])}
    assert headings[0]["bookmarks"] != headings[1]["bookmarks"]
    assert links["second"] == "#" + headings[1]["bookmarks"][0]
    assert links["first"] == "#" + headings[0]["bookmarks"][0]
    LOG.debug("Verified distinct file-scoped anchors")


def test_typed_block_wrapped_by_a_link_keeps_its_target(tmp_path):
    book = read(tmp_path, '<a href="https://example.com"><div>Linked block.</div></a>')
    assert book["blocks"][0]["links"] == [{"text": "Linked block.", "href": "https://example.com"}]


def test_preformatted_anchor_is_reachable(tmp_path):
    book = read(tmp_path, '<p>See <a href="#code">code</a>.</p><pre id="code">a  b</pre>')
    assert book["blocks"][1]["bookmarks"] == ["code"]


def test_repeated_asset_bytes_are_reused_without_losing_placements(tmp_path):
    raw = png_bytes(12, 8)
    book = read(tmp_path, '<img src="a/p.png"/><img src="b/p.png"/>', extras={"a/p.png": raw, "b/p.png": raw})
    assert len(book["blocks"]) == 2
    assert book["blocks"][0]["asset"] == book["blocks"][1]["asset"]


def test_nested_lists_keep_each_text_once_and_in_order(tmp_path):
    book = read(tmp_path, '<ol><li>Outer<ul><li>Inner</li></ul>Tail</li></ol>')
    assert visible(book) == ["Outer", "Inner", "Tail"]
    assert [b["level"] for b in book["blocks"]] == [1, 2, 1]
    assert [b["ordered"] for b in book["blocks"]] == [True, False, True]


def test_extract_cli_uses_epub_metadata_language(tmp_path):
    import argparse
    import extract
    parser = argparse.ArgumentParser()
    extract.add_arguments(parser)
    source = make_epub(tmp_path, {"one.xhtml": "<p>本文。</p>"}, language="ja")
    work = tmp_path / "work"
    extract.extract(parser.parse_args([str(source), "--out", str(work)]))
    assert ir.load_book(work / "book.json")["meta"]["lang_source"] == "ja"


def test_extract_refusal_preserves_existing_book(tmp_path):
    import argparse
    import extract
    parser = argparse.ArgumentParser()
    extract.add_arguments(parser)
    source = make_epub(tmp_path, {"one.xhtml": '<p>Words.</p><img src="missing.png"/>'})
    work = tmp_path / "work"
    work.mkdir()
    path = work / "book.json"
    ir.save_book(ir.new_book(), path)
    before = path.read_bytes()
    with pytest.raises(extract.ExtractError, match="EPUB extraction refused"):
        extract.extract(parser.parse_args([str(source), "--out", str(work)]))
    assert path.read_bytes() == before


@pytest.mark.parametrize("route", ["chunks", "pages"])
def test_epub_assets_and_cross_file_notes_reach_the_production_docx(tmp_path, route):
    import argparse
    import chunk
    import merge
    import pagerun
    import qa
    import worksheet
    import opc
    from build_docx import Builder, add_arguments

    source = make_epub(tmp_path, {
        "one.xhtml": '<h1>Opening</h1><p>Before<img src="a/p.png"/>After<a epub:type="noteref" href="notes.xhtml#n">1</a>.</p><img src="b/p.png"/>',
        "notes.xhtml": '<aside id="n"><p>12 people kept <code>A  B</code>.</p></aside>'},
        extras={"a/p.png": png_bytes(12, 8), "b/p.png": png_bytes(24, 16)}, spine=["c0"])
    assets, book_path, jobs = tmp_path / "assets", tmp_path / "book.json", tmp_path / route
    book = read_epub(str(source), assets)
    ir.save_book(book, book_path)
    manifest = (chunk if route == "chunks" else pagerun).build(book_path, jobs, glossary_path=None)
    for entry in manifest["chunks"]:
        sheet = worksheet.read_worksheet((jobs / entry["file"]).read_text(encoding="utf-8"))
        reply = [worksheet.request_line(entry["request"])]
        for unit in sheet:
            # This is a transport/build fixture, not an LLM-quality verdict.
            target = "متن فارسی آزمایشی. " + unit["text"]
            reply += [f"@@ {unit['id']} {unit['kind']}", target]
        ir.write_text(jobs / entry["output"], "\n".join(reply) + "\n")
    report = merge.merge(book_path, jobs) if route == "chunks" else pagerun.merge_page(book_path, jobs, 1)
    assert report["ok"], report
    finished = ir.load_book(book_path)
    assert [b.get("text") for b in finished["blocks"]] == [b.get("text") for b in book["blocks"]]
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    opts = parser.parse_args(["--book", str(book_path), "--out", str(tmp_path / "out.docx"), "--font", "Tahoma"])
    destination = tmp_path / "out.docx"
    Builder(finished, assets, opts).build(destination)
    report = qa.check_docx(destination, finished).summary()
    assert report["ok"], report
    with zipfile.ZipFile(destination) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        assert len(opc.footnote_references(document)) == 1
        assert len(opc.drawing_extents(document)) == 2
        assert b"A  B" in archive.read("word/footnotes.xml")

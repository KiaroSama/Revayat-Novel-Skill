"""Original chapter locations remain the scope of ordinary fragment links."""

import json
import zipfile
from xml.etree import ElementTree as ET

import bookir as ir
import pytest
from bs4 import BeautifulSoup
import webimport
from preview import production_options


def test_renamed_chapters_keep_forward_back_links_through_native_publication(tmp_path):
    from build_docx import Builder

    chapters = [
        ("first-original.html", "alpha", '<p id="same">First prose. '
         '<a href="second-original.html#same">Forward words</a></p>'),
        ("second-original.html", "omega", '<p id="same">Second prose. '
         '<a href="first-original.html#same">Backward words</a> '
         '<a href="https://outside.example/story#same">External words</a> '
         '<a href="missing.html#same">Unresolved words</a></p>'),
    ]
    manifest = tmp_path / "chapters.json"
    for path, _, content in chapters:
        ir.write_text(tmp_path / path, '<html><main>' + content + '</main></html>')
    ir.write_text(manifest, json.dumps({"schema": webimport.SCHEMA, "title": "Link story",
        "source_language": "en", "chapters": [
            {"id": identity, "path": path, "title": identity, "content_selector": "main"}
            for path, identity, _ in chapters]}))
    out = tmp_path / "work"
    webimport.import_book(manifest, out)
    book = ir.load_book(out / "book.json")
    paragraphs = [b for b in book["blocks"] if b["type"] == "paragraph"]
    forward = next(link for link in paragraphs[0].get("links", [])
                   if link["text"] == "Forward words")
    backward = next(link for link in paragraphs[1].get("links", [])
                    if link["text"] == "Backward words")
    assert forward["href"][1:] != backward["href"][1:]
    assert forward["href"][1:] in paragraphs[1]["bookmarks"]
    assert backward["href"][1:] in paragraphs[0]["bookmarks"]
    assert any(link.get("href") == "https://outside.example/story#same"
               for link in paragraphs[1]["links"])
    assert "Unresolved words" in ir.plain_text(paragraphs[1]["text"])
    assert any(w["kind"] == "unresolved-internal-link"
               for w in book["source"]["epub_warnings"])
    for block in paragraphs:
        block["target"] = block["text"]
    target = out / "links.docx"
    Builder(book, out / "assets", production_options()).build(target)
    with zipfile.ZipFile(target) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    anchors = {link.get("{" + ns["w"] + "}anchor")
               for link in root.findall(".//w:hyperlink", ns)}
    assert {forward["href"][1:], backward["href"][1:]} <= anchors
    before = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert webimport.import_book(manifest, out, resume=True)["reused"] is True
    assert {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()} == before


def test_remote_original_and_redirected_canonical_links_rebase_without_external_changes():
    records = [{"id": "a", "path": "https://chapters.example/new-a.html"},
               {"id": "b", "path": "https://chapters.example/new-b.html"}]
    inputs = [{"url": "https://chapters.example/old-a.html"},
              {"url": "https://chapters.example/old-b.html"}]
    content = b'<p><a href="https://chapters.example/old-b.html#same">Old target</a> '
    content += b'<a href="new-b.html#same">New target</a> '
    content += b'<a href="https://outside.example/new-b.html#same">External</a></p>'
    chapters = [("a", content), ("b", b'<p id="same">Second.</p>')]
    result = webimport.rebase_chapters(chapters, records, inputs)
    soup = BeautifulSoup(result[0][1], "html.parser")
    assert [a["href"] for a in soup.find_all("a")] == [
        "b.xhtml#same", "b.xhtml#same", "https://outside.example/new-b.html#same"]


def test_ambiguous_canonical_chapter_mapping_refuses_rather_than_guesses():
    records = [{"id": "a", "path": "https://chapters.example/story"},
               {"id": "b", "path": "https://chapters.example/story"}]
    with pytest.raises(ValueError, match="canonical source"):
        webimport.rebase_chapters([("a", b"<p>One</p>"), ("b", b"<p>Two</p>")],
                                  records, [{"url": records[0]["path"]}, {"url": records[1]["path"]}])


def test_explicit_cross_chapter_note_is_still_refused_before_rebasing(tmp_path):
    from webhtml import chapter_html

    body = b'<main><p>Source<a role="doc-noteref" href="other.html#note">1</a></p></main>'
    with pytest.raises(ValueError, match="note target"):
        chapter_html(body, "main", "Chapter", lambda _: "unused")

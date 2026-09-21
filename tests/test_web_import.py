"""Web chapter intake through the public CLI and the resulting Book IR."""

import json
from pathlib import Path
import sys

import pytest

import bookir as ir
import bookwrite
from tests_support import png_bytes
import webimport

CLI = Path(__file__).resolve().parents[1] / "skills/revayat-novel/scripts/revayat-novel.py"


def invoke(manifest, out, *options):
    result = ir.run_bounded([sys.executable, str(CLI), "web-import", "--manifest",
                             str(manifest), "--out", str(out), *options], 20)
    return result, result.stdout.decode("utf-8")


def source_chapters(tmp_path):
    image = png_bytes(32, 24)
    (tmp_path / "drawing.png").write_bytes(image)
    ir.write_text(tmp_path / "second.html", '<html><nav>Skip this menu</nav><main id="chapter">'
                  '<h1>Second first</h1><div>Before <b>bold</b>.<br>Next line.</div>'
                  '<p>Before image.<img src="drawing.png" alt="A drawing">After image.</p>'
                  '</main></html>')
    ir.write_text(tmp_path / "first.html", '<html><article id="chapter"><h1>First last</h1>'
                  '<p>The final paragraph.</p></article></html>')
    data = {"schema": "revayat-novel/web-input@1", "title": "Original serial",
            "source_language": "en", "chapters": [
                {"id": "second", "title": "Second first", "path": "second.html", "content_selector": "#chapter"},
                {"id": "first", "title": "First last", "path": "first.html", "content_selector": "#chapter"}]}
    manifest = tmp_path / "chapters.json"
    ir.write_text(manifest, json.dumps(data, ensure_ascii=False))
    return manifest, image


def test_saved_chapters_preserve_declared_order_prose_and_image_bytes(tmp_path):
    manifest, image = source_chapters(tmp_path)
    out = tmp_path / "book work"
    process, output = invoke(manifest, out)
    assert process.returncode == 0, (output, process.stderr.decode("utf-8"))
    result = json.loads(output)
    assert result["ok"] and result["chapters"] == 2
    book = ir.load_book(Path(result["book"]))
    texts = [block.get("text", "") for block in book["blocks"]]
    assert texts[0] == "Second first"
    assert texts.index("First last") > texts.index("After image.")
    assert "Before **bold**." in texts and "Next line." in texts
    assert "Skip this menu" not in " ".join(texts)
    figure = next(block for block in book["blocks"] if block["type"] == "image")
    index = book["blocks"].index(figure)
    assert texts[index - 1] == "Before image." and texts[index + 1] == "After image."
    assert (out / "assets" / figure["asset"]).read_bytes() == image
    assert (figure["pixel_width"], figure["pixel_height"]) == (32, 24)


def test_web_import_refuses_non_public_url_without_a_book(tmp_path):
    manifest, _ = source_chapters(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    chapter = data["chapters"][0]
    chapter.pop("path")
    chapter["url"] = "http://127.0.0.1/private"
    ir.write_text(manifest, json.dumps(data))
    out = tmp_path / "web work"
    process, output = invoke(manifest, out)
    assert process.returncode == 2
    assert json.loads(output)["refused"] == "unsafe-source"
    assert not (out / "book.json").exists()


def test_resume_preserves_translation_and_refuses_changed_local_source(tmp_path):
    manifest, _ = source_chapters(tmp_path)
    out = tmp_path / "book work"
    process, output = invoke(manifest, out)
    assert process.returncode == 0, output
    book_path = out / "book.json"
    with bookwrite.transaction(book_path, actor="test.translation") as tx:
        tx.book["blocks"][0]["target"] = "فصل ترجمه‌شده"
    before = book_path.read_bytes()
    process, output = invoke(manifest, out, "--resume")
    assert process.returncode == 0, output
    assert json.loads(output)["reused"] is True
    assert book_path.read_bytes() == before
    ir.write_text(tmp_path / "first.html", '<article id="chapter"><p>Changed source.</p></article>')
    process, output = invoke(manifest, out, "--resume")
    assert process.returncode == 2 and json.loads(output)["refused"] == "source-changed"
    assert book_path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["snapshot", "chapter", "asset", "epub"])
def test_corrupt_cached_sources_cannot_resume_or_change_translation(tmp_path, mutation):
    manifest, _ = source_chapters(tmp_path)
    out = tmp_path / "work"
    webimport.import_book(manifest, out)
    book_path = out / "book.json"
    before = book_path.read_bytes()
    snapshot_path = out / "web-source.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if mutation == "snapshot":
        snapshot["files"] = []
        ir.write_text(snapshot_path, json.dumps(snapshot))
    else:
        relative = {"chapter": snapshot["chapters"][0]["original"],
                    "asset": next(item["path"] for item in snapshot["files"] if item["path"].startswith("assets/")),
                    "epub": snapshot["epub"]}[mutation]
        (out / relative).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="changed|disappeared"):
        webimport.import_book(manifest, out, resume=True)
    assert book_path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["duplicate-id", "duplicate-source", "empty", "outside", "bad-selector"])
def test_invalid_chapter_input_never_creates_a_book(tmp_path, mutation):
    manifest, _ = source_chapters(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if mutation == "duplicate-id":
        data["chapters"][1]["id"] = "second"
    elif mutation == "duplicate-source":
        data["chapters"][1]["path"] = "second.html"
    elif mutation == "empty":
        data["chapters"] = []
    elif mutation == "outside":
        data["chapters"][0]["path"] = "../outside.html"
    else:
        data["chapters"][0]["content_selector"] = "missing"
    ir.write_text(manifest, json.dumps(data))
    out = tmp_path / "work"
    with pytest.raises(ValueError):
        webimport.import_book(manifest, out)
    assert not (out / "book.json").exists()


def test_plain_text_chapter_keeps_unicode_and_does_not_parse_tags(tmp_path):
    ir.write_text(tmp_path / "chapter.txt", "第一段 <not-html>。\n\nSecond paragraph.")
    manifest = tmp_path / "chapters.json"
    ir.write_text(manifest, json.dumps({"schema": webimport.SCHEMA, "title": "Original",
        "source_language": "zh", "chapters": [{"id": "one", "title": "Chapter one",
        "path": "chapter.txt", "content_selector": "body"}]}))
    result = webimport.import_book(manifest, tmp_path / "work")
    book = ir.load_book(result["book"])
    assert [item.get("text") for item in book["blocks"]] == [
        "Chapter one", "第一段 <not-html>。", "Second paragraph."]


@pytest.mark.parametrize("container", ["article", "div", "section"])
def test_web_notes_and_nested_literary_structure_survive_intake(tmp_path, container):
    manifest, _ = source_chapters(tmp_path)
    ir.write_text(tmp_path / "second.html", f'<{container} id="chapter"><h1>Chapter</h1>'
        '<p>First<sup><a href="#n1">1</a></sup> and second<sup><a href="#n2">2</a></sup>.</p>'
        '<aside id="n1"><p>First note.</p></aside><aside id="n2"><p>Second note.</p></aside>'
        '<blockquote><p>Quoted <cite>title</cite>.</p></blockquote>'
        f'<ol><li><p>A listed item.</p></li></ol></{container}>')
    result = webimport.import_book(manifest, tmp_path / "work")
    book = ir.load_book(result["book"])
    assert [note["text"] for note in book["footnotes"]] == ["First note.", "Second note."]
    paragraph = next(block for block in book["blocks"] if block.get("text", "").startswith("First"))
    assert paragraph["text"] == "First[[fn:fn0001]] and second[[fn:fn0002]]."
    assert any(block["type"] == "blockquote" and block["text"] == "Quoted *title*." for block in book["blocks"])
    assert any(block["type"] == "listitem" and block["text"] == "A listed item." for block in book["blocks"])


def test_user_chapter_id_cannot_collide_with_generated_image_manifest_id(tmp_path):
    manifest, _ = source_chapters(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["chapters"][0]["id"] = "asset-0"
    ir.write_text(manifest, json.dumps(data))
    result = webimport.import_book(manifest, tmp_path / "work")
    book = ir.load_book(result["book"])
    assert book["blocks"][0]["text"] == "Second first"
    assert sum(block["type"] == "image" for block in book["blocks"]) == 1

"""Native PDF draws, not resource entries, are publication occurrences."""
from __future__ import annotations

import io

import pymupdf
from PIL import Image

import bookir as ir
from read_pdf import read_pdf


def test_each_draw_keeps_geometry_while_asset_bytes_are_shared(tmp_path):
    buffer = io.BytesIO()
    Image.new("RGB", (20, 10), "navy").save(buffer, format="PNG")
    source = tmp_path / "draws.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=400)
        xref = page.insert_image((20, 30, 120, 80), stream=buffer.getvalue())
        page.insert_image((150, 180, 190, 260), xref=xref, rotate=90)
        document.save(source)
    before = source.read_bytes()
    assets = tmp_path / "assets"
    book = read_pdf(str(source), assets)
    images = [block for block in book["blocks"] if block["type"] == "image"]
    assert len(images) == 2
    assert [image["bbox"] for image in images] == [
        [20.0, 30.0, 120.0, 80.0], [150.0, 180.0, 190.0, 260.0]]
    assert [(image["width_pt"], image["height_pt"]) for image in images] == [
        (100.0, 50.0), (40.0, 80.0)]
    assert images[0]["asset"] == images[1]["asset"]
    assert len(list(assets.iterdir())) == 1
    assert ir.sha256_file(assets / images[0]["asset"]) == images[0]["sha256"]
    assert source.read_bytes() == before
    assert [image["drawing_order"] for image in images] == [1, 2]
    assert images[0]["transform"] != images[1]["transform"]


def test_dead_resource_is_not_invented_as_drawn_content(tmp_path):
    source = tmp_path / "dead-resource.pdf"
    buffer = io.BytesIO()
    Image.new("RGB", (20, 10), "navy").save(buffer, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=400)
        page.insert_text((30, 60), "Real native text.")
        page.insert_image((20, 90, 120, 140), stream=buffer.getvalue())
        document.update_stream(page.get_contents()[-1], b" ")
        document.save(source)
    with pymupdf.open(source) as document:
        assert len(document[0].get_images(full=True)) == 1
        assert document[0].get_image_info(xrefs=True) == []
    assets = tmp_path / "assets"
    book = read_pdf(str(source), assets)
    assert not any(block["type"] == "image" for block in book["blocks"])
    assert list(assets.iterdir()) == []
    assert any("Real native text." in block.get("text", "") for block in book["blocks"])

"""Native illustration identity and page-local MinerU crop geometry."""
import io
import json

import pymupdf
from PIL import Image

import adapters
import bookir as ir


def _output(tmp_path, page=0, bbox=None):
    root = tmp_path / "mineru"
    root.mkdir()
    Image.new("RGB", (20, 10), "red").save(root / "plate.png")
    (root / "book_content_list.json").write_text(json.dumps([
        {"type": "image", "page_idx": page, "bbox": bbox or [500, 100, 1000, 600],
         "img_path": "plate.png"}]), encoding="utf-8")
    return root


def test_native_illustration_is_retained_next_to_new_crop(tmp_path):
    book = ir.new_book()
    native = ir.make_block("image", 1, page=1, asset="native.png",
                           bbox=[10, 10, 40, 30], width_pt=30, height_pt=20)
    book["blocks"] = [native, ir.make_block("paragraph", 2, page=1,
                        text="Below the plate.", bbox=[20, 300, 250, 320])]
    result = adapters.merge_mineru_figures(book, _output(tmp_path), tmp_path / "assets")
    images = [block for block in book["blocks"] if block["type"] == "image"]
    assert result["page_scans_replaced"] == 0
    assert result["figures_added"] == 1
    assert images[0] is native and len(images) == 2
    assert book["blocks"][-1]["text"] == "Below the plate."


def test_imported_ambiguous_raster_reports_uncertain_mineru_replacement(tmp_path):
    from read_pdf import read_pdf

    source = tmp_path / "letterboxed.pdf"
    buffer = io.BytesIO()
    Image.new("RGB", (280, 350), "navy").save(buffer, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=400)
        page.insert_image(pymupdf.Rect(10, 20, 290, 370), stream=buffer.getvalue())
        page.insert_text((30, 60), "Recognized caption.", render_mode=3)
        document.save(source)
    assets = tmp_path / "assets"
    book = read_pdf(str(source), assets, ocr_text=True, ocr_pages=[1])
    original = next(block for block in book["blocks"] if block["type"] == "image")
    result = adapters.merge_mineru_figures(book, _output(tmp_path), assets)
    assert result.get("replacement_uncertain_pages") == [1]
    assert result["page_scans_replaced"] == 0
    assert original in book["blocks"]
    assert original["source_role"] == "unknown-page-raster"


def test_known_scan_is_replaced_without_deleting_other_artwork(tmp_path):
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("image", 1, page=1, asset="scan.png", source_role="scan"),
        ir.make_block("image", 2, page=1, asset="native.png", source_role="illustration",
                      bbox=[20, 250, 80, 280]),
        ir.make_block("paragraph", 3, page=1, text="Caption.", bbox=[20, 300, 150, 320])]
    result = adapters.merge_mineru_figures(book, _output(tmp_path), tmp_path / "assets")
    images = [block for block in book["blocks"] if block["type"] == "image"]
    assert result["page_scans_replaced"] == 1 and len(images) == 2
    assert images[1]["asset"] == "native.png"
    assert all(block["asset"] != "scan.png" for block in images)


def test_malformed_content_closes_opened_source(tmp_path, monkeypatch):
    import pytest

    source = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(source)
    output = _output(tmp_path)
    (output / "book_content_list.json").write_text(json.dumps([
        {"type": "image", "img_path": "plate.png", "page_idx": "invalid"}]),
        encoding="utf-8")
    opened = []
    actual = pymupdf.open

    def observe(*args, **kwargs):
        document = actual(*args, **kwargs)
        opened.append(document)
        return document

    monkeypatch.setattr(pymupdf, "open", observe)
    with pytest.raises(ValueError):
        adapters.merge_mineru_figures(ir.new_book(), output, tmp_path / "assets",
                                      source_pdf=source)
    assert opened and all(document.is_closed for document in opened)


def test_rotated_source_crop_preserves_original_pixels(tmp_path):
    source = tmp_path / "rotated.pdf"
    buffer = io.BytesIO()
    image = Image.new("RGB", (400, 200), "white")
    image.paste("navy", (0, 0, 200, 100))
    image.save(buffer, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=200)
        page.insert_image(page.rect, stream=buffer.getvalue())
        page.set_rotation(90)
        document.save(source)
    assets = tmp_path / "assets"
    book = ir.new_book()
    adapters.merge_mineru_figures(book, _output(tmp_path, bbox=[500, 0, 1000, 500]),
                                  assets, source_pdf=source)
    plate = next(block for block in book["blocks"] if block["type"] == "image")
    assert plate["bbox"] == [100.0, 0.0, 200.0, 200.0]
    with Image.open(assets / plate["asset"]) as crop:
        assert crop.size == (100, 200)
        assert crop.getpixel((50, 50)) == (0, 0, 128)


def test_crop_uses_actual_landscape_page_pixels_and_physical_box(tmp_path):
    source = tmp_path / "mixed.pdf"
    buffer = io.BytesIO()
    image = Image.new("RGB", (400, 200), "white")
    image.paste("navy", (200, 0, 400, 100))
    image.save(buffer, format="PNG")
    with pymupdf.open() as document:
        document.new_page(width=200, height=400)
        page = document.new_page(width=400, height=200)
        page.insert_image(page.rect, stream=buffer.getvalue())
        document.save(source)
    book = ir.new_book()
    book["page"].update(width_pt=200, height_pt=400)
    book["blocks"] = [ir.make_block("paragraph", 1, page=2, text="Caption.",
                                    bbox=[10, 150, 150, 165])]
    assets = tmp_path / "assets"
    adapters.merge_mineru_figures(book, _output(tmp_path, page=1, bbox=[500, 0, 1000, 500]),
                                  assets, source_pdf=source)
    plate = next(block for block in book["blocks"] if block["type"] == "image")
    assert plate["bbox"] == [200.0, 0.0, 400.0, 100.0]
    assert (plate["width_pt"], plate["height_pt"]) == (200.0, 100.0)
    with Image.open(assets / plate["asset"]) as crop:
        assert crop.size == (200, 100)
        assert crop.getpixel((50, 50)) == (0, 0, 128)

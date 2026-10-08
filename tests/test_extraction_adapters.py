"""MinerU and Markdown import, figure placement and source-raster cropping."""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest
import bookir as ir
from extract import ExtractError, _mineru_bbox, merge_mineru_figures, from_markdown, from_mineru

def _mineru_run(root: Path, png: bytes) -> Path:
    """A MinerU output directory, matching its documented content_list schema."""
    out = root / "mineru"
    (out / "images").mkdir(parents=True)
    (out / "images" / "fig.png").write_bytes(png)
    content = [
        {"type": "text", "text": "Chapter One", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "The morning came slowly.", "text_level": 0,
         "page_idx": 0},
        {"type": "image", "img_path": "images/fig.png",
         "image_caption": ["Figure 1: the ridge"], "page_idx": 0},
        {"type": "text", "text": "A second page of prose.", "text_level": 0,
         "page_idx": 1},
    ]
    (out / "book_content_list.json").write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8"
    )
    return out


def test_mineru_import_maps_levels_images_and_captions(tmp_path, sample_png):
    run = _mineru_run(tmp_path, sample_png.read_bytes())
    book = from_mineru(run, tmp_path / "assets", source_name="book",
                       lang_source="en", lang_target="fa-IR")

    assert ir.validate_book(book) == []
    kinds = [b["type"] for b in book["blocks"]]
    assert kinds.count("heading") == 1
    assert kinds.count("image") == 1
    assert kinds.count("caption") == 1
    # text_level 1 is a heading; 0 is body.
    heading = next(b for b in book["blocks"] if b["type"] == "heading")
    assert ir.plain_text(heading["text"]) == "Chapter One"
    image = next(b for b in book["blocks"] if b["type"] == "image")
    assert (tmp_path / "assets" / image["asset"]).exists()
    # A page change becomes a soft break, not a lost boundary.
    assert "pagebreak" in kinds


def test_mineru_import_without_a_content_list_says_how_to_produce_one(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ExtractError, match="mineru -p"):
        from_mineru(tmp_path / "empty", tmp_path / "assets", source_name="b",
                    lang_source="en", lang_target="fa-IR")


def test_markdown_import_reads_structure_and_copies_images(tmp_path, sample_png):
    source = tmp_path / "md"
    source.mkdir()
    (source / "fig.png").write_bytes(sample_png.read_bytes())
    (source / "book.md").write_text(
        "# Chapter One\n"
        "\n"
        "He whispered, *do not look back*.\n"
        "Still on the same paragraph.\n"
        "\n"
        "![A red rectangle](fig.png)\n"
        "\n"
        "> A quoted line.\n"
        "\n"
        "- First item\n"
        "- Second item\n"
        "\n"
        "---\n"
        "\n"
        "## Chapter Two\n",
        encoding="utf-8",
    )

    book = from_markdown(source / "book.md", tmp_path / "assets",
                         lang_source="en", lang_target="fa-IR")
    assert ir.validate_book(book) == []

    kinds = [b["type"] for b in book["blocks"]]
    assert kinds.count("heading") == 2
    assert kinds.count("image") == 1
    assert kinds.count("listitem") == 2
    assert "separator" in kinds

    image = next(b for b in book["blocks"] if b["type"] == "image")
    assert image["alt"] == "A red rectangle"
    assert (tmp_path / "assets" / image["asset"]).exists()

    # Lines of one paragraph are joined, and emphasis survives the import.
    paragraph = next(b for b in book["blocks"] if b["type"] == "paragraph")
    assert "*do not look back*" in paragraph["text"]
    assert "Still on the same paragraph." in paragraph["text"]


def test_markdown_import_tolerates_a_missing_image(tmp_path):
    source = tmp_path / "md"
    source.mkdir()
    (source / "book.md").write_text("![gone](missing.png)\n\nProse.\n", encoding="utf-8")
    book = from_markdown(source / "book.md", tmp_path / "assets",
                         lang_source="en", lang_target="fa-IR")
    assert not any(b["type"] == "image" for b in book["blocks"])
    assert any(b["type"] == "paragraph" for b in book["blocks"])


def _mineru_output(tmp_path, items, *, name="book") -> Path:
    """Build the directory layout MinerU 3.4.x actually writes."""
    from PIL import Image

    root = tmp_path / "mineru" / name / "auto"
    (root / "images").mkdir(parents=True, exist_ok=True)
    content = []
    for number, item in enumerate(items, start=1):
        relative = item.get("img_path", f"images/fig{number}.jpg")
        target = root / relative.replace("/", os.sep)
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (item.pop("_px", (60, 40))),
                  item.pop("_colour", (10, 20 * number % 250, 30))).save(target)
        content.append({"type": "image", "img_path": relative, **item})
    (root / f"{name}_content_list.json").write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path / "mineru"


def _scanned_book(pages: int = 4) -> dict:
    """A book of whole-page scans, which is what OCR of an image PDF leaves."""
    book = ir.new_book(lang_source="fa", lang_target="fa-IR")
    blocks = []
    for page in range(1, pages + 1):
        blocks.append(ir.make_block("image", len(blocks) + 1, page=page,
                                    asset=f"page{page}.png", alt="", source_role="scan"))
        blocks.append(ir.make_block("paragraph", len(blocks) + 1, page=page,
                                    text=f"متن صفحهٔ {page}"))
    book["blocks"] = blocks
    return book


def test_mineru_bbox_is_scaled_from_thousandths_to_points():
    page = {"width_pt": 595.3, "height_pt": 841.9}
    assert _mineru_bbox([0, 0, 1000, 1000], page) == [0.0, 0.0, 595.3, 841.9]
    # Measured against a real crop: 286/1000 of a 200-DPI A4 render is 170pt.
    left, _, right, _ = _mineru_bbox([100, 200, 386, 400], page)
    assert round(right - left) == 170


@pytest.mark.parametrize("bbox", [None, [], [1, 2, 3], ["a", "b", "c", "d"]])
def test_mineru_bbox_rejects_junk_without_raising(bbox):
    assert _mineru_bbox(bbox, {"width_pt": 595.3, "height_pt": 841.9}) is None


def test_figures_replace_the_page_scan_they_were_cropped_from(tmp_path):
    book = _scanned_book(2)
    mineru = _mineru_output(tmp_path, [
        {"page_idx": 0, "bbox": [100, 200, 500, 400], "image_caption": ["قارچ"]},
        {"page_idx": 0, "bbox": [100, 500, 500, 700], "_colour": (200, 40, 40)},
    ])

    report = merge_mineru_figures(book, mineru, tmp_path / "assets")
    assert report["figures_added"] == 2
    assert report["page_scans_replaced"] == 1

    images = [b for b in book["blocks"] if b["type"] == "image"]
    # The page-1 scan is gone, replaced by its two crops; page 2 is untouched.
    assert [b["page"] for b in images] == [1, 1, 2]
    assert all((tmp_path / "assets" / b["asset"]).exists() for b in images[:2])
    assert images[0]["alt"] == "قارچ"
    # Real size travels with the crop, or a picture cannot be placed to scale.
    # Derived from the page the book actually has: the MinerU box spans
    # 100..500 of 1000, so two fifths of the trim, whatever the trim is.
    expected = 0.4 * book["page"]["width_pt"]
    assert images[0]["width_pt"] == pytest.approx(expected, abs=0.1)
    assert ir.validate_book(book) == []


def test_figures_are_ordered_down_the_page(tmp_path):
    book = _scanned_book(1)
    mineru = _mineru_output(tmp_path, [
        {"page_idx": 0, "bbox": [0, 700, 500, 900], "image_caption": ["پایین"]},
        {"page_idx": 0, "bbox": [0, 100, 500, 300], "image_caption": ["بالا"]},
    ])
    merge_mineru_figures(book, mineru, tmp_path / "assets")
    captions = [b["alt"] for b in book["blocks"] if b["type"] == "image"]
    assert captions == ["بالا", "پایین"]


def test_page_offset_maps_a_partial_mineru_run_onto_the_book(tmp_path):
    """`mineru -s 39 -e 41` numbers its own pages from zero."""
    book = _scanned_book(41)
    mineru = _mineru_output(tmp_path, [
        {"page_idx": 0, "bbox": [0, 100, 500, 300]},
        {"page_idx": 1, "bbox": [0, 100, 500, 300]},
    ])
    merge_mineru_figures(book, mineru, tmp_path / "assets", page_offset=39)
    pages = [b["page"] for b in book["blocks"]
             if b["type"] == "image" and "fig" in b["asset"]]
    assert pages == [40, 41]


def test_figures_append_when_the_page_scan_was_already_dropped(tmp_path):
    """read_pdf drops page-sized scans, so there is nothing left to replace."""
    book = _scanned_book(2)
    book["blocks"] = [b for b in book["blocks"] if b["type"] != "image"]
    mineru = _mineru_output(tmp_path, [{"page_idx": 0, "bbox": [0, 100, 500, 300]}])

    report = merge_mineru_figures(book, mineru, tmp_path / "assets")
    assert report["page_scans_replaced"] == 0 and report["figures_added"] == 1
    # It lands after page 1's prose and before page 2 starts.
    order = [(b["type"], b["page"]) for b in book["blocks"]]
    assert order == [("paragraph", 1), ("image", 1), ("paragraph", 2)]


def test_a_watermark_cropped_on_every_page_is_dropped(tmp_path):
    """MinerU crops a translucent publisher stamp as if it were a picture.

    Measured on a real scan: the same 170x69pt box came back from page after
    page. Nothing that recurs in the same spot on a quarter of the book is an
    illustration.
    """
    stamp = {"bbox": [100, 40, 386, 122]}
    items = [{"page_idx": page, **stamp} for page in range(6)]
    items.append({"page_idx": 2, "bbox": [100, 300, 800, 700]})  # a real photo
    book = _scanned_book(6)

    report = merge_mineru_figures(book, _mineru_output(tmp_path, items),
                                  tmp_path / "assets")
    assert report["furniture_dropped"] == 6
    assert report["figures_added"] == 1
    kept = [b for b in book["blocks"] if b["type"] == "image"
            and "fig" in b["asset"]]
    assert len(kept) == 1 and kept[0]["page"] == 3


def test_a_repeated_figure_is_kept_when_the_book_is_short(tmp_path):
    """Two crops out of two pages is not evidence of furniture."""
    items = [{"page_idx": p, "bbox": [100, 40, 386, 122]} for p in range(2)]
    book = _scanned_book(2)
    report = merge_mineru_figures(book, _mineru_output(tmp_path, items),
                                  tmp_path / "assets")
    assert report["furniture_dropped"] == 0 and report["figures_added"] == 2


def test_missing_mineru_output_says_how_to_produce_it(tmp_path):
    with pytest.raises(ExtractError, match="mineru -p"):
        merge_mineru_figures(_scanned_book(1), tmp_path, tmp_path / "assets")


def test_a_crop_file_that_vanished_is_skipped_not_fatal(tmp_path):
    mineru = _mineru_output(tmp_path, [
        {"page_idx": 0, "bbox": [0, 100, 500, 300]},
        {"page_idx": 0, "bbox": [0, 400, 500, 600], "img_path": "images/gone.jpg"},
    ])
    (mineru / "book" / "auto" / "images" / "gone.jpg").unlink()

    book = _scanned_book(1)
    assert merge_mineru_figures(book, mineru, tmp_path / "assets")["figures_added"] == 1


def test_a_figure_lands_between_the_paragraphs_it_sat_between(tmp_path):
    """Reading order comes from the page, not from the order of arrival.

    When the whole-page scan was already dropped there is nothing to replace,
    and appending the crops at the end of the page moves a picture that stood
    between two paragraphs to after both of them. The caption then explains the
    wrong thing, and the book reads as if the illustration were an afterthought.
    """
    book = _scanned_book(1)
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=1, bbox=[50, 80, 350, 140],
                      text="Paragraph A, above the picture."),
        ir.make_block("paragraph", 2, page=1, bbox=[50, 500, 350, 560],
                      text="Paragraph B, below the picture."),
    ]
    mineru = _mineru_output(tmp_path, [
        {"page_idx": 0, "bbox": [100, 400, 800, 700], "image_caption": ["میان دو بند"]},
    ])

    report = merge_mineru_figures(book, mineru, tmp_path / "assets")
    assert report["figures_added"] == 1
    assert "placed_at_page_end" not in report, "placement was guessed, not measured"

    order = [(b["type"], ir.plain_text(b.get("text") or b.get("alt") or ""))
             for b in book["blocks"]]
    assert order == [("paragraph", "Paragraph A, above the picture."),
                     ("image", "میان دو بند"),
                     ("paragraph", "Paragraph B, below the picture.")]


def test_several_figures_interleave_with_several_paragraphs(tmp_path):
    """Block boxes are in points; MinerU's are thousandths of the page.

    The default page is 612pt tall, so a MinerU top of 250 is 153pt and one of
    900 is 551pt. Mixing the two scales up is an easy way to write a test that
    asserts the wrong order and then "fixes" correct code.
    """
    book = _scanned_book(1)
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=1, bbox=[50, 60, 350, 100], text="A"),
        ir.make_block("paragraph", 2, page=1, bbox=[50, 300, 350, 340], text="B"),
        ir.make_block("paragraph", 3, page=1, bbox=[50, 500, 350, 540], text="C"),
    ]
    mineru = _mineru_output(tmp_path, [
        {"page_idx": 0, "bbox": [100, 900, 800, 980]},   # 551pt — after C
        {"page_idx": 0, "bbox": [100, 250, 800, 380]},   # 153pt — between A and B
    ])

    merge_mineru_figures(book, mineru, tmp_path / "assets")
    assert [b["type"] for b in book["blocks"]] == [
        "paragraph", "image", "paragraph", "paragraph", "image"
    ]


def test_a_figure_above_all_the_prose_goes_first(tmp_path):
    book = _scanned_book(1)
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=1, bbox=[50, 400, 350, 460], text="Below."),
    ]
    mineru = _mineru_output(tmp_path, [{"page_idx": 0, "bbox": [100, 60, 800, 300]}])
    merge_mineru_figures(book, mineru, tmp_path / "assets")
    assert [b["type"] for b in book["blocks"]] == ["image", "paragraph"]


def test_a_placement_that_cannot_be_measured_is_reported(tmp_path):
    """Silence here would look exactly like a correct end-of-page placement."""
    book = _scanned_book(1)
    book["blocks"] = [ir.make_block("paragraph", 1, page=1, text="No box at all.")]
    mineru = _mineru_output(tmp_path, [{"page_idx": 0, "bbox": [100, 400, 800, 700]}])

    report = merge_mineru_figures(book, mineru, tmp_path / "assets")
    assert report["figures_added"] == 1
    assert report["placed_at_page_end"], "a guessed placement passed as measured"


def test_the_built_document_keeps_the_interleaved_order(tmp_path):
    """The order has to survive all the way into the file Word opens."""
    import argparse
    import zipfile

    from build_docx import Builder, add_arguments

    book = _scanned_book(1)
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=1, bbox=[50, 80, 350, 140], text="Above."),
        ir.make_block("paragraph", 2, page=1, bbox=[50, 500, 350, 560], text="Below."),
    ]
    for block in book["blocks"]:
        block["target"] = "بند فارسی که به اندازهٔ کافی بلند است تا رد شود."
    mineru = _mineru_output(tmp_path, [{"page_idx": 0, "bbox": [100, 400, 800, 700]}])
    merge_mineru_figures(book, mineru, tmp_path / "assets")

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma",
                                 "--no-toc"])
    destination = tmp_path / "order.docx"
    Builder(book, tmp_path / "assets", options).build(destination)

    with zipfile.ZipFile(destination) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
    # The drawing must sit between the two paragraphs, not after both.
    first = document.index("بند فارسی")
    drawing = document.index("<w:drawing>")
    last = document.rindex("بند فارسی")
    assert first < drawing < last


def _scan_pdf(tmp_path, *, pixels=(1200, 1600), page_pt=(396.0, 612.0)) -> Path:
    """A one-page PDF that is a single full-page raster, like a real scan."""
    import pymupdf
    from PIL import Image, ImageDraw

    raster = Image.new("RGB", pixels, (250, 248, 244))
    draw = ImageDraw.Draw(raster)
    # A dark rectangle in the lower half, standing in for a plate.
    draw.rectangle([pixels[0] // 4, pixels[1] // 2,
                    pixels[0] * 3 // 4, pixels[1] * 3 // 4], fill=(20, 40, 90))
    source = tmp_path / "scan.png"
    raster.save(source)

    document = pymupdf.open()
    page = document.new_page(width=page_pt[0], height=page_pt[1])
    page.insert_image(pymupdf.Rect(0, 0, *page_pt), filename=str(source))
    destination = tmp_path / "scan.pdf"
    document.save(destination)
    document.close()
    return destination


def test_the_crop_comes_from_the_scan_not_from_minerus_export(tmp_path):
    """MinerU's export is a re-encode of its own render; the book has better.

    The exported crop here is deliberately tiny (60x40). If the pipeline copied
    it, the asset would be 60x40. Cutting the same box out of the page's own
    1200x1600 raster yields a plate several times larger — which is the whole
    point of using MinerU for detection only.
    """
    from PIL import Image

    scan = _scan_pdf(tmp_path)
    book = _scanned_book(1)
    book["page"] = {"width_pt": 396.0, "height_pt": 612.0}
    mineru = _mineru_output(tmp_path, [
        {"page_idx": 0, "bbox": [250, 500, 750, 750], "_px": (60, 40)},
    ])

    report = merge_mineru_figures(book, mineru, tmp_path / "assets",
                                  source_pdf=scan)
    assert report["figures_added"] == 1
    assert "fell_back_to_mineru_crop" not in report

    figure = next(b for b in book["blocks"] if b["type"] == "image"
                  and b.get("source") == "source-raster")
    # A context manager, not a bare open: Pillow is lazy about a path, so the
    # handle outlives the test and Windows then refuses to delete the tmp_path —
    # as a PermissionError in a *later* run's setup, naming a directory from
    # three runs ago.
    with Image.open(tmp_path / "assets" / figure["asset"]) as written:
        assert written.width > 200 and written.height > 100, (
            f"the low-resolution export was copied instead: {written.size}"
        )
        assert (figure["pixel_width"], figure["pixel_height"]) == written.size


def test_the_crop_is_exactly_the_box_with_no_resampling(tmp_path):
    """Never upscale, never downscale — the crop is the pixels inside the box."""
    scan = _scan_pdf(tmp_path, pixels=(1200, 1600), page_pt=(396.0, 612.0))
    book = _scanned_book(1)
    book["page"] = {"width_pt": 396.0, "height_pt": 612.0}
    # 250..750 of 1000 across a 396pt page -> 99..297pt -> x3.0303 px/pt.
    mineru = _mineru_output(tmp_path, [{"page_idx": 0, "bbox": [250, 500, 750, 750]}])

    merge_mineru_figures(book, mineru, tmp_path / "assets", source_pdf=scan)
    figure = next(b for b in book["blocks"] if b["type"] == "image"
                  and b.get("source") == "source-raster")

    # The scan keeps its own aspect ratio, so a 1200x1600 raster on a
    # 396x612pt page is letterboxed to 396x528pt: 1200/396 = 3.0303 px/pt in
    # both directions. The requested box is 198pt wide and 153pt tall.
    scale = 1200 / 396
    assert figure["pixel_width"] == pytest.approx(round(198 * scale), abs=2)
    assert figure["pixel_height"] == pytest.approx(round(153 * scale), abs=2)
    assert figure["crop"]["resized"] is False
    # Mapped through the raster's placement, not the page box; measuring from
    # the page corner would slide the crop down by the 42pt margin.
    assert figure["crop"]["placement_pt"][1] == pytest.approx(42.0, abs=1)


def test_the_crop_records_where_it_came_from(tmp_path):
    """Provenance travels with the plate, or nobody can audit the quality."""
    scan = _scan_pdf(tmp_path)
    book = _scanned_book(1)
    book["page"] = {"width_pt": 396.0, "height_pt": 612.0}
    mineru = _mineru_output(tmp_path, [{"page_idx": 0, "bbox": [250, 500, 750, 750]}])

    merge_mineru_figures(book, mineru, tmp_path / "assets", source_pdf=scan)
    figure = next(b for b in book["blocks"] if b["type"] == "image"
                  and b.get("source") == "source-raster")

    crop = figure["crop"]
    assert crop["method"] == "embedded-page-image", crop
    assert crop["source_page"] == 1
    assert crop["encoding"] == "png"
    assert len(crop["bbox_pt"]) == 4 and len(crop["pixel_box"]) == 4
    assert figure["aspect_ratio"] == pytest.approx(
        figure["pixel_width"] / figure["pixel_height"], abs=0.001)


def test_without_a_source_pdf_the_export_is_still_used(tmp_path):
    """MinerU alone must keep working; the better crop is an improvement, not
    a new requirement."""
    book = _scanned_book(1)
    mineru = _mineru_output(tmp_path, [{"page_idx": 0, "bbox": [250, 500, 750, 750]}])
    merge_mineru_figures(book, mineru, tmp_path / "assets")
    figure = next(b for b in book["blocks"] if b["type"] == "image"
                  and "fig" in b["asset"])
    assert figure.get("source") == "mineru"

"""Text-only dictionary readers retain native defaults except image payloads."""
import io

import pymupdf
from PIL import Image

import pagepdf
from read_pdf import read_pdf


def test_text_dictionary_calls_exclude_only_images(tmp_path, monkeypatch):
    source = tmp_path / "text-and-image.pdf"
    buffer = io.BytesIO()
    Image.new("RGB", (20, 10), "navy").save(buffer, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=400)
        page.insert_text((30, 60), "Bold native sentence.", fontname="hebo")
        page.insert_image((30, 90, 130, 140), stream=buffer.getvalue())
        document.save(source)
    actual = pymupdf.Page.get_text
    requests = []

    def observe(page, option="text", *args, **kwargs):
        if option == "dict":
            requests.append(kwargs.get("flags", pymupdf.TEXTFLAGS_DICT))
        return actual(page, option, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", observe)
    book = read_pdf(str(source), tmp_path / "assets")
    view = pagepdf.page_view(source, 0)
    expected = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    assert len(requests) >= 2 and set(requests) == {expected}
    assert any("**Bold native sentence.**" in block.get("text", "")
               for block in book["blocks"])
    assert sum(block["type"] == "image" for block in book["blocks"]) == 1
    assert view["fonts"] == ["Helvetica-Bold"]
    assert view["images"][0]["width_pt"] == 100

"""A recurring margin label must not erase an equal body occurrence."""
import pymupdf

import bookir as ir
from read_pdf import read_pdf


def test_body_repeat_survives_margin_filter_on_each_page_height(tmp_path):
    source = tmp_path / "furniture.pdf"
    with pymupdf.open() as document:
        for index, height in enumerate((600, 600, 300)):
            page = document.new_page(width=400, height=height)
            page.insert_text((30, 18), "Chapter 12", fontsize=10)
            page.insert_text((30, height - 8), str(index + 1), fontsize=10)
            page.insert_text((30, 120), "A complete body sentence.", fontsize=10)
            if index == 2:
                page.insert_text((30, 250), "Chapter 77", fontsize=10)
                page.insert_text((30, 270), "A complete closing sentence.", fontsize=10)
        document.save(source)
    book = read_pdf(str(source), tmp_path / "assets")
    texts = [ir.plain_text(block.get("text", "")) for block in book["blocks"]]
    assert sum("Chapter 77" in text for text in texts) == 1
    assert all("Chapter 12" not in text for text in texts)
    assert "chapter #" in book["source"]["running_heads_dropped"]
    assert sum("A complete body sentence." in text for text in texts) == 3

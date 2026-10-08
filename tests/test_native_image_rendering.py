"""Actual renderer must show an entire rotated native picture, not just XML."""
import argparse
import io

import pymupdf
import pytest
from PIL import Image, ImageDraw

import bookir as ir
import wordrender
from build_docx import Builder, add_arguments

pytestmark = pytest.mark.render


def test_quarterturn_picture_renders_all_four_quadrants(tmp_path):
    if not wordrender.backend():
        pytest.skip('no render backend')
    original = Image.new('RGB', (160, 80))
    draw = ImageDraw.Draw(original)
    colors = [(0, 0, 128), (255, 0, 0), (0, 128, 0), (255, 215, 0)]
    for box, color in zip([(0, 0, 79, 39), (80, 0, 159, 39),
                           (0, 40, 79, 79), (80, 40, 159, 79)], colors):
        draw.rectangle(box, fill=color)
    buffer = io.BytesIO()
    original.save(buffer, format='PNG')
    data = buffer.getvalue()
    asset = tmp_path / 'quadrants.png'
    asset.write_bytes(data)
    book = ir.new_book()
    book['blocks'] = [ir.make_block('image', 1, asset=asset.name, sha256=ir.sha256_bytes(data),
                                   width_pt=40, height_pt=80, transform=[0, 80, -40, 0, 40, 0])]
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(['--book', 'unused', '--out', 'unused', '--no-toc', '--no-page-numbers'])
    source = tmp_path / 'quarterturn.docx'
    Builder(book, tmp_path, options).build(source)
    pdf, chosen = wordrender.render(source, tmp_path / 'render', timeout=60)
    assert chosen and pdf is not None
    with pymupdf.open(pdf) as document:
        assert len(document) == 1
        pix = document[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    points = [[] for _ in colors]
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pixel = pixels[x, y]
            # White page and black text cannot be one of the fixture colors.
            if max(pixel) - min(pixel) < 64:
                continue
            for index, color in enumerate(colors):
                if max(abs(a - b) for a, b in zip(pixel, color)) <= 8:
                    points[index].append((x, y))
                    break
    assert all(len(group) > 2000 for group in points), [len(group) for group in points]
    all_points = [point for group in points for point in group]
    width = max(x for x, y in all_points) - min(x for x, y in all_points) + 1
    height = max(y for x, y in all_points) - min(y for x, y in all_points) + 1
    assert abs(width - 80) <= 3 and abs(height - 160) <= 3, (width, height)
    centers = [(sum(x for x, y in group) / len(group), sum(y for x, y in group) / len(group))
               for group in points]
    navy, red, green, gold = centers
    assert green[0] < navy[0] and gold[0] < red[0]
    assert navy[1] < red[1] and green[1] < gold[1]
    assert asset.read_bytes() == data

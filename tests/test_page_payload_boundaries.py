"""Page transport must distinguish literal payload from its own scaffolding."""
from __future__ import annotations

import logging

import pytest

import bookir as ir
import pagerun
import worksheet
from tests_support import png_bytes

LOG = logging.getLogger(__name__)


@pytest.mark.parametrize('literal', [
    '@@ b99999 para',
    '  @@ b99999 para',
    r'\@@ b99999 para',
    r'\\@@ b99999 para',
    '<!-- revayat-novel: an actual line of the novel -->',
    '<!-- author comment, not generated scaffolding -->',
])
def test_page_payload_round_trips_once_without_inventing_units(tmp_path, literal):
    source = 'Before.\n' + literal + '\nAfter.'
    book = ir.new_book(source_format='epub', pages=1)
    book['blocks'] = [ir.make_block('paragraph', 1, page=1, text=source)]
    # Compare with the canonical IR source, after the constructor's prose cleanup.
    source = book['blocks'][0]['text']
    path, out = tmp_path / 'book.json', tmp_path / 'pages'
    ir.save_book(book, path)
    manifest = pagerun.build(path, out)
    entry = manifest['chunks'][0]
    units = worksheet.read_worksheet((out / entry['file']).read_text(encoding='utf-8'))
    assert len(units) == 1
    assert units[0]['text'] == source
    reply = worksheet.request_line(entry['request']) + '\n@@ b00001 para\n' + worksheet.escape_payload(source) + '\n'
    ir.write_text(out / entry['output'], reply)
    assert pagerun.merge_page(path, out, 1)['ok']
    result = ir.load_book(path)
    assert result['blocks'][0]['text'] == source
    assert result['blocks'][0]['target'] == source
    LOG.debug('Page payload round trip preserved the single source unit')


def test_generated_image_description_never_becomes_prose(tmp_path):
    book = ir.new_book(source_format='epub', pages=1)
    picture = png_bytes(12, 8)
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'image.png').write_bytes(picture)
    book['blocks'] = [ir.make_block('paragraph', 1, page=1, text='Before.'),
        ir.make_block('image', 2, page=1, asset='image.png', alt='An image',
                      pixel_width=12, pixel_height=8, sha256=ir.sha256_bytes(picture)),
        ir.make_block('paragraph', 3, page=1, text='After.')]
    path, out = tmp_path / 'book.json', tmp_path / 'pages'
    ir.save_book(book, path)
    entry = pagerun.build(path, out)['chunks'][0]
    units = worksheet.read_worksheet((out / entry['file']).read_text(encoding='utf-8'))
    assert [(u['id'], u['text']) for u in units] == [
        ('b00001', 'Before.'), ('b00002#alt', 'An image'), ('b00003', 'After.')]

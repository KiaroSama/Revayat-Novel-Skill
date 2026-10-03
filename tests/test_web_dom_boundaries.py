"""Web normalization must not change semantic node identity before EPUB import."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import bookir as ir
import webhtml
import webimport
from tests_support import png_bytes

LOG = logging.getLogger(__name__)


def import_source(tmp_path: Path, body: str, *, selector: str = '#chapter'):
    source = tmp_path / 'chapter.html'
    ir.write_text(source, body)
    manifest = tmp_path / 'chapters.json'
    ir.write_text(manifest, json.dumps({
        'schema': webimport.SCHEMA, 'title': 'Book', 'source_language': 'en',
        'chapters': [{'id': 'one', 'title': 'Chapter', 'path': source.name,
                      'content_selector': selector}],
    }))
    result = webimport.import_book(manifest, tmp_path / 'work')
    LOG.info('Imported synthetic chapter through the production importer')
    return ir.load_book(result['book']), result, manifest


@pytest.mark.parametrize('tag', ['pre', 'code', 'kbd', 'samp', 'tt'])
@pytest.mark.parametrize('selected', [False, True])
def test_one_literal_remains_one_exact_span(tmp_path, tag, selected):
    text = 'A  B\n\tC'
    element = f'<{tag} id="literal">A  <span>B</span>\n\tC</{tag}>'
    body = element if selected else '<article id="chapter">'+element+'</article>'
    book, _, _ = import_source(tmp_path, body, selector='#literal' if selected else '#chapter')
    values = [value for block in book['blocks'] for value in ir.verbatim_spans(block.get('text', ''))]
    assert values == [text]


@pytest.mark.parametrize('tag', ['span', 'a', 'strong', 'em'])
@pytest.mark.parametrize('boundary', ['nested', 'linebreak', 'image'])
def test_one_source_anchor_is_not_duplicated_by_fragmentation(tmp_path, tag, boundary):
    (tmp_path / 'image.png').write_bytes(png_bytes(20, 10))
    middle = {'nested': '<em>second</em>', 'linebreak': '<br>second',
              'image': '<img src="image.png" alt="An image">second'}[boundary]
    href = ' href="https://example.org/target"' if tag == 'a' else ''
    body = (f'<article id="chapter"><p><{tag} id="unique"{href}>first {middle} third</{tag}>'
            '</p><p><a href="#unique">Return</a></p></article>')
    book, _, _ = import_source(tmp_path, body)
    anchored = [block for block in book['blocks'] if 'unique' in block.get('bookmarks', [])]
    assert len(anchored) == 1
    assert any(link['href'] == '#unique' for block in book['blocks'] for link in block.get('links', []))
    assert 'first' in ' '.join(ir.plain_text(b.get('text', '')) for b in book['blocks'])
    if boundary == 'image':
        assert sum(b['type'] == 'image' for b in book['blocks']) == 1


@pytest.mark.parametrize('reference', [
    '<a role="doc-noteref" href="#n1">read <em>this</em></a>',
    '<a epub:type="noteref" href="#n1"><b>1</b> note</a>',
    '<sup><a href="#n1">1<span>2</span></a></sup>',
])
def test_one_note_reference_is_not_multiplied_by_its_display_runs(tmp_path, reference):
    book, _, _ = import_source(tmp_path, '<article id="chapter"><p>Before'+reference+
        ' after.</p><aside id="n1">12 people came.</aside></article>')
    assert len(book['footnotes']) == 1
    assert book['footnotes'][0]['text'] == '12 people came.'
    assert [r for b in book['blocks'] for r in ir.footnote_refs(b.get('text', ''))] == ['fn0001']


@pytest.mark.parametrize('tag', ['pre', 'code', 'kbd', 'samp', 'tt'])
def test_literal_boundaries_preserve_space_nodes_and_explicit_breaks(tmp_path, tag):
    book, _, _ = import_source(tmp_path,
        f'<article id="chapter"><{tag}><b>A</b>  <i>B</i><br>\tC D‌E [[fn:example]]</{tag}></article>')
    assert [s for b in book['blocks'] for s in ir.verbatim_spans(b.get('text', ''))] == [
        'A  B\n\tC D‌E [[fn:example]]']
    assert not book['footnotes']


@pytest.mark.parametrize('tag', ['pre', 'code', 'kbd', 'samp', 'tt'])
def test_whitespace_only_literal_is_not_an_empty_prose_block(tmp_path, tag):
    book, _, _ = import_source(tmp_path,
        f'<article id="chapter"><p>Prose.</p><{tag}>  \t\n </{tag}></article>')
    assert [s for b in book['blocks'] for s in ir.verbatim_spans(b.get('text', ''))] == ['  \t\n ']


@pytest.mark.parametrize('surrounding', [False, True])
def test_empty_explicit_reference_retains_one_note(tmp_path, surrounding):
    marker = '<a role="doc-noteref" href="#n1"></a>'
    prose = 'Before'+marker+' after.' if surrounding else marker
    book, _, _ = import_source(tmp_path, '<article id="chapter"><p>'+prose+
        '</p><aside id="n1">12 people <code>A  B</code>.</aside></article>')
    assert len(book['footnotes']) == 1
    assert book['footnotes'][0]['text'] == '12 people `A  B`.'
    assert [r for b in book['blocks'] for r in ir.footnote_refs(b.get('text', ''))] == ['fn0001']


@pytest.mark.parametrize('parent', ['ol', 'ul'])
def test_selected_list_item_keeps_numbering_kind(tmp_path, parent):
    book, _, _ = import_source(tmp_path, f'<{parent}><li id="chapter">Item.</li></{parent}>')
    assert book['blocks'][-1]['type'] == 'listitem'
    assert book['blocks'][-1]['level'] == 1
    assert book['blocks'][-1]['ordered'] is (parent == 'ol')


@pytest.mark.parametrize('tag', ['pre', 'code'])
def test_literal_note_target_keeps_its_root_semantics(tmp_path, tag):
    book, _, _ = import_source(tmp_path, '<article id="chapter"><p>Read'
        '<a role="doc-noteref" href="#n1">note</a>.</p>'
        f'<{tag} id="n1">A  <b>B</b><br>\tC [[fn:example]]</{tag}></article>')
    assert [s for n in book['footnotes'] for s in ir.verbatim_spans(n['text'])] == [
        'A  B\n\tC [[fn:example]]']


@pytest.mark.parametrize('tag', ['strong', 'a'])
def test_selected_inline_root_keeps_its_own_semantics(tmp_path, tag):
    attrs = ' href="https://example.org/target"' if tag == 'a' else ''
    book, _, _ = import_source(tmp_path, f'<{tag} id="chapter"{attrs}>First <em>second</em>.</{tag}>')
    block = book['blocks'][-1]
    if tag == 'strong':
        assert all(s['bold'] for s in ir.parse_markup(block['text']) if s['text'].strip())
    else:
        assert block['links'] == [{'text': 'First second.', 'href': 'https://example.org/target'}]


@pytest.mark.parametrize('destination', ['<p id="target"></p>', '<hr id="target">'])
def test_empty_block_and_separator_destinations_remain_reachable(tmp_path, destination):
    book, _, _ = import_source(tmp_path, '<article id="chapter"><p><a href="#target">Go</a></p>'
        +destination+'<p>Destination.</p></article>')
    assert sum('target' in b.get('bookmarks', []) for b in book['blocks']) == 1
    assert any(link['href'] == '#target' for b in book['blocks'] for link in b.get('links', []))


def test_selected_caption_keeps_its_kind(tmp_path):
    book, _, _ = import_source(tmp_path, '<figure><figcaption id="chapter">Caption.</figcaption></figure>')
    assert book['blocks'][-1]['type'] == 'caption'


def test_nested_lists_keep_depth_and_numbering_kind(tmp_path):
    book, _, _ = import_source(tmp_path, '<article id="chapter"><ol><li>Outer'
        '<ul><li>Inner</li></ul>After</li><li>Second</li></ol></article>')
    assert [(b['text'], b['level'], b['ordered']) for b in book['blocks'] if b['type'] == 'listitem'] == [
        ('Outer', 1, True), ('Inner', 2, False), ('After', 1, True), ('Second', 1, True)]


def test_empty_inline_anchor_and_its_link_survive(tmp_path):
    book, _, _ = import_source(tmp_path, '<article id="chapter"><p><span id="target"></span>'
        'Target.</p><p><a href="#target">Go</a></p></article>')
    assert any('target' in b.get('bookmarks', []) for b in book['blocks'])
    assert any(link['href'] == '#target' for b in book['blocks'] for link in b.get('links', []))


@pytest.mark.parametrize('tag', ['code', 'pre'])
def test_structured_literals_refuse_instead_of_erasing_an_image(tmp_path, tag):
    (tmp_path / 'image.png').write_bytes(png_bytes(20, 10))
    with pytest.raises(ValueError, match='literal|structured'):
        import_source(tmp_path, f'<article id="chapter"><{tag}>A<img src="image.png">B</{tag}></article>')
    assert not (tmp_path / 'work' / 'book.json').exists()


def test_distinct_source_nodes_with_duplicate_ids_are_still_refused(tmp_path):
    with pytest.raises(ValueError, match='duplicate.*anchor'):
        import_source(tmp_path, '<article id="chapter"><p><span id="same">A</span>'
            '<span id="same">B</span></p></article>')


@pytest.mark.parametrize('root', [
    '<table id="chapter"><tr><td>Text.</td></tr></table>',
    '<svg id="chapter"><text>Text.</text></svg>',
    '<iframe id="chapter">Text.</iframe>',
    '<object id="chapter">Text.</object>',
    '<audio id="chapter">Text.</audio>',
    '<video id="chapter">Text.</video>',
    '<canvas id="chapter">Text.</canvas>',
    '<embed id="chapter">Text.',
    '<article id="chapter" hidden>Text.</article>',
    '<article id="chapter" aria-hidden="true">Text.</article>',
    '<nav id="chapter">Text.</nav>',
])
def test_selected_unusable_root_refuses_before_publishing_a_book(tmp_path, root):
    with pytest.raises(ValueError, match='selected|unsupported|hidden|excluded'):
        import_source(tmp_path, root)
    assert not (tmp_path / 'work' / 'book.json').exists()


def test_one_link_stays_one_link_through_nested_formatting():
    raw = b'<article id="chapter"><p><a href="https://example.org">one <em>two</em> three</a></p></article>'
    result = BeautifulSoup(webhtml.chapter_html(raw, '#chapter', 'Chapter', lambda _: ''), 'html.parser')
    links = result.find_all('a')
    assert len(links) == 1
    assert links[0].get_text() == 'one two three'
    assert links[0].em.get_text() == 'two'


@pytest.mark.parametrize('route', ['chunks', 'pages'])
def test_web_nodes_reach_both_production_docx_routes(tmp_path, route):
    import argparse
    import zipfile
    from xml.etree import ElementTree as ET

    from build_docx import Builder, add_arguments
    import chunk
    import merge
    import opc
    import pagerun
    import qa
    import worksheet

    raw_image = png_bytes(120, 80)
    (tmp_path / 'image.png').write_bytes(raw_image)
    original, result, manifest_path = import_source(tmp_path,
        '<article id="chapter"><h1>Opening</h1><pre>A  <span>B</span>\n\tC</pre>'
        '<p>Before<img src="image.png" alt="A drawing">After'
        '<a role="doc-noteref" href="#n1">read <em>note</em></a>.</p>'
        '<aside id="n1">12 people came.</aside></article>')
    book_path = Path(result['book'])
    work = book_path.parent
    jobs = work / route
    manifest = (chunk if route == 'chunks' else pagerun).build(book_path, jobs, glossary_path=None)
    for entry in manifest['chunks']:
        units = worksheet.read_worksheet((jobs / entry['file']).read_text(encoding='utf-8'))
        reply = [worksheet.request_line(entry['request'])]
        for unit in units:
            # Preserve literals and note edges; this fixture tests transport, not a model's translation.
            reply.extend([f"@@ {unit['id']} {unit['kind']}", worksheet.escape_payload('متن آزمایشی. ' + unit['text'])])
        ir.write_text(jobs / entry['output'], '\n'.join(reply) + '\n')
    merged = merge.merge(book_path, jobs) if route == 'chunks' else pagerun.merge_page(book_path, jobs, 1)
    assert merged['ok'], merged
    book = ir.load_book(book_path)
    assert [b.get('text') for b in book['blocks']] == [b.get('text') for b in original['blocks']]
    assert [literal for b in book['blocks'] for literal in ir.verbatim_spans(b.get('target') or '')] == ['A  B\n\tC']
    assert len(book['footnotes']) == 1
    assert all('illustration ' not in (b.get('target') or '') for b in book['blocks'])
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    destination = work / 'output.docx'
    opts = parser.parse_args(['--book', str(book_path), '--out', str(destination), '--font', 'DejaVu Sans'])
    Builder(book, work / 'assets', opts).build(destination)
    report = qa.check_docx(destination, book).summary()
    assert report['ok'], report
    with zipfile.ZipFile(destination) as archive:
        document = ET.fromstring(archive.read('word/document.xml'))
        assert len(opc.footnote_references(document)) == 1
        assert len(opc.drawing_extents(document)) == 1
        assert b'A  B' in archive.read('word/document.xml')
    image_block = next(b for b in book['blocks'] if b['type'] == 'image')
    assert (work / 'assets' / image_block['asset']).read_bytes() == raw_image
    before = book_path.read_bytes()
    assert webimport.import_book(manifest_path, work, resume=True)['reused']
    assert book_path.read_bytes() == before


@pytest.mark.parametrize('tag', ['pre', 'ul'])
def test_retained_blocks_keep_inherited_emphasis(tmp_path, tag):
    content = 'Exact  text' if tag == 'pre' else '<li>Listed text</li>'
    book, _, _ = import_source(tmp_path, '<article id="chapter"><strong><'+tag+'>'+content+
        '</'+tag+'></strong></article>')
    spans = [span for block in book['blocks'][1:] for span in ir.parse_markup(block.get('text', ''))]
    assert spans and all(span['bold'] for span in spans if span['text'].strip())


def test_retained_list_ruby_matches_ordinary_ruby_normalization():
    raw = '<article id="chapter"><ul><li><ruby>字<rp>(</rp><rt>reading</rt><rp>)</rp></ruby></li></ul></article>'
    result = BeautifulSoup(webhtml.chapter_html(raw, '#chapter', 'Chapter', lambda _: ''), 'html.parser')
    assert result.li.get_text() == '字 (reading)'
    assert not result.find(['rp', 'rt'])


def test_selected_blockquote_remains_a_quote(tmp_path):
    book, _, _ = import_source(tmp_path, '<blockquote id="chapter"><p>Quoted text.</p></blockquote>')
    assert book['blocks'][-1]['type'] == 'blockquote'


@pytest.mark.parametrize('container', ['div', 'p'])
def test_recursive_blocks_preserve_inherited_inline_style_and_anchor(tmp_path, container):
    book, _, _ = import_source(tmp_path, '<article id="chapter"><strong id="styled">'
        '<'+container+'>First.</'+container+'><'+container+'>Second.</'+container+'>'
        '</strong><p><a href="#styled">Return.</a></p></article>')
    prose = [b for b in book['blocks'] if b.get('text') in {'**First.**', '**Second.**'}]
    assert len(prose) == 2
    assert sum('styled' in b.get('bookmarks', []) for b in book['blocks']) == 1


@pytest.mark.parametrize('route', ['web', 'native'])
@pytest.mark.parametrize('body', [
    '<span id="target"></span><p>Destination.</p>',
    '<strong id="target"><p>Destination.</p></strong>',
    '<div><span id="target"></span></div><p>Destination.</p>',
    '<div id="target"></div><p>Destination.</p>',
    '<p>Destination.<span id="target"></span></p>',
    '<p>Destination.</p><span id="target"></span>',
])
def test_empty_or_wrapping_destinations_bind_to_a_real_block(tmp_path, route, body):
    from read_epub import read_epub

    body = '<p><a href="#target">Return.</a></p>' + body
    if route == 'web':
        book, _, _ = import_source(tmp_path, '<article id="chapter">'+body+'</article>')
    else:
        path = tmp_path / 'book.epub'
        path.write_bytes(webimport.epub_bytes(
            {'title': 'Book', 'source_language': 'en'},
            [('one', ('<html><body>'+body+'</body></html>').encode('utf-8'))], {}))
        book = read_epub(str(path), tmp_path / 'assets')
    destinations = [b for b in book['blocks'] if 'target' in b.get('bookmarks', [])]
    assert len(destinations) == 1
    assert ir.plain_text(destinations[0]['text']) == 'Destination.'
    assert sum(link['href'] == '#target' for b in book['blocks'] for link in b.get('links', [])) == 1

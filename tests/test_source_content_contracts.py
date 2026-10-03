"""Actual source import refuses unrepresented structures and retains hard lines."""
import pytest

from read_epub import read_epub
import webimport
from test_web_dom_boundaries import import_source


@pytest.mark.parametrize('ancestor', ['hidden', 'nav', 'form', 'template'])
def test_selected_descendant_cannot_escape_an_excluded_ancestor(tmp_path, ancestor):
    start, end = ('<div hidden>', '</div>') if ancestor == 'hidden' else (f'<{ancestor}>', f'</{ancestor}>')
    with pytest.raises(ValueError, match='selected|excluded|hidden'):
        import_source(tmp_path, start+'<article id="chapter">Visible-looking text.</article>'+end)
    assert not (tmp_path/'work/book.json').exists()


@pytest.mark.parametrize('route', ['web', 'native'])
@pytest.mark.parametrize('math', ['math', 'm:math'])
def test_unrepresented_math_never_becomes_concatenated_prose(tmp_path, route, math):
    body = f'<article id="chapter"><p>Before.</p><{math} xmlns:m="http://www.w3.org/1998/Math/MathML"><mfrac><mn>1</mn><mn>2</mn></mfrac></{math}></article>'
    with pytest.raises(ValueError, match='math|Math|structured'):
        if route == 'web':
            import_source(tmp_path, body)
        else:
            path = tmp_path/'source.epub'
            path.write_bytes(webimport.epub_bytes({'title':'Book','source_language':'en'},
                [('one', ('<html><body>'+body+'</body></html>').encode('utf-8'))], {}))
            read_epub(str(path), tmp_path/'assets')


@pytest.mark.parametrize('route', ['web', 'native'])
@pytest.mark.parametrize(('tag', 'kind'), [('p','paragraph'), ('h2','heading'), ('blockquote','blockquote'), ('figcaption','caption')])
def test_hard_breaks_stay_in_the_original_source_block(tmp_path, route, tag, kind):
    body = f'<article id="chapter"><{tag}>First<br><br><em>second</em><wbr>word.</{tag}></article>'
    if route == 'web':
        book, _, _ = import_source(tmp_path, body)
    else:
        path = tmp_path/'source.epub'
        path.write_bytes(webimport.epub_bytes({'title':'Book','source_language':'en'},
            [('one', ('<html><body>'+body+'</body></html>').encode('utf-8'))], {}))
        book = read_epub(str(path), tmp_path/'assets')
    blocks = [b for b in book['blocks'] if b['type'] == kind and 'First' in b.get('text','')]
    assert len(blocks) == 1
    assert blocks[0]['text'] == 'First\n\n*second*word.'


@pytest.mark.parametrize('route', ['web', 'native'])
@pytest.mark.parametrize('note', [False, True])
def test_explicit_edge_breaks_survive_native_and_web_intake(tmp_path, route, note):
    content = '<p><br>First<br></p>'
    if note:
        content = '<p>Body<a role="doc-noteref" href="#n">1</a>.</p><aside id="n" role="doc-footnote"><br>First<br></aside>'
    body = '<article id="chapter">'+content+'</article>'
    if route == 'web':
        book, _, _ = import_source(tmp_path, body)
    else:
        path = tmp_path/'source.epub'
        path.write_bytes(webimport.epub_bytes({'title':'Book','source_language':'en'},
            [('one', ('<html><body>'+body+'</body></html>').encode('utf-8'))], {}))
        book = read_epub(str(path), tmp_path/'assets')
    text = book['footnotes'][0]['text'] if note else next(b['text'] for b in book['blocks'] if 'First' in b.get('text', ''))
    assert text == '\nFirst\n'

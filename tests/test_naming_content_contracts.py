"""Naming enforcement and QA share complete eligible prose occurrences."""
from copy import deepcopy

import pytest

import bookir as ir
import glossary as gl
import qa


@pytest.mark.parametrize('policy', ['first_mention', 'first_per_chapter', 'never'])
@pytest.mark.parametrize('protected', ['جعلی (Ali)', 'https://example.org/علی(Ali)',
                                       'https://example.org/**علی**(Ali)', '`علی (Ali)`'])
def test_naming_preserves_protected_content_and_reaches_a_fixed_point(policy, protected):
    book = ir.new_book()
    book['blocks'] = [ir.make_block('paragraph', 1, page=1, text='Ali arrived.', target=protected),
                      ir.make_block('paragraph', 2, page=1, text='Ali spoke.', target='**علی** (Ali) گفت.'),
                      ir.make_block('paragraph', 3, page=1, text='Ali left.', target='علی (Ali) رفت.')]
    glossary = gl.new_glossary()
    glossary['policy']['original_parenthetical'] = policy
    entry = gl.make_entry(1, 'Ali', category='person', frequency=3)
    entry.update(target='علی', later_form='علی', first_form='علی (Ali)', locked=True, first_block_id='b00002')
    glossary['entries'] = [entry]
    before = deepcopy(book['blocks'][0])
    gl.enforce_first_mentions(glossary,book)
    assert book['blocks'][0] == before
    assert book['blocks'][1]['target'] == ('**علی** گفت.' if policy == 'never' else '**علی** (Ali) گفت.')
    assert book['blocks'][2]['target'] == 'علی رفت.'
    settled = deepcopy(book)
    gl.enforce_first_mentions(glossary,book)
    assert book == settled
    codes = {f['code'] for f in qa.check_book(book,glossary=glossary).summary()['findings']}
    assert not codes & {'first-mention-missing','first-mention-repeated','first-mention-misplaced'}


@pytest.mark.parametrize('url', ['HTTPS://example.org/علی', 'HtTp://example.org/علی', 'WWW.example.org/علی'])
def test_case_insensitive_urls_cannot_own_or_receive_name_introductions(url):
    book = ir.new_book()
    book['blocks'] = [ir.make_block('paragraph', 1, text='Ali.', target=url),
                      ir.make_block('paragraph', 2, text='Ali.', target='علی آمد.')]
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, 'Ali', category='person', frequency=2)
    entry.update(target='علی', later_form='علی', first_form='علی (Ali)', locked=True, first_block_id='b00001')
    glossary['entries'] = [entry]
    assert gl.enforce_first_mentions(glossary, book)['introduced'] == {'g0001': 'b00002'}
    assert book['blocks'][0]['target'] == url
    assert book['blocks'][1]['target'] == 'علی (Ali) آمد.'


@pytest.mark.parametrize('barrier', ['`literal`', '[[fn:fn0001]]'])
def test_qa_cannot_count_an_introduction_across_a_protected_barrier(barrier):
    book = ir.new_book()
    book['blocks'] = [ir.make_block('paragraph', 1, text='Ali.', target='علی'+barrier+' (Ali)')]
    if barrier.startswith('[['):
        book['footnotes'] = [ir.make_footnote(1, anchor_block='b00001', text='Note.')]
        book['footnotes'][0]['target'] = 'یادداشت.'
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, 'Ali', category='person', frequency=1)
    entry.update(target='علی', later_form='علی', first_form='علی (Ali)', locked=True, first_block_id='b00001')
    glossary['entries'] = [entry]
    codes = {f['code'] for f in qa.check_book(book, glossary=glossary).summary()['findings']}
    assert 'first-mention-missing' in codes


@pytest.mark.parametrize('styled', ['ع**لی** (Ali)', '**ع**لی (Ali)', 'ع*لی* (Ali)'])
def test_existing_complete_introduction_across_styles_is_preserved(styled):
    book = ir.new_book()
    book['blocks'] = [ir.make_block('paragraph', 1, text='Ali.', target=styled)]
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, 'Ali', category='person', frequency=1)
    entry.update(target='علی', later_form='علی', first_form='علی (Ali)', locked=True, first_block_id='b00001')
    glossary['entries'] = [entry]
    before = deepcopy(book)
    assert gl.enforce_first_mentions(glossary, book)['introduced'] == {'g0001': 'b00001'}
    assert book == before
    assert not {f['code'] for f in qa.check_book(book, glossary=glossary).summary()['findings']} & {
        'first-mention-missing', 'first-mention-repeated', 'first-mention-misplaced'}


@pytest.mark.parametrize('suffix', ['_suffix', '‍رضا', 'ِرضا'])
def test_attached_name_fragment_is_not_an_introduction_site(suffix):
    book = ir.new_book()
    book['blocks'] = [ir.make_block('paragraph', 1, text='Other word.', target='علی'+suffix)]
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, 'Ali', category='person', frequency=1)
    entry.update(target='علی', later_form='علی', first_form='علی (Ali)', locked=True)
    glossary['entries'] = [entry]
    before = deepcopy(book)
    assert gl.enforce_first_mentions(glossary, book)['introduced'] == {}
    assert book == before


def test_request_bound_merge_introduces_name_once_and_replays_unchanged(tmp_path):
    import chunk
    import merge
    import worksheet

    book = ir.new_book()
    book['blocks'] = [ir.make_block('paragraph', 1, page=1, text='Ali spoke.')]
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, 'Ali', category='person', frequency=1)
    entry.update(target='علی', later_form='علی', first_form='علی (Ali)', locked=True, first_block_id='b00001')
    glossary['entries'] = [entry]
    path, terms, jobs = tmp_path/'book.json', tmp_path/'terms.json', tmp_path/'chunks'
    ir.save_book(book, path)
    gl.save(glossary, terms)
    manifest = chunk.build(path, jobs, glossary_path=terms, budget=5000)
    assert len(manifest['chunks']) == 1
    request = manifest['chunks'][0]
    ir.write_text(jobs/request['output'], worksheet.request_line(request['request'])+'\n@@ b00001 para\n**علی** (Ali) گفت.\n')
    assert merge.merge(path, jobs, glossary_path=terms)['ok']
    assert ir.load_book(path)['blocks'][0]['target'] == '**علی** (Ali) گفت.'
    before = path.read_bytes()
    assert merge.merge(path, jobs, glossary_path=terms)['ok']
    assert path.read_bytes() == before


def test_name_after_note_marker_has_the_same_owner_for_enforcement_and_qa():
    book = ir.new_book()
    book['blocks'] = [ir.make_block('paragraph', 1, text='Ali.', target='[[fn:fn0001]] علی')]
    book['footnotes'] = [ir.make_footnote(1, anchor_block='b00001', text='Note.')]
    book['footnotes'][0]['target'] = 'یادداشت.'
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, 'Ali', category='person', frequency=1)
    entry.update(target='علی', later_form='علی', first_form='علی (Ali)', locked=True, first_block_id='b00001')
    glossary['entries'] = [entry]
    assert gl.enforce_first_mentions(glossary, book)['introduced'] == {'g0001': 'b00001'}
    assert book['blocks'][0]['target'] == '[[fn:fn0001]] علی (Ali)'
    settled = deepcopy(book)
    gl.enforce_first_mentions(glossary, book)
    assert book == settled
    assert not {f['code'] for f in qa.check_book(book, glossary=glossary).summary()['findings']} & {
        'first-mention-missing', 'first-mention-repeated', 'first-mention-misplaced'}

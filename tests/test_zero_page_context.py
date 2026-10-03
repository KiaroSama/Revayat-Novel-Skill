"""Zero disables context; invalid limits must not touch an existing page run."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

import bookir as ir
import eligible
import pagerun
import pagesheet
from tests_support import reply_text


def book_at(tmp_path):
    book = ir.new_book(source_format='epub', pages=3)
    book['blocks'] = [ir.make_block('paragraph', i, page=i, text=(f'Page {i} stays separate. ' * 80))
                      for i in range(1, 4)]
    path = tmp_path / 'book.json'
    ir.save_book(book, path)
    return book, path


@pytest.mark.parametrize('index', [0, 1, 2])
def test_zero_returns_no_context_on_either_side(tmp_path, index):
    book, _ = book_at(tmp_path)
    assert pagesheet.neighbour_context(book, pagerun.owners(book), index, 0) == ('', '')


@pytest.mark.parametrize('limit', [-1, -600, True, False, 0.5, '0', None])
def test_invalid_context_limits_are_explicit_and_nonmutating(tmp_path, limit):
    _, path = book_at(tmp_path)
    out = tmp_path / 'pages'
    out.mkdir()
    ir.write_text(out / 'manifest.json', 'preserve this existing evidence')
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(ValueError, match='neighbour|context|whole number'):
        pagerun.build(path, out, neighbour_chars=limit)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == before


def test_zero_context_fits_and_uses_the_same_freshness_rule(tmp_path):
    book, path = book_at(tmp_path)
    out = tmp_path / 'pages'
    manifest = pagerun.build(path, out, budget=1400, neighbour_chars=0)
    for entry in manifest['chunks']:
        question = (out / entry['file']).read_text(encoding='utf-8')
        assert len(question) <= 1400
        assert 'Surrounding pages' not in question
        body = '\n'.join(f"@@ {uid} {entry['unit_kinds'][uid]}\nترجمهٔ همان بخش." for uid in entry['unit_ids'])
        ir.write_text(out / entry['output'], reply_text(out / entry['file'], body))
    # Changing another page cannot invalidate context that was explicitly absent.
    book['blocks'][0]['text'] = book['blocks'][0]['text'].replace('separate', 'different')
    ir.save_book(book, path)
    states = eligible.every(out, book_path=path)
    by_id = {entry['id']: entry for entry in manifest['chunks']}
    assert all(s['usable'] for s in states if by_id[s['id']]['page'] == 2)
    result = pagerun.merge_page(path, out, 2)
    assert result['ok'], result


@pytest.mark.parametrize('limit', [1, 20, 600])
def test_positive_context_has_exact_independent_bounds(tmp_path, limit):
    book, _ = book_at(tmp_path)
    before, after = pagesheet.neighbour_context(book, pagerun.owners(book), 1, limit)
    assert before == ir.plain_text(book['blocks'][0]['text']).strip()[-limit:]
    assert after == ir.plain_text(book['blocks'][2]['text']).strip()[:limit]


def test_negative_context_cli_returns_named_failure_without_traceback(tmp_path):
    _, path = book_at(tmp_path)
    cli = Path(pagerun.__file__).with_name('revayat-novel.py')
    process = ir.run_bounded([sys.executable, str(cli), 'pages', 'build', '--book', str(path),
        '--out', str(tmp_path / 'pages'), '--neighbour-chars', '-1'], 20)
    assert process.returncode == 2
    assert json.loads(process.stdout.decode('utf-8'))['ok'] is False
    assert b'Traceback' not in process.stderr
    assert not (tmp_path / 'pages').exists()

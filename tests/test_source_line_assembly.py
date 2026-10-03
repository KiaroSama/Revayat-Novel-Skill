"""Segment envelopes do not erase source-proven line boundaries."""
import pytest

import bookir as ir
import chunk
import merge
import pagerun
import worksheet


@pytest.mark.parametrize('route', ['chunks', 'pages'])
def test_segmented_lines_rejoin_with_source_proven_newlines(tmp_path, route):
    source = '\n'+'\n\n'.join(f'line{n:02d}'+'x'*300 for n in range(12))+'\n'
    book = ir.new_book(source_format='epub', pages=1)
    book['blocks'] = [ir.make_block('verse', 1, page=1, text=source)]
    source = book['blocks'][0]['text']
    path, jobs = tmp_path/'book.json', tmp_path/route
    ir.save_book(book,path)
    manifest = (chunk if route == 'chunks' else pagerun).build(path,jobs,glossary_path=None,budget=1400)
    assert len(manifest['chunks']) > 1
    for entry in manifest['chunks']:
        units = worksheet.read_worksheet((jobs/entry['file']).read_text(encoding='utf-8'))
        reply = [worksheet.request_line(entry['request'])]
        for unit in units:
            reply += [f"@@ {unit['id']} {unit['kind']}",worksheet.escape_payload(unit['text'])]
        ir.write_text(jobs/entry['output'],'\n'.join(reply)+'\n')
    def run():
        return merge.merge(path,jobs) if route == 'chunks' else pagerun.merge_page(path,jobs,1)
    assert run()['ok']
    assert ir.load_book(path)['blocks'][0]['target'] == source
    before = path.read_bytes()
    assert run()['ok']
    assert path.read_bytes() == before


@pytest.mark.parametrize('route', ['chunks', 'pages'])
def test_fresh_segmented_owner_can_merge_beside_an_unselected_stale_owner(tmp_path, route):
    source = '\n\n'.join(f'line{n:02d}'+'x'*300 for n in range(12))
    book = ir.new_book(source_format='epub', pages=2)
    book['blocks'] = [ir.make_block('verse', 1, page=1, text=source),
                      ir.make_block('verse', 2, page=2, text=source)]
    path, jobs = tmp_path/'book.json', tmp_path/route
    ir.save_book(book, path)
    options = {'neighbour_chars': 0} if route == 'pages' else {}
    manifest = (chunk if route == 'chunks' else pagerun).build(path, jobs, glossary_path=None, budget=1400, **options)
    selected = []
    for entry in manifest['chunks']:
        if not all(unit.startswith('b00001#') for unit in entry['unit_ids']):
            continue
        selected.append(entry['id'])
        units = worksheet.read_worksheet((jobs/entry['file']).read_text(encoding='utf-8'))
        reply = [worksheet.request_line(entry['request'])]
        for unit in units:
            reply += [f"@@ {unit['id']} {unit['kind']}", worksheet.escape_payload(unit['text'])]
        ir.write_text(jobs/entry['output'], '\n'.join(reply)+'\n')
    assert len(selected) > 1
    book['blocks'][1]['text'] += ' changed'
    ir.save_book(book, path)
    result = merge.merge(path, jobs, only=selected) if route == 'chunks' else pagerun.merge_page(path, jobs, 1)
    assert result['ok'], result
    changed = ir.load_book(path)
    assert changed['blocks'][0]['target'] == source
    assert changed['blocks'][1].get('target') is None

"""Review payload content and approval identity retain their declared boundaries."""
from copy import deepcopy

import pytest

import fluencysheet
import meaning
import fluency
import bookir as ir
from test_repair_budget import translated as translated, _settled, _pass
from tests_support import review_reply
import published
import reviewsheet


@pytest.mark.parametrize('stage', ['meaning', 'fluency'])
@pytest.mark.parametrize('line', ['~\tمتن', '<!--revayat-novel: literal -->',
                                  '<!--\trevayat-novel: literal -->', r'\~'+'\tمتن',
                                  r'  \\<!--revayat-novel: unfinished'])
def test_review_payload_round_trip_keeps_decoder_shaped_lines(stage, line):
    text = 'آغاز\n'+line+'\nپایان'
    header = '?? b00001 sense' if stage == 'meaning' else '++ b00001 calque'
    reply = header+'\n'+reviewsheet.escape_payload(text)+'\n!! reviewed sheet_0001\n'
    if stage == 'fluency':
        records, claimed, problems = fluencysheet.read_edits(reply)
        key = 'target'
    else:
        records, claimed, problems = meaning.read_findings(reply)
        key = 'detail'
    assert not problems
    assert claimed == ['sheet_0001']
    assert records[0][key] == text


def test_published_digest_frames_source_and_target_fields():
    record = {'id': 'b00001', 'kind': 'para', 'part': 'body', 'origin': 'source',
              'anchor': '', 'context': '', 'source': 'a\0b', 'target': 'c'}
    other = dict(record, source='a', target='b\0c')
    assert published.digest_of([record]) != published.digest_of([other])
    assert published.digest_of([record]) == published.digest_of([dict(reversed(list(record.items())))])
    assert published.digest_of([dict(record, source=None)]) != published.digest_of([dict(record, source='')])
    assert published.digest_of([record], sides=('target',)) == published.digest_of([dict(record, source='different')], sides=('target',))
    changed = deepcopy(record)
    changed['anchor'] = 'a\0b'
    changed['context'] = 'c'
    other = dict(record, anchor='a', context='b\0c')
    assert published.digest_of([changed]) != published.digest_of([other])


def test_fluency_applies_decoder_shaped_persian_then_requires_meaning_recheck(translated, tmp_path):
    mean, out = tmp_path/'meaning', tmp_path/'fluency'
    assert _settled(translated,mean)['ok']
    sheet = fluency.write_sheets(translated,out,mean)['sheets'][0]
    text = 'آغاز\n~\tپیشنهاد فارسی\n<!--revayat-novel: literal -->\nپایان'
    body = '++ b00001 calque\n'+reviewsheet.escape_payload(text)+'\n!! reviewed '+sheet+'\n'
    ir.write_text(out/f'out_{sheet}.md',review_reply(out/f'{sheet}.md',body))
    assert fluency.record(out,translated)['ok']
    assert fluency.apply_edits(translated,out)['ok']
    assert ir.load_book(translated)['blocks'][0]['target'] == text
    assert not meaning.verdict(mean,meaning.revision(meaning.pairs(ir.load_book(translated))))['ok']


def test_recorded_meaning_approval_is_stale_after_ambiguous_field_pair_change(translated, tmp_path):
    book = ir.load_book(translated)
    book['blocks'][0].update(text='a\0b', target='c')
    ir.save_book(book, translated)
    out = tmp_path/'meaning'
    assert _settled(translated, out)['ok']
    book['blocks'][0].update(text='a', target='b\0c')
    ir.save_book(book, translated)
    before = translated.read_bytes()
    assert not meaning.verdict(out, meaning.revision(meaning.pairs(book)))['ok']
    assert translated.read_bytes() == before


@pytest.mark.parametrize('stage', ['meaning','fluency'])
def test_legacy_review_digest_cannot_be_promoted_or_mutate_state(translated,tmp_path,stage):
    mean, out = tmp_path/'meaning', tmp_path/'fluency'
    assert _settled(translated,mean)['ok']
    if stage == 'fluency':
        assert _pass(translated,out,mean,'متن فارسی تازه برای بررسی.')['ok']
    else:
        out = mean
    module = meaning if stage == 'meaning' else fluency
    import json
    sidecar = module.sidecar_path(out)
    state = json.loads(sidecar.read_text(encoding='utf-8'))
    state['revision'] = stage+'3:'+state['revision'].partition(':')[2]
    ir.write_text(sidecar,json.dumps(state,ensure_ascii=False))
    before = (translated.read_bytes(),sidecar.read_bytes())
    args = (out,meaning.revision(meaning.pairs(ir.load_book(translated)))) if stage == 'meaning' else (out,translated,mean)
    assert not module.verdict(*args)['ok']
    assert before == (translated.read_bytes(),sidecar.read_bytes())

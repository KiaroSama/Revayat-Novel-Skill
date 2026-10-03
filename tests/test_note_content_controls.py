"""Word note controls are structural content, independently read back and checked."""
import argparse
import zipfile
from xml.etree import ElementTree as ET

import pytest

import bookir as ir
import opc
import qa
from build_docx import Builder, add_arguments


@pytest.fixture(scope='module')
def note_document(tmp_path_factory):
    work = tmp_path_factory.mktemp('note-controls')
    book = ir.new_book(source_format='epub')
    book['blocks'] = [ir.make_block('paragraph', 1, text='Body.[[fn:fn0001]]',
        target='متن اصلی.[[fn:fn0001]]'), ir.make_block('paragraph', 2,
        text='Link\n\ntext\tend', target='پیوند\n\nمتن\tپایان',
        links=[{'text':'پیوند\n\nمتن\tپایان', 'href':'https://example.org'}])]
    text = 'سطر اول\n\nسطر دوم\tپایان `*literal* [[fn:fn9999]]`'
    book['footnotes'] = [ir.make_footnote(1, anchor_block='b00001', text=text)]
    book['footnotes'][0]['target'] = text
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    opts = parser.parse_args(['--book', 'x', '--out', 'y', '--font', 'Tahoma'])
    doc = work/'book.docx'
    Builder(book, work, opts).build(doc)
    return book, doc


def test_note_writer_emits_ordered_native_controls(note_document):
    book, doc = note_document
    with zipfile.ZipFile(doc) as archive:
        root = ET.fromstring(archive.read('word/footnotes.xml'))
    note = next(n for n in root if n.get(opc.qname('w','id')) == '1')
    assert len(list(note.iter(opc.qname('w','br')))) == 2
    assert len(list(note.iter(opc.qname('w','tab')))) == 1
    assert not any('\n' in (n.text or '') or '\t' in (n.text or '') for n in note.iter(opc.qname('w','t')))
    assert opc.text_of(note).strip() == 'سطر اول\n\nسطر دوم\tپایان *literal* [[fn:fn9999]]'
    assert qa.check_docx(doc, book).summary()['ok']


@pytest.mark.parametrize('mutation', ['space', 'raw', 'move', 'append-break', 'append-tab'])
def test_package_refuses_lost_or_relocated_note_controls(note_document, tmp_path, mutation):
    book, doc = note_document
    with zipfile.ZipFile(doc) as archive:
        items = {n:archive.read(n) for n in archive.namelist()}
    root = ET.fromstring(items['word/footnotes.xml'])
    note = next(n for n in root if n.get(opc.qname('w','id')) == '1')
    control = next(note.iter(opc.qname('w','br')))
    parent = next(n for n in note.iter() if control in list(n))
    if mutation.startswith('append-'):
        run = ET.SubElement(note.find(opc.qname('w','p')), opc.qname('w','r'))
        ET.SubElement(run, opc.qname('w', 'br' if mutation == 'append-break' else 'tab'))
    elif mutation == 'move':
        parent.remove(control)
        note.find(opc.qname('w','p')).append(control)
    else:
        control.tag = opc.qname('w','t')
        control.text = ' ' if mutation == 'space' else '\n'
    items['word/footnotes.xml'] = ET.tostring(root, encoding='utf-8')
    out = tmp_path/'changed.docx'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, blob in items.items():
            archive.writestr(name,blob)
    codes = {item['code'] for item in qa.check_docx(out,book).summary()['findings']}
    assert codes & {'footnote-text-mismatch','footnote-control-text'}


@pytest.mark.parametrize('mutation', ['none', 'space', 'raw', 'move'])
def test_link_controls_are_native_and_package_detects_loss_or_relocation(note_document, tmp_path, mutation):
    book, doc = note_document
    with zipfile.ZipFile(doc) as archive:
        items = {name:archive.read(name) for name in archive.namelist()}
    root = ET.fromstring(items['word/document.xml'])
    link = next(root.iter(opc.qname('w','hyperlink')))
    assert opc.text_of(link) == 'پیوند\n\nمتن\tپایان'
    assert len(list(link.iter(opc.qname('w','br')))) == 2
    assert len(list(link.iter(opc.qname('w','tab')))) == 1
    if mutation == 'none':
        assert qa.check_docx(doc, book).summary()['ok']
        return
    control = next(link.iter(opc.qname('w','br')))
    parent = next(node for node in link.iter() if control in list(node))
    if mutation == 'move':
        parent.remove(control)
        link.append(control)
    else:
        control.tag = opc.qname('w','t')
        control.text = ' ' if mutation == 'space' else '\n'
    items['word/document.xml'] = ET.tostring(root, encoding='utf-8')
    out = tmp_path/'changed.docx'
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, blob in items.items():
            archive.writestr(name, blob)
    codes = {item['code'] for item in qa.check_docx(out, book).summary()['findings']}
    assert codes & {'link-control-text', 'link-text-mismatch'}

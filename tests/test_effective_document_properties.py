"""Namespace/boolean/style-chain evidence is measured from saved native parts."""
import zipfile

import pytest

import pagedocx

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def native(tmp_path, properties='', styles='', prefix='w'):
    path = tmp_path / 'properties.docx'
    document = (f'<w:document xmlns:w="{W}"><w:body><w:p>{properties}'
                '<w:r><w:t>فارسی</w:t></w:r></w:p></w:body></w:document>')
    root = (f'<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr>'
            '<w:rFonts w:cs="Vazir" w:ascii="Times New Roman"/>'
            '</w:rPr></w:rPrDefault></w:docDefaults>' + styles + '</w:styles>')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', document.replace('w:', prefix + ':').replace('xmlns:w', 'xmlns:' + prefix).encode('utf-8'))
        archive.writestr('word/styles.xml', root.replace('w:', prefix + ':').replace('xmlns:w', 'xmlns:' + prefix).encode('utf-8'))
    return path


@pytest.mark.parametrize('value', ['0', 'false', 'off'])
def test_explicit_false_direction_is_not_tag_presence(tmp_path, value):
    path = native(tmp_path, f'<w:pPr><w:bidi w:val="{value}"/></w:pPr>',
                  '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:pPr><w:bidi/></w:pPr></w:style>')
    assert any(f['code'] == 'document-not-rtl' for f in pagedocx.check_direction_in_document(path))


@pytest.mark.parametrize('prefix', ['w', 'native'])
def test_actual_style_chain_and_default_fonts_are_namespace_independent(tmp_path, prefix):
    styles = ('<w:style w:type="paragraph" w:styleId="Base"><w:pPr><w:bidi w:val="on"/></w:pPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Child"><w:basedOn w:val="Base"/></w:style>')
    path = native(tmp_path, '<w:pPr><w:pStyle w:val="Child"/></w:pPr>', styles, prefix)
    assert pagedocx.check_direction_in_document(path) == []
    assert pagedocx.requested_fonts(path) == {'complex': 'Vazir', 'ascii': 'Times New Roman'}


def test_selected_style_false_overrides_normal_true(tmp_path):
    styles = ('<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:pPr><w:bidi/></w:pPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Other"><w:basedOn w:val="Normal"/><w:pPr><w:bidi w:val="0"/></w:pPr></w:style>')
    path = native(tmp_path, '<w:pPr><w:pStyle w:val="Other"/></w:pPr>', styles)
    assert pagedocx.check_direction_in_document(path)


@pytest.mark.parametrize('defect', ['cycle', 'missing', 'invalid'])
def test_unresolved_style_evidence_cannot_pass(tmp_path, defect):
    if defect == 'invalid':
        styles = '<w:style w:type="paragraph" w:styleId="Child"><w:pPr><w:bidi w:val="maybe"/></w:pPr></w:style>'
    else:
        target = 'Child' if defect == 'cycle' else 'Absent'
        styles = f'<w:style w:type="paragraph" w:styleId="Child"><w:basedOn w:val="{target}"/></w:style>'
    path = native(tmp_path, '<w:pPr><w:pStyle w:val="Child"/></w:pPr>', styles)
    assert pagedocx.check_direction_in_document(path)

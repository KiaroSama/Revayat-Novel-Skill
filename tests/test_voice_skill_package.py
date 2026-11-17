"""Contracts for editorial guidance shipped with the native plugin."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / 'skills'
BOOK = SKILLS / 'revayat-novel'


@pytest.mark.parametrize(('name', 'resources'), [
    ('humanizer', ()),
    ('great-russian', ('references/antipatterns.md', 'references/typography.md')),
    ('persian', ('NOTICE', 'references/ai-tells.md', 'scripts/persian_lint.py',
                 'scripts/lookup.py', 'scripts/data/idioms.tsv', 'scripts/data/glossary.tsv')),
])
def test_plugin_ships_named_licensed_voice_payload(name, resources):
    skill = SKILLS / name
    header = (skill / 'SKILL.md').read_text(encoding='utf-8').split('---', 2)[1]
    assert f'name: {name}' in header.splitlines()
    assert 'MIT License' in (skill / 'LICENSE').read_text(encoding='utf-8')
    for path in resources:
        assert (skill / path).read_text(encoding='utf-8').strip(), path


def test_book_routes_by_editable_prose_and_keeps_standalone_fallback():
    workflow = (BOOK / 'SKILL.md').read_text(encoding='utf-8')
    route = BOOK / 'references' / 'writing-voice.md'
    payload = json.loads((ROOT / 'install' / 'payload.json').read_text(encoding='utf-8'))
    assert workflow.count('(references/writing-voice.md)') == 2
    assert 'references/writing-voice.md' in payload
    text = route.read_text(encoding='utf-8')
    for label, slug in [('Persian', 'persian'), ('English', 'humanizer'),
                        ('Russian', 'great-russian')]:
        assert f'| {label} | `revayat-novel:{slug}` |' in text
    assert 'Russian source translated into Persian uses Persian guidance' in text
    assert 'English source translated into Persian uses Persian guidance' in text
    assert 'persian-typography.md' in text
    assert 'translation-policy.md' in text


@pytest.mark.parametrize(('source', 'expected'), [
    ('    print(1)\n\nكتاب', '    print(1)\n\nکتاب'),
    ('نسخه 1.2.3.4', 'نسخه 1.2.3.4'),
    ('این یک جمله فارسی است. "Go, now?"', 'این یک جمله فارسی است. "Go, now?"'),
])
def test_persian_linter_preserves_embedded_literals_and_english(source, expected):
    result = subprocess.run(
        [sys.executable, '-I', '-B', str(SKILLS / 'persian/scripts/persian_lint.py'), '--fix'],
        input=source, text=True, encoding='utf-8', capture_output=True, check=True, timeout=10,
    )
    assert result.stdout == expected

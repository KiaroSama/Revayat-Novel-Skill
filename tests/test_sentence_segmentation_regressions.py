"""Unspaced sentence boundaries must fit without damaging source or markup."""
from __future__ import annotations

import logging
import random

import pytest
import segments

LOG = logging.getLogger(__name__)
SENTENCES = [
    "彼女は窓を開けた。雨が降っていた。それでも外へ出た。",
    "她推开门。外面正在下雨。她仍然走了出去。",
    "「本当なの！？」彼女は聞いた。「そうだ。」彼は答えた。",
    "『次は何？』彼は立ち止まった。",
]


@pytest.mark.parametrize("sentence", SENTENCES)
@pytest.mark.parametrize("budget", [32, 80, 240])
def test_unspaced_sentences_fit_and_rejoin(sentence, budget):
    text = sentence * 80
    parts = segments.split_text(text, budget)
    assert len(parts) > 1
    assert max(map(len, parts)) <= budget
    assert "".join(parts) == text
    LOG.debug("Verified sentence segmentation: characters=%d parts=%d", len(text), len(parts))


@pytest.mark.parametrize("mark", ["。", "！", "？", "！？"])
def test_closing_quotes_stay_with_their_sentence(mark):
    text = ("「一つ目" + mark + "」次の文。") * 40
    parts = segments.split_text(text, 10)
    assert "".join(parts) == text
    assert all(not part.startswith("」") for part in parts[1:])


@pytest.mark.parametrize("literal", ["`文字。文字？文字！`", "**文字。文字？**", "***文字。文字！***"])
def test_sentence_punctuation_inside_markup_remains_atomic(literal):
    text = ("前の文。" + literal + "次の文。") * 40
    parts = segments.split_text(text, 32)
    edges, offset = [], 0
    for part in parts:
        edges.append((offset, offset + len(part)))
        offset += len(part)
    for token in segments.ir._INLINE.finditer(text):
        assert any(lo <= token.start() and token.end() <= hi for lo, hi in edges)
    assert "".join(parts) == text
    assert max(map(len, parts)) <= 32


def test_literal_url_is_not_cut_on_unicode_punctuation():
    url = "https://example.invalid/" + "第一句。第二句？" * 20
    parts = segments.split_text("前の文。 " + url + " 後の文。", 32)
    assert any(url in part for part in parts)


def test_combining_mark_is_not_separated_from_punctuation():
    text = "文章。\u0301次の文。" * 40
    parts = segments.split_text(text, 9)
    assert "".join(parts) == text
    assert all(not part.startswith("\u0301") for part in parts)


def test_overlong_indivisible_sentence_is_not_cut_arbitrarily():
    text = "漢" * 500 + "。"
    assert segments.split_text(text, 80) == [text]


def test_nonpositive_budget_and_empty_input_preserve_existing_contract():
    for text in ["", "  ", SENTENCES[0]]:
        assert segments.split_text(text, 0) == [text]
        assert segments.split_text(text, -1) == [text]


def test_random_payloads_rejoin_byte_for_byte():
    rng = random.Random(92817)
    atoms = ["彼女。", "雨！", "「本当？」", "a ", "b\\*c", "`。？`", "**文。**", "\n", "。\u0301"]
    for _ in range(300):
        text = "".join(rng.choice(atoms) for _ in range(rng.randrange(1, 70)))
        assert "".join(segments.split_text(text, rng.randrange(1, 80))) == text


def test_real_fitter_accepts_a_long_unspaced_paragraph():
    text = SENTENCES[0] * 150
    units = [("b00001", "para", text)]
    def render(group):
        return "request\n" + "".join(f"@@ {i} {k}\n{s}\n" for i, k, s in group)
    fitted = segments.fit_units(units, render, 320)
    assert len(fitted) > 1
    assert "".join(s for _, _, s in fitted) == text
    jobs = segments.fit_jobs(render, fitted, 320)
    assert all(len(sheet) <= 320 for _, sheet in jobs)
    assert segments.coverage_problems(segments.segments_by_owner(i for i, _, _ in fitted),
                                      [i for i, _, _ in fitted]) == []


def test_space_separated_prose_keeps_word_boundaries():
    for text in ["hello world another sentence " * 30, "صبح به آرامی آمد و او ایستاد. " * 30]:
        parts = segments.split_text(text, 32)
        assert "".join(parts) == text
        assert all(p[-1].isspace() for p in parts[:-1])

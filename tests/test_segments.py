"""One paragraph longer than the whole budget, cut and put back exactly.

The budget exists to keep a payload inside a model's context. Refusing an
oversized paragraph and telling the operator to raise `--budget` preserves every
word and defeats the point: the ceiling that was there to prevent a
context-limit failure is raised for every other job on the run.

So the unit is cut. Everything here is about the cut being undoable — the join
property first, because if that does not hold nothing else matters, and then the
markup, the identity and the order.
"""

from __future__ import annotations

import random

import pytest

import bookir as ir
import segments

PERSIAN = ("صبح به آرامی از فراز تپه‌ها بالا آمد و الیزابت کنار پنجره "
           "ایستاده بود و به جاده نگاه می‌کرد. ")
MARKED = ("The letter called it a **fee simple absolute**, and[[fn:fn0001]] "
          "she read it twice before she believed it. ")


def _fuzz(count: int = 400) -> list[str]:
    """Strings including malformed markup — a lone backtick, an unclosed pair.

    Deliberately not only well-formed text. `render_spans(parse_markup(x))` is
    the inverse of the *renderer*, not of arbitrary input, so a design that
    rebuilt pieces by re-rendering parsed spans would silently drop a stray
    marker. Cutting the original string cannot, and this is what proves it.
    """
    random.seed(7)
    return ["".join(random.choice("ab ‌*`[]:fn0123")
                    for _ in range(random.randint(0, 300)))
            for _ in range(count)]


# --------------------------------------------------------------------------- #
# The property everything else rests on
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("budget", [1, 5, 20, 50, 120, 400, 5000])
def test_the_pieces_always_rejoin_to_the_original(budget):
    for text in [PERSIAN * 20, MARKED * 8, "word" * 500, "", "   ", *_fuzz()]:
        parts = segments.split_text(text, budget)
        assert "".join(parts) == text, (
            f"{len(text)} characters at budget {budget} came back as "
            f"{sum(len(p) for p in parts)}"
        )


@pytest.mark.parametrize("budget", [5, 20, 120, 400])
def test_no_markup_token_is_ever_halved(budget):
    """`**bold` in one segment and `**` in the next is unrecoverable."""
    for text in [MARKED * 8, PERSIAN * 20, *_fuzz(120)]:
        parts = segments.split_text(text, budget)
        edges, offset = [], 0
        for part in parts:
            edges.append((offset, offset + len(part)))
            offset += len(part)
        for token in ir._INLINE.finditer(text):
            assert any(low <= token.start() and token.end() <= high
                       for low, high in edges), (
                f"{token.group(0)!r} was split at budget {budget}"
            )


def test_a_cut_lands_after_a_space_where_one_is_available(budget=120):
    parts = segments.split_text(PERSIAN * 20, budget)
    assert len(parts) > 1
    for part in parts[:-1]:
        assert part[-1].isspace(), f"{part[-30:]!r} ends mid-word"


def test_a_word_longer_than_the_budget_is_left_whole(budget=10):
    """Two fragments of one word are words in no language."""
    text = "short " + "x" * 60 + " tail"
    parts = segments.split_text(text, budget)
    assert "".join(parts) == text
    assert any("x" * 60 in part for part in parts)


def test_nothing_is_split_when_it_already_fits():
    assert segments.split_text(PERSIAN, 10_000) == [PERSIAN]


# --------------------------------------------------------------------------- #
# Identity: the block owns the text, a segment only carries it
# --------------------------------------------------------------------------- #

def test_a_segment_id_names_its_block_and_its_place():
    assert segments.segment_id("b00042", 2) == "b00042#2"
    assert segments.base_of("b00042#2") == "b00042"
    assert segments.index_of("b00042#2") == 2
    assert segments.index_of("b00042") == 0


def test_an_images_alt_text_is_not_mistaken_for_a_segment():
    """`#alt` was there first, and folding it into `b00042` would overwrite
    the paragraph's translation with the picture's caption."""
    assert segments.base_of("b00042#alt") == "b00042#alt"
    assert segments.rejoin({"b00042#alt": "caption"}) == {"b00042#alt": "caption"}


def test_segments_rejoin_in_index_order_whatever_order_they_arrive_in():
    joined = segments.rejoin({"b1#3": "third.", "b1#1": "first ", "b1#2": "second "})
    assert joined == {"b1": "first second third."}


def test_a_stripped_reply_still_rejoins_with_its_words_apart():
    """What actually comes back: `parse_worksheet` strips every unit body."""
    joined = segments.rejoin({"b1#1": "first", "b1#2": "second", "b1#3": "third."})
    assert joined == {"b1": "first second third."}


def test_a_segment_nobody_answered_does_not_glue_two_words_together():
    joined = segments.rejoin({"b1#1": "first", "b1#2": "   ", "b1#3": "third."})
    assert joined == {"b1": "first third."}


def test_owners_deduplicates_and_keeps_reading_order():
    assert segments.owners(["b1#1", "b1#2", "b2", "b3#alt", "b1#3"]) == [
        "b1", "b2", "b3#alt"]


# --------------------------------------------------------------------------- #
# Fitting: what the page run actually calls
# --------------------------------------------------------------------------- #

def _render(units, overhead: int = 300) -> str:
    """Stands in for a rendered worksheet: prose plus a constant scaffold."""
    return "x" * overhead + "".join(text for _, _, text in units)


def test_one_oversized_unit_is_cut_and_every_payload_fits():
    """The claim of the whole module: no operator has to raise the budget."""
    budget = 1000
    long_one = ("b00002", "para", PERSIAN * 30)
    units = [("b00001", "para", "A short one."), long_one,
             ("b00003", "para", "Another short one.")]

    fitted = segments.fit_units(units, _render, budget)

    assert len(fitted) > len(units), "the long unit was not cut at all"
    for unit in fitted:
        assert len(_render([unit])) <= budget, (
            f"{unit[0]} renders {len(_render([unit]))} against a {budget} budget"
        )
    rejoined = segments.rejoin({unit_id: text for unit_id, _, text in fitted})
    # Word for word, in order. `rejoin` normalises the separator at a cut to
    # one space, because a reply comes back stripped and the byte the cut fell
    # on is not recoverable — `split_text` is where the byte-exact property
    # lives, and it is tested above.
    assert rejoined["b00002"].split() == long_one[2].split(), (
        "the paragraph came back changed"
    )
    assert set(rejoined) == {"b00001", "b00002", "b00003"}, (
        "the book would have gained or lost a block"
    )


def test_a_unit_that_already_fits_is_left_exactly_as_it_was():
    """The ordinary book must be unchanged down to the tuple."""
    units = [("b00001", "para", "Short."), ("b00002", "para", "Also short.")]
    assert segments.fit_units(units, _render, 5000) == units


def test_a_budget_with_no_room_left_for_prose_refuses_rather_than_fragments():
    """A worksheet carrying four words is not an answer; the refusal is."""
    units = [("b00001", "para", PERSIAN * 30)]
    fitted = segments.fit_units(units, lambda u: _render(u, overhead=990), 1000)
    assert fitted == units, "it should be left whole for the caller to refuse"


def test_the_room_a_segment_gets_is_measured_not_guessed():
    """A page whose glossary is large has less room, and must be given less."""
    units = [("b00001", "para", PERSIAN * 30)]
    roomy = segments.fit_units(units, lambda u: _render(u, 300), 2000)
    cramped = segments.fit_units(units, lambda u: _render(u, 1500), 2000)
    assert len(cramped) > len(roomy), (
        "the same text was cut into the same number of pieces regardless of "
        "how much of the budget the scaffolding took"
    )

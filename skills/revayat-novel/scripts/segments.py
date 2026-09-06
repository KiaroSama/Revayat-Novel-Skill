"""Splitting one oversized unit, reversibly, without cutting the prose.

A page whose units are many and short is split into sub-jobs and that is the
end of it. The awkward case is a *single* unit longer than the whole budget —
a page-long paragraph, an unbroken speech, a wall of transcribed text. Until
now the run refused and told the operator to raise `--budget`, which preserves
every word and defeats the purpose: the budget exists to keep a payload inside
a model's context, and raising it to fit the one paragraph that overflowed puts
every other job at risk of the failure the budget was there to prevent.

So the unit is cut, under four rules that make the cut safe to undo:

* **Nothing is ever dropped.** ``"".join(split_text(t, n)) == t``, always, for
  any text and any budget. That is a property, and it is tested as one. The
  round trip through a translator is one space weaker, and deliberately: a
  reply arrives stripped, so ``rejoin`` puts a single space back where the cut
  fell. The words and their order are exact; the whitespace at a segment
  boundary is normalised, because a model cannot be asked to preserve a
  trailing space it cannot see.
* **The cut never lands inside markup.** Cut points are found on the original
  string with the same tokeniser ``parse_markup`` uses, and every piece is a
  plain slice — so ``**bold**`` cannot come back as ``**bold`` in one segment
  and ``**`` in the next, and a ``[[fn:...]]`` marker cannot be halved.
* **The block keeps its identity.** ``b00042`` stays the owner; ``b00042#1``
  and ``b00042#2`` are transport, they exist between the worksheet and the
  merge and nowhere else. The finished book holds one ``b00042``.
* **Order is the text's own.** Segments rejoin by their index, so a translator
  answering part two out of order cannot reorder the paragraph.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

import bookir as ir

#: ``b00042#2``. Numeric on purpose: ``b00042#alt`` is an image's alt text and
#: must not be mistaken for a segment of it.
SEGMENT = re.compile(r"^(?P<base>.+)#(?P<index>\d+)$")

#: Below this there is no room for prose worth translating, so a unit that
#: cannot be given at least this much is left whole and the run refuses as it
#: always did — an honest refusal beats a worksheet carrying four words.
MIN_SEGMENT_CHARS = 200

#: How many times the room may shrink before giving up. Each pass costs one
#: render per segment, and the overshoot is a handful of characters, so this
#: converges in one or two; the cap is here so a pathological render function
#: cannot spin.
FIT_ATTEMPTS = 6


def segment_id(unit_id: str, index: int) -> str:
    return f"{unit_id}#{index}"


def base_of(unit_id: str) -> str:
    """``b00042#2`` -> ``b00042``; anything else unchanged."""
    found = SEGMENT.match(unit_id)
    return found.group("base") if found else unit_id


def index_of(unit_id: str) -> int:
    """Where this segment sits in its unit. ``0`` for a unit that is whole."""
    found = SEGMENT.match(unit_id)
    return int(found.group("index")) if found else 0


def cut_points(text: str) -> list[int]:
    """Indices where ``text`` may be cut without halving a markup token.

    Computed on the original string with the tokeniser ``parse_markup`` itself
    uses, so the pieces are plain slices and rejoining them is exact by
    construction - for any input, including a string whose markup is malformed.
    Re-rendering parsed spans instead would be lossy exactly there: a lone
    backtick is not verbatim markup, and round-tripping it through the parser
    quietly drops it.
    """
    points = {0, len(text)}
    cursor = 0
    for match in ir._INLINE.finditer(text):
        # Every position inside a plain run is safe; inside a token, none is.
        points.update(range(cursor, match.start() + 1))
        points.add(match.end())
        cursor = match.end()
    points.update(range(cursor, len(text) + 1))
    return sorted(points)


def split_text(text: str, budget: int) -> list[str]:
    """``text`` in pieces of at most ``budget`` characters that rejoin exactly.

    ``"".join(split_text(t, n)) == t`` for every ``t`` and every ``n``. That is
    the property the whole design rests on, and it is tested as one.

    Cuts land after a space where one is available, because a piece ending
    mid-word hands the translator half a word and the next job the other half.
    A word longer than the budget is left whole and over: cutting inside one
    produces two fragments that are words in no language.
    """
    if budget <= 0 or len(text) <= budget:
        return [text]

    safe = cut_points(text)
    ends_a_word = [p for p in safe if p and (p == len(text) or text[p - 1].isspace())]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        limit = start + budget
        within = [p for p in ends_a_word if start < p <= limit]
        if within:
            end = max(within)
        else:
            # One word longer than the whole allowance. Run on to the end of
            # it rather than hand the translator two halves of a word that are
            # words in no language; the overshoot is bounded by that word.
            beyond = [p for p in ends_a_word if p > limit]
            end = beyond[0] if beyond else len(text)
        pieces.append(text[start:end])
        start = end
    return pieces


def fit_units(
    units: list[tuple[str, str, str]],
    render: Callable[[list[tuple[str, str, str]]], str],
    budget: int,
) -> list[tuple[str, str, str]]:
    """Units in reading order, with any that cannot fit alone cut into segments.

    The room a segment gets is *measured* and then *checked*, never estimated
    once. A first guess comes from rendering the unit with its text emptied,
    which gives the scaffolding a job of this shape carries. That guess is
    close and not exact - a worksheet's glossary rows are the ones its own
    prose calls for, so a segment can pull in a row the empty one did not, and
    the result overshoots by a handful of characters. Measured: 3001 against a
    3000 budget. So the segments are rendered and, if any is still over, the
    room shrinks by the worst overshoot and it is cut again.
    """
    fitted: list[tuple[str, str, str]] = []
    for unit in units:
        unit_id, kind, text = unit
        if len(render([unit])) <= budget:
            fitted.append(unit)
            continue

        room = budget - len(render([(unit_id, kind, "")]))
        cut = None
        for _ in range(FIT_ATTEMPTS):
            if room < MIN_SEGMENT_CHARS:
                break
            parts = split_text(text, room)
            if len(parts) < 2:
                break
            candidate = [(segment_id(unit_id, index), kind, part)
                         for index, part in enumerate(parts, start=1)]
            overshoot = max(len(render([one])) - budget for one in candidate)
            if overshoot <= 0:
                cut = candidate
                break
            room -= max(overshoot, 1)

        # Nothing that fits. Leave it whole so the caller refuses with the real
        # numbers rather than writing a worksheet of fragments.
        fitted.extend(cut or [unit])
    return fitted


def rejoin(units: dict[str, str]) -> dict[str, str]:
    """Fold every segment back into its unit, in index order.

    Called once, between validating a worksheet's headers and writing anything
    onto the book, so everything downstream — `apply_units`, the QA gates, the
    finished IR — sees one unit per block and never learns that a segment
    existed.
    """
    whole: dict[str, str] = {}
    parts: dict[str, list[tuple[int, str]]] = {}
    for unit_id, value in units.items():
        found = SEGMENT.match(unit_id)
        if found:
            parts.setdefault(found.group("base"), []).append(
                (int(found.group("index")), value))
        else:
            whole[unit_id] = value
    for base, found in parts.items():
        # One space between segments, not the original separator. A worksheet
        # reply arrives stripped - `parse_worksheet` strips it, and no model can
        # be asked to preserve a trailing space it cannot see - so the byte the
        # cut fell on is not recoverable and pretending otherwise would join two
        # sentences into one word. The split guarantees no word was broken; the
        # join puts a single space where a word boundary was.
        whole[base] = " ".join(value.strip() for _, value in sorted(found)
                               if value.strip())
    return whole


def owners(unit_ids: Iterable[str]) -> list[str]:
    """The blocks these units belong to, deduplicated, in order.

    A gate that asks "is every unit of this page translated" must ask the book
    about ``b00042``, never about ``b00042#2`` — the book has never heard of
    the second and would report it missing from a page that is complete.
    """
    seen: list[str] = []
    for unit_id in unit_ids:
        base = base_of(unit_id)
        if base not in seen:
            seen.append(base)
    return seen

"""One name, one introduction, in one place — decided by code, not by prompt.

Chunks are translated in parallel by agents that cannot see one another, so
"is this the first mention of this name?" is a question none of them can answer
correctly. Each one looks at its own chunk, sees the name for the first time,
and introduces it. The finished book then reads
«الیزابت بنت (Elizabeth Bennet)» in thirty places.

The worksheet asks the owning chunk to do the introducing, and asking is worth
doing — but it is not a guarantee, and this file is about the part that is. The
enforcement pass runs after every chunk is back and settles the question from
outside: flatten every introduction, then re-introduce exactly one.
"""

from __future__ import annotations

import pytest

import bookir as ir
import glossary as gl
import qa

ELIZABETH = "الیزابت بنت"
INTRODUCED = "الیزابت بنت (Elizabeth Bennet)"


def _glossary(*, first_block_id="b00002", locked=True, policy="first_mention"):
    book_glossary = gl.new_glossary()
    book_glossary["policy"]["original_parenthetical"] = policy
    entry = gl.make_entry(1, "Elizabeth Bennet", category="person", frequency=9)
    entry.update({"target": ELIZABETH, "later_form": ELIZABETH,
                  "first_form": INTRODUCED, "locked": locked,
                  "first_block_id": first_block_id})
    book_glossary["entries"] = [entry]
    return book_glossary


def _book(targets: list[str]) -> dict:
    book = ir.new_book()
    blocks = []
    for index, target in enumerate(targets, start=1):
        block = ir.make_block("paragraph", index, page=1,
                              text=f"Source sentence {index} mentioning her.")
        block["target"] = target
        blocks.append(block)
    book["blocks"] = blocks
    return book


def _targets(book) -> list[str]:
    return [b["target"] for b in book["blocks"]]


def _codes(book, glossary) -> dict[str, str]:
    summary = qa.check_book(book, glossary=glossary).summary()
    return {f["code"]: f["severity"] for f in summary["findings"]}


# --------------------------------------------------------------------------- #
# Enforcement
# --------------------------------------------------------------------------- #

def test_every_chunk_introducing_the_name_collapses_to_one():
    """The failure this exists for: three parallel agents, three introductions."""
    book = _book([f"او {INTRODUCED} را دید.",
                  f"سپس {INTRODUCED} برخاست.",
                  f"و {INTRODUCED} رفت."])
    report = gl.enforce_first_mentions(_glossary(first_block_id="b00001"), book)

    assert _targets(book) == [f"او {INTRODUCED} را دید.",
                              f"سپس {ELIZABETH} برخاست.",
                              f"و {ELIZABETH} رفت."]
    assert report["introduced"] == {"g0001": "b00001"}
    assert report["flattened"] == 3


def test_the_introduction_lands_in_the_block_the_scan_chose():
    book = _book([f"او {ELIZABETH} را دید.",
                  f"سپس {ELIZABETH} برخاست.",
                  f"و {ELIZABETH} رفت."])
    gl.enforce_first_mentions(_glossary(first_block_id="b00002"), book)
    assert _targets(book)[1] == f"سپس {INTRODUCED} برخاست."
    assert INTRODUCED not in _targets(book)[0]
    assert INTRODUCED not in _targets(book)[2]


def test_it_lands_on_the_first_occurrence_inside_that_block():
    book = _book([f"{ELIZABETH} گفت و {ELIZABETH} رفت."])
    gl.enforce_first_mentions(_glossary(first_block_id="b00001"), book)
    assert _targets(book)[0] == f"{INTRODUCED} گفت و {ELIZABETH} رفت."


def test_running_it_twice_changes_nothing():
    """It runs after every merge, so it has to be idempotent."""
    book = _book([f"او {INTRODUCED} را دید.", f"سپس {ELIZABETH} برخاست."])
    glossary = _glossary(first_block_id="b00001")
    gl.enforce_first_mentions(glossary, book)
    once = _targets(book)
    gl.enforce_first_mentions(glossary, book)
    assert _targets(book) == once


def test_a_missing_introduction_is_added_not_merely_reported():
    """No chunk introduced her at all; the pass places it."""
    book = _book([f"او {ELIZABETH} را دید.", f"سپس {ELIZABETH} برخاست."])
    gl.enforce_first_mentions(_glossary(first_block_id="b00002"), book)
    assert "".join(_targets(book)).count(INTRODUCED) == 1


def test_an_unlocked_name_is_left_alone():
    """Locking is the translator's decision that this spelling is final."""
    book = _book([f"او {INTRODUCED} را دید.", f"باز {INTRODUCED} آمد."])
    before = _targets(book)
    report = gl.enforce_first_mentions(_glossary(locked=False), book)
    assert _targets(book) == before and report["skipped"] == 1


def test_policy_never_removes_the_parenthetical_everywhere():
    book = _book([f"او {INTRODUCED} را دید.", f"باز {INTRODUCED} آمد."])
    gl.enforce_first_mentions(_glossary(policy="never"), book)
    assert _targets(book) == [f"او {ELIZABETH} را دید.", f"باز {ELIZABETH} آمد."]


def test_a_name_that_appears_nowhere_is_reported_not_forced_in():
    book = _book(["متنی که هیچ نامی ندارد.", "و متنی دیگر."])
    report = gl.enforce_first_mentions(_glossary(), book)
    assert report["unplaceable"] == ["g0001"]
    assert INTRODUCED not in "".join(_targets(book))


# --------------------------------------------------------------------------- #
# The substring trap
# --------------------------------------------------------------------------- #

def test_a_name_inside_a_longer_word_is_not_touched():
    """The reason this is not `str.replace`.

    A short name is a substring of longer words. Replacing blindly turns
    «علیرضا» into «علی (Ali)رضا» — a corruption that reads as a translation
    error and is very hard to trace back to the glossary.
    """
    book = ir.new_book()
    block = ir.make_block("paragraph", 1, page=1, text="Ali and Alireza.")
    block["target"] = "علیرضا و علی رفتند."
    book["blocks"] = [block]

    glossary = _glossary(first_block_id="b00001")
    entry = glossary["entries"][0]
    entry.update({"source": "Ali", "target": "علی", "later_form": "علی",
                  "first_form": "علی (Ali)"})

    gl.enforce_first_mentions(glossary, book)
    assert block["target"] == "علیرضا و علی (Ali) رفتند."


def test_a_zero_width_non_joiner_compound_is_not_a_mention():
    """Persian glues word parts with U+200C; a name flanked by it is a fragment."""
    assert gl.standalone_spans("علی‌رضا رفت", "علی") == []
    assert gl.standalone_spans("او و علی رفتند", "علی") == [(5, 8)]


def test_aliases_keep_their_own_spelling():
    """A nickname with a distinct form is a decision, not drift to normalise."""
    book = _book([f"او {ELIZABETH} را دید.", "و لیزی خندید."])
    glossary = _glossary(first_block_id="b00001")
    glossary["entries"][0]["aliases"] = ["Lizzy"]
    gl.enforce_first_mentions(glossary, book)
    assert _targets(book)[1] == "و لیزی خندید."


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #

def test_qa_rejects_two_introductions_in_different_blocks():
    book = _book([f"او {INTRODUCED} را دید.", f"باز {INTRODUCED} آمد."])
    assert _codes(book, _glossary(first_block_id="b00001")).get(
        "first-mention-repeated") == qa.ERROR


def test_qa_rejects_two_introductions_inside_one_block():
    """The case a block-level list cannot see."""
    book = _book([f"او {INTRODUCED} را دید و {INTRODUCED} رفت."])
    finding = next(f for f in qa.check_book(
        book, glossary=_glossary(first_block_id="b00001")).summary()["findings"]
        if f["code"] == "first-mention-repeated")
    assert "x2" in finding["detail"], finding["detail"]


def test_qa_rejects_an_introduction_that_never_happened():
    book = _book([f"او {ELIZABETH} را دید.", f"باز {ELIZABETH} آمد."])
    assert _codes(book, _glossary()).get("first-mention-missing") == qa.ERROR


def test_qa_rejects_an_introduction_in_the_wrong_block():
    book = _book([f"او {INTRODUCED} را دید.", f"باز {ELIZABETH} آمد."])
    assert _codes(book, _glossary(first_block_id="b00002")).get(
        "first-mention-misplaced") == qa.ERROR


def test_qa_rejects_the_long_form_reused_after_the_introduction():
    """A later block repeating the introduction is caught by the count.

    There is no separate rule for it, and there should not be: the long form
    always carries the parenthetical, so a second use of one is a second use of
    the other. A rule of its own would fire alongside this one on every input
    and report the same defect twice.
    """
    book = _book([f"او {ELIZABETH} را دید.",
                  f"سپس {INTRODUCED} برخاست.",
                  f"باز {INTRODUCED} آمد."])
    codes = _codes(book, _glossary(first_block_id="b00002"))
    assert codes.get("first-mention-repeated") == qa.ERROR


def test_qa_rejects_a_parenthetical_when_policy_forbids_it():
    book = _book([f"او {INTRODUCED} را دید."])
    assert _codes(book, _glossary(policy="never")).get(
        "first-mention-forbidden") == qa.ERROR


def test_the_enforced_book_passes_the_gate():
    """Enforcement and the gate must agree, or one of them is wrong."""
    book = _book([f"او {INTRODUCED} را دید.",
                  f"سپس {INTRODUCED} برخاست.",
                  f"و {INTRODUCED} رفت."])
    glossary = _glossary(first_block_id="b00002")
    gl.enforce_first_mentions(glossary, book)

    codes = _codes(book, glossary)
    assert not any(code.startswith("first-mention") for code in codes), codes

"""One naming plan, read by the pass that writes it and the gate that checks it.

Three ways the two could disagree, all of them measured, and all of them the same
failure: the book is *correct* and the gate demands a change no pass can make, so
re-running converges on nothing.

* the per-chapter branch of the gate asked for ``first_block_id`` — where the
  entity first appears in the **source**. When that block's Persian is a
  nickname, the parenthetical cannot go there: the pass put the introduction in
  the next block that uses the canonical form and the gate demanded the pinned
  one.
* eligibility was computed on two views of the text. `prose_of` concatenates the
  markup spans, so ``**الیزابت** بنت`` read as contiguous and the block looked
  eligible; the writer works inside one span at a time and could place nothing
  there.
* the writer's own fallback — the first block of the group that can carry the
  introduction — lived where the gate could not reach it.

And one drift defect of the same family: with both "Elizabeth Bennet" and "Lizzy"
in a source unit, the check asked whether **any** accepted form was present, so
«لیزی» alone discharged both obligations and the full name vanished from a
paragraph that names her twice.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import glossary as gl  # noqa: E402
import naming  # noqa: E402
import qa  # noqa: E402

FULL = "الیزابت بنت"
NICK = "لیزی"
INTRODUCED = "الیزابت بنت (Elizabeth Bennet)"


def _glossary(*, policy: str = "first_mention", pinned: str = "b00001",
              alias: str = "Lizzy", distinct: bool = True) -> dict:
    store = gl.new_glossary()
    store["policy"]["original_parenthetical"] = policy
    store["policy"]["keep_aliases_distinct"] = distinct
    entry = gl.make_entry(1, "Elizabeth Bennet", category="person", frequency=9)
    entry.update({"target": FULL, "later_form": FULL, "first_form": INTRODUCED,
                  "locked": True, "first_block_id": pinned,
                  "aliases": [alias], "alias_targets": {alias: NICK}})
    store["entries"] = [entry]
    return store


def _book(*pairs: tuple[str, str], headings: tuple[int, ...] = ()) -> dict:
    """``(source, target)`` per block; ``headings`` names the chapter openers."""
    book = ir.new_book()
    for index, (source, target) in enumerate(pairs, start=1):
        block = ir.make_block(
            "heading" if index in headings else "paragraph", index,
            text=source, **({"level": 1} if index in headings else {}))
        block["target"] = target
        book["blocks"].append(block)
    return book


def _placed(store: dict, book: dict) -> str:
    report = gl.enforce_first_mentions(store, book)
    return (report.get("introduced") or {}).get("g0001", "")


def _first_mention_findings(store: dict, book: dict) -> list[str]:
    return [f"{finding['code']}:{finding['unit']}"
            for finding in qa.check_book(book, glossary=store).summary(None)["findings"]
            if finding["code"].startswith("first-mention")]


# --------------------------------------------------------------------------- #
# The pinned block cannot always carry the introduction
# --------------------------------------------------------------------------- #

def test_a_nickname_paragraph_is_not_demanded_by_the_per_chapter_gate():
    """The gate's per-chapter branch asked for the pinned block regardless.

    Chapter one names her by her nickname, so the parenthetical — which attaches
    to the canonical form — cannot go in that paragraph. The pass puts it in the
    next paragraph that uses the full name; the gate used to demand the first.
    """
    store = _glossary(policy="first_per_chapter", pinned="b00002")
    book = _book(("Chapter One", "فصل یکم"),
                 ("Lizzy spoke first of all.", f"{NICK} اول حرف زد."),
                 ("Elizabeth Bennet arrived later.", f"{FULL} دیرتر رسید."),
                 headings=(1,))

    placed = _placed(store, book)

    assert placed == "b00003", placed
    assert _first_mention_findings(store, book) == []


def test_every_chapter_that_can_introduce_her_does_and_the_gate_agrees():
    store = _glossary(policy="first_per_chapter", pinned="b00002")
    book = _book(("Chapter One", "فصل یکم"),
                 ("Elizabeth Bennet arrived.", f"{FULL} رسید."),
                 ("Chapter Two", "فصل دوم"),
                 ("Elizabeth Bennet spoke again.", f"{FULL} باز سخن گفت."),
                 headings=(1, 3))

    gl.enforce_first_mentions(store, book)

    targets = [block.get("target") or "" for block in book["blocks"]]
    assert sum(target.count(INTRODUCED) for target in targets) == 2, targets
    assert _first_mention_findings(store, book) == []


def test_a_chapter_that_never_names_her_is_not_asked_to_introduce_her():
    store = _glossary(policy="first_per_chapter", pinned="b00002")
    book = _book(("Chapter One", "فصل یکم"),
                 ("Elizabeth Bennet arrived.", f"{FULL} رسید."),
                 ("Chapter Two", "فصل دوم"),
                 ("Somebody else spoke.", "کس دیگری حرف زد."),
                 headings=(1, 3))

    gl.enforce_first_mentions(store, book)

    assert _first_mention_findings(store, book) == []


# --------------------------------------------------------------------------- #
# One eligibility rule, the writer's own
# --------------------------------------------------------------------------- #

def test_a_name_split_across_emphasis_is_ineligible_for_both_sides():
    """`prose_of` joins the spans; the writer works inside one of them.

    So the owner said the pinned paragraph was eligible and the writer could
    place nothing there. Both now read `placement_spans`, and the emphasis is
    left alone rather than rewritten to make a placement possible.
    """
    store = _glossary(pinned="b00001")
    entry = store["entries"][0]
    book = _book(("Elizabeth Bennet arrived.", "**الیزابت** بنت رسید."),
                 ("Elizabeth Bennet spoke again.", f"{FULL} باز سخن گفت."))

    owner = naming.owner_of(entry, book["blocks"])
    placed = _placed(store, book)

    assert owner == placed == "b00002", (owner, placed)
    assert book["blocks"][0]["target"] == "**الیزابت** بنت رسید.", (
        "the emphasis was rewritten to make the placement possible")
    assert _first_mention_findings(store, book) == []


@pytest.mark.parametrize("pinned_target, why", [
    ("در نمونهٔ کد `الیزابت بنت` را ببینید.", "inside a code span"),
    ("به https://example.com/الیزابت بنت سر بزنید.", "inside a URL"),
])
def test_protected_text_is_not_an_eligible_placement_for_either_side(
        pinned_target, why):
    store = _glossary(pinned="b00001")
    entry = store["entries"][0]
    book = _book(("Elizabeth Bennet arrived.", pinned_target),
                 ("Elizabeth Bennet spoke again.", f"{FULL} باز سخن گفت."))

    owner = naming.owner_of(entry, book["blocks"])
    placed = _placed(store, book)

    assert owner == placed, (
        f"the plan and the writer disagree when the pinned paragraph's only "
        f"mention is {why}: {owner!r} against {placed!r}")


def test_a_correct_book_survives_repeated_enforcement_unchanged():
    """Convergence, stated as a property: the second pass changes nothing."""
    store = _glossary(policy="first_per_chapter", pinned="b00002")
    book = _book(("Chapter One", "فصل یکم"),
                 ("Lizzy spoke first.", f"{NICK} اول حرف زد."),
                 ("Elizabeth Bennet arrived.", f"{FULL} رسید."),
                 headings=(1,))

    gl.enforce_first_mentions(store, book)
    once = json.dumps(book, ensure_ascii=False)
    gl.enforce_first_mentions(store, book)

    assert json.dumps(book, ensure_ascii=False) == once
    assert _first_mention_findings(store, book) == []


def test_a_name_the_translation_never_uses_is_one_actionable_explanation():
    """No eligible occurrence anywhere: reported once, and not as a retry."""
    store = _glossary(pinned="b00001")
    book = _book(("Elizabeth Bennet arrived.", "کس دیگری حرف زد."))

    report = gl.enforce_first_mentions(store, book)

    assert report["unplaceable"] == ["g0001"], report
    assert report["introduced"] == {}
    codes = _first_mention_findings(store, book)
    assert codes == ["first-mention-missing:g0001"], codes


# --------------------------------------------------------------------------- #
# One alias cannot discharge two obligations
# --------------------------------------------------------------------------- #

def test_a_unit_naming_her_twice_owes_both_renderings():
    """Measured: «لیزی» alone passed a block whose source used both forms."""
    store = _glossary()
    book = _book(("Elizabeth Bennet and Lizzy are the same person.",
                  f"{NICK} و او یک نفرند."))

    violations = gl.check(store, book)

    assert [violation["source_forms"] for violation in violations] \
        == [["Elizabeth Bennet"]], violations
    assert violations[0]["expected"] == FULL
    assert violations[0]["used"] == ["Elizabeth Bennet", "Lizzy"]


def test_both_renderings_present_discharges_both():
    store = _glossary()
    book = _book(("Elizabeth Bennet and Lizzy are the same person.",
                  f"{FULL} و {NICK} یک نفرند."))

    assert gl.check(store, book) == []


def test_the_nickname_alone_is_enough_when_the_source_used_only_it():
    """The other direction, which the strictness must not break."""
    store = _glossary()
    book = _book(("Lizzy spoke first of all.", f"{NICK} اول حرف زد."))

    assert gl.check(store, book) == []


def test_a_policy_that_does_not_keep_aliases_distinct_accepts_either():
    """`keep_aliases_distinct: false` is a deliberate translation decision."""
    store = _glossary(distinct=False)
    book = _book(("Elizabeth Bennet and Lizzy are the same person.",
                  f"{FULL} و او یک نفرند."))

    assert gl.check(store, book) == []


def test_the_obligations_are_reported_per_source_form():
    """The resolver itself, so a future caller does not re-derive it."""
    entry = _glossary()["entries"][0]

    used, owed = gl.obligations_for(
        entry, "Elizabeth Bennet and Lizzy arrived.")

    assert used == ["Elizabeth Bennet", "Lizzy"]
    assert owed == [{"source_form": "Elizabeth Bennet", "accepted": [FULL]},
                    {"source_form": "Lizzy", "accepted": [NICK]}]

    # The full name alone owes only the canonical, even though its first word is
    # an alias in other books.
    used, owed = gl.obligations_for(entry, "Elizabeth Bennet arrived.")
    assert used == ["Elizabeth Bennet"]
    assert owed == [{"source_form": "Elizabeth Bennet", "accepted": [FULL]}]

"""A nickname is a translation decision, not a drift to normalise.

Four things disagreed about what a locked name owes:

* the worksheet table listed the English aliases under "keep distinct" and never
  said what Persian to keep them *as* — because `alias_targets` was a flat list
  beside a `sorted(set(...))` of aliases, so nothing in the file paired "Lizzy"
  with «لیزی» at all;
* the drift check accepted the canonical form wherever *any* surface form
  appeared, so a source block saying "Lizzy" passed on the full «الیزابت بنت» —
  the opposite of `keep_aliases_distinct`;
* enforcement introduced the original spelling in the first block that actually
  names her, QA demanded the block the scan pinned, and with a nickname in block
  one those are different blocks — three identical passes left the same error;
* the parenthetical was counted in the raw target, so a «علی (Ali)» quoted inside
  backticks read as a placement the enforcement pass had correctly left alone.

The last two are the same defect twice: two answers to one question.
"""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import glossary as gl  # noqa: E402
import qa  # noqa: E402


def _entry(**over) -> dict:
    entry = gl.make_entry(1, "Elizabeth Bennet", category="person",
                          aliases=["Lizzy"], frequency=9)
    entry.update(target="الیزابت بنت", locked=True,
                 first_form="الیزابت بنت (Elizabeth Bennet)",
                 later_form="الیزابت بنت", first_block_id="b00001")
    entry["alias_targets"] = {"Lizzy": "لیزی"}
    entry.update(over)
    return entry


def _glossary(entry: dict, **policy) -> dict:
    store = gl.new_glossary()
    store["policy"].update(policy)
    store["entries"] = [entry]
    return store


def _book(pairs: list[tuple[str, str]]) -> dict:
    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    for index, (source, target) in enumerate(pairs, start=1):
        block = ir.make_block("paragraph", index, text=source)
        block["target"] = target
        book["blocks"].append(block)
    return book


# --------------------------------------------------------------------------- #
# The worksheet has to say what to use
# --------------------------------------------------------------------------- #

def test_the_term_table_names_the_alias_target():
    """"Keep Lizzy distinct" without saying from what to what is an instruction a
    translator cannot follow — so each parallel chunk invents its own Persian and
    the drift check then rejects all of them."""
    store = _glossary(_entry())

    table = gl.render_term_table(store["entries"], store["policy"])

    assert "Lizzy → لیزی" in table, table


def test_an_alias_with_no_approved_persian_is_shown_bare():
    """A glossary nobody has finished is the normal case. The alias is still
    listed; it simply carries no promise."""
    store = _glossary(_entry(alias_targets={}))

    table = gl.render_term_table(store["entries"], store["policy"])

    assert "Lizzy" in table and "→" not in table, table


def test_an_older_glossary_with_a_list_still_loads():
    """`alias_targets` used to be a list. It cannot express a pairing — which is
    why it changed — but an existing glossary must not become invalid, and its
    forms are still accepted."""
    entry = _entry(alias_targets=["لیزی"])

    assert gl.alias_map(entry) == {}
    assert gl.alias_accepted(entry) == ["لیزی"]


# --------------------------------------------------------------------------- #
# The drift check asks which English was used
# --------------------------------------------------------------------------- #

def test_a_preserved_nickname_is_accepted():
    store = _glossary(_entry())
    book = _book([("Lizzy went down.", "لیزی پایین رفت.")])

    assert gl.check(store, book) == []


def test_expanding_a_nickname_to_the_canonical_is_a_violation():
    """The defect. Source says "Lizzy", translation says «الیزابت بنت», and the
    check passed — so the gate that exists to protect a name was asking for the
    voice change it should have caught."""
    store = _glossary(_entry())
    book = _book([("Lizzy went down.", "الیزابت بنت پایین رفت.")])

    violations = gl.check(store, book)

    assert violations, "an expanded nickname was accepted"
    assert violations[0]["expected"] == "لیزی", (
        f"the report asks for the wrong form: {violations[0]}")


def test_the_canonical_is_expected_where_the_source_used_the_full_name():
    store = _glossary(_entry())
    book = _book([("Elizabeth Bennet sat.", "الیزابت بنت نشست.")])

    assert gl.check(store, book) == []


def test_with_the_policy_off_either_form_is_accepted():
    """`keep_aliases_distinct` is a policy, not a law. Turned off, a translator
    who normalises the nickname is making an allowed choice."""
    store = _glossary(_entry(), keep_aliases_distinct=False)
    book = _book([("Lizzy went down.", "الیزابت بنت پایین رفت.")])

    assert gl.check(store, book) == []


# --------------------------------------------------------------------------- #
# One resolver, so the two passes agree
# --------------------------------------------------------------------------- #

def test_enforcement_and_qa_agree_on_who_introduces_the_name():
    """The convergence test. Nickname in block one, canonical in block two: the
    pass can only attach the parenthetical in block two, and the gate used to
    demand block one. Neither was wrong on its own — there were two answers."""
    store = _glossary(_entry())
    book = _book([("Lizzy came in.", "لیزی آمد."),
                  ("Elizabeth Bennet sat.", "الیزابت بنت نشست.")])

    for attempt in range(3):
        gl.enforce_first_mentions(store, book)
        findings = qa.check_book(book, glossary=store, strict=False).summary()
        codes = {f["code"] for f in findings["findings"]}
        assert "first-mention-misplaced" not in codes, (
            f"attempt {attempt + 1} still disagrees: {findings['findings']}")


def test_the_nickname_is_not_expanded_to_make_the_gate_pass():
    """The direction matters. Converging by rewriting «لیزی» into the canonical
    form would satisfy every check and change the book's voice."""
    store = _glossary(_entry())
    book = _book([("Lizzy came in.", "لیزی آمد."),
                  ("Elizabeth Bennet sat.", "الیزابت بنت نشست.")])

    gl.enforce_first_mentions(store, book)

    assert book["blocks"][0]["target"] == "لیزی آمد.", book["blocks"][0]
    assert "(Elizabeth Bennet)" in book["blocks"][1]["target"]


def test_the_pinned_block_still_wins_when_it_is_eligible():
    """The ordinary case must not change: where the first block does name her
    canonically, that is where the introduction goes."""
    store = _glossary(_entry())
    book = _book([("Elizabeth Bennet came in.", "الیزابت بنت آمد."),
                  ("Elizabeth Bennet sat.", "الیزابت بنت نشست.")])

    gl.enforce_first_mentions(store, book)

    assert "(Elizabeth Bennet)" in book["blocks"][0]["target"]
    assert "(Elizabeth Bennet)" not in book["blocks"][1]["target"]


def test_the_resolver_answers_nothing_when_no_block_names_her():
    """A name the translation never uses has no owner, and inventing one would
    make the gate demand an insertion nowhere legal."""
    store = _glossary(_entry())
    book = _book([("Somebody else spoke.", "کس دیگری حرف زد.")])

    assert gl.introduction_owner(store["entries"][0], book["blocks"]) == ""


# --------------------------------------------------------------------------- #
# Protected text is not eligible prose
# --------------------------------------------------------------------------- #

def test_a_parenthetical_inside_backticks_is_not_counted_as_a_placement():
    """The enforcement pass masks literals before inserting anything, so a
    «علی (Ali)» quoted as an example is text it will never touch. Counting it as a
    placement made the gate demand a removal nothing could perform."""
    entry = gl.make_entry(1, "Ali", category="person", frequency=5)
    entry.update(target="علی", locked=True, first_form="علی (Ali)",
                 later_form="علی", first_block_id="b00001")
    store = _glossary(entry)
    book = _book([
        ("Ali spoke first.", "علی (Ali) اول حرف زد."),
        ("Type `علی (Ali)` to search.", "برای جست‌وجو `علی (Ali)` را بنویسید."),
    ])

    findings = qa.check_book(book, glossary=store, strict=False).summary()
    codes = [f["code"] for f in findings["findings"]]

    assert "first-mention-repeated" not in codes, findings["findings"]
    assert "first-mention-misplaced" not in codes, findings["findings"]

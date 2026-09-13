"""What counts as a mention of a name — decided once, for every consumer.

Three places ask "does this block mention this entity": the pass that introduces
the original spelling, the gate that checks for drift, and the worksheet
instruction that tells a translator which form to use. While each had its own
answer they disagreed, and the disagreement had no fixed point:

* `introduction_owner` tested `canonical in target` — a substring — so a paragraph
  whose only «علی» was inside backticks, inside `https://example.com/علی`, or
  inside «علیرضا» counted as eligible. `_place_once` is careful and went to the
  next paragraph. The gate then demanded the pinned one, for ever: three identical
  passes, the same error, and no number of re-runs converged.
* `check` accepted the **union** of the full name's Persian and every alias's
  Persian whenever the source matched either. "Elizabeth Bennet arrived." contains
  the alias "Elizabeth" inside the full name, so «الیزابت آمد» passed — the
  surname dropped, on the one check whose job is to catch a dropped name.

A voice card had the mirror problem: matched by its own spelling, so the chapter
that calls her "Lizzy" — where register matters most — got no card at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import glossary as gl  # noqa: E402


def _ali_glossary() -> tuple[dict, dict]:
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, "Ali", category="person")
    entry.update({"target": "علی", "first_form": "علی (Ali)", "locked": True,
                  "first_block_id": "b00001"})
    glossary["entries"].append(entry)
    return glossary, entry


def _two_paragraphs(pinned_target: str) -> dict:
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    book["blocks"].append(ir.make_block("paragraph", 1, text="Ali is mentioned here."))
    book["blocks"][0]["target"] = pinned_target
    book["blocks"].append(ir.make_block("paragraph", 2, text="Ali arrived at noon."))
    book["blocks"][1]["target"] = "علی سر ظهر رسید."
    return book


@pytest.mark.parametrize("pinned, why", [
    ("در نمونهٔ کد `علی` را ببینید.", "inside a code span"),
    ("به https://example.com/علی سر بزنید.", "inside a URL"),
    ("علیرضا وارد شد.", "inside a longer name"),
])
def test_placement_and_the_gate_agree_about_what_counts_as_a_mention(pinned, why):
    glossary, entry = _ali_glossary()
    book = _two_paragraphs(pinned)

    owner = gl.introduction_owner(entry, book["blocks"])
    placed = (gl.enforce_first_mentions(glossary, book).get("introduced")
              or {}).get(entry["id"], "")

    assert owner == placed, (
        f"the owner ({owner!r}) and the placement ({placed!r}) disagree when the "
        f"pinned paragraph's only «علی» is {why}")


def test_enforcement_and_the_gate_both_converge():
    """The consequence of the disagreement was a pair that never settled.

    Two separate properties, and only the second one is about being clean: the
    book stops changing, and the gate stops changing its mind. A remaining
    violation is allowed here and is correct — the pinned paragraph's translation
    names «علی» only inside a code span, so that block really does owe a mention
    it does not have. What must not happen is the answer moving every pass.
    """
    glossary, entry = _ali_glossary()
    book = _two_paragraphs("در نمونهٔ کد `علی` را ببینید.")

    gl.enforce_first_mentions(glossary, book)
    once = [block.get("target") for block in book["blocks"]]
    first_verdict = [(item["block"], item["expected"])
                     for item in gl.check(glossary, book)]

    gl.enforce_first_mentions(glossary, book)
    gl.enforce_first_mentions(glossary, book)

    assert [block.get("target") for block in book["blocks"]] == once
    assert [(item["block"], item["expected"])
            for item in gl.check(glossary, book)] == first_verdict


def test_the_gate_accepts_a_faithful_translation_after_enforcement():
    """And when the Persian does name her, enforcement leaves a clean book."""
    glossary, entry = _ali_glossary()
    book = _two_paragraphs("علی در اینجا نام برده می‌شود.")

    report = gl.enforce_first_mentions(glossary, book)

    assert (report.get("introduced") or {}).get(entry["id"]) == "b00001"
    assert gl.check(glossary, book) == []
    gl.enforce_first_mentions(glossary, book)
    assert gl.check(glossary, book) == []


def _bennet(alias: str = "Elizabeth") -> dict:
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, "Elizabeth Bennet", category="person")
    entry.update({"target": "الیزابت بنت", "locked": True, "aliases": [alias],
                  "alias_targets": {alias: "الیزابت"}})
    glossary["entries"].append(entry)
    return glossary


def test_an_inner_alias_does_not_satisfy_the_full_name():
    glossary = _bennet()
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    book["blocks"].append(
        ir.make_block("paragraph", 1, text="Elizabeth Bennet arrived."))
    book["blocks"][0]["target"] = "الیزابت آمد."

    found = gl.check(glossary, book)

    assert len(found) == 1, found
    assert found[0]["expected"] == "الیزابت بنت"


def test_the_faithful_translation_and_a_standalone_alias_both_pass():
    """The negative controls: a stricter gate must not reject correct work."""
    glossary = _bennet()
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    book["blocks"].append(
        ir.make_block("paragraph", 1, text="Elizabeth Bennet arrived."))
    book["blocks"][0]["target"] = "الیزابت بنت آمد."
    assert gl.check(glossary, book) == []

    book["blocks"][0]["text"] = "Elizabeth arrived."
    book["blocks"][0]["target"] = "الیزابت آمد."
    assert gl.check(glossary, book) == []


def test_the_source_side_ignores_a_name_shown_as_an_example():
    """A name inside backticks in the *source* is not a mention either."""
    glossary = _bennet()
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    book["blocks"].append(ir.make_block(
        "paragraph", 1, text="The tag is written `Elizabeth Bennet` in the file."))
    book["blocks"][0]["target"] = "برچسب در فایل چنین نوشته می‌شود."

    assert gl.check(glossary, book) == [], (
        "a documented example demanded a translation of the name")


def test_owed_forms_reports_what_the_text_really_uses():
    """The resolver itself, so a future caller does not re-derive it."""
    glossary = _bennet(alias="Lizzy")
    entry = glossary["entries"][0]

    used, owed = gl.owed_forms(entry, "Elizabeth Bennet and Lizzy arrived.")
    assert used == ["Elizabeth Bennet", "Lizzy"]
    assert set(owed) == {"الیزابت بنت", "الیزابت"}

    # The full name alone owes only the canonical, even though its first word is
    # also an alias in other books.
    used, owed = gl.owed_forms(entry, "Elizabeth Bennet arrived.")
    assert used == ["Elizabeth Bennet"]
    assert owed == ["الیزابت بنت"]


# --------------------------------------------------------------------------- #
# A voice card belongs to a character, not to a spelling
# --------------------------------------------------------------------------- #

def _voiced() -> dict:
    glossary = gl.new_glossary()
    entry = gl.make_entry(1, "Elizabeth Bennet", category="person")
    entry.update({"target": "الیزابت بنت", "locked": True, "aliases": ["Lizzy"],
                  "alias_targets": {"Lizzy": "لیزی"}})
    glossary["entries"].append(entry)
    glossary["voices"].append({"character": "Elizabeth Bennet", "register": "dry",
                               "persian_policy": "کوتاه و خشک"})
    return glossary


def test_a_chunk_that_uses_the_nickname_still_gets_the_voice_card():
    glossary = _voiced()
    assert gl.render_voice_cards(glossary, "Elizabeth Bennet said nothing.")
    assert gl.render_voice_cards(glossary, "Lizzy said nothing."), (
        "the nickname chunk got no voice card, which is the chunk that needs it")
    assert not gl.render_voice_cards(glossary, "Mr Darcy said nothing.")
    assert not gl.render_voice_cards(glossary, "see `Lizzy` in the sample"), (
        "a mention inside a code span is an example, not a speaker")


def test_an_ambiguous_voice_card_is_reported_rather_than_guessed():
    """Two characters, one name. A card on the wrong one is worse than none."""
    glossary = _voiced()
    twin = gl.make_entry(2, "Elizabeth Bennet", category="person")
    twin.update({"target": "الیزابت بنت دیگر", "locked": True})
    glossary["entries"].append(twin)

    problems = gl.voice_problems(glossary)
    assert problems and "add an `entry` id" in problems[0], problems

    glossary["voices"][0]["entry"] = twin["id"]
    assert gl.voice_problems(glossary) == []
    assert gl.voice_entry(glossary, glossary["voices"][0])[0] is twin


def test_a_voice_card_naming_an_entry_that_is_gone_is_reported():
    glossary = _voiced()
    glossary["voices"][0]["entry"] = "g9999"
    problems = gl.voice_problems(glossary)
    assert problems and "does not have" in problems[0], problems

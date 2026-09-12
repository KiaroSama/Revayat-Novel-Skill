"""A ZWNJ is not whitespace, so inserting one is a rewrite of the word.

Two Persian shapes are identical without knowing the part of speech:

* «می» is the verbal prefix **and** the noun *wine* — «می رود» is *he goes*,
  «می سفید» is *white wine*.
* «تری» is the comparative suffix **and** the noun *wetness* — «بزرگ تری» is *a
  bigger one*, «از تری موهایش» is *from the wetness of her hair*.

The rule these tests replace tested the following word's **last letter**, because
every finite می-form ends in م/ی/د/ت. True, and not sufficient: so do «سفید» and
«تلخی». The default is now inverted — a join needs positive evidence that the word
is a verb form, and a word the stem list does not know is left alone and linted.

That direction is the whole point. An unjoined prefix is a blemish a proofreader
fixes in a second; a wrongly joined one is a changed sentence nobody notices.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import falint  # noqa: E402
import famorph  # noqa: E402

ZWNJ = "‌"


# --------------------------------------------------------------------------- #
# The three phrases the audit found corrupted
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text, gloss", [
    ("می سفید نوشید.", "he drank white wine — «سفید» ends in د and is no verb"),
    ("می تلخی نوشید.", "he drank a bitter wine — «تلخی» ends in ی and is no verb"),
    ("از تری موهایش فهمیدم.", "from the wetness of her hair — «از» has no comparative"),
])
def test_correct_prose_is_left_exactly_as_written(text, gloss):
    """Not an exception list: each of these is declined because the evidence for
    joining is absent, and any other noun in the same shape is declined too."""
    assert falint.fix_text(text) == text, gloss


def test_a_noun_the_audit_did_not_name_is_also_left_alone():
    """The check that this is a rule and not three special cases. «ناب» (pure)
    was never in the brief, and «می ناب» — pure wine — must survive too."""
    assert falint.fix_text("می ناب بود.") == "می ناب بود."


# --------------------------------------------------------------------------- #
# And the joins that must keep happening
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text, expected", [
    ("او می رود.", f"او می{ZWNJ}رود."),
    ("می رفتند.", f"می{ZWNJ}رفتند."),
    ("نمی دانم.", f"نمی{ZWNJ}دانم."),
    ("می خورد.", f"می{ZWNJ}خورد."),
    ("می نویسم.", f"می{ZWNJ}نویسم."),
    ("کتاب ها را برد.", f"کتاب{ZWNJ}ها را برد."),
    ("بزرگ ترین شهر", f"بزرگ{ZWNJ}ترین شهر"),
    ("گرم تری", f"گرم{ZWNJ}تری"),
])
def test_a_real_affix_is_still_joined(text, expected):
    """A fix that declines everything is not a fix. These are the cases the pass
    exists for, and they are what stops the safe direction becoming useless."""
    assert falint.fix_text(text) == expected


def test_fixing_twice_changes_nothing_more():
    """`fix(fix(text)) == fix(text)`, over the mixed bag. The pass runs on every
    merge, so a second application that kept editing would drift the book."""
    for text in ["می سفید نوشید.", "او می رود.", "از تری موهایش فهمیدم.",
                 "کتاب ها و بزرگ ترین شهر", "نمی دانم چه می گفت."]:
        once = falint.fix_text(text)
        assert falint.fix_text(once) == once, text


# --------------------------------------------------------------------------- #
# The uncertainty has to reach somebody
# --------------------------------------------------------------------------- #

def test_a_declined_prefix_is_reported_not_silently_left():
    """Preserving the text is only half the answer — a reader has to be told,
    or a genuinely missing ZWNJ ships unnoticed."""
    issues = falint.lint_text("می سفید نوشید.")

    codes = [issue["code"] for issue in issues]
    assert "zwnj-prefix-undecided" in codes, issues
    detail = next(i["detail"] for i in issues if i["code"] == "zwnj-prefix-undecided")
    assert "سفید" in detail or "می سفید" in detail, detail


def test_a_declined_comparative_is_reported_too():
    issues = falint.lint_text("از تری موهایش فهمیدم.")

    assert "zwnj-comparative-undecided" in [i["code"] for i in issues], issues


def test_a_joined_prefix_is_not_reported():
    """The report is for what was declined. Reporting the successes too would
    bury the three lines a reader actually has to look at."""
    issues = falint.lint_text(f"او می{ZWNJ}رود.")

    assert "zwnj-prefix-undecided" not in [i["code"] for i in issues], issues


# --------------------------------------------------------------------------- #
# The evidence itself
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("word, verb", [
    ("رود", True), ("روم", True), ("رفت", True), ("رفتند", True),
    ("دانم", True), ("خورد", True), ("نویسم", True), ("آید", True),
    ("سفید", False), ("تلخی", False), ("ناب", False), ("موهایش", False),
])
def test_is_verb_form_answers_only_what_it_knows(word, verb):
    """`False` means *not known to be a verb*, which is deliberately the answer
    that preserves the text. A verb outside the list is linted, not joined — and
    that is a documented limit, not a silent one."""
    assert famorph.is_verb_form(word) is verb


@pytest.mark.parametrize("word, adjective", [
    ("بزرگ", True), ("گرم", True), ("نو", True),
    ("از", False), ("به", False), ("این", False), ("که", False),
])
def test_takes_comparative_excludes_only_a_closed_class(word, adjective):
    """Function words are a closed class, which is what makes this check
    defensible: everything outside it is allowed to be an adjective."""
    assert famorph.takes_comparative(word) is adjective


def test_a_url_is_not_touched_by_either_rule():
    """Masking happens before any rule, and these two are no exception — a
    Persian-looking fragment inside a link is one of its bytes."""
    text = "به https://example.com/می رفت و برگشت."

    assert "https://example.com/می" in falint.fix_text(text)

"""Persian typography: what the fix pass must not touch, and what it must.

Every rule here is a *fix* rule, so every case is a rewrite that either has to
happen or has to be refused. The refusals matter more: a wrong rewrite of a URL
or of a real Persian word is silent data loss that reads as a translation error
and is untraceable by the time anyone notices.
"""

from __future__ import annotations

import bookir as ir
import falint

ZWNJ = falint.ZWNJ

URL = "https://example.com/كتاب/١"


def fix(text: str, **kwargs) -> str:
    return falint.fix_text(text, falint.Options(**kwargs))


# --------------------------------------------------------------------------- #
# Protected literals survive the character, digit and quote passes
# --------------------------------------------------------------------------- #

def test_a_bare_url_survives_character_and_digit_normalisation():
    """An Arabic kaf and an Arabic-Indic digit inside a URL are its *bytes*.

    Normalising them produces a different, usually dead, address — and the
    reader has no way to tell it was ever right.
    """
    assert fix(f"نشانی {URL} را ببین.") == f"نشانی {URL} را ببین."


def test_a_url_inside_emphasis_survives():
    """Protection cannot depend on the user backticking the URL by hand."""
    assert fix(f"**{URL}**") == f"**{URL}**"


def test_a_quoted_query_string_is_not_turned_into_guillemets():
    url = 'https://ex.com/s?q="کتاب"&page=1'
    assert fix(f"برو به {url} و بخوان.") == f"برو به {url} و بخوان."


def test_a_url_with_adjacent_punctuation_survives():
    assert fix(f"({URL}) و بعد") == f"({URL}) و بعد"
    assert fix(f"ببین: {URL}، بعد برو") == f"ببین: {URL}، بعد برو"


def test_an_email_address_survives():
    address = "Jo.Smith@Example.com"
    assert fix(f"به {address} بنویس.") == f"به {address} بنویس."


def test_prose_around_a_protected_url_is_still_normalised():
    """Protection must be a span, not an excuse to stop working."""
    assert fix(f"كتاب {URL} ١٢ تا") == f"کتاب {URL} ۱۲ تا"


# --------------------------------------------------------------------------- #
# ZWNJ: the joins that are orthography, and the ones that are guesses
# --------------------------------------------------------------------------- #

def test_the_verbal_prefix_still_joins():
    assert fix("او می رود") == f"او می{ZWNJ}رود"
    assert fix("او می شد") == f"او می{ZWNJ}شد"
    assert fix("او نمی رفت") == f"او نمی{ZWNJ}رفت"


def test_the_plural_suffix_still_joins_and_the_kaf_normalises():
    assert fix("كتاب ها") == f"کتاب{ZWNJ}ها"


def test_the_inflected_comparatives_still_join():
    """«تری» and «ترین» have no standalone reading, so they are safe."""
    assert fix("بزرگ ترین") == f"بزرگ{ZWNJ}ترین"
    assert fix("بزرگ تری") == f"بزرگ{ZWNJ}تری"


def test_the_noun_wine_is_not_glued_to_the_word_after_it():
    """«می ناب» is wine, not a verb. Joining it invents a word."""
    assert fix("می ناب نوشید.") == "می ناب نوشید."


def test_a_standalone_wet_is_not_glued_as_a_comparative_suffix():
    """«موی تر» is wet hair; «مویتر» is nothing at all."""
    assert fix("موی تر داشت.") == "موی تر داشت."
    assert fix("دست تر را خشك كرد.") == "دست تر را خشک کرد."


# --------------------------------------------------------------------------- #
# Quotation marks, which do not respect span boundaries
# --------------------------------------------------------------------------- #

def test_a_quotation_wrapping_emphasis_still_becomes_guillemets():
    """The pair's halves land in two different spans, so no per-span rule sees
    both. Leaving the Latin quotes is the one typographic error a reader meets on
    every page of dialogue."""
    assert fix('گفت: "او **خوب** است."') == "گفت: «او **خوب** است.»"


def test_a_quotation_inside_one_span_still_becomes_guillemets():
    assert fix('گفت: "او خوب است."') == "گفت: «او خوب است.»"


def test_punctuation_after_the_closing_quote_survives():
    assert fix('گفت: "او **خوب** است"، بعد رفت.') == "گفت: «او **خوب** است»، بعد رفت."


def test_an_unmatched_quote_is_left_for_the_lint_pass():
    """Half a pair is a defect to report, not a bracket to guess at."""
    fixed = fix('او گفت: "**خوب** بود')
    assert '"' in fixed
    assert "latin-quotes" in {f["code"] for f in falint.lint_text(fixed)}


def test_quotes_inside_a_verbatim_span_are_not_converted():
    assert fix('`say "hi"` را اجرا کن.') == '`say "hi"` را اجرا کن.'


def test_the_quote_pass_keeps_emphasis_parity():
    source = 'گفت: "او **خوب** و *بد* است."'
    assert ir.emphasis_signature(fix(source)) == ir.emphasis_signature(source)


def test_the_bare_comparative_is_reported_instead_of_guessed():
    """Neither reading can be ruled out, so the decision goes to a human.

    «بزرگ تر» wants the ZWNJ and «موی تر» must not have it — same shape, and
    only the part of speech of the preceding word tells them apart. A lint
    finding costs a review; a wrong rewrite costs the sentence.
    """
    codes = {f["code"] for f in falint.lint_text("بزرگ تر از آن بود.")}
    assert "zwnj-comparative" in codes
    assert falint.lint_text("بزرگ‌تر از آن بود.") == []

"""Persian typography: what the fix pass must not touch, and what it must.

Every rule here is a *fix* rule, so every case is a rewrite that either has to
happen or has to be refused. The refusals matter more: a wrong rewrite of a URL
or of a real Persian word is silent data loss that reads as a translation error
and is untraceable by the time anyone notices.
"""

from __future__ import annotations

import falint

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

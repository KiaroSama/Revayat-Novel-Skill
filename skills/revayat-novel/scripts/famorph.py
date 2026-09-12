"""When a Persian space may become a ZWNJ, and when only a reader can say.

A ZWNJ is not whitespace. Inserting one rewrites the word, so a rule that cannot
tell an affix from a word corrupts valid prose silently — and the two shapes this
module exists for are genuinely identical without knowing the part of speech:

* «می» is the verbal prefix *and* the noun *wine*. «می رود» is *he goes*; «می سفید»
  is *white wine*.
* «تری» is the comparative suffix *and* the noun *wetness*. «بزرگ تری» is *a bigger
  one*; «از تری موهایش» is *from the wetness of her hair*.

The rule this replaces tested the **last letter** of the following word, on the
reasoning that every finite می-form ends in م/ی/د/ت. True, and not sufficient: so
does «سفید», and so does «تلخی». Its own comment admitted the gap and named the
upgrade — a stem list — which is what this is.

**The default is inverted.** A join now needs positive evidence that the word is a
verb form, rather than the absence of evidence that it is not. A verb outside the
list below is **left alone and linted**, because preserving the text and asking for
a reader is the only safe direction: an unjoined prefix is a typographic blemish a
proofreader fixes, a wrongly joined one is a changed sentence nobody notices.

Deliberately a closed list of common verbs, not a dictionary. It covers the
overwhelming majority of «می» in narrative prose; everything else is reported.
"""

from __future__ import annotations

import re

#: Present stems of the commonest Persian verbs. After «می» a present form always
#: carries a personal ending, so «می‌رو» is not a word and «می‌رود» is.
_PRESENT_STEMS = (
    "رو", "آی", "کن", "شو", "گو", "بین", "خور", "ده", "گیر", "دان", "توان",
    "خواه", "مان", "آور", "زن", "بر", "نویس", "خوان", "نشین", "ایست", "افت",
    "رس", "پرس", "فهم", "کش", "گذار", "دار", "باش", "یاب", "شنو", "خواب",
    "ترس", "لرز", "چرخ", "گرد", "پذیر", "سپار", "بخش", "اندیش", "گریز",
    "پوش", "شکن", "سوز", "رنج", "خند", "گری", "جوش", "پر", "دو", "آ",
)

#: Past stems. The bare past stem *is* the third person singular — «می‌رفت» — so
#: the empty ending belongs in this set and not in the present one.
_PAST_STEMS = (
    "رفت", "آمد", "کرد", "شد", "گفت", "دید", "خورد", "داد", "گرفت", "دانست",
    "توانست", "خواست", "ماند", "آورد", "زد", "برد", "نوشت", "خواند", "نشست",
    "ایستاد", "افتاد", "رسید", "پرسید", "فهمید", "کشید", "گذاشت", "داشت",
    "بود", "یافت", "شنید", "خوابید", "ترسید", "لرزید", "چرخید", "گشت",
    "پذیرفت", "سپرد", "بخشید", "اندیشید", "گریخت", "پوشید", "شکست", "سوخت",
    "رنجید", "خندید", "گریست", "جوشید", "پرید", "دوید",
)

_PERSONAL = ("م", "ی", "د", "یم", "ید", "ند")

#: A finite verb form that may follow «می»/«نمی». Longest alternatives first so a
#: stem that is a prefix of another cannot mask it.
_VERB_FORM = re.compile(
    "^(?:"
    + "|".join(
        sorted(
            [f"{stem}{end}" for stem in _PRESENT_STEMS for end in _PERSONAL]
            + [f"{stem}{end}" for stem in _PAST_STEMS for end in ("",) + _PERSONAL],
            key=len, reverse=True,
        )
    )
    + ")$"
)

#: Words that cannot take a comparative suffix: prepositions, conjunctions,
#: determiners and particles — a closed class, which is what makes the check
#: defensible. «از تری» is *from the wetness*, never *from-er*.
_NO_COMPARATIVE = frozenset((
    "از", "به", "با", "در", "بر", "تا", "که", "را", "این", "آن", "هر", "چه",
    "یا", "و", "اگر", "چون", "زیرا", "ولی", "اما", "پس", "نیز", "هم", "بی",
    "جز", "مگر", "البته", "چرا", "کی", "کجا", "وقتی", "همان", "چنین", "چنان",
))


def is_verb_form(word: str) -> bool:
    """Does ``word`` read as a finite verb that «می» would attach to?

    Positive evidence only. ``False`` means *not known to be a verb*, which is
    the answer that preserves the text.
    """
    return bool(_VERB_FORM.match(word))


def takes_comparative(word: str) -> bool:
    """Could ``word`` be the adjective a «تری»/«ترین» attaches to?

    Again only the confident negative is claimed: a closed class of function words
    is excluded, everything else is allowed to be an adjective.
    """
    return word not in _NO_COMPARATIVE

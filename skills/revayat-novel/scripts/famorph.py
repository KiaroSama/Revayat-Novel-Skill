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

#: Adjectives a «تری» may attach to. **Positive evidence**, and that direction is
#: the whole point: the previous rule was "not in a list of function words", so
#: every other word in Persian was allowed to be an adjective — and «من تری لباس
#: را حس کردم» (*I felt the wetness of the cloth*) came out as «من‌تری», because
#: «تری» is also the noun *wetness*. The same happened after «او» and «هنوز». A
#: blacklist cannot fix that: the words it would have to exclude are every noun
#: and every pronoun in the language.
#:
#: So the list below is a lexicon, not a set of exceptions. A word that is not in
#: it is *not known to be an adjective*, which is the answer that leaves the text
#: alone and raises `zwnj-comparative-undecided` for a person instead. Adding an
#: adjective here is an ordinary improvement; it never changes the rule.
_COMPARABLE = frozenset((
    # size, extent
    "بزرگ", "کوچک", "ریز", "درشت", "بلند", "کوتاه", "پهن", "باریک", "کلان",
    "عظیم", "وسیع", "تنگ", "گسترده", "عمیق", "کم", "زیاد", "بیش", "اندک",
    # light, colour, temperature
    "روشن", "تاریک", "سفید", "سیاه", "تیره", "پررنگ", "کم‌رنگ", "گرم", "سرد",
    "داغ", "خنک", "سوزان", "یخ",
    # quality, worth
    "خوب", "بد", "زیبا", "زشت", "قوی", "ضعیف", "سخت", "نرم", "آسان", "دشوار",
    "گران", "ارزان", "تازه", "کهنه", "نو", "قدیمی", "جدید", "مهم", "بهتر",
    "ساده", "پیچیده", "دقیق", "درست", "نادرست", "روان", "سنگین", "سبک",
    # people, feeling
    "جوان", "پیر", "خوشحال", "غمگین", "شاد", "ناراحت", "آرام", "عصبانی",
    "مهربان", "تند", "کند", "سریع", "آهسته", "خسته", "هوشیار", "دانا", "نادان",
    "شجاع", "ترسان", "مشهور", "ناشناس", "نزدیک", "دور", "شبیه", "متفاوت",
    # condition
    "خالی", "پر", "پاک", "کثیف", "خشک", "تمیز", "شلوغ", "ساکت", "بلندتر",
    "محکم", "شکننده", "زنده", "مرده", "سالم", "بیمار", "گرسنه", "سیر", "تشنه",
))


def is_verb_form(word: str) -> bool:
    """Does ``word`` read as a finite verb that «می» would attach to?

    Positive evidence only. ``False`` means *not known to be a verb*, which is
    the answer that preserves the text.
    """
    return bool(_VERB_FORM.match(word))


def takes_comparative(word: str, *, suffix: str = "تری") -> bool:
    """Is ``word`` known to be the adjective a «تری»/«ترین» attaches to?

    Positive evidence, like :func:`is_verb_form`. ``False`` means *not known to be
    an adjective*, and the caller leaves the space alone and reports it rather than
    joining on a guess.

    ``ترین`` is the one case where the shape itself is the evidence: Persian has no
    ordinary noun of that form, so a space before it is a superlative somebody
    typed loosely. «تری» and «تر» are genuinely ambiguous — both are also words,
    *wetness* and *wet* — which is why they need the lexicon.
    """
    if suffix == "ترین":
        return True
    return word in _COMPARABLE

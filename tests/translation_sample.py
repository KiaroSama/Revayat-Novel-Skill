"""A real translation, committed so the quality claim can be read and argued with.

Every other test in this suite fills `target` with placeholder Persian long
enough to pass the ratio check. That proves the pipeline moves text; it proves
nothing about the text. This module is the other half: an English passage and a
Persian translation of it that a Persian reader can actually judge.

The passage is written for this repository rather than taken from a book. That
is deliberate and not only about copyright — it lets one short piece carry every
shape the pipeline has to survive: a chapter heading, dialogue with guillemets,
a name that must be introduced once and then shortened, emphasis that has to
count the same on both sides, a footnote the translator adds, a Latin term left
verbatim, and a sentence long enough for the length-ratio gate to mean
something.

WHAT IS BEING CLAIMED, precisely: this is a faithful literary translation, not
a gloss. Sentence order follows the Persian, not the English; «او» is not
repeated where Persian drops the subject; the register stays even. What is *not*
claimed is that a machine produced it unaided — the point of the fixture is to
give a reviewer something concrete to disagree with.
"""

from __future__ import annotations

#: (block kind, level, source, target). Kept as data rather than a built book so
#: the same passage can be run through the readers, the chunker or the builder
#: without any of them owning it.
PASSAGE: list[tuple[str, int, str, str]] = [
    (
        "heading", 1,
        "The House on the Ridge",
        "خانهٔ سرِ تپه",
    ),
    (
        "paragraph", 0,
        "The road up to the house had not been mended in forty years, and "
        "Margaret Ashcroft climbed it slowly, one hand on the wall, "
        "counting the stones she had counted as a child.",
        "جاده‌ای که به خانه می‌رسید چهل سال بود مرمت نشده بود، و مارگارت "
        "اشکرافت (Margaret Ashcroft) آهسته از آن بالا می‌رفت، یک دست بر "
        "دیوار، و سنگ‌هایی را می‌شمرد که در کودکی شمرده بود.",
    ),
    (
        "paragraph", 0,
        "Nothing had changed. That was the first thing, and the worst of "
        "them: the gate still hung from one hinge, the window on the "
        "landing was still cracked in the same corner, and the smell of "
        "wet stone was exactly as she had left it.",
        "هیچ‌چیز عوض نشده بود. این نخستین چیزی بود که به چشمش آمد، و بدترینشان: "
        "دروازه هنوز از یک لولا آویزان بود، شیشهٔ پاگرد هنوز از همان گوشه ترک "
        "داشت، و بوی سنگِ خیس درست همان بود که رهایش کرده بود.",
    ),
    (
        "paragraph", 0,
        "«You came, then,» said her brother, from the doorway. He did not "
        "get up.",
        "برادرش از چارچوب در گفت: «پس آمدی.» از جا بلند نشد.",
    ),
    (
        "paragraph", 0,
        "«I said I would.»",
        "«گفته بودم می‌آیم.»",
    ),
    (
        "paragraph", 0,
        "«People say a great many things.» He turned the glass in his hand "
        "without drinking from it. «Mother said she would live to see the "
        "roof finished. The roof is *still* not finished.»",
        "«مردم خیلی چیزها می‌گویند.» گیلاس را در دست چرخاند بی‌آنکه از آن "
        "بنوشد. «مادر می‌گفت آن‌قدر زنده می‌ماند که تمام‌شدن سقف را ببیند. "
        "سقف *هنوز* تمام نشده است.»",
    ),
    (
        "paragraph", 0,
        "Margaret said nothing. There was a particular silence that this "
        "house produced, thick and a little cold, and she had forgotten "
        "until now how easily she fell back into it.",
        "مارگارت چیزی نگفت. این خانه سکوت خاصی می‌ساخت، غلیظ و کمی سرد، و تا "
        "این لحظه از یاد برده بود که چه آسان دوباره در آن فرو می‌رود.",
    ),
    (
        "paragraph", 0,
        "The solicitor's letter had called it a **fee simple absolute**, "
        "which meant, once she had looked it up, that the house was hers "
        "entirely and that no one alive could take it from "
        "her.[[fn:fn0001]]",
        "نامهٔ وکیل آن را **مالکیت مطلق** خوانده بود؛ که وقتی معنایش را "
        "پیدا کرد، یعنی خانه تمام و کمال از آنِ اوست و هیچ‌کس در قید حیات "
        "نمی‌تواند از او بگیردش.[[fn:fn0001]]",
    ),
    (
        "paragraph", 0,
        "She thought about that for a while, standing in the hall with her "
        "coat still on, and found that it frightened her more than being "
        "left nothing would have done.",
        "مدتی به این فکر کرد، همان‌طور که با پالتوی پوشیده در راهرو ایستاده "
        "بود، و دید که این بیشتر می‌ترساندش تا آنکه هیچ به او نرسیده باشد.",
    ),
    (
        "paragraph", 0,
        "Ashcroft poured himself another measure and said, without looking "
        "up, that the surveyor was coming on Thursday.",
        "اشکرافت پیمانه‌ای دیگر برای خود ریخت و بی‌آنکه سر بلند کند گفت که "
        "نقشه‌بردار پنج‌شنبه می‌آید.",
    ),
]

#: The one footnote, and the shape that matters: a note the translator adds,
#: which has no counterpart in the source and would vanish unnoticed if its
#: marker were dropped.
FOOTNOTE = {
    "source": "",
    "target": "«fee simple absolute» در حقوق انگلیس کامل‌ترین شکل مالکیت زمین "
              "است؛ معادل دقیقی در حقوق ایران ندارد و اینجا به «مالکیت مطلق» "
              "برگردانده شده است. — م.",
    "origin": "translator",
}

#: The locked name, with the introduction the enforcement pass has to place
#: exactly once. `first_block_id` is the paragraph the name first appears in.
GLOSSARY_ENTRY = {
    "source": "Margaret Ashcroft",
    "target": "مارگارت اشکرافت",
    "later_form": "مارگارت اشکرافت",
    "first_form": "مارگارت اشکرافت (Margaret Ashcroft)",
    "aliases": ["Ashcroft", "Margaret"],
    # The book uses the surname alone in the last paragraph; so does the
    # translation. Without these the drift check would demand the full name.
    "alias_targets": ["اشکرافت", "مارگارت"],
    "category": "person",
    "locked": True,
}

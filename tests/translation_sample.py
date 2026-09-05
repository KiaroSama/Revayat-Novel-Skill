"""A real translation, committed so the quality claim can be read and argued with.

Every other test in this suite fills `target` with placeholder Persian long
enough to pass the ratio check. That proves the pipeline moves text; it proves
nothing about the text. This module is the other half: an English passage and a
Persian translation of it that a Persian reader can actually judge.

The passage is written for this repository rather than taken from a book. That
is deliberate and not only about copyright — it lets one short piece carry every
shape the pipeline has to survive: a chapter heading, dialogue, a name that must
be introduced once and then shortened, emphasis that has to count the same on
both sides, a footnote the translator adds, and a sentence long enough for the
length-ratio gate to mean something.

REGISTER. The first version of this was rejected by the reader it was written
for, in one word: stilted. It had «بدترینشان», «پیمانه‌ای دیگر برای خود ریخت»,
«بی‌آنکه از آن بنوشد» — constructions that belong to a translated 1950s text,
not to a novel someone would read now. The lesson is worth keeping next to the
data: **fidelity is not the same as literalness, and a sentence that maps
word-for-word onto the English is usually the wrong sentence.** Persian drops
subjects English must state, prefers a verb where English takes a noun, and
will not carry an English relative clause without breaking it in two.

So this version reads first and matches second. Where the English says "found
that it frightened her more than being left nothing would have done", the
Persian says the same thing the way a Persian writer would say it, in two
clauses instead of one.
"""

from __future__ import annotations

#: (block kind, level, source, target). Kept as data rather than a built book so
#: the same passage can be run through the readers, the chunker or the builder
#: without any of them owning it.
PASSAGE: list[tuple[str, int, str, str]] = [
    (
        "heading", 1,
        "The House on the Ridge",
        "خانهٔ بالای تپه",
    ),
    (
        "paragraph", 0,
        "Nobody had mended the road up to the house in forty years. Margaret "
        "Ashcroft climbed it slowly, one hand on the wall, counting the same "
        "stones she had counted as a child.",
        "چهل سال بود کسی جادهٔ بالای تپه را درست نکرده بود. مارگارت اشکرافت "
        "(Margaret Ashcroft) آرام از آن بالا می‌رفت، یک دستش روی دیوار، و همان "
        "سنگ‌هایی را می‌شمرد که بچه که بود شمرده بود.",
    ),
    (
        "paragraph", 0,
        "Nothing had changed, and that was the worst of it. The gate still "
        "hung from one hinge. The window on the landing was cracked in the "
        "same corner. Even the smell of wet stone was the one she had left "
        "behind.",
        "هیچ‌چیز عوض نشده بود، و بدتر از همه همین بود. در حیاط هنوز به یک لولا "
        "بند بود. شیشهٔ پاگرد از همان گوشه ترک داشت. حتی بوی سنگ خیس همان بویی "
        "بود که پشت سرش جا گذاشته بود.",
    ),
    (
        "paragraph", 0,
        "«So you came,» her brother said from the doorway. He did not get up.",
        "برادرش از دم در گفت: «پس آمدی.» بلند نشد.",
    ),
    (
        "paragraph", 0,
        "«I said I would.»",
        "«گفتم که می‌آیم.»",
    ),
    (
        "paragraph", 0,
        "«People say all sorts of things.» He turned the glass in his hand "
        "but did not drink. «Mother said she would live to see the roof "
        "finished. The roof is *still* not finished.»",
        "«مردم هرچیزی می‌گویند.» لیوان را در دستش چرخاند ولی نخورد. «مادر "
        "می‌گفت می‌ماند تا سقف را تمام‌شده ببیند. سقف *هنوز* تمام نشده.»",
    ),
    (
        "paragraph", 0,
        "Margaret did not answer. This house had a silence of its own, heavy "
        "and cold, and she had forgotten how quickly she slipped back into it.",
        "مارگارت جوابی نداد. این خانه سکوت خودش را داشت، سنگین و سرد، و یادش "
        "رفته بود چه زود دوباره توی آن فرو می‌رود.",
    ),
    (
        "paragraph", 0,
        "The solicitor's letter called it a **fee simple absolute**. When she "
        "looked the words up, they meant the house was hers, completely, and "
        "that nobody living could take it away.[[fn:fn0001]]",
        "نامهٔ وکیل اسمش را **مالکیت مطلق** گذاشته بود. وقتی معنی‌اش را پیدا "
        "کرد، یعنی خانه کامل مال خودش است و هیچ‌کس تا زنده است نمی‌تواند از او "
        "بگیرد.[[fn:fn0001]]",
    ),
    (
        "paragraph", 0,
        "She stood in the hall with her coat still on and thought about it. "
        "It frightened her. Being left nothing would have frightened her less.",
        "با پالتو توی راهرو ایستاد و به این فکر کرد. می‌ترساندش. اگر هیچ به او "
        "نمی‌رسید، کمتر می‌ترسید.",
    ),
    (
        "paragraph", 0,
        "Ashcroft poured himself another and said, without looking up, that "
        "the surveyor was coming on Thursday.",
        "اشکرافت یکی دیگر برای خودش ریخت و بدون اینکه سرش را بلند کند گفت "
        "نقشه‌بردار پنج‌شنبه می‌آید.",
    ),
]

#: The one footnote, and the shape that matters: a note the translator adds,
#: which has no counterpart in the source and would vanish unnoticed if its
#: marker were dropped.
FOOTNOTE = {
    "source": "",
    "target": "«fee simple absolute» کامل‌ترین شکل مالکیت زمین در حقوق انگلیس "
              "است. معادل دقیقی در حقوق ایران ندارد و اینجا «مالکیت مطلق» "
              "ترجمه شده. — م.",
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

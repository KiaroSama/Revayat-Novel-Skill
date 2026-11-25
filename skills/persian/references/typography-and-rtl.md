# typography-and-rtl.md: Persian text mechanics

Run `revayat-novel persian-lint` for mechanical checks. Use this reference to resolve the cases
it flags without fixing. If the binary is unavailable, apply sections 1 to 4 by hand.

    revayat-novel persian-lint --check file.txt          # report
    revayat-novel persian-lint --fix   file.txt          # normalized text to stdout
    revayat-novel persian-lint --check --style file.txt  # also flag bureaucratic / AI phrases

## 1. Letters: Persian, not Arabic
Persian and Arabic share a script but differ in a few letters. Mixed text is the most common
encoding tell (it comes from Arabic keyboards, copy-paste, and old PDFs).

| Wrong (Arabic) | Right (Persian) | Note |
|---|---|---|
| ي U+064A | ی U+06CC | yeh, the number-one offender |
| ك U+0643 | ک U+06A9 | kaf |
| ى U+0649 | ی U+06CC | alef maksura → yeh |
| ة U+0629 | ه U+0647 | teh marbuta → heh (علاقه, not علاقة) |
| kashida (U+0640) | (delete) | decorative elongation; never in digital text |

Persian-only letters پ چ ژ گ (and ک ی) are correct; never "normalize" them to Arabic. Leave
diacritics and tanwin alone («بعداً», «مثلاً»). The hamza forms ء أ ؤ ئ are all valid Persian.

A kashida with spaces on both sides is a dash, not elongation (some typists use it that way:
«پر می‌کردند ـ بابا»). The linter turns that into a spaced en dash (–) and deletes the rest.

## 2. ZWNJ (نیم‌فاصله, U+200C)
ZWNJ joins a suffix or prefix to its stem visually without a full space. «میرود» (glued) and
«می رود» (full space) are both wrong; «می‌رود» is right. This is the clearest sign of unpolished
Persian.

Required (the linter fixes the space → ZWNJ cases):
- verb prefixes: می‌رود، نمی‌خواهم
- plural and clitic ها: کتاب‌ها، خانه‌های، بچه‌هایش
- comparative and superlative: بزرگ‌تر، بزرگ‌ترین
- enclitics after silent heh: خانه‌ام، خانه‌ات، خانه‌اش، رفته‌اند، خانه‌ای (a house)
- compounds written as one word: کسب‌وکار، جمع‌وجور، به‌موقع, «هم» prefix (هم‌درد), «به» prefix in
  adverbs (به‌خوبی)

Do not insert ZWNJ between separate words. Cases the linter only flags:
- bare «تر» with a space: join if it is the comparative, leave if it means "wet" («زمینِ تر»).
- a glued «میX»: usually needs «می‌X», but not when «می» is part of a word (میز, میمون, میدان).
  The linter skips a stoplist of such words and flags the rest.

## 3. Digits
Persian digits: ۰ ۱ ۲ ۳ ۴ ۵ ۶ ۷ ۸ ۹.
- Default: convert Latin (0-9) and Arabic-Indic (٠-٩) digits to Persian in prose.
- Keep Latin digits inside code, URLs, emails, and version numbers (`v1.2.3`). A token mixing
  letters and digits (MP3, COVID-19, iPhone15) keeps Latin digits.
- Percent after a Persian digit becomes «٪» (۵۰٪).
- Separators: Persian thousands «٬» (U+066C), decimal «٫» (U+066B). The linter converts the digits
  but leaves a Latin thousands comma and decimal point alone (it never mangles `1,000`). Apply true
  Persian separators by hand if the house style wants them.
- Override with `--digits {persian,latin,keep}`.
- Units (feet → متر, °F → °C) are not a digit task. That is a translation decision; ask the user.

## 4. Punctuation and spacing
- Quotes: «…» (guillemets), not "…" or “…”. Nested: «… ‹…› …» or the same guillemets.
- Comma «،», semicolon «؛», question mark «؟». Period and «!» use the Latin glyph. Ellipsis «…».
- Spacing: no space before ، ؛ ؟ ! . : » and none after «; one space after ، ؛ ؟ !. Collapse
  double spaces; strip trailing spaces.
- Mixed text: the linter converts , ; ? and quote pairs only where a Persian character sits next to
  them. An English sentence inside a Persian document keeps its English punctuation.
- Dashes: Persian prose mostly avoids them. Prefer restructuring; use a spaced en dash (–) when a
  dash is really needed. The em dash «—» is flagged as an AI tell (see `ai-tells.md`).

## 5. Bidirectional (RTL) layout
Persian runs right-to-left, but numbers, Latin words, and some punctuation are left-to-right.
Where they meet, glyphs can land on the visually wrong side. This is layout, not characters, so the
linter cannot fully fix it. Handle it consciously:

- When the first word is English, enclose the whole Persian sentence, including final punctuation,
  in `RLI` (`U+2067`) and `PDI` (`U+2069`). In plain text and `dir="auto"`, prepend `RLM`
  (`U+200F`) before `RLI` to set the outer paragraph direction. Purely English lines stay unwrapped.
- A number or Latin run inside Persian keeps its own LTR order. That is correct. Don't reorder
  digits.
- Parentheses and brackets mirror automatically in a proper RTL renderer: type «(۲۰۲۰)» logically
  and it displays correctly. If a bracket or digit at a boundary renders on the wrong side (common
  in plain-text fields, chat, code comments), insert a directional mark: RLM U+200F after a trailing
  Latin/number run, or LRM U+200E to isolate an LTR run. Use sparingly, only to fix a visible glitch.
- Keep sentence punctuation in Persian form (، ؛ ؟) so the engine treats it as RTL and it sits on
  the correct side. A Latin «?» at the end of a Persian sentence is the classic "question mark on
  the wrong side" bug.
- For mixed Persian + English UI strings, wrap the Latin span in its own element with `dir="ltr"`
  rather than trusting auto-detection.

## 6. Dates and calendars
- Iran uses the Jalali (Hijri-Shamsi) calendar. When the calendar matters, state it or convert
  explicitly; don't silently assume Gregorian.
- Write dates with Persian digits in prose: ۲۷ خرداد ۱۴۰۳ or ۱۴۰۳/۳/۲۷.

## 7. What the linter does vs. leaves to you
| Deterministic (linter) | Needs judgment (you) |
|---|---|
| ي/ك/ى/ة → Persian; kashida removal; spaced kashida → en dash | bidi marks for a visible glitch |
| digit conversion with protected spans; «٪» | true «٬» / «٫» separators |
| ، ؛ ؟ «» … and spacing, in Persian context only | comparative «تر» vs "wet"; glued «می» |
| ZWNJ for می/نمی, ها, ترین, heh enclitics | ZWNJ in compounds (کسب‌وکار, به‌خوبی) |
| flags (never fixes): em dash, `--style` phrases | unit conversion (ask the user) |

## 8. Rendering Persian in files (RTL + alignment + font)
The linter fixes characters; a file also needs direction, alignment, and a Persian-capable font.
Apply all three to every Persian document you generate. RTL without right alignment still looks
wrong.

Default to RTL with logical start alignment and Vazirmatn as the complex-script font. Embed the
font in the document to avoid substitution on machines that do not have it installed.

**docx (docx-js):** set the font on ascii/hAnsi and `cs` (complex script), and align to START:
```js
new Paragraph({
  bidirectional: true,                 // RTL direction
  alignment: AlignmentType.START,      // logical leading edge = visually right for RTL
  children: [ new TextRun({
    text, rightToLeft: true,
    font: { ascii: 'Vazirmatn', hAnsi: 'Vazirmatn', cs: 'Vazirmatn' },   // cs = the Persian font
  }) ],
});
```
If the toolkit won't emit `w:cs`, post-process `word/document.xml` and add `w:cs="Vazirmatn"` to each
`<w:rFonts>`; otherwise Word renders the Persian in its default complex-script font.

**Alignment gotcha (important):** in a bidi paragraph Word reads `w:jc` logically: `left` = start,
`right` = end. So `w:jc="right"` is the trailing edge, which is the LEFT side for RTL, and the text
jumps left. LibreOffice reads it physically, so the bug only shows in Word. Always align RTL text
with the leading edge: `w:jc="start"` (docx-js `AlignmentType.START`). Never `right` for RTL.
This was confirmed on a real file: a `<w:bidi/>` paragraph with `w:jc="right"` rendered
left-aligned in Word; changing it to `w:jc="start"` fixed it.

**Native book builder:** `revayat-novel build <work> --out <file.docx>` writes `w:bidi`
and logical-start paragraph alignment. Check `w:rFonts` for `w:cs="Vazirmatn"` and confirm
that the font is embedded. The alignment value is `start`, emitted as `<w:jc w:val="start"/>`.

**HTML/CSS:**
```css
body { direction: rtl; text-align: start;
       font-family: 'Vazirmatn', Tahoma, sans-serif; }
```
Add `lang="fa"` on the root element so browsers pick Persian glyph shapes and hyphenation.

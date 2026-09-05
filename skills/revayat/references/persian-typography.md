# Persian typography and bidirectional text

## The rule everything else follows from

**Never reverse a string, and never pre-shape letters.**

Persian is stored in *logical* order — the order you say the words in. Visual
order is produced at render time by the Unicode bidirectional algorithm, given
a base direction. Word is told that base direction with `w:bidi` on the
paragraph and `w:rtl` on the run; the reader's text engine does the rest.

Reversing a string produces text that happens to look right in one viewer, and
is broken in every other, unsearchable, uncopyable and impossible to edit. If a
tool's Persian output shows disconnected letters or words in reverse order, its
renderer lacks bidi and shaping — that is a bug in the renderer, and the fix is
never to pre-mangle the text.

This is also why the sentence-final full stop needs no special handling. `.` is
a neutral character; with the correct base direction it lands on the left of a
Persian sentence automatically.

## Mixed Persian and Latin

A Latin name inside a Persian sentence stays left-to-right *within* the
right-to-left paragraph:

> او در سال ۱۹۸۴ با John Smith دیدار کرد.

The builder splits each paragraph into runs by script: Persian runs get
`w:rtl`, Latin runs do not. That per-run direction is what makes the ordering
correct — see `split_by_script` in `scripts/build_docx.py`.

The same applies in HTML if you ever render a preview: set
`<html lang="fa" dir="rtl">`, use logical CSS properties
(`padding-inline-start`, `border-inline-start`) rather than left/right, and
isolate Latin fragments with `<bdi>` or `unicode-bidi: isolate`.

## What `falint fix` changes

| From | To | Note |
| --- | --- | --- |
| `ي` `ى` | `ی` | Arabic yeh → Persian yeh |
| `ك` | `ک` | Arabic kaf → Persian keheh |
| `ـ` | removed | tatweel/kashida is decorative |
| `٠`–`٩` | `۰`–`۹` | Arabic-Indic → Persian digits |
| `0`–`9` | `۰`–`۹` | only outside identifiers; `--digits keep` disables |
| `,` `;` `?` | `،` `؛` `؟` | only after a Persian letter or digit |
| `"…"` | `«…»` | `--no-quotes` disables |
| `...` | `…` | `--no-ellipsis` disables |
| ` ،` | `،` | no space before punctuation |
| `،x` | `، x` | one space after |

## Zero-width non-joiner (نیم‌فاصله)

U+200C is a real orthographic character in Persian, not decoration. `می‌رود`
and `می رود` are different; so are `کتاب‌ها` and `کتاب ها`.

Because it carries meaning, the fixer only inserts it for patterns that are
unambiguous:

- verb prefixes: `می` and `نمی` followed by a Persian word
- plural and possessive suffixes: `ها`, `های`, `هایی`, `هایم`, `هایت`, `هایش`,
  `هایمان`, `هایتان`, `هایشان`
- comparatives: `تر`, `تری`, `ترین`

It never removes an existing ZWNJ, and it never guesses at `بی`, `ای`, `تان`,
`شان` or `مان`, where a blind rule would corrupt real words. Anything beyond
this list is a translator's judgement, not a regex's.

## What is protected from every rule

The fixer decomposes text with the markup parser first, so it never sees
emphasis markers, verbatim spans or footnote tokens. Within the remaining
prose it also masks:

- URLs and email addresses
- digit groups joined by `.` `:` `/` `-` — versions, dates, ISBNs
- any Latin word

So `https://example.com/a,b?x=1`, `978-0-19-953556-9` and `COVID-19` come
through a Persian paragraph untouched, while the Persian comma two words later
is still corrected.

## What `falint lint` reports but will not fix

These need a human or a re-translation:

| Code | Meaning |
| --- | --- |
| `untranslated` | a long Latin passage, or no Persian at all |
| `arabic-forms` | Arabic letterforms survived the fix pass |
| `guillemets` | unbalanced `«` / `»` |
| `latin-quotes` | straight or curly Latin quotes in Persian prose |
| `double-punctuation` | repeated `،` `؛` `؟` `!` |
| `script-collision` | a Latin and a Persian letter with no space between them |

## Fonts

Word picks the *complex script* font for Persian runs, which is a separate
setting from the Latin font. The builder sets both: `--font` for Persian
(`w:rFonts w:cs`), `--latin-font` for Latin (`w:ascii`/`w:hAnsi`).

- `Vazirmatn` — free, modern, widely installed by Persian users. Default.
- `B Nazanin` — the classic Persian book face; ships with Persian Office setups.
- `Tahoma` — not a book face, but present on every Windows machine. Use it when
  the file must render correctly somewhere you do not control.

A font that lacks Persian glyphs is a *different* problem from a renderer that
lacks shaping. Changing the font fixes missing glyphs only.

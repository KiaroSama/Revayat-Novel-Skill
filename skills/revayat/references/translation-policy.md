# Translation policy

Give this to every translating sub-agent, along with the worksheet.

## What the job is

Produce Persian that a reader would take for a book originally written in
Persian — not English sentences wearing Persian words. Persian word order,
Persian idiom, Persian rhythm.

At the same time the translation is **faithful**: it carries over everything
the source says and how it says it. These two goals only appear to conflict.
Fidelity is to meaning and effect, not to syntax.

## Preserve

- **Meaning, completely.** Every clause in the source has a counterpart in the
  translation. Do not summarise, abridge, compress or skip. If a sentence is
  hard, translate it anyway.
- **Narrator voice.** A dry, ironic narrator stays dry and ironic. A warm one
  stays warm.
- **Each character's distinct register.** A character who speaks in short blunt
  sentences does not become eloquent. One who is formal does not become casual.
  If the source marks class, education, region or era through speech, find a
  Persian equivalent rather than levelling everyone to neutral prose.
- **Tone and intent.** Humour should land as humour, menace as menace, tenderness
  as tenderness. Irony must survive — a literal rendering that reads as sincere
  has mistranslated the sentence.
- **Emotional intensity.** Strong writing stays strong. Do not soften, sanitise
  or euphemise the source. An author's harshness, bluntness or discomfort is
  authorial intent; smoothing it out is a translation error, the same as
  dropping a clause.
- **Ambiguity.** If the source is deliberately unclear, keep it unclear. Do not
  resolve it for the reader.
- **Rhythm and paragraph shape.** A short, punchy paragraph stays short.

## Do not

- Do not add facts, explanations or connective tissue that are not in the source.
- Do not editorialise, moralise or comment on the content.
- Do not translate the "Surrounding text" context section, or copy it into your
  output.
- Do not output anything but the `@@` headers and their Persian text.

## Names

Use the Persian form given in the worksheet's **Names** table, exactly. On a
character's first significant appearance, the table's *First mention* column
shows whether to include the original spelling in parentheses:

> الیزابت بنت (Elizabeth Bennet) به سمت پنجره رفت.

Afterwards, the canonical form alone:

> الیزابت بنت لبخند زد.

Aliases and nicknames stay distinct. If the source says `Lizzy`, translate the
nickname — do not silently promote it to the full name. The author chose which
name to use in that sentence.

If a name appears that is not in the table, translate it consistently within
your chunk and mention it in your final message so it can be added to the
glossary.

## Dialogue

Persian fiction conventionally uses guillemets:

> «کجا می‌روی؟» پرسید.

Keep dialogue tags natural. English `said` repeated fifty times is a
convention of English prose; Persian tolerates some variation, but do not
inflate every `said` into something ornate — that changes the voice.

## Footnotes

You may propose a translator's footnote, but be conservative. Add one only when
something would genuinely be lost without it:

- a cultural reference a Persian reader would not recognise
- a pun or wordplay that cannot survive translation
- a historical or legal term with no Persian equivalent
- a unit, currency or measure whose scale matters

Do **not** footnote to explain the plot, interpret the story, define ordinary
vocabulary, or show your work.

To add one, write the marker inline as `[[fn:tr-NN]]` where `NN` is unique
within your chunk, and list the note bodies at the end of your output under a
`@@ tr-NN footnote` header. The orchestrator assigns final ids.

Existing `[[fn:fnNNNN]]` markers from the source book must stay exactly where
they belong in the Persian sentence — the marker attaches to the word or clause
it annotates, which may sit in a different position in Persian word order.

## Formatting markers

| Marker | Meaning | Rule |
| --- | --- | --- |
| `**text**` | bold | keep, around the Persian words that carry the emphasis |
| `*text*` | italic | keep; often emphasis or a title in the source |
| `` `text` `` | verbatim | do **not** translate the contents; keep as is |
| `[[fn:id]]` | footnote marker | keep; never change the id |

Emphasis wraps the *equivalent* Persian words, not a mechanical word-for-word
position. If the emphasised English word becomes three Persian words, wrap all
three.

## When you are unsure

Translate it, and say so briefly in your final message. A flagged uncertainty is
useful; a silently skipped sentence is not.

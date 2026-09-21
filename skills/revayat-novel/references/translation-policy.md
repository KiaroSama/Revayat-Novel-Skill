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
- Output the exact worksheet request line, the requested `@@` headers and their Persian text.

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

Translate the complete unit and report the uncertainty with its source unit id
to the orchestrator outside the worksheet reply. Resolve it against the source
before final approval; preserve deliberate source ambiguity.

## What the review afterwards will and will not ask of you

A later stage reads your translation beside its source and files findings under
five rubrics. Three are about meaning and will come back to you as a request to
translate that unit again:

- **omission** — a clause of the source is not in the translation;
- **addition** — the translation states something the source does not;
- **sense** — a negation, a number, a time or an agent has changed.

Two are about style, and will **not**:

- **register** — the voice is more formal, or plainer, than the source's;
- **fluency** — it reads as translated English rather than as Persian.

Those two are recorded for you to read and nothing sends them back to you as a
re-translation request. The reason is not indifference to style. A faithful
sentence sent back to be made smoother comes back smoother and slightly
different, and that second change is invisible to every check here.

If you believe a clumsy sentence genuinely changes the meaning, that is a
`sense` finding, and it has to be argued as one.

## How the prose does get smoothed, and why not by you

There *is* a stage that edits Persian for how it reads — but it happens after
your work, without the source in front of it, and it is not a request to you.

An independent Persian reading complements the bilingual review and can expose
calques it overlooked. A bilingual reviewer can also assess fluency. The later
stage receives Persian without its source and focuses on natural phrasing,
continuity and the intended narrator/character voices.

What that means for you:

- **Write it well the first time.** A blind editor can fix word order and the
  join between sentences. They cannot restore a nuance you smoothed away,
  because they do not know it was there.
- **Nothing that stage proposes is accepted on its own.** Every edit moves the
  book, which makes the meaning review stale, and the translation is compared
  against its source again before anything passes. A smoothing that changed the
  meaning is caught there, by someone holding both texts.
- **Deliberate roughness survives if the source has it.** An abrupt sentence, a
  character who speaks badly, a repetition the author chose: a blind editor may
  propose smoothing it, and the source comparison is what puts it back. You do
  not need to defend it in your final message — though a line saying *this is
  meant to read this way* makes the second reviewer's job easier.

So: meaning first and yours to get right, style second and never at meaning's
expense. Both are re-checked after any change.

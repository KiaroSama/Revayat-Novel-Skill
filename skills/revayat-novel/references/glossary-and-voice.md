# Glossary and character voice

## Why this exists

Each worksheet is translated by a sub-agent with a fresh context. Nothing stops
chapter 3 calling him «جان» and chapter 12 calling him «جون» — except shared
state that every chunk sees. That is the glossary.

Name drift is the most visible defect in a machine-assisted book translation,
and the cheapest to prevent.

## The file

`work/glossary.json`:

```json
{
  "schema": "revayat-novel/glossary@1",
  "policy": {
    "original_parenthetical": "first_mention",
    "lock_canonical": true,
    "keep_aliases_distinct": true,
    "book_voice": ""
  },
  "entries": [
    {
      "id": "g0001",
      "source": "Elizabeth Bennet",
      "target": "الیزابت بنت",
      "first_form": "الیزابت بنت (Elizabeth Bennet)",
      "later_form": "الیزابت بنت",
      "category": "person",
      "aliases": ["Lizzy", "Eliza", "Miss Bennet"],
      "gender": "female",
      "locked": true,
      "frequency": 412,
      "notes": ""
    }
  ],
  "voices": []
}
```

`scan` fills in `source`, `aliases`, `category` (a guess) and `frequency`. You
fill in the Persian. `locked: true` makes it enforceable — `qa check` then
reports a `glossary-drift` finding whenever a block mentions the entity in the
source but its canonical Persian form is absent from the translation.

## Naming policy

`policy.original_parenthetical` controls where the original spelling appears:

- `first_mention` (default) — once, on the character's first significant
  appearance, then the Persian form alone. This is the convention most Persian
  publishers use for fiction.
- `first_per_chapter` — repeat it at the start of each chapter. Useful for
  non-fiction with many proper nouns.
- `never` — Persian only.

> الیزابت بنت (Elizabeth Bennet) به سمت پنجره رفت.
>
> …
>
> الیزابت بنت لبخند زد.

## Aliases are not synonyms

This is the part a naive find-and-replace gets wrong. `Lizzy` is not a shorter
way of writing `Elizabeth Bennet` — it is a *choice the author made in that
sentence*, and it usually signals intimacy, or who is speaking.

So aliases are listed on the entry to keep the entity unified for the term
table and the drift check, but the translator is told to render the nickname as
a nickname. Give each alias its own Persian form in `alias_targets` when the
distinction matters:

```json
"aliases": ["Lizzy"],
"alias_targets": {"Lizzy": "لیزی"}
```

**A mapping, not a list.** Two parallel arrays could not say which Persian belonged
to which English — `aliases` is stored sorted, so position carried no meaning — and
that pairing is the whole point: the term table prints `Lizzy → لیزی` so a translator
is told what to use, and with `keep_aliases_distinct` on, answering a source
«Lizzy» with the full canonical name is reported as drift. A bare list still loads
for an older glossary, but it can only mark forms *acceptable*, which is exactly the
looseness the mapping removes.

The original spelling is introduced where the **canonical** form first appears: the
scan pins the block where the entity first appears in the source, which may be a
block whose Persian is a nickname, and the parenthetical attaches to the canonical
form. The rule — pinned block if eligible, otherwise the first eligible one — is
used by the enforcement pass and the QA gate alike, and a nickname is never expanded
to make a block eligible.

`scan` folds single-word forms into a longer name automatically — `Elizabeth`
becomes an alias of `Elizabeth Bennet` rather than competing with it as a
separate entity. It also strips genitives (`Alice's` → `Alice`) and rejects
contractions (`I'm`, `I've`), which otherwise flood the candidate list.

## What to bother with

Names, places, organisations, invented terms, and anything with a
non-obvious rendering. Skip vocabulary any translator would render the same way
— a glossary of 500 ordinary nouns just fills the sub-agent's context and
crowds out the entries that matter.

The report from `scan` lists candidates by frequency. Working down from the top
until the names stop mattering is usually the right amount of effort.

## The book's own voice

`policy.book_voice` is one or two sentences of Persian describing how the
**narration** sounds: formal or plain, contemporary or period, spare or
ornamented. It is rendered into every worksheet on both routes, above the
character cards.

```json
"policy": {
  "book_voice": "روایت رسمی اما ساده؛ جملات کوتاه، بدون آرایه‌های ادبی و بدون لحن محاوره‌ای."
}
```

Empty by default, and deliberately: an invented house style is worse than none.
Left empty, each chunk's translator picks a register from the prose in front of
them — which is how a book turns administrative halfway through, the drift
`meaning`'s `register` rubric and `fluency`'s consistency question keep finding.
Filled in, the twentieth chunk is told what the first was told.

Write it in Persian, for the same reason a voice card's `persian_policy` is in
Persian: it is an instruction to the translator and it reads more precisely in
the target language. Changing it changes every worksheet, so it moves the request
token and the worksheets are rebuilt — decide it before translating, not halfway
through.

This is not a character card. `book_voice` is the prose around the dialogue;
`voices` is who is speaking. A book with one narrator and six distinct characters
needs both.

## Character voices

Consistency in *names* is not the same as consistency in *voice*. Add entries
to `voices` for characters whose speech is distinctive:

```json
"voices": [
  {
    "character": "John",
    "register": "informal",
    "speech_style": "short, sarcastic, occasionally blunt",
    "persian_policy": "محاوره‌ای، کوتاه و طعنه‌آمیز؛ از جملات بلند ادبی پرهیز کن"
  },
  {
    "character": "Lady Catherine",
    "register": "formal",
    "speech_style": "imperious, long sentences, never apologises",
    "persian_policy": "رسمی و آمرانه؛ جملات بلند و ساختار کتابی"
  }
]
```

A voice card is injected into any chunk where that character's name appears, so
the sub-agent translating chapter 9 knows how chapter 2 made him sound.

Write `persian_policy` in Persian — it is an instruction to the translator, and
it reads more precisely in the target language.

## Adding to the glossary mid-run

Sub-agents will meet names that were not in the initial scan. When one reports
a new name, add the entry and re-run `scan` (it preserves existing entries and
only appends), then refresh frequencies:

```bash
$PY scripts/revayat-novel.py glossary count --glossary work/glossary.json --book work/book.json
```

Chunks already translated are not re-translated automatically. Decide whether
the new name appeared earlier in the book; if it did, re-run those chunks.
`qa check --glossary` will tell you where a locked name is missing.

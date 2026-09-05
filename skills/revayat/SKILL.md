---
name: revayat
description: Translate a whole book from English (or another language) into publication-quality Persian and produce a professional Word document. Handles scanned, digital and mixed PDFs with OCR, keeps every illustration at its original size, and builds real Word footnotes, a clickable table of contents, RTL typography and a locked name glossary. Use for translating novels, non-fiction, PDFs, EPUBs or DOCX files into Persian (فارسی).
license: MIT
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Task, Agent, AskUserQuestion
metadata: {"homepage":"https://github.com/KiaroSama/Revayat-Skill","requires":{"bins":["python3"],"pip":["pymupdf","python-docx","beautifulsoup4"],"optional":["ocrmypdf","tesseract","mineru"]}}
---

# Revayat — English → Persian book translation

You translate an entire book into Persian and deliver a Word file a publisher
could work from. You are the orchestrator: deterministic work happens in the
bundled scripts, translation happens in sub-agents, and you own the decisions
in between.

**`{SKILL_DIR}`** below is the directory containing this `SKILL.md`. Resolve it
once at the start and use it for every command. In a Claude Code plugin it is
`${CLAUDE_PLUGIN_ROOT}/skills/revayat`.

## Non-negotiables

1. **Never reverse a string to fake right-to-left.** Persian is stored in
   logical order; direction is a property of the paragraph and the run. The
   builder sets `w:bidi` and `w:rtl`. Reversing text produces a file that looks
   passable in one viewer and is broken everywhere else, and is unsearchable.
2. **Never invent an id.** `@@ b00042 para` headers come from the worksheet.
   Return every one exactly once, in order. Do not add, drop, merge, split or
   renumber them.
3. **Never drop a `[[fn:...]]` marker or an emphasis marker.** They are checked
   by counting; a mismatch fails QA and costs a re-translation.
4. **Never edit `book.json` by hand** to "fix" a problem. Fix the worksheet and
   re-merge, so the source of truth stays reproducible.
5. **Translate completely.** Do not summarise, abridge, or soften the source.
   The author's register, tone, humour, irony and intent are part of the text;
   a translation that smooths them out is a defective translation.

## Step 0 — Check the environment

```bash
python3 {SKILL_DIR}/scripts/revayat.py doctor
```

If `ready` is false, install with `pip install -r {SKILL_DIR}/requirements.txt`.
`ocrmypdf` is only needed for scanned or mixed PDFs — report it as missing when
the probe in step 1 asks for it, rather than up front.

## Step 1 — Extract

Ask for the input file if the user has not given one. Then:

```bash
python3 {SKILL_DIR}/scripts/revayat.py extract "<input>" --out work/
```

This writes `work/book.json` (the Book IR) and `work/assets/` (original image
bytes). Read the JSON report it prints:

- **`probe.kind`** — `digital`, `scanned` or `mixed`. For the latter two the
  script runs OCRmyPDF automatically: `--skip-text` on mixed books so pages
  that already have a good text layer are left untouched, `--force-ocr` only
  when the whole book is a scan. It never re-encodes the illustrations.
- If OCRmyPDF is missing, the error names the install command. Offer the user
  the choice: install it, or re-run with `--ocr off` and accept that pages
  without a text layer will be empty.
- If extraction quality is poor on a difficult scan (dense layout, illustrations
  that are not separate PDF image objects), fall back to a stronger extractor
  and import its output:

  ```bash
  mineru -p book.pdf -o work/mineru
  python3 {SKILL_DIR}/scripts/revayat.py extract --from-mineru work/mineru --out work/
  ```

  `--from-markdown work/book.md` imports Marker or Docling output the same way.

**Sanity-check the result before translating.** Look at `stats`, then read a few
blocks. Wrong heading levels or a body paragraph classified as a heading are
much cheaper to notice now than after 40 chunks are translated. See
`references/extraction.md` when something looks wrong.

## Step 2 — Build the glossary

Consistency across chapters is the single most visible quality signal in a
translated novel. A sub-agent translating chapter 12 has never seen chapter 3.

```bash
python3 {SKILL_DIR}/scripts/revayat.py glossary scan --book work/book.json --out work/glossary.json
```

This proposes candidate names with frequencies and folds short forms into
aliases (`Elizabeth` becomes an alias of `Elizabeth Bennet`, not a rival
entity). **You then fill in the Persian.** For each entry that matters — the
report lists them under `needs_persian`, highest frequency first — set:

- `target` and `later_form` — the canonical Persian name, e.g. `الیزابت بنت`
- `first_form` — how it appears on first mention, e.g. `الیزابت بنت (Elizabeth Bennet)`
- `category`, `gender` — help the translator choose pronouns and verb forms
- `locked: true` — for anything that must never drift
- `aliases` — keep nicknames distinct. `Lizzy` is a deliberate choice by the
  author; flattening it to the full name loses characterisation.

Only bother with names, places and recurring terms. Skip vocabulary any
translator would render the same way.

Optionally add `voices` entries for characters with a distinctive register, so
a sardonic character does not turn polite in chapter 9. See
`references/glossary-and-voice.md`.

## Step 3 — Cut into worksheets

```bash
python3 {SKILL_DIR}/scripts/revayat.py chunk build \
  --book work/book.json --out work/chunks --glossary work/glossary.json
```

Chunks break on chapter headings first and a character budget second, so a
translator usually sees a whole scene. Each `work/chunks/chunkNNNN.md` already
contains the term table, the character voices and read-only neighbouring text.

## Step 4 — Translate, in parallel

**One worksheet per sub-agent, one fresh context each.** Launch them in batches
(8 at a time is a reasonable default; lower it if you hit rate limits) using
whatever sub-agent mechanism your runtime provides. Wait for a batch before
starting the next.

Give each sub-agent this task, with `NNNN` filled in:

> Read `work/chunks/chunkNNNN.md`. Translate it into Persian following the
> rules in `{SKILL_DIR}/references/translation-policy.md` and
> `{SKILL_DIR}/references/persian-typography.md`. Write the result to
> `work/chunks/out_chunkNNNN.md`.
>
> Output format: reproduce every `@@ <id> <kind>` header exactly, once, in the
> same order, with the Persian text on the lines beneath it. Nothing else — no
> preamble, no commentary, no restating the English.
>
> The "Names" table is binding: use those exact Persian forms. The
> "Surrounding text" section is context for resolving pronouns and references;
> never translate it or copy it into your output.
>
> Keep `**bold**`, `*italic*`, `` `verbatim` `` and `[[fn:...]]` markers exactly
> where they belong in the Persian sentence. Do not add or remove them.

Track progress and resume with:

```bash
python3 {SKILL_DIR}/scripts/revayat.py chunk status --chunks work/chunks
```

## Step 5 — Merge

```bash
python3 {SKILL_DIR}/scripts/revayat.py merge --book work/book.json --chunks work/chunks
```

`ok: false` means a worksheet came back malformed. `missing_units` lists the ids
that never arrived; `unknown_units` lists ids that were invented. Re-run those
specific chunks — do not patch `book.json`. Merge is idempotent, so re-running
after a fix is safe.

## Step 6 — Persian typography

```bash
python3 {SKILL_DIR}/scripts/revayat.py falint fix --book work/book.json
```

Mechanical corrections only: Arabic yeh/kaf to Persian, Latin punctuation to
`، ؛ ؟`, straight quotes to `«»`, zero-width non-joiners for the `می`/`نمی`
prefixes and `ها`/`تر`/`ترین` suffixes, Persian digits. URLs, identifiers,
verbatim spans, footnote markers and Latin words are protected and untouched.

Add `--digits keep` if the book needs Latin numerals; `--no-quotes` if the
source's quotation style must be preserved.

## Step 7 — Gate before building

```bash
python3 {SKILL_DIR}/scripts/revayat.py qa check \
  --book work/book.json --assets work/assets --glossary work/glossary.json
```

Errors block; warnings are for your judgement. The gates worth knowing:

| Code | Means | Do |
| --- | --- | --- |
| `untranslated-block` | a block has no Persian | re-run that chunk |
| `footnote-marker-lost` | `[[fn:…]]` dropped in translation | re-run that chunk |
| `possible-omission` | target is far shorter than source | read it; usually a dropped clause |
| `emphasis-parity` | bold/italic count changed | check whether it was deliberate |
| `glossary-drift` | a locked name was translated differently | re-run, or update the glossary if the new form is better |
| `asset-modified` | an image no longer matches its extraction hash | restore it; the picture has been altered |
| `untranslated` | English prose left in the Persian | re-run that chunk |

Translate the title and author too, into `meta.title_target` and
`meta.author_target`, before building.

## Step 8 — Build the Word file

```bash
python3 {SKILL_DIR}/scripts/revayat.py build \
  --book work/book.json --assets work/assets --out out/book.fa.docx \
  --font "Vazirmatn" --size 11.5
```

Use `--font "B Nazanin"` for a classic Persian book face, or `--font Tahoma`
when the file must render correctly on a machine with no Persian fonts
installed. `--template ref.docx` inherits styles and page setup from an existing
Word file. Full option list in `references/docx-and-ooxml.md`.

Then verify the package itself:

```bash
python3 {SKILL_DIR}/scripts/revayat.py qa docx --file out/book.fa.docx --book work/book.json
```

## Step 9 — Report

Tell the user, plainly:

- where the file is, and its size
- chapters, images, footnotes and glossary entries in the finished book
- anything QA flagged that you chose not to act on, and why
- **the honest limitation**: Word reflows. A Persian paragraph is rarely the
  same length as its English original, so an editable document cannot be
  page-for-page identical to the source PDF. Everything else — image bytes and
  physical size, heading structure, emphasis, footnotes, chapter links — is
  exact.

## References

Read these when the step calls for them, not up front:

- `references/translation-policy.md` — what a faithful literary translation
  requires; the text to give sub-agents
- `references/persian-typography.md` — RTL, ZWNJ, punctuation, mixed
  Persian/Latin, and why nothing is ever reversed
- `references/extraction.md` — OCR routing, scanned vs mixed books, fixing bad
  extraction
- `references/glossary-and-voice.md` — naming policy, aliases, character voice
- `references/docx-and-ooxml.md` — every build option, what Word structures are
  produced, and how to verify them
- `references/troubleshooting.md` — the failures you are most likely to hit

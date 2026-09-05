---
name: revayat-novel
description: Translate a whole book from English (or another language) into publication-quality Persian and produce a professional Word document. Handles scanned, digital and mixed PDFs with OCR, removes colour watermarks, keeps every illustration at its original size, and builds real Word footnotes, a clickable table of contents, RTL typography and a locked name glossary. Use for translating novels, non-fiction, PDFs, EPUBs or DOCX files into Persian (فارسی).
license: GPL-3.0-or-later
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Task, Agent, AskUserQuestion
metadata: {"homepage":"https://github.com/KiaroSama/Revayat-Novel-Skill","requires":{"bins":["python3"],"pip":["pymupdf","python-docx","beautifulsoup4","pillow"],"optional":["ocrmypdf","tesseract","ghostscript","mineru"]}}
---

# Revayat Novel — English → Persian book translation

Run the nine steps below **in order**. Each one is a command plus a rule for
what to do with its output. Do not improvise a different order, and do not skip
a step because the previous one looked fine.

Set two variables once, then use them everywhere:

- `SKILL_DIR` — the folder holding this file. In a Claude Code plugin it is
  `${CLAUDE_PLUGIN_ROOT}/skills/revayat-novel`.
- `WORK` — a working folder for this book, e.g. `work/`.

Every command is `python3 $SKILL_DIR/scripts/revayat-novel.py <stage> …`.

---

## The five rules that must never be broken

1. **Never reverse Persian text** to make it read right-to-left. Direction is
   set by the builder. Reversing produces a file that is broken everywhere but
   one viewer.
2. **Never invent, drop, merge, split or reorder a `@@ id` header.** Return
   exactly the ones you were given.
3. **Never drop a `[[fn:…]]`, `**bold**`, `*italic*` or `` `verbatim` ``
   marker.** They are counted; a mismatch fails QA.
4. **Never hand-edit `book.json` to fix a translation.** Fix the worksheet and
   re-run merge.
5. **Never shorten the book.** No summarising, no skipping a hard sentence, no
   softening. If a step reports missing content, re-run that chunk.

---

## Step 1 — Check the tools

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py doctor
```

- `"ready": true` → continue.
- `"ready": false` → run `pip install -r $SKILL_DIR/requirements.txt`, then run
  `doctor` again.
- `optional_tools` showing `not found` is fine for now. Step 2 will tell you if
  OCR is actually needed.

## Step 2 — Extract

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py extract "<input file>" --out $WORK
```

For a Persian-language source, add `--ocr-lang fas`. For other languages use the
Tesseract code (`deu`, `fra`, `ara`, …).

Read the JSON it prints and follow the table:

| What you see | What to do |
| --- | --- |
| `"kind": "digital"` | nothing; no OCR was needed |
| `"kind": "scanned"` or `"mixed"` | OCR ran automatically; check `ocr.probe_after.text_share` is above `0.7` |
| an error naming OCRmyPDF | install it as the message says, then re-run this step |
| `clean_scan.cleaned` above 0 | a colour watermark was removed from that many pages |
| `ocr.warning` is not null | read it; the file was still usable, so continue |
| `page_scans_dropped` above 0 | that many whole-page rasters were recognised as the scan itself, not as pictures |

The cleaner never edits your file: the original stays untouched and the cleaned
copy is written to `$WORK/cleaned.pdf`, with a per-page record in `clean_scan`
of what was removed and what was left alone. Pass `--clean-scan off` to skip it
entirely, or `--clean-scan force` when a stamp survived.

**If the book was scanned, do these two extra passes now.**

*Recognition confidence* — without it a misread word is indistinguishable from
a correct one, because both are fluent Persian:

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py ocr-sidecar   --pdf $WORK/ocr.pdf --out $WORK --lang fas --book $WORK/book.json
```

This writes `source.ocr.json` (per word, line, block and page: box, confidence,
reading order, and what preprocessing ran) and `source.ocr.txt`, then stamps
each block in `book.json` with the confidence of the region it came from.
Blocks graded `low` are reported by `qa check` as `ocr-low-confidence`, and
accepting one means looking at the page image. Thresholds default to 85 and 60
and move with `--high` / `--low`.

*Illustrations inside a scan* — a scanned page is one flat image, so a
photograph on it is not a separate picture until something finds it:

```bash
mineru -p $WORK/cleaned.pdf -o $WORK/mineru -b pipeline -l arabic
python3 $SKILL_DIR/scripts/revayat-novel.py extract "<input file>" --out $WORK   --figures-from-mineru $WORK/mineru
```

Run MinerU on `cleaned.pdf`, not the original, or it crops the watermark as if
it were a figure. **Take only the pictures from MinerU.** Its own recognised
text for Persian comes back with the words *and the letters inside them*
reversed; Tesseract with `fas` reads the same page correctly, so the text stays
where it is. Use `--figures-page-offset N` when MinerU ran over a page range.

**Then look at the result before going further:**

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py qa check --book $WORK/book.json --allow-incomplete
```

Ignore `untranslated-block` here — nothing is translated yet. You are looking
for `asset-missing`. If `stats.text_blocks` is under 20 for a real book,
extraction failed: see `references/extraction.md`.

## Step 3 — Glossary

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py glossary scan \
  --book $WORK/book.json --out $WORK/glossary.json
```

The report lists `needs_persian`, most frequent first. Open
`$WORK/glossary.json` and for **each of the first 20 entries** fill in four
fields:

```json
"target":      "الیزابت بنت",
"later_form":  "الیزابت بنت",
"first_form":  "الیزابت بنت (Elizabeth Bennet)",
"locked":      true
```

Leave `first_block_id` exactly as it is — the pipeline uses it to decide which
single chunk introduces the name. Do not edit it, and do not decide first
mentions yourself.

Delete entries that are not real names. Then continue.

## Step 4 — Chunk

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py chunk build \
  --book $WORK/book.json --out $WORK/chunks --glossary $WORK/glossary.json
```

Note the number of chunks. Each becomes one translation task.

If you have already translated some worksheets and an input has changed since,
this refuses with `"refused": "stale-worksheets"` rather than quietly handing
you worksheets that no longer match the book. Re-read the reason it gives; add
`--force` only once you have decided the existing translations are still good.

## Step 5 — Translate

For each `$WORK/chunks/chunkNNNN.md`, produce `$WORK/chunks/out_chunkNNNN.md`.
Use a separate sub-agent per chunk when your runtime has them, 8 at a time. If
it does not, do them one at a time — the result is the same, only slower.

**Give the sub-agent exactly this:**

> Read `$WORK/chunks/chunkNNNN.md` and write `$WORK/chunks/out_chunkNNNN.md`.
>
> Translate into Persian. Read
> `$SKILL_DIR/references/translation-policy.md` first and follow it.
>
> Output format — this is mechanical, get it exactly right:
> - Copy each `@@ <id> <kind>` line **unchanged**, in the same order.
> - Put the Persian translation on the lines under it.
> - Output nothing else: no preamble, no English, no commentary, no summary.
>
> Rules:
> - The "Names" table is binding. Where a row says "first mention, introduce it
>   here", use that longer form. Everywhere else use the short form. Do not
>   decide this yourself — the table already did.
> - "Surrounding text" is context only. Never translate it or copy it out.
> - Keep `**bold**`, `*italic*`, `` `verbatim` `` and `[[fn:…]]` exactly, around
>   the equivalent Persian words. Do not add or remove any.
> - Translate every unit fully. Never summarise or skip.
> - To add your own footnote: write `[[fn:tr-01]]` in the sentence and add a
>   `@@ tr-01 footnote` block at the end with its text. Only for a genuine
>   cultural reference or wordplay.

Check what is left at any time:

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py chunk status --chunks $WORK/chunks
```

## Step 6 — Merge

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py merge --book $WORK/book.json --chunks $WORK/chunks
```

| Field | Meaning | Action |
| --- | --- | --- |
| `"ok": true` | everything landed | continue to step 7 |
| `missing_outputs` | those chunks were never translated | translate them |
| `missing_units` | headers were dropped | re-run those chunks |
| `unknown_units` | headers were invented | re-run those chunks |

Re-running merge after a fix is always safe.

## Step 7 — Persian typography

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py falint fix --book $WORK/book.json
```

Mechanical only, and safe to run twice. Add `--digits keep` if the book must
keep Latin numerals.

## Step 8 — Gate, then build

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py qa check \
  --book $WORK/book.json --assets $WORK/assets --glossary $WORK/glossary.json
```

**Do not build while `"ok"` is false.** Fix by error code:

| Code | Meaning | Action |
| --- | --- | --- |
| `untranslated-block` | a block has no Persian | translate that chunk |
| `footnote-marker-lost` | a `[[fn:…]]` was dropped | re-run that chunk |
| `footnote-marker-invented` | a marker points at nothing | re-run that chunk |
| `possible-omission` | target far shorter than source | read it; usually a dropped clause |
| `untranslated` | English left in the Persian | re-run that chunk |
| `asset-missing` / `asset-modified` | a picture is gone or altered | re-extract |
| `copied-source-run` | a clause of the source is alive inside the Persian | re-run that chunk |
| `duplicate-translation` | two different sources got the same Persian | a worksheet reply was pasted twice; re-run both |
| `first-mention-repeated` | a name is introduced in more than one place | keep the first, drop the rest |
| `image-order` | pictures are in the wrong order in the package | re-build |
| `bookmarks-missing` / `bookmark-duplicate` | the TOC would link nowhere, or to the wrong place | re-build |
| `emphasis-parity` (warning) | bold/italic count changed | check one; often fine |
| `glossary-drift` (warning) | a locked name was rendered differently | re-run that chunk |
| `ocr-low-confidence` (warning) | the engine was unsure of this block | open the page image and compare |

Add `--strict` to make the last three blocking as well, for publication work.

Translate the book's title and author into `meta.title_target` and
`meta.author_target` in `book.json` — that is the one hand-edit that *is*
expected, because there is no worksheet for them.

Then build:

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py build \
  --book $WORK/book.json --assets $WORK/assets --out out/book.fa.docx \
  --font "Vazirmatn" --size 11.5
```

Useful flags: `--font Tahoma` when the file must render on a machine with no
Persian fonts; `--heading-size source` to reproduce the original heading point
sizes; `--template ref.docx` to inherit styles from an existing Word file. Full
list in `references/docx-and-ooxml.md`.

## Step 9 — Verify and report

```bash
python3 $SKILL_DIR/scripts/revayat-novel.py qa docx --file out/book.fa.docx --book $WORK/book.json
```

If `"ok": false`, fix it before telling the user the file is ready.

Then report: where the file is, how many chapters, images and footnotes it has,
anything QA flagged that you chose not to act on, and this limitation —

> Word reflows text, so an editable Persian document cannot be page-for-page
> identical to the source PDF. Image bytes and physical size, chapter
> structure, emphasis, footnotes and chapter links are exact.

---

## References

Read these only when the step points at them:

- `references/translation-policy.md` — what to give the translating sub-agent
- `references/persian-typography.md` — RTL, ZWNJ, punctuation, mixed scripts
- `references/extraction.md` — OCR routing, watermarks, difficult books
- `references/glossary-and-voice.md` — naming policy, aliases, character voice
- `references/docx-and-ooxml.md` — every build option and what it produces
- `references/troubleshooting.md` — the failures you are most likely to hit

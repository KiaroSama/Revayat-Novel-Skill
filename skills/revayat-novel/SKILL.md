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

Set four variables once, then use them everywhere:

- `SKILL_DIR` — the folder holding this file. In a Claude Code plugin it is
  `${CLAUDE_PLUGIN_ROOT}/skills/revayat-novel`.
- `WORK` — a working folder for this book, e.g. `work/`.
- `PY` — the Python interpreter. **Resolve it once**; `python3` does not exist
  on most Windows installations, so a command line that hard-codes it works on
  two platforms out of three:
  - macOS / Linux: `PY=python3`
  - Windows: `PY=python` — or `PY="py -3"` if the launcher is what is installed
  - if neither runs, `doctor` in step 1 will not start, and that is the signal
- `OCR_LANG` — the Tesseract code for the language **printed in the book you are
  translating**, not the language you are translating into. For the usual
  English → Persian job that is `eng`. A Persian source is `fas`; German `deu`,
  French `fra`, Arabic `ara`. **The same value must be used at every OCR step.**
  Recognising English pages with the Persian model returns confident-looking
  nonsense, which is worse than a low score because nothing downstream can see
  it.

Every command is `$PY $SKILL_DIR/scripts/revayat-novel.py <stage> …`.

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
$PY $SKILL_DIR/scripts/revayat-novel.py doctor
```

- `"ready": true` → continue.
- `"ready": false` → run `pip install -r $SKILL_DIR/requirements.txt`, then run
  `doctor` again.
- `optional_tools` showing `not found` is fine for now. Step 2 will tell you if
  OCR is actually needed.

## Step 2 — Extract

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py extract "<input file>" --out $WORK --ocr-lang $OCR_LANG
```

`$OCR_LANG` is the language *printed in the book*, set once above. Getting it
wrong here is not a visible failure: OCR still returns text, it is simply the
wrong text.

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
a correct one, because both are ordinary words of the source language, sitting
in a grammatical sentence. Nothing later in the pipeline can tell them apart,
and the translator will render the wrong one faithfully.

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py ocr-sidecar \
  --pdf $WORK/ocr.pdf --out $WORK --lang $OCR_LANG --book $WORK/book.json
```

`--lang` here is the **same `$OCR_LANG` you gave `extract`**. It has to be: this
pass re-recognises the same pages to find out how sure the engine was, so a
different model reads different words and scores something the book does not
say.

This writes `source.ocr.json` — box, confidence and reading order for every
word, aggregated up to line, block and page, plus what preprocessing ran — and
`source.ocr.txt`, then stamps each block in `book.json` with the confidence of
the region it came from. Blocks graded `low` are reported by `qa check` as
`ocr-low-confidence`, and accepting one means looking at the page image.
Thresholds default to 85 and 60 and move with `--high` / `--low`.

*Illustrations inside a scan* — a scanned page is one flat image, so a
photograph on it is not a separate picture until something finds it:

```bash
mineru -p $WORK/cleaned.pdf -o $WORK/mineru -b pipeline -l en
$PY $SKILL_DIR/scripts/revayat-novel.py extract "<input file>" --out $WORK \
  --figures-from-mineru $WORK/mineru
```

MinerU's `-l` takes its own codes, not Tesseract's: `en` for an English source,
`arabic` for Persian or Arabic script. Match it to the book, the same way
`$OCR_LANG` is matched — layout detection uses the script to segment the page.

Run MinerU on `cleaned.pdf`, not the original, or it crops the watermark as if
it were a figure. **Take only the pictures from MinerU.** Measured on a real
Persian scan, its own recognised text came back with the words *and the letters
inside them* reversed, while Tesseract read the same page correctly — so the
text keeps coming from the OCR pass above and MinerU is used only for the one
thing OCR cannot do, which is finding where a picture sits in a flat raster.
Use `--figures-page-offset N` when MinerU ran over a page range.

**Then look at the result before going further:**

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py qa check --book $WORK/book.json --allow-incomplete
```

Ignore `untranslated-block` here — nothing is translated yet. You are looking
for `asset-missing`. If `stats.text_blocks` is under 20 for a real book,
extraction failed: see `references/extraction.md`.

## Step 3 — Glossary

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py glossary scan \
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

When an entry has `aliases` — a surname or a given name the book uses on its
own — put their Persian in `alias_targets`:

```json
"aliases":       ["Ashcroft", "Margaret"],
"alias_targets": ["اشکرافت", "مارگارت"]
```

Where the source says only «Ashcroft», the Persian should say only «اشکرافت».
Leaving this empty makes the drift check demand the full name every time, which
is both worse Persian and a false alarm.

Leave `first_block_id` exactly as it is — the pipeline uses it to decide which
single chunk introduces the name. Do not edit it, and do not decide first
mentions yourself.

Delete entries that are not real names. Then continue.

## Step 4 — Cut into worksheets

A whole novel must never reach one model context, so it is cut up first. There
are two ways to cut, and **the source decides which** — this is not a
preference:

| Source | Route |
| --- | --- |
| **PDF**, born-digital or scanned | **by page** — below |
| EPUB, DOCX, plain text | by character budget — [step 4b](#step-4b--when-the-source-has-no-pages) |

**Cut a PDF by page.** A page is a boundary the book already has: stable
between runs, the unit a reviewer looks at, and the only unit a *rendered* page
can be compared against. A worksheet cut by character budget breaks wherever
the budget happens to run out, which is nowhere in particular — and a page that
does not exist in the source cannot be checked against the source. The formats
below the line have no pages of their own to cut on, so they take the budget
route and give up that check.

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py pages build \
  --book $WORK/book.json --out $WORK/pages --glossary $WORK/glossary.json
```

This writes one worksheet per source page — `page0001.md`, and its translation
goes in `out_page0001.md` beside it. **Use those names, not the `chunkNNNN.md`
of step 5**: the format is identical, only the filenames differ, and `pages
next` tells you exactly which file to open so there is nothing to guess.

It also writes `$WORK/pages/source/page-0001.pdf` — each source page as its own
PDF, copied rather than re-rendered so boxes, rotation and image quality are the
book's own. The manifest records each one with its SHA-256, and names the
`reference_pdf` the whole run was read from. **Read the source PDF from the
manifest**; it is the original for a born-digital book and the OCR'd copy for a
scan, and hard-coding either one is wrong for the other.

Each page job carries only what it needs: the glossary rows that apply on that
page, the voices that speak there, the words OCR was unsure of there, and a
**bounded** slice of the neighbouring pages marked *do not translate*. A page
whose own text exceeds the budget is split into numbered parts rather than
truncated — `part` and `parts` in the manifest say so — and the parts rejoin
before the page is judged.

A paragraph the page break cut in half belongs to the page it *started* on and
is translated exactly once; the page it runs onto sees it as context only.
Never translate a block that appears under the neighbour-context heading — it
already belongs to another page's worksheet.

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py pages status --pages $WORK/pages
$PY $SKILL_DIR/scripts/revayat-novel.py pages next   --pages $WORK/pages
```

`status` reports every page's state; `next` names the first page still to do,
so an interrupted run resumes where it stopped rather than from the beginning.

Then, per page, after you have written `out_pageNNNN.md`:

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py pages merge \
  --book $WORK/book.json --pages $WORK/pages --page 12 --glossary $WORK/glossary.json
$PY $SKILL_DIR/scripts/revayat-novel.py pages accept \
  --book $WORK/book.json --pages $WORK/pages --page 12
```

`accept` refuses unless the page has actually passed its gates — it reads the
evidence rather than taking your word for it, so a page cannot be marked done
by asserting that it is.

Rebuilding is safe and never throws a translation away. A page whose source
text has changed since it was last cut is listed under `invalidated` — those,
and only those, need translating again. A worksheet left over from a rebuild at
a different budget is listed under `orphaned`: it is still on disk, but nothing
reads it any more, so its translation will not reach the book.

## Step 4b — When the source has no pages

EPUB, DOCX and plain text only. For a PDF, use step 4.

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py chunk build \
  --book $WORK/book.json --out $WORK/chunks --glossary $WORK/glossary.json
```

Note the number of chunks. Each becomes one translation task.

If you have already translated some worksheets and an input has changed since,
this refuses with `"refused": "stale-worksheets"` rather than quietly handing
you worksheets that no longer match the book. Re-read the reason it gives; add
`--force` only once you have decided the existing translations are still good.

## Step 5 — Translate

Both routes write the same worksheet format, and differ only in what the file
is called. Take the names from the route you built:

| Route | Read | Write |
| --- | --- | --- |
| pages (step 4) | `$WORK/pages/pageNNNN.md` | `$WORK/pages/out_pageNNNN.md` |
| chunks (step 4b) | `$WORK/chunks/chunkNNNN.md` | `$WORK/chunks/out_chunkNNNN.md` |

Below, `$JOB` is whichever worksheet you are on — `pages next` names it for the
page route, and there is no guessing to do. Use a separate sub-agent per
worksheet when your runtime has them, 8 at a time. If it does not, do them one
at a time — the result is the same, only slower.

**Give the sub-agent exactly this:**

> Read `$JOB` and write `out_` + the same filename, in the same directory.
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

Check what is left at any time — one of these, matching your route:

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py chunk status --chunks $WORK/chunks
$PY $SKILL_DIR/scripts/revayat-novel.py pages status --pages  $WORK/pages
```

## Step 6 — Merge

**Page route: you have already done this.** `pages merge` merges a page into
`book.json` the moment it is translated, glossary and all, and re-settles first
mentions across everything merged so far each time. There are no `$WORK/chunks`
to point the command below at — go straight to step 7.

Chunk route:

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py merge \
  --book $WORK/book.json --chunks $WORK/chunks --glossary $WORK/glossary.json
```

**`--glossary` is not optional.** Merge is where each locked name's single
introduction is settled. The worksheets *ask* the owning chunk to introduce the
name, but chunks are translated by agents that cannot see one another, so every
one of them answers "yes, this is the first mention" — and without this pass the
finished book either repeats «الیزابت بنت (Elizabeth Bennet)» in thirty places
or never introduces her at all. Merge flattens every introduction and puts back
exactly one. `first_mentions.introduced` in the report says where each landed.

| Field | Meaning | Action |
| --- | --- | --- |
| `"ok": true` | everything landed | continue to step 7 |
| `missing_outputs` | those chunks were never translated | translate them |
| `missing_units` | headers were dropped | re-run those chunks |
| `unknown_units` | headers were invented | re-run those chunks |
| `first_mentions.unplaceable` | a locked name appears nowhere in the Persian | check that name's translation |

Re-running merge after a fix is always safe; the first-mention pass is
idempotent.

## Step 7 — Persian typography

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py falint fix --book $WORK/book.json
```

Mechanical only, and safe to run twice. Add `--digits keep` if the book must
keep Latin numerals.

## Step 8 — Gate, then build

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py qa check \
  --book $WORK/book.json --assets $WORK/assets --glossary $WORK/glossary.json
```

### If you took the page route, look at each page before accepting it

Every other gate here reads the IR, and a page can be right in the IR and wrong
on the page: a picture that slid to the far side of a break, a paragraph Word
set left-to-right because a style lost its `w:bidi`, a caption clipped off the
trim, a page that came out blank because the build failed halfway.

```bash
# The source PDF is whichever one this run was read from: the book's own for a
# born-digital PDF, the OCR'd copy for a scan. The manifest recorded it — take
# it from there rather than naming a file that may not exist.
SOURCE_PDF=$($PY -c "import json,sys;print(json.load(open(sys.argv[1]))['reference_pdf'])" \
  $WORK/pages/manifest.json)

$PY $SKILL_DIR/scripts/revayat-novel.py render-qa \
  --book $WORK/book.json --work $WORK --page 12 \
  --docx $WORK/book.docx --source-pdf $SOURCE_PDF
```

`--docx` is how the translated page gets laid out at all: without it there is
nothing to compare the source against, and the report comes back `unverified`
rather than passing. Word does the laying out on Windows and LibreOffice
elsewhere; the report records which one ran, because the two do not paginate
identically.

It writes `renders/source/page-0012.png`, `renders/target/page-0012.png` and
`qa/pages/page-0012.json`, then compares them **structurally**. Do not expect
the two images to match: Persian is a different language set in the other
direction, so the line breaks, the line count and often the page count differ.
What must hold is that every block is present once, nothing is clipped or
outside the margins, the illustrations are the same ones in the same order at
the same aspect ratio, and the paragraphs are right-to-left.

### Then look at the two images yourself

The checks above are geometric, and geometry has a blind spot the size of the
thing you were worried about. A plate can sit inside the body area, at exactly
the right aspect ratio, present exactly once — three pages away from the
paragraph it illustrates. Persian can clear every one of those checks and still
render as disconnected letters, because the font it fell back to has no joining
forms. A heading can be present, correctly placed, and look like body text.
None of that is in `book.json`; it is on the page, which is why both images
were written.

**Open `renders/source/page-0012.png` and `renders/target/page-0012.png` and
look at them side by side.** Then answer all five, and mean it:

| Question | What you are looking for |
| --- | --- |
| `figure-placement` | is each picture beside the text it belongs to? |
| `script-integrity` | joined, readable Persian — no disconnected letters, no boxes, no dotted circles |
| `no-source-language` | is everything that should be Persian actually Persian, captions and headings included? |
| `hierarchy` | do headings still read as headings, and dialogue as dialogue? |
| `reads-as-a-book` | even margins, an even colour of type, no line crushed or stretched to fit |

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py pages review \
  --pages $WORK/pages --page 12 \
  --answer figure-placement=yes --answer script-integrity=yes \
  --answer no-source-language=yes --answer hierarchy=yes \
  --answer reads-as-a-book=yes --note "what you saw, in your own words"
```

All five are required: an unanswered question is not a question nobody minded,
so a partial answer sheet is refused and nothing is written. Answer `no` where
it is `no` — a `no` names the fault in the report and stops the page being
accepted, which is the entire point of being asked.

The verdict is tied to the image it was made from. Re-render the page and the
review goes stale automatically, because it describes a page that no longer
exists. `pages accept` will not take a page without a current one.

A page that fails is re-translated and re-rendered on its own — `--max-attempts`
bounds the retries so a page that cannot be fixed stops rather than looping.
Accept a page only when its report is clean, and run the whole-document check
again after assembly, because a page that passed alone can still regress when
the book is put together.

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
$PY $SKILL_DIR/scripts/revayat-novel.py build \
  --book $WORK/book.json --assets $WORK/assets --out out/book.fa.docx \
  --font "Vazirmatn" --size 11.5
```

Useful flags: `--font Tahoma` when the file must render on a machine with no
Persian fonts; `--heading-size source` to reproduce the original heading point
sizes; `--template ref.docx` to inherit styles from an existing Word file. Full
list in `references/docx-and-ooxml.md`.

## Step 9 — Verify and report

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py qa docx --file out/book.fa.docx --book $WORK/book.json
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

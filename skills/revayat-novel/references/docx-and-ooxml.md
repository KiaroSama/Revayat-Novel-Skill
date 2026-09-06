# The Word document

## Build options

```bash
$PY scripts/revayat-novel.py build --book work/book.json --out out/book.fa.docx [options]
```

| Option | Default | Notes |
| --- | --- | --- |
| `--assets DIR` | `<book dir>/assets` | where the extracted pictures live |
| `--template REF.docx` | none | inherit styles and page setup from a Word file |
| `--font NAME` | `Vazirmatn` | Persian (complex-script) face |
| `--latin-font NAME` | `Times New Roman` | Latin runs inside Persian text |
| `--size PT` | `11.5` | body size |
| `--toc` / `--no-toc` | on | table of contents |
| `--toc-title TEXT` | `فهرست مطالب` | |
| `--toc-depth N` | `2` | heading levels included |
| `--justify` / `--no-justify` | on | justify body paragraphs |
| `--captions` / `--no-captions` | on | render image alt text as a caption |
| `--rtl` / `--no-rtl` | on | `--no-rtl` for a left-to-right target language |
| `--page-breaks` | `chapter` | `chapter`, `source` or `none` |
| `--heading-size` | `style` | `style` uses Word's Heading N sizes; `source` reproduces the point size measured in the original book |

`--heading-size style` is the default because Word's heading styles give a
coherent document even when the source's own sizes are erratic. Use
`--heading-size source` when the brief is to reproduce the book's exact
typography — extraction records the real point size of every heading it finds,
and this reinstates it.

`--page-breaks chapter` breaks before each level-1 heading. `source` also
honours hard page breaks from the original. Soft page breaks recorded from PDF
page boundaries are always ignored: Persian reflows differently, so forcing the
original pagination would leave large gaps mid-chapter.

## What gets built

Everything below is a real Word structure, not a visual imitation.

**Right-to-left, at three levels.** `w:bidi` in the section properties makes the
section RTL; `w:bidi` on each paragraph sets its base direction; `w:rtl` on each
Persian run marks its characters. Latin runs deliberately carry no `w:rtl`, so
a name inside a Persian sentence stays left-to-right — see
`persian-typography.md`.

**Real footnotes.** A `word/footnotes.xml` part, related from the document and
declared in `[Content_Types].xml`, with Word's reserved separator entries at ids
-1 and 0 and content from 1 up. Each marker in the body is a `w:footnoteReference`
in a run styled `FootnoteReference`. Word paginates them itself, so a note lands
at the foot of whichever page its marker ends up on — which a manual superscript
plus an endnote list cannot do.

`FootnoteText` and `FootnoteReference` styles are created by the builder: the
default Word template declares them as latent but does not define them.

**A clickable table of contents.** Each heading gets a `w:bookmarkStart` /
`w:bookmarkEnd` pair with a stable anchor (`rv_0001`). The TOC is a genuine
`TOC \o "1-N" \h \z \u` field, and its *cached result* is a list of
`w:hyperlink w:anchor` links. That single construct covers both cases: Word
rebuilds the field with page numbers when fields update (`w:updateFields` is set,
so it offers to on open), and a viewer that never updates fields still shows a
working clickable list rather than a blank page.

**Pictures at their real size.** Each image is placed with an explicit
`wp:extent` in EMU derived from the points recorded at extraction. If the source
gave no physical size — EPUB is reflowable and has none — pixels are converted
at 96 DPI. Anything wider than the text block is scaled down with its aspect
ratio preserved.

**Sections, and the running heads on them.** A DOCX that changes page size,
orientation or margins partway through arrives as `book["sections"]`, each entry
naming the block it opens at, and the builder emits a real `w:sectPr` for each.
`book["page"]` still reports the first section's geometry, which is what a stage
with no page in hand reads. Final render QA does not: it resolves each rendered
page to the section that page belongs to, so a landscape section 3 is measured
against section 3's own setup rather than reported as a page-size failure.

A section carries the heads and feet the author wrote, as pieces:

```json
{"headers": {"default": {"paragraphs": [
    {"align": "center", "pieces": [
        {"id": "rh0001", "text": "Pride and Prejudice", "target": null},
        {"tab": true},
        {"field": " PAGE "}]}]}},
 "footers": {}}
```

Only the slots a section *defines* appear — `default`, `first`, `even` — and a
missing one inherits from the section before, exactly as Word resolves it.

**The heads are translated, not copied.** Each piece with prose becomes an
ordinary worksheet unit (`rhNNNN`, kind `header` or `footer`), so it goes out
through the same `@@ id kind` protocol as the body, is checked by `falint`, and
is counted by `qa check` — which reports `untranslated-running-head` for one
that never came back. A tab and a `PAGE` field are layout, not language, so a
translator is never shown either.

An untranslated head is **left off the page**, and the builder warns. It is not
copied through in English: a running head prints on every page, and an English
title across a Persian one is worse than no running head at all. That judgement
is why these were dropped wholesale before; the answer was to carry them in
Persian rather than to drop them.

**The generated page number yields to the author's.** `--page-numbers` exists
because most sources have no foot of their own. When the first section brings
one, the generated number is not added — two things where the author put one is
not fidelity, and the author's foot may well be a page number already.

**Structure.** `Heading 1`–`Heading 6` styles, `Quote` for block quotes,
`Caption` for captions, `List Bullet` / `List Number` for lists, `Title` and
`Subtitle` for the front matter. Page size and margins come from `book.page`.

## Verifying the result

```bash
$PY scripts/revayat-novel.py qa docx --file out/book.fa.docx --book work/book.json
```

This reads the saved package, not the builder's own report:

| Code | Severity | Meaning |
| --- | --- | --- |
| `dead-link` | error | a TOC entry points at a bookmark that does not exist |
| `footnote-body-missing` | error | a reference with no matching footnote |
| `footnotes-part-missing` | error | references but no `word/footnotes.xml` |
| `footnotes-content-type` | error | the part is not declared in `[Content_Types].xml` |
| `images-lost` | error | fewer pictures in the package than in the book |
| `picture-size-implicit` | warning | a picture with no explicit extent |
| `no-rtl` | warning | no `w:bidi` anywhere — the document is not RTL |

## Using a reference template

`--template ref.docx` opens an existing Word file and appends into it, so the
document inherits its styles, page setup, headers and footers. Build the
template once in Word with the fonts, margins and heading styles a publisher
wants, then reuse it.

The builder still applies RTL properties and creates any missing footnote
styles, so a plain Latin template is a valid starting point.

## The limitation worth stating plainly

Word is a reflowable format. A Persian paragraph is rarely the same length as
its English original, so an **editable** document cannot also be page-for-page
identical to the source PDF. Any tool promising both is either producing
uneditable text boxes or is wrong.

What *is* exact: image bytes and physical size, aspect ratio, image anchoring
to the right point in the text, heading hierarchy, chapter breaks, bold and
italic, footnote placement and numbering, chapter links, and selectable,
editable, searchable Persian text.

If page-faithful layout matters more than editability for a particular job,
that is a different output format — a fixed-layout PDF — and a different tool.

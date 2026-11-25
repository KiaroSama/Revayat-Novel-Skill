# The Word document

## Build options

```sh
revayat-novel build "translation work" --out "delivery/book.fa.docx"
```

The Rust OOXML writer takes a work directory with its Book IR and assets. Use `revayat-novel build --help` for options supported by the installed binary. The default Persian face is the bundled static Vazirmatn, under `skills/revayat-novel/assets/fonts/vazirmatn/`. Check `w:rFonts`, its `w:cs` value, and the embedded font parts. Keep Persian text in logical order. Use `w:bidi` and `<w:jc w:val="start"/>` with [RLI/PDI isolation](persian-typography.md#mixed-persian-and-latin) at mixed-script sentence boundaries.

Carry through the source's dimensions, margins, orientation and authored section breaks where present. A pageless source needs an agreed output geometry. Persian reflows, so physical page dimensions do not imply identical line or page breaks.

## What gets built

Everything below is a real Word structure, not a visual imitation.

**Ordered tables.** Ordinary Book IR blocks retain their cell coordinates and
spans. Multiple paragraphs, drawings, note markers and source-break events are
written in order inside the owning cell; nested tables retain their immediate
parent-cell ownership. See [docx-intake-integrity.md](docx-intake-integrity.md)
for native intake refusals and separate-workspace migration. A cell-scoped hard
break is emitted when retained by the writer, but final pagination remains the
viewer’s layout decision.

**Right-to-left, at three levels.** `w:bidi` in the section properties makes the
section RTL; `w:bidi` on each paragraph sets its base direction; `w:rtl` on each
Persian run marks its characters. Latin runs deliberately carry no `w:rtl`, so
a name inside a Persian sentence stays left-to-right - see
`persian-typography.md`.

**Real footnotes.** A `word/footnotes.xml` part, related from the document and
declared in `[Content_Types].xml`, with Word's reserved separator entries at ids
-1 and 0 and content from 1 up. Each marker in the body is a `w:footnoteReference`
in a run styled `FootnoteReference`. Word paginates them itself, so a note lands
at the foot of whichever page its marker ends up on - which a manual superscript
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
gave no physical size - EPUB is reflowable and has none - pixels are converted
at 96 DPI. Anything wider than the text block is scaled down with its aspect
ratio preserved.

**Sections, and the running heads on them.** A DOCX that changes page size,
orientation or margins partway through arrives as `book["sections"]`, each entry
naming the block it opens at, and the builder emits a real `w:sectPr` for each.
`book["page"]` still reports the first section's geometry, which is what a stage
with no page in hand reads. Final render QA does not: it measures each rendered
page against the declared geometry it is nearest to, so a landscape section 3 is
not reported as a page-size failure.

**Order is checked separately, and twice.** Nearest-geometry matching asks only
whether a page's size is one the book allows, so sections that came out in the
wrong order are three runs of individually legal pages and pass. `doc-qa`
therefore also compares the built `w:sectPr` sequence with `book["sections"]` -
count, ordered size, orientation, start type - and requires the rendered page
shapes to be a walk *forward* through the declared sequence. The first sees a
section duplicated while another is dropped, which leaves no trace on paper; the
second sees a renderer that disagreed with its own package. Adjacent sections of
the same trim, and repeated pages inside one section, are ambiguous by nature
and pass.

A section carries the heads and feet the author wrote, as pieces:

```json
{"headers": {"default": {"paragraphs": [
    {"align": "center", "pieces": [
        {"id": "rh0001", "text": "Pride and Prejudice", "target": null},
        {"tab": true},
        {"field": " PAGE "}]}]}},
 "footers": {}}
```

Only the slots a section *defines* appear - `default`, `first`, `even` - and a
missing one inherits from the section before, exactly as Word resolves it.

**The heads are translated, not copied.** Each piece with prose becomes an
ordinary worksheet unit (`rhNNNN`, kind `header` or `footer`), so it goes out
through the same `@@ id kind` protocol as the body, is checked by `falint`, and
is counted by `qa check` - which reports `untranslated-running-head` for one
that never came back. A tab and a `PAGE` field are layout, not language, so a
translator is never shown either.

An untranslated head is **left off the page**, and the builder warns. It is not
copied through in English: a running head prints on every page, and an English
title across a Persian one is worse than no running head at all. That judgement
is why these were dropped wholesale before; the answer was to carry them in
Persian rather than to drop them.

**The generated page number yields to the author's.** Generated numbering is available
because most sources have no foot of their own. When the first section brings
one, the generated number is not added - two things where the author put one is
not fidelity, and the author's foot may well be a page number already.

**Structure.** `Heading 1`–`Heading 6` styles, `Quote` for block quotes,
`Caption` for captions, `List Bullet` / `List Number` for lists, `Title` and
`Subtitle` for the front matter. Page size and margins come from `book.page`.

## Literal line controls

Notes and raw internal hyperlinks use native `w:br` / `w:tab` controls, not layout
characters hidden inside `w:t`. CRLF represents one logical break. Package QA
compares note text literally after interpreting markup on the IR side only;
edge controls and literal markup examples are not stripped or parsed again.
`footnote-control-text`, `link-control-text`, `footnote-text-mismatch` and
`link-text-mismatch` require rebuilding from current reviewed IR, not editing the
package or suppressing its findings. Link control comparison applies to declared
control-bearing source labels; this is not universal hyperlink translation QA.

## Verifying the result

Run `revayat-novel qa docx` against the actual output and source Book IR. Use `revayat-novel doc-qa check` for Word/LibreOffice layout evidence, and `revayat-novel render-qa <file.docx> --out <render-dir>` for a render run.

```bash
revayat-novel qa docx --help
```

This reads the saved package, not the builder's own report - and it reads it as
**OPC**: the XML is parsed with namespaces, the content types are resolved through
the Default and Override rules, and the relationship targets are resolved to part
names. That matters because the same document has many legal serialisations: a
regex looking for one spelling of a tag rejected a package whose relationships
used a different prefix, and passed a truncated part, where a parse simply fails.
The archive is bounded before it is read - a member that would write outside the
package, or one that expands past a ceiling, is refused rather than examined.

The rows below are the ones this reference is about; `references/findings.md` has
every code:

| Code | Severity | Meaning |
| --- | --- | --- |
| `part-malformed` / `part-root-wrong` | error | a required part does not parse, or its root is not the one the format specifies |
| `footnotes-lost` | error | the book places notes and the document has no reference at all |
| `footnotes-relationship-missing` | error | references, and no footnotes relationship for Word to resolve them through |
| `footnotes-part-missing` | error | the footnotes relationship resolves to a part that is not in the package |
| `footnote-body-missing` | error | a reference with no matching footnote |
| `footnote-reference-duplicated` | error | two markers on one note, so one sentence is footnoted by the other's text |
| `footnote-text-mismatch` | error | the notes in the document are not the notes in the book, compared in reading order and by content |
| `footnotes-content-type` | error | the part is not declared in `[Content_Types].xml` |
| `picture-aspect` / `picture-too-wide` | error | a picture is drawn at a shape or a width the builder's fitting never produces |
| `images-lost` | error | fewer pictures in the package than in the book |
| `dead-link` | error | a TOC entry points at a bookmark that does not exist |
| `no-rtl` | warning | no `w:bidi` element - the document is not RTL |

The note checks compare the **book's** inventory with what the package places, in
reading order, by content: the correspondence is the sequence of note texts, not
the numeric ids, which Word allocates and renumbers. Matching ids cannot see a
substituted body, and a set of ids cannot see a duplicated reference.

`doc-qa check` asks a second set of questions, about the assembled book's
sections. Sizes in the wrong order are each individually legal, so this is
checked twice - once against the built `w:sectPr` sequence and once against the
rendered page shapes:

| Code | Severity | Meaning |
| --- | --- | --- |
| `section-count` | error | the book declares one number of sections and the document has another: a break was lost or duplicated |
| `section-size` | error | a section's page size is not the one the book declares for it |
| `section-property` | error | a section's orientation, margins or start type disagrees with the book |
| `section-order` | error | the rendered pages do not walk forward through the declared sequence - the renderer disagreed with its own package |
| `section-package-unread` | warning | the built document's sections could not be read back, so their order was not checked |

All five mean the same thing for a reader: **re-build.** None of them is
something to fix in `book.json`.

## A PDF of the finished book

Word or LibreOffice (`soffice`) lays out the editable document for rendering. The exported PDF can have different page locations from the source or another renderer. Verify its links separately if it is part of delivery; the editable DOCX remains the book deliverable.

## What stays exact

Keep the original encoded image bytes, pixel dimensions, aspect ratio, and authored drawing dimensions where present. The writer should also preserve emphasis, tables, true footnotes, and heading links. If the PDF source must keep its exact pagination, agree on a separate fixed-layout deliverable instead of claiming that a reflowable Word file has identical pages.

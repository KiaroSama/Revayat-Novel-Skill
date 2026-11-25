# Native DOCX Book Workflow

This guide ships with Revayat Novel. Use it for Word book intake, translated Word
production and verification. It requires no separate document skill installation.
The supported scope is book translation, not general form or Track Changes editing.

## Inspect and extract

Start the adjacent workflow log required by preservation-and-logging.md. Preserve
the original file. Run `revayat-novel doctor`, then `revayat-novel extract <input.docx> --out <work>`. Inspect the report and Book IR before translating.

The Rust intake engine reads namespaced OOXML directly for notes,
hyperlink targets, section breaks and running heads. Check those surfaces as well
as the body: a correct paragraph count does not establish complete extraction.
Record source section width/height/orientation, image native pixels and placed
size. A legacy binary .doc must first be converted to a separate .docx using an
available document application; do not rename its extension and claim conversion.

If the source has unresolved tracked changes, comments carrying manuscript content,
floating objects or complex tables, inspect them explicitly and record what the
native reader preserved or could not represent. Do not silently accept/reject
editorial changes or claim exact preservation of unsupported objects. Obtain a
settled source or resolve the specific missing content before delivery.

## Native intake fidelity and refusals

Read `docx-intake-integrity.md` for the event-order contract and recovery. A native
run can contain prose, several drawings, a page break and a note reference; these
must be consumed in XML order rather than treating the first drawing as the whole
run. Footnote and endnote IDs are separate namespaces, and every occurrence must
retain its own source position. Missing or malformed referenced content refuses
extraction instead of producing a shortened success. Resolve unsupported source
objects in a separate faithful copy; never delete them merely to clear a gate.

## Translate the structured book

Keep Book IR authoritative. Use `revayat-novel chunk build`, then follow the [six-stage workflow](multi-agent-workflow.md). Verify names and specialized terms online, record the sources in the locked glossary, and preserve each speaker's voice in natural Persian. Never replace this
route with a Markdown round-trip: that loses note identity and page/image geometry.
Translate metadata and running heads too. Revisions after approval require renewed
review; package validation alone cannot establish semantic fidelity.

## Authored structure without prose

Source tables have an optional validated `tables` inventory describing actual grids,
occupied/merged cells and immediate parent-cell ownership. Ordered `table` events
place even empty or child-only tables. `layout` events retain authored empty or
control-only paragraphs, including additional blank paragraphs inside cells. A
cell's final mandatory empty terminator is recorded separately from those authored
boundaries. Neither is a translation unit; never invent empty replies.
Legacy books without the inventory keep the old table path with disclosed limits,
not guessed empty shape. Page previews select the relevant structural closure.

Applicable complex-script emphasis uses bounded native style/default chains and
explicit false overrides, not an OR of Latin and complex-script flags. Native
control text includes tabs, line breaks and nonbreaking/soft hyphens. Mirrored
margins belong to document settings; gutter stays section-local. Normal widow
control is explicitly false, not an inherited accidental template value.

## Build using the native writer

Use `revayat-novel build <work> --out <file.docx>`. The Rust OOXML writer handles
styles, paragraph/run direction, real footnotes, headings/bookmarks, hyperlinks,
TOC fields and section page setup. Preserve source trim size instead of applying
a generic A4/Letter template. Use `w:bidi`, logical start alignment, and embedded static Vazirmatn from `skills/revayat-novel/assets/fonts/vazirmatn/`. Apply the [RLI/PDI rule](persian-typography.md#mixed-persian-and-latin) to mixed-script sentences;
never reverse Unicode strings or pre-shape their letters.

Keep original image bytes and pixel dimensions. Preserve authored drawing dimensions and aspect ratio where available. Treat a low-quality enhancement as a logged
derivative with the original retained. Do not add a second document engine or
machine-specific font path to the copyable skill.

## Verify and correct

Run `revayat-novel qa check` with current semantic approvals, build, then run `revayat-novel qa docx` against the actual output and source IR.
This checks package structure and relationships, expected notes, image geometry
and required document parts. Run `revayat-novel doc-qa check` and `revayat-novel render-qa <file.docx> --out <render-dir>` with Word or LibreOffice (`soffice`) where available and compare the final content/geometry.

Open every generated page image. Check joined Persian, actual font, mixed-script
names, footnote placement, chapter hierarchy, overflow/clipping and illustration
clarity. Record the evidence with `revayat-novel doc-qa review`, then rerun `revayat-novel doc-qa check` to consume that
review. Correct the owning IR/build setting and rebuild; do not patch the final
ZIP while leaving the authoritative book and its approvals stale.

Deliver the checked Word file and adjacent workflow-log path. A missing renderer
or unresolved structure is an explicit unverified area, never an implied pass.

## Native ownership and sources

See [docx-and-ooxml.md](docx-and-ooxml.md) for package checks and [ECMA-376](https://ecma-international.org/publications-and-standards/standards/ecma-376/) for the format specification.

# Troubleshooting

## Merge says `ok: false`

The worksheet protocol was broken. The report tells you which way:

- **`missing_units`** - ids the worksheet asked for that never came back. The
  sub-agent dropped, merged or renamed a header. Re-run that chunk with the
  output-format rule restated.
- **`unknown_units`** - ids in the output that were not in the worksheet. The
  sub-agent invented one, or continued numbering past the end. Re-run.
- **`missing_outputs`** - no `out_chunkNNNN.md` at all. The sub-agent failed or
  was never launched. `chunk status` lists what is still pending.
- **`malformed`** - the reply broke the protocol: an id answered twice, answered
  under the wrong kind, the headers reordered, a footnote graph that does not
  resolve - or the **transport itself** is wrong. A reply that opens a code fence
  and never closes it is the shape of a truncated answer, and merge now refuses
  it instead of merging whatever arrived before the cut. Ask for the reply again
  rather than repairing the file: the missing part is missing.

Never patch `book.json` by hand to work around this. Merge is idempotent, so
fixing the worksheet and re-running is always safe and keeps the run
reproducible.

## Persian text looks corrupted in the terminal

Use a UTF-8 terminal and confirm the text from the UTF-8 output or log. A display problem does not justify reversing characters or recoding the manuscript. See [Persian typography](persian-typography.md).

## Literal line controls or name introductions keep failing QA

A `footnote-control-text` / `link-control-text` finding means a raw layout character
was written inside `w:t`; rebuild with the current writer. Text-mismatch findings
also compare authored break/tab order, including note edges. Preserve the original
source and old outputs; never replace breaks with spaces or clear reviews to pass.

Naming enforcement and QA share eligible complete prose introductions. URLs,
code, notes and attached word fragments are not introduction sites. Keep an already
correct styled introduction; fix redundant complete forms only. Regenerate affected
worksheets after glossary/source changes and reread current review sheets rather
than renaming archived replies. After fluency edits, fresh meaning review is required.

## The Word file opens, but the table of contents is empty

Word only fills a TOC field when fields update. The builder sets
`w:updateFields`, so Word offers on open - say yes, or press `Ctrl+A` then `F9`.

If a viewer never updates fields, the cached hyperlink list is still there and
still clickable; only the page numbers are missing.

## Persian letters are disconnected, or words run backwards

The renderer lacks bidi and Arabic shaping. This is a property of whatever is
displaying the file, not of the file. Open it in Word or LibreOffice.

If it happens there too, check the document really has `w:bidi` -
`revayat-novel qa docx` reports `no-rtl` when it does not.

**Do not "fix" it by reversing text.** See `persian-typography.md`.

## Persian shows as boxes or the wrong font

Run `revayat-novel doctor` and confirm that the bundled static faces are available under `skills/revayat-novel/assets/fonts/vazirmatn/`. Rebuild with `revayat-novel build <work> --out <file.docx>` and inspect `word/fontTable.xml`, its font relationships, and the embedded font parts. Check `w:cs="Vazirmatn"` on Persian runs and confirm what the renderer actually used. Investigate `font-fallback` or box glyphs even if the package opens. Missing glyphs and missing bidi shaping are different problems.

## The book came out much shorter than expected

Almost always one of:

1. **OCR coverage is missing.** Check the extraction and sidecar reports against the actual scan pages.
2. **Chunks were never translated.** `chunk status` shows `pending`.
3. **Omissions inside chunks.** `qa check` reports `possible-omission` where a
   target is far shorter than its source.

## `asset-modified` from `qa check`

An image on disk no longer matches the SHA-256 recorded at extraction. Something
edited or re-encoded it. Re-extract, or restore the file - do not clear the
hash, which is the only thing proving the picture is the book's own.

## OCR is slow or headings are wrong

Run `revayat-novel doctor` to check Tesseract. Identify the affected source pages before repeating OCR, and retain native text that already reads correctly. Keep the sidecar and source identity together. Check chapter headings and reading order against the original; a title set like body text can need a verified correction. Do not proceed from a partial or timed-out result.

## A name drifted anyway

`revayat-novel glossary check` reports drift only for entries with
`locked: true`. Unlocked entries are advisory. Lock the ones that matter, and
re-run the chunks the report names.

If a sub-agent's rendering is genuinely better than the glossary's, update the
glossary and re-run the *earlier* chunks instead - consistency matters more than
which of two good renderings won.

## Emphasis parity warnings everywhere

The source's emphasis marks were not carried into Persian. Usually the
sub-agent's prompt was truncated or the model summarised. Spot-check one chunk:
if the Persian is otherwise good and only the markers are gone, re-running that
chunk with the format rule restated is enough.

Occasional single warnings are normal - Persian sometimes needs the emphasis on
a different number of words. Systematic warnings are a prompt problem.

## A scan has missing illustrations or incomplete OCR

A picture drawn into a scan has no separate image object. Keep the original page. If a verified crop is needed, log it as a derivative. Never report its hash or dimensions as those of the original image.

Use `revayat-novel ocr-sidecar <pdf> --out <work>` after checking Tesseract and source-language data. Verify page count, geometry, and each affected page; a textless raster can be a plate or a missed page of prose. If OCR produces almost no text, try a reviewed `revayat-novel clean-scan` derivative or obtain a better source. `ocr-coverage-unverified` means coverage remains unresolved.

## A watermark remains on a page

Use `revayat-novel clean-scan preview` to compare the original and the candidate at reading size. A faint remnant may be safer to retain than a partly erased letter. Keep artwork untouched and never substitute a cleaned scan for the original assets. See [watermarks.md](watermarks.md).

## `pages build` refuses with `source-pdf-unavailable`

The book was read from a PDF and that PDF is no longer where `book.json` says
it is. The page route cuts one real PDF per source page so a reviewer can set
the translation beside the page it came from; without the file it cannot cut
any of them.

It refuses rather than carrying on, because carrying on used to produce a page
run with an empty `reference_pdf` and an empty `source_pdf` on every page -
which every later gate read as *a format that has no source pages*, exactly
what a DOCX or an EPUB looks like. The book then reached `accepted` with
nothing ever compared against it. **Losing the source file is not a new source
format.**

Put the PDF back where the book names it, or beside `book.json`, and run
`pages build` again. If the file is genuinely gone, re-run `extract` against
the copy you do have.

## `pages status` calls a page I already accepted `stale`

Run `revayat-novel pages status` against the current book and page manifest. A content change or an unverifiable digest invalidates earlier visual evidence without deleting the translation. Recreate the affected preview using `revayat-novel pages preview`, inspect it beside the source, and repeat `revayat-novel pages review` and `revayat-novel pages accept`. Do not promote a stored label without current evidence.

## `render-qa` says `source-missing` or `source-hash-mismatch`

Two different diagnoses about the same artefact, `pages/source/page-NNNN.pdf`:

- **`source-missing`** - the one-page PDF `pages build` cut is not there. Run
  `pages build` again; it is safe on an existing run and re-cuts the sources.
- **`source-hash-mismatch`** - a file *is* there and it is not the one the
  manifest committed to. Something replaced it, and rendering it would compare
  the translation against the wrong page. `pages build` again restores it.

Either way the page comes back `unverified`, and `pages review` and `pages
accept` both refuse until it is resolved. That is the gate working: the page
route accepts a page by *comparison*, and every deterministic check reads only
the translated side, so all of them would pass on a page nobody set beside its
source.

For page runs, use the manifest’s verified source identity. An unrelated PDF cannot establish page acceptance.

## `render-qa` or `doc-qa` reported a code on a page

These come from the one source page laid out on its own (`render-qa`) or from a
page of the assembled book (`doc-qa check`). Both measure geometry against what
`book.json` says belongs there.

| Code | What it means | What to do |
| --- | --- | --- |
| `text-missing` | a block that belongs on this page is not on the rendered page | re-translate that page; if it recurs, the block has no Persian yet |
| `text-duplicated` | a block appears more than once on one page | a worksheet reply was pasted twice; re-run that page |
| `text-clipped` | text runs off the edge of the paper | a long unbreakable run - usually a URL or a Latin name; check that paragraph |
| `text-overflow` | text is on the page but outside the body area | a style lost its indents; re-build |
| `text-image-overlap` | text is printed on top of a picture | re-build; if it recurs the illustration is wider than the text block |
| `blank-region` | a hole in the body area, or nothing on the page at all | the build failed partway, or a plate pushed a page empty; re-build and look |
| `image-missing` / `image-extra` | the page carries fewer or more illustrations than it owns | re-extract, then re-build |
| `image-reordered` | the right pictures in the wrong order - a caption now sits under the wrong plate | re-build from `book.json` |
| `image-aspect` | an illustration's shape is not the book's | the picture was resized; re-extract |
| `preview-empty` | the page's preview rendered no pages at all | the renderer produced nothing; check `doctor` under `optional_tools.render` |
| `font-unverified` (warning) | the render reported no font names, so which face was used was not checked | nothing to fix; it describes the renderer, not the book |
| `font-fallback` (warning) | the page asked for one face and was set in another | check the bundled Vazirmatn parts and the font selected by the renderer |

`font-unverified` and `font-fallback` are the two codes that describe **the
machine that rendered**, not the book, so neither decides the verdict - see
the findings reported by `revayat-novel doc-qa check`.

## Optional tools are installed but not detected

Run `revayat-novel doctor --json` from the same environment as the book command. Check that `tesseract` and required language data are available for OCR, and that LibreOffice (`soffice`) or Word is available for layout checks. On Windows, check the installed application's path and the account running the binary. If startup still fails, keep the diagnostic and report the missing coverage. A native PDF rasterizer does not replace a Word-layout renderer.

## Extraction or rendering refused the size

Treat archive, image, page, and render limits as containment checks. Check source identity, dimensions and counts, and whether the archive is malformed. Obtain a valid source or agree on preserving the book as smaller source volumes. Never overwrite the original, shrink images silently, or accept a partial book as complete.

## A command refused to write: `locked`, `lost-update`, `invalid-book`, `write-failed`

Book writes, review recording and fluency application share one transaction.
Validation refusals occur before commit. I/O failures can leave a recoverable
partial write; inspect the journal and result before deciding what changed.

| Refusal | What happened | What to do |
| --- | --- | --- |
| `locked` | another command owns the OS lock | retry after it finishes. The OS releases a crashed process's lock; file age is irrelevant |
| `lost-update` | the book changed between this command reading it and writing it, so the decision it made may no longer hold | run it again. Nothing was discarded - refusing is how the other writer's work survives |
| `invalid-book` | the change would leave the book invalid: a duplicate id, or a footnote edge that does not resolve | the detail names the unit. A `[[fn:…]]` naming a note the book does not define is the usual cause |
| `write-failed` | journal, side file, book replacement or cleanup failed | files may have changed. Preserve `book.json.journal.json` and recover before relying on approval |
| `review-changed` / `sidecar-changed` | proposal evidence changed after it was read | the competing evidence is retained; inspect it and run against the current proposal |
| `unresolved-sidecar` / `unresolved-journal` | a file matches neither recorded state | preserve the independent edit and journal; choose a state from evidence before recovery |
| `invalid-journal` / `unsupported-journal` | recovery cannot validate the journal | keep the evidence; never substitute an empty successful transaction |

A journal is recovered under the same OS lock as a normal write. Absolute paths
stay rooted in the book's work directory. Recovery checks every side file before
changing any; interrupted recovery is resumable. Side-only transactions have an
explicit commit marker because their book digest stays unchanged.

The diagnostic `.lock` file persists after release: do not delete it. All
cooperating project writers use its OS lock. Snapshot checks detect many external
writes, but an external program ignoring the lock can still write in the interval
between a check and replacement; this is not filesystem compare-and-swap.

For an interrupted write, preserve the journal and staging files. Retry a supported native operation only once the lock owner has exited. If the installed binary refuses recovery, leave the evidence intact and report the work directory and reason. Do not invoke private writer internals.

## `qa check` says `semantic-unverified` or `semantic-rejected`

`semantic-unverified` means required meaning or fluency confirmation is absent. `semantic-rejected` means the evidence does not apply to the current revision or still has a refusal. Record the complete bound replies again through `revayat-novel meaning` and `revayat-novel fluency`, following [review-contracts.md](review-contracts.md). Never copy an old approval to a changed book.

If publication is pending, add the translated title, author, or other missing prose through supported locked operations. Run `revayat-novel falint fix` before the review. Any edit after approval needs fresh evidence before delivery.

## CLI execution logs

CLI calls write a unique UTF-8 `revayat-novel_YYYY-MM-DD_HH-mm-ss_UTC.log`, with
a collision suffix when necessary. Logs contain UTC levels, startup/runtime,
exit status and sanitized exception locations; manuscript text, arguments and
exception payloads are not recorded. Handles close on success and failure.

Set `REVAYAT_NOVEL_LOG_DIR` to the translated file's parent directory. Without
it, the CLI does not select a global log location. Every using agent must also
write the workflow log required by [preservation-and-logging.md](preservation-and-logging.md)
in that same output directory. If CLI logging cannot start, the console reports
that limitation and retains the command's real result; the agent must resolve
the logging failure before continuing translation.
Logs are local diagnostic evidence; review them before sharing and remove old
runs when no longer needed.

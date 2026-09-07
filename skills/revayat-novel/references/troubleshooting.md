# Troubleshooting

## Merge says `ok: false`

The worksheet protocol was broken. The report tells you which way:

- **`missing_units`** — ids the worksheet asked for that never came back. The
  sub-agent dropped, merged or renamed a header. Re-run that chunk with the
  output-format rule restated.
- **`unknown_units`** — ids in the output that were not in the worksheet. The
  sub-agent invented one, or continued numbering past the end. Re-run.
- **`missing_outputs`** — no `out_chunkNNNN.md` at all. The sub-agent failed or
  was never launched. `chunk status` lists what is still pending.

Never patch `book.json` by hand to work around this. Merge is idempotent, so
fixing the worksheet and re-running is always safe and keeps the run
reproducible.

## `UnicodeEncodeError` printing Persian

A Windows console defaulting to a legacy code page. Every script calls
`use_utf8_stdio()` first, so this should not happen from the CLI. If you see it
from your own wrapper, set `PYTHONIOENCODING=utf-8`.

## The Word file opens, but the table of contents is empty

Word only fills a TOC field when fields update. The builder sets
`w:updateFields`, so Word offers on open — say yes, or press `Ctrl+A` then `F9`.

If a viewer never updates fields, the cached hyperlink list is still there and
still clickable; only the page numbers are missing.

## Persian letters are disconnected, or words run backwards

The renderer lacks bidi and Arabic shaping. This is a property of whatever is
displaying the file, not of the file. Open it in Word or LibreOffice.

If it happens there too, check the document really has `w:bidi` —
`qa docx` reports `no-rtl` when it does not — and that you did not build with
`--no-rtl`.

**Do not "fix" it by reversing text.** See `persian-typography.md`.

## Persian shows as boxes or the wrong shape

Missing glyphs, which is a font problem, not a direction problem. Rebuild with
`--font Tahoma` (present on every Windows machine) to confirm, then install
Vazirmatn or B Nazanin if you want a proper book face.

## The book came out much shorter than expected

Almost always one of:

1. **OCR was skipped.** Check `probe.kind` and whether the extract report shows
   `ocr.skipped`. A `scanned` or `mixed` PDF run with `--ocr off` yields only
   the pages that already had text.
2. **Chunks were never translated.** `chunk status` shows `pending`.
3. **Omissions inside chunks.** `qa check` reports `possible-omission` where a
   target is far shorter than its source.

## `asset-modified` from `qa check`

An image on disk no longer matches the SHA-256 recorded at extraction. Something
edited or re-encoded it. Re-extract, or restore the file — do not clear the
hash, which is the only thing proving the picture is the book's own.

## OCR is very slow, or times out

A long scanned book legitimately takes a long time. The default ceiling is 5400
seconds; raise it with `--ocr-timeout`. If it is genuinely stuck, split the PDF
and run the halves.

`ocr.pdf` is reused on a second run, so a completed OCR is never repeated.
`--force-ocr` overrides that.

## Headings are wrong

PDF heading detection is a font-size heuristic and does not always survive an
unusual design. Fix the levels in `book.json` before chunking — that is the one
kind of manual edit that is expected, because it is extraction output rather
than translation output. Re-run `chunk build` afterwards.

For a book where the heuristic fails badly, MinerU's layout model does better:
see `extraction.md`.

## A name drifted anyway

`qa check --glossary` reports `glossary-drift` only for entries with
`locked: true`. Unlocked entries are advisory. Lock the ones that matter, and
re-run the chunks the report names.

If a sub-agent's rendering is genuinely better than the glossary's, update the
glossary and re-run the *earlier* chunks instead — consistency matters more than
which of two good renderings won.

## Emphasis parity warnings everywhere

The source's emphasis marks were not carried into Persian. Usually the
sub-agent's prompt was truncated or the model summarised. Spot-check one chunk:
if the Persian is otherwise good and only the markers are gone, re-running that
chunk with the format rule restated is enough.

Occasional single warnings are normal — Persian sometimes needs the emphasis on
a different number of words. Systematic warnings are a prompt problem.

## Images are missing from a scanned book

In a scan, an illustration is usually part of the page raster rather than a
separate PDF image object, so there is nothing to extract. Use MinerU, whose
layout model finds and crops the figure regions — see `extraction.md`.

## `TesseractConfigError` / `read_params_file: Can't open hocr`

`TESSDATA_PREFIX` points at a directory that holds language files but not
Tesseract's `configs/` folder. Copy the whole `tessdata` directory, not just the
`.traineddata` files. Nothing is wrong with the book.

## OCR produced almost no text from a scan

Check `clean_scan` in the extract report. If pages were cleaned and the text is
still missing, re-run with `--clean-scan off` and compare: the cleaner should
never blank a page, and if it does that is a bug worth reporting. Otherwise the
scan is simply too poor for Tesseract — try MinerU (see `extraction.md`).

Also confirm the language: a Persian scan read with the default `--ocr-lang eng`
returns very little. Use `--ocr-lang fas`.

## The watermark is still faintly visible

Expected, and documented. Colour is removed; the grey remnant where the stamp
overlapped text cannot be separated from the text without damaging it.
`--ghost-threshold 120` removes the remnant at the cost of eroding the letters
underneath. It does not affect the Word output, which is built from the
recognised text rather than the page image.

## A whole illustration page was left with its watermark

The cleaner skips pages whose coloured fraction looks like artwork rather than a
stamp, to avoid wiping real pictures. `--clean-scan force` overrides that for a
page you know is text.

## `pages build` refuses with `source-pdf-unavailable`

The book was read from a PDF and that PDF is no longer where `book.json` says
it is. The page route cuts one real PDF per source page so a reviewer can set
the translation beside the page it came from; without the file it cannot cut
any of them.

It refuses rather than carrying on, because carrying on used to produce a page
run with an empty `reference_pdf` and an empty `source_pdf` on every page —
which every later gate read as *a format that has no source pages*, exactly
what a DOCX or an EPUB looks like. The book then reached `accepted` with
nothing ever compared against it. **Losing the source file is not a new source
format.**

Put the PDF back where the book names it, or beside `book.json`, and run
`pages build` again. If the file is genuinely gone, re-run `extract` against
the copy you do have.

## `render-qa` says `source-missing` or `source-hash-mismatch`

Two different diagnoses about the same artefact, `pages/source/page-NNNN.pdf`:

- **`source-missing`** — the one-page PDF `pages build` cut is not there. Run
  `pages build` again; it is safe on an existing run and re-cuts the sources.
- **`source-hash-mismatch`** — a file *is* there and it is not the one the
  manifest committed to. Something replaced it, and rendering it would compare
  the translation against the wrong page. `pages build` again restores it.

Either way the page comes back `unverified`, and `pages review` and `pages
accept` both refuse until it is resolved. That is the gate working: the page
route accepts a page by *comparison*, and every deterministic check reads only
the translated side, so all of them would pass on a page nobody set beside its
source.

`--source-pdf` will not get you past this on a page that has a manifest — the
manifest's own artefact wins, deliberately, because an override let any
readable PDF stand in as a page's evidence. It is still honoured where there is
no page run at all, which is how one-off diagnostics work.

## LibreOffice is installed, but `render-qa` says it is not

`wordrender` looks for `soffice` on `PATH` first, then in the places an ordinary
install leaves it without touching `PATH`:

```text
/Applications/LibreOffice.app/Contents/MacOS/soffice     macOS cask / .dmg
/usr/local/bin/soffice · /opt/homebrew/bin/soffice        Homebrew links
C:\Program Files\LibreOffice\program\soffice.exe          Windows installer
C:\Program Files (x86)\LibreOffice\program\soffice.exe
```

The macOS cask is the one that bit: it installs into the app bundle and adds
nothing to `PATH`, so a Mac with LibreOffice installed the normal way reported
it missing and every render check skipped itself. Found by running the full
suite on a macOS runner with nothing allowed to skip.

If yours is somewhere else — a portable build, a distro that installs under
`/opt/libreoffice*/program` — put that directory on `PATH` or symlink `soffice`
into one of the locations above. `doctor` prints the answer under
`optional_tools.render`, and it is the same answer the pipeline uses: the two
are one function, so they cannot disagree about a machine.

On Windows, Word is preferred when `pywin32` is installed; LibreOffice is the
fallback there, not the first choice.

## `extract` refuses an EPUB or DOCX with `ArchiveTooLarge`

Both formats are zip files, and a zip's members *declare* their own unpacked
sizes — a few kilobytes of well-compressed zeros can announce gigabytes and fill
the disk when a reader inflates them. The readers therefore check the central
directory before opening anything, against three ceilings:

| ceiling | value | what it stops |
| --- | --- | --- |
| members | 20 000 | a zip of a hundred thousand empty files |
| total unpacked | 2 GB | a handful of members that add up to a disk |
| one member's inflation | 400:1, only for members over 8 MB | a single large run of zeros |

A real novel is a few thousand members and a few hundred megabytes at the
outside, so these clear it by an order of magnitude. The inflation test applies
only to members over 8 MB on purpose: a 100 KB chapter of repetitive XHTML was
measured compressing 558:1 and is harmless — the cost of inflating it is 100 KB.

If a genuine book trips this — a scanned facsimile EPUB with thousands of page
images, say — the message names which ceiling and which member. Open the archive
with any zip tool and look at that member before raising a limit; the ceilings
are constants at the top of `bookir.py`, not command-line flags, because a book
that needs them raised is rare enough to be worth a moment's attention.

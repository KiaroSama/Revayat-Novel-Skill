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
Vazir or B Nazanin if you want a proper book face.

## The Persian came out in Calibri or Times New Roman, but the font is installed

The renderer could not use the face that was asked for, and until recently
nothing said so. `render-qa` and `doc-qa` now report it as `font-fallback`,
naming what was requested and what arrived.

Two causes, both measured:

- **The font is a *variable* font.** Word will not resolve one for a
  complex-script run. `Vazirmatn` is usually installed this way
  (`Vazirmatn-VariableFont_wght.ttf`), and a book asking for it came back set
  in Calibri on a machine that had it. Install a static build — `Vazir` is the
  default for exactly this reason — or use `--font Tahoma` to confirm the
  pipeline is otherwise fine.
- **The machine has no Persian face at all.** Common on a Linux or macOS CI
  runner, where the render is still useful for checking structure but is not
  what a reader will see. The warning says so rather than failing the run.

A fallback is not only ugly: its metrics differ, so every geometry finding in
the same report describes a page nobody will get.

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

## `render-qa` or `doc-qa` reported a code on a page

These come from the one source page laid out on its own (`render-qa`) or from a
page of the assembled book (`doc-qa check`). Both measure geometry against what
`book.json` says belongs there.

| Code | What it means | What to do |
| --- | --- | --- |
| `text-missing` | a block that belongs on this page is not on the rendered page | re-translate that page; if it recurs, the block has no Persian yet |
| `text-duplicated` | a block appears more than once on one page | a worksheet reply was pasted twice; re-run that page |
| `text-clipped` | text runs off the edge of the paper | a long unbreakable run — usually a URL or a Latin name; check that paragraph |
| `text-overflow` | text is on the page but outside the body area | a style lost its indents; re-build |
| `text-image-overlap` | text is printed on top of a picture | re-build; if it recurs the illustration is wider than the text block |
| `blank-region` | a hole in the body area, or nothing on the page at all | the build failed partway, or a plate pushed a page empty; re-build and look |
| `image-missing` / `image-extra` | the page carries fewer or more illustrations than it owns | re-extract, then re-build |
| `image-reordered` | the right pictures in the wrong order — a caption now sits under the wrong plate | re-build from `book.json` |
| `image-aspect` | an illustration's shape is not the book's | the picture was resized; re-extract |
| `preview-empty` | the page's preview rendered no pages at all | the renderer produced nothing; check `doctor` under `optional_tools.render` |
| `font-unverified` (warning) | the render reported no font names, so which face was used was not checked | nothing to fix; it describes the renderer, not the book |
| `font-fallback` (warning) | the page asked for one face and was set in another | install the face, or build with `--font Tahoma` — see the section above |

`font-unverified` and `font-fallback` are the two codes that describe **the
machine that rendered**, not the book, so neither decides the verdict — see
`MACHINE_DEPENDENT_CODES` in `docqa.py`.

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

## `RenderTooLarge`, or `render-qa` says a page could not be rendered

A PDF page declares its own size, and the renderer draws whatever it is told:
a legal 200-inch page is 900 megapixels at 150 dpi and 6.4 gigapixels at the
400 dpi the illustration crop uses. Measured, PyMuPDF renders a 40-inch page at
150 dpi (36 Mpx, 103 MB) in 0.02 s without a word, so the ceiling is this
pipeline's, not the library's: **400 megapixels per render**, checked from the
declared page size before a pixel exists. A whole A4 sheet at 1200 dpi is about
140 Mpx, so no real book page is anywhere near it.

Where it shows up depends on the stage. `render-qa` and `doc-qa` report the page
as *not rendered* and therefore `unverified` — the same word they use for a
missing converter. `extract` on a scan (the OCR sidecar and the illustration
crop) raises `RenderTooLarge` and names the page and its size, because a scan
with a page like that is not a book and OCR silently skipping a page is worse
than stopping.

Pillow's own decode ceiling (`Image.MAX_IMAGE_PIXELS`, about 89 Mpx) covers the
other side — an image file that declares an absurd size — and is left at its
default on purpose. Raise a ceiling if a genuine oversized plate needs it; never
remove one.

## `ImageTooLarge` — an image was refused before it was decoded

An image file declares its own pixel size, and a 66-byte PNG can declare
60000x60000 and cost ten gigabytes to decode. Pillow's ceiling
(`Image.MAX_IMAGE_PIXELS`, about 89 megapixels) covers that — but it has two
bands, and only one of them refuses. Above twice the ceiling it raises; between
one and two times it merely prints a warning and decodes anyway. Measured: a
PNG declaring 15000x9000 (135 Mpx, some 400 MB) opened with one line on stderr
that nothing reads.

Every decode in this pipeline goes through `open_image`, which turns both bands
into this one named refusal. A genuine plate that large is rare; if you have
one, raise `Image.MAX_IMAGE_PIXELS` deliberately for that run. Never set it to
`None` — that is the whole ceiling gone, not a bigger one.

## `TooManyPages` — a PDF declares more than 20 000 pages

A PDF can declare millions of empty pages in a few megabytes, and the page route
writes one file per page. The longest real books run to about two thousand
pages, so the ceiling is ten times that. It is checked from the page count
alone, before the first page is read. If a real book trips it, the constant is
`PDF_MAX_PAGES` in `bookir.py`; look at the file first, because a book that
long is more likely a concatenation of several than one volume.

## Tesseract, Ghostscript or MinerU is installed, but the tool says it is not

Being on `PATH` is a different question from being installed, and on Windows it
is usually the wrong one. `extract.find_tool` therefore asks three questions in
order: is it on `PATH`; is it in the script directory of the interpreter running
the skill; is it where an ordinary installer leaves it?

```text
<drive>\Program Files\Tesseract-OCR\tesseract.exe          winget / installer
<drive>\Program Files\gs\gs*\bin\gswin64c.exe              Ghostscript, versioned
<drive>\Program Files\MinerU\venv\Scripts\mineru.exe       MinerU in its own venv
/opt/homebrew/bin/tesseract, /usr/local/bin/gs             Homebrew, MacPorts
~/.local/bin/mineru                                        pip --user, pipx
```

`<drive>` is every fixed drive, not just `C:` — a large optional tool is
routinely installed elsewhere, and the MinerU this was measured against lives on
`G:`. A `<drive>` pattern is skipped off Windows; every other pattern is tried
anywhere, with `~` expanded.

The interpreter's script directory is the general form of what
`extract.find_ocrmypdf` already did for its own tool: a virtual environment is
routinely absent from `PATH`, so a tool installed beside the skill's own
dependencies is invisible to a `PATH` search on every platform.

**`doctor`, the `ocr-sidecar` stage and the OCR test tier all call that one
function**, so they cannot disagree about a machine. They used to: each asked
`shutil.which` on its own, so a machine with all four tools installed was told
it had none, `ocr-sidecar` refused to run with "tesseract was not found on
PATH", and ten OCR tests skipped themselves while reporting success.

If yours is somewhere else — a portable build, a custom prefix — put its
directory on `PATH`. `doctor` prints what it found under `optional_tools`, and
that is the same answer the pipeline will get.

# Extraction, OCR and difficult books

## The three shapes a PDF arrives in

`revayat-novel extract` probes every page and classifies the file before doing
anything:

| `probe.kind` | What it means | What happens |
| --- | --- | --- |
| `digital` | ≥92% of pages have a text layer | read directly, no OCR |
| `scanned` | no page has a text layer | OCRmyPDF with `--force-ocr --deskew` |
| `mixed` | some pages have text, some do not | OCRmyPDF with `--skip-text` |

`mixed` is the common case for older titles and the reason page-level detection
matters. Running OCR over pages that already carry good text replaces accurate
characters with recognised ones — strictly worse. `--skip-text` leaves those
pages alone and only works on the images.

Deskew is only applied to fully scanned books. It rewrites the page raster, so
on a mixed book it would damage the pages that were fine.

## Image fidelity

OCR is invoked with `--optimize 0 --output-type pdf`. That is deliberate:
OCRmyPDF's default is a PDF/A rewrite with image optimisation, which
re-encodes the illustrations. Here the pictures matter more than archival
conformance.

Images then come out of the PDF via `Document.extract_image`, which returns the
*original compressed stream* — not a re-render of the page. The picture in the
Word file is byte-identical to the picture in the book, and `qa check` verifies
that with a SHA-256 recorded at extraction time.

Physical geometry comes from `page.get_image_rects`, so a 4.2 cm illustration
is placed at 4.2 cm rather than stretched to the text width.

## When OCRmyPDF is not installed

The error names the fix:

```bash
pip install ocrmypdf

# Tesseract, the recognition engine:
winget install tesseract-ocr.tesseract     # Windows
brew install tesseract                     # macOS
sudo apt install tesseract-ocr             # Debian/Ubuntu

# Ghostscript. Verified 2026-09-05: it is NOT in the winget default
# source, so on Windows take the installer from
# https://ghostscript.com/releases/gsdnld.html and put its bin/ on PATH.
brew install ghostscript                   # macOS
sudo apt install ghostscript               # Debian/Ubuntu
```

`revayat-novel.py doctor` reports all three. On Windows it looks for
`gswin64c`/`gswin32c` as well as `gs`, because Ghostscript does not ship a
binary called `gs` there.

OCRmyPDF is also found when it is installed in the same interpreter as the
skill's other dependencies, even if its script directory is not on PATH.

`--ocr off` proceeds without it, extracting only the pages that already have a
text layer. Say so explicitly to the user — a book that comes back suspiciously
short is usually this.

## Colour watermarks on a scan

A scanned book is one raster per page, so a watermark is burned into the pixels:
there is no image object to drop and no text run to filter. It also degrades OCR
wherever it crosses a line.

`extract` removes it automatically before OCR. The signal is narrow and
measured: body text in a scan is *grayscale* — on a real 1785x2577 page every
text pixel had HSV saturation 0 — while a colour watermark reaches 255. So
"saturated pixels" selects the watermark and nothing else.

Two guards stop it eating real content:

- a page is only cleaned when its coloured fraction is small. A watermark
  measured 0.24% of a page; an illustration is orders of magnitude more, so
  artwork pages are skipped automatically. On a real 70-page book this cleaned
  52 pages and correctly left 18 illustration pages alone.
- only saturated pixels change. Grayscale content is untouched.

| Flag | Effect |
| --- | --- |
| `--clean-scan auto` | default: clean pages that look like text with a stamp |
| `--clean-scan off` | never touch the raster |
| `--clean-scan force` | clean even pages that look like artwork |
| `--ghost-threshold N` | also whiten grey pixels lighter than N (0-255) |

**Where the watermark lies on top of text, removal is lossy and cannot be
otherwise.** Those glyph pixels were blended with the stamp when it was applied,
so the original ink value is not in the file any more. `--ghost-threshold 120`
does erase the grey remnant, but it eats the letters underneath it too. The
default therefore removes colour only. For this pipeline that is usually moot:
the deliverable is built from OCR'd *text*, so the watermark never reaches the
Word file either way — what matters is which version Tesseract reads better.

## A scanned page is not an illustration

In a scanned book every page *is* one full-page raster. Emitting those as
pictures would put the whole book into the output twice — once as the recognised
text, once as photographs of the same pages. Measured on a real 70-page scan,
that produced a 13.8 MB DOCX instead of 2.6 MB, showing every page a second time.

So a page-sized image is dropped when the page also yielded real text: its
content is now the text. A page-sized image on a page with little or no text is
kept, because that is a genuine full-page plate — a cover, a frontispiece, an
illustration. On the same book this dropped 56 pages and kept 14.

`source.page_scans_dropped` in the extract report says how many went.

What this cannot do is separate a picture that sits *inside* a page of text: the
whole page is one flat raster, so the illustration and the words around it are
the same pixels. Such a page is kept whole, and its captions are also recognised
as text — so they can be translated — which means those few pages show their
original wording inside the picture as well. **This is what MinerU is for**: its
layout model crops the figure out, so the Word file gets the picture and the
translated caption instead of a flat page image with the source language baked
into it. `--from-mineru` imports that.

## Languages other than English

Pass the Tesseract language code: `--ocr-lang fas` for Persian, `ara`, `deu`,
`fra`, and so on. `tesseract --list-langs` shows what is installed; extra
languages are `.traineddata` files from the `tessdata` or `tessdata_best`
repositories, dropped into Tesseract's own `tessdata` directory.

If you point `TESSDATA_PREFIX` at a directory of your own, **copy the whole
`tessdata` folder**, not just the language files. It also contains `configs/`,
and without those Tesseract fails with the misleading
`read_params_file: Can't open hocr`, which OCRmyPDF reports as
`TesseractConfigError`.

## When the extraction is poor

Symptoms and what they mean:

| Symptom | Cause | Fix |
| --- | --- | --- |
| Body paragraphs classified as headings | the book's body font is unusually large relative to the running text | check `source.body_font_pt`; correct levels in `book.json` before chunking |
| Chapter titles missed | the title is set in the same size as body text and does not start with a chapter word | add the heading manually, or use MinerU |
| Running heads inlined into paragraphs | the head sits outside the top/bottom 8% band | see `source.running_heads_dropped` to confirm what *was* removed |
| Illustrations missing entirely | in a scanned book the picture is part of the page raster, not a separate PDF image object | use MinerU — layout detection can find the figure region |
| Text in reading order but garbled | multi-column layout | use MinerU |

### Importing a stronger extractor

Neither of these is reimplemented here; the adapters import their output.

```bash
# MinerU: OCR + layout analysis + figure extraction
mineru -p book.pdf -o work/mineru
$PY scripts/revayat-novel.py extract --from-mineru work/mineru --out work/
```

```bash
# Marker or Docling, or any Markdown you already have
$PY scripts/revayat-novel.py extract --from-markdown work/book.md --out work/
```

The Markdown importer resolves `![alt](path)` images relative to the Markdown
file and copies them into `work/assets/`, so the rest of the pipeline is
unchanged.

## EPUB and DOCX

EPUB is the best source when you have a choice: it is already structured, so
headings, emphasis, block quotes, lists and image references need no guessing.
Spine order is authoritative. Footnotes are recovered from
`epub:type="noteref"` links and from `<sup><a href="#id">` markers, with the
note body pulled from the element the link points at and its leading `1.`
stripped — Word numbers footnotes itself.

DOCX input reads paragraphs, runs and styles through python-docx, and reads
`word/footnotes.xml` and `word/endnotes.xml` directly because python-docx has
no API for either. Section breaks travel as `book["sections"]`, each entry
naming the block it opens at and carrying that section's page size,
orientation, margins, gutter and header/footer distance; the builder puts the
breaks back. `book["page"]` still reports the first section's geometry, exactly
as it did when that was the only geometry the reader kept, so nothing
downstream had to change. Both become footnotes in the Persian edition: that is where
a Persian reader looks for a note, and it is what the builder writes. The two
id spaces are kept apart — Word numbers footnotes and endnotes separately, so a
book with a footnote 1 and an endnote 1 has two notes, not one.

`book["source"]["docx_warnings"]` says what the reader could not carry across
in full. None of them stops the import; each one exists so the loss is visible
rather than discovered by a reader of the finished book.

| Warning | What it means |
| --- | --- |
| `table-merged-cells` | a table had merged cells; the spans are recorded, so check the rebuilt table looks right |
| `section-columns-dropped` | that section is set in two or more columns; its page size, orientation and margins are carried but the Persian edition is set in one |
| `hyperlinks-kept-as-metadata` | every target is on its block as `links`; a live link goes back only where the translation kept the display phrase word for word, and each one that could not be placed is named |
| `running-heads-dropped` | the source had headers or footers; the Persian edition generates its own instead of copying an English running head onto a Persian page |

## What the Book IR holds

`work/book.json` is the source of truth. Markdown deliberately is not — it
cannot carry image geometry, footnote identity or page setup.

```json
{
  "id": "b00042", "type": "paragraph", "page": 12,
  "bbox": [72, 140, 540, 220],
  "text": "He whispered, *do not look back*.[[fn:fn0003]]",
  "target": null
}
```

```json
{
  "id": "b00043", "type": "image", "page": 12,
  "asset": "p0012-img002.png",
  "sha256": "…",
  "width_pt": 392.0, "height_pt": 230.0,
  "pixel_width": 1200, "pixel_height": 705,
  "alt": "", "target_alt": null
}
```

Block types: `heading` (with `level`), `paragraph`, `blockquote`, `listitem`,
`caption`, `verse`, `image`, `pagebreak`, `separator`.

Only `target`, `target_alt`, `meta.title_target` and `meta.author_target` are
ever written by translation. Everything else is extraction's output and stays
fixed — which is what makes a re-run reproducible.

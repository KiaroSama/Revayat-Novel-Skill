# Extraction, OCR and difficult books

## The three shapes a PDF arrives in

`revayat extract` probes every page and classifies the file before doing
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

`revayat.py doctor` reports all three. On Windows it looks for
`gswin64c`/`gswin32c` as well as `gs`, because Ghostscript does not ship a
binary called `gs` there.

OCRmyPDF is also found when it is installed in the same interpreter as the
skill's other dependencies, even if its script directory is not on PATH.

`--ocr off` proceeds without it, extracting only the pages that already have a
text layer. Say so explicitly to the user — a book that comes back suspiciously
short is usually this.

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
python3 scripts/revayat.py extract --from-mineru work/mineru --out work/
```

```bash
# Marker or Docling, or any Markdown you already have
python3 scripts/revayat.py extract --from-markdown work/book.md --out work/
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
`word/footnotes.xml` directly because python-docx has no footnote API.

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

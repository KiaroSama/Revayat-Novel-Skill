# Extraction, OCR and difficult books

## Choose the intake route

Run `revayat-novel doctor` and start the workflow log beside the intended Word file. Preserve the original and use a new work directory for the first extraction.

```sh
revayat-novel extract "source/book.pdf" --out "translation work"
```

The native PDF route uses `lopdf` for PDF objects and `hayro` for page interpretation and rasterization. Inspect the extraction report and compare the Book IR with the source. Record page count, dimensions and orientation, text order, notes, and image locations. A selectable text layer can still be incomplete or garbled.

| PDF | What to check |
| --- | --- |
| Digital | Read the native text and verify its reading order and coverage. |
| Scanned | Compare recognized text against every relevant source page. Keep the original scan. |
| Mixed | Retain good native text. Check textless and garbled pages individually before deciding which need OCR. |

A textless page may be a plate, a blank, or a scan containing prose. Do not infer its role from the overall share of textual pages. A plate or blank needs evidence of its role. Unknown coverage blocks delivery.

## Source-complete OCR evidence

`revayat-novel doctor` reports optional Tesseract availability. Run the OCR sidecar command with the source PDF and work path. Use `revayat-novel ocr-sidecar --help` for source-language options; recognition needs the source language rather than Persian by default.

```sh
revayat-novel ocr-sidecar "source/book.pdf" --out "translation work"
```

Install required language data through the operating system's Tesseract installation. If the tool or language data is missing, record the omitted coverage. Review page identities, geometry, and recognized words against the original. Pay particular attention to names, numbers, page edges, columns, and mixed-script passages. Keep the sidecar and its source identity; incompatible or partial OCR evidence does not authorize a full book. Keep usable native text and do not infer bold or italics from OCR font styles. If the sidecar or scan processing produced a replacement page, verify that its identity and placement match the source before reuse.

## Image fidelity

Retain the original assets byte for byte, with encoded format, SHA-256, pixel dimensions, and authored placement where available. A resized drawing may still refer to original image bytes. If the illustration is part of an entire scan rather than a separate image object, any crop is a derivative. Keep the source page intact and record the crop coordinates and source identity. Never label a crop, deskewed image, or cleaned scan as the original.

If a watermark or poor scan interferes with recognition, follow [watermarks.md](watermarks.md) and use `revayat-novel clean-scan survey`, `revayat-novel clean-scan preview`, and `revayat-novel clean-scan run` on separate work copies. Compare the result and use a cleaned copy only for the role it was verified to serve.

## When extraction is incomplete

Verify chapter boundaries, tables, columns, captions, and embedded text against the source before dispatching worksheets. If the source cannot be represented faithfully, keep the original, record the affected units, and obtain a faithful supported copy or transcribe uncertain content under review. Do not synthesize missing source text from context. A corrected or reimported source goes into a new work directory; it does not overwrite an existing translation.

Use the PDF page route or the pageless chunk route described in [multi-agent-workflow.md](multi-agent-workflow.md). Keep the title, names, and specialist terms pending until Stage 1 checks their sources and locks the chosen forms.

## EPUB and DOCX

EPUB is the best source when you have a choice: it is already structured, so
headings, emphasis, block quotes, lists and image references need no guessing.
Spine order is authoritative. Before dispatch, follow
[EPUB integrity and recovery](epub-integrity.md): restore missing chapters,
images or note targets in the source package, never delete their references.
An unspecified source language uses EPUB metadata; an explicit override wins.
Footnotes are recovered from
`epub:type="noteref"` links and from `<sup><a href="#id">` markers, with the
note body pulled from the element the link points at and its leading `1.`
stripped - Word numbers footnotes itself.

An ordinary link's target travels on its block as `links`, the same field the
DOCX reader fills, so one place in the builder rebuilds both. The spine is one
book, so a link into another spine document is an *internal* link once the
documents are concatenated: `c2.xhtml#later` becomes `#later`, and the anchor it
points at is carried on the block that owns it. Only the anchors something
actually links to are kept - a book is full of ids nothing points at. A link to
a whole document with no fragment has nothing to point at once the file
boundaries are gone; the words stay, the target does not, and
`link-to-a-whole-document` in `book["source"]["epub_warnings"]` says so.

DOCX input reads the OOXML package directly. Check body order, inherited styles, section geometry, native notes, tables, hyperlinks, running heads, and drawing relationships. For content that the importer refuses or cannot preserve, use the recovery notes in [docx-intake-integrity.md](docx-intake-integrity.md). Resolve defects in a faithful copy, and retain the original.

## What the Book IR holds

`book.json` is authoritative. It tracks the extracted source, chapters, ordered blocks and runs, assets, notes, and page geometry where available. Assets retain original checksums and dimensions. Do not rebuild from a Markdown export or edit the structured book by hand. Use the locked merge and editorial operations in the [six-stage workflow](multi-agent-workflow.md).

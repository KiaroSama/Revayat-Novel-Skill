# Native PDF Book Workflow

Use this bundled route for digital, scanned or mixed PDF books. It integrates the
book-relevant PDF Processing Pro workflow principles through Revayat Novel's own tools:
validate first, retain originals, bound processing and inspect actual results.
No separately installed PDF skill or unshipped upstream helper is required.

## Inspect before processing

Start the workflow log beside the intended translated output. Preserve original
bytes and record the input identity. Inspect page count, dimensions, orientation,
text coverage and illustrations. A selectable text layer can still be wrong; a
textless plate is not automatically a failed OCR page. Password-protected or
unreadable inputs require a usable authorized source, not silent blank output.

Run `revayat-novel doctor`, then `revayat-novel extract <input.pdf> --out <work>`. The native engine uses `lopdf` to inspect PDF objects and `hayro` to interpret and rasterize pages. Check text order and coverage even if the PDF is searchable.

## OCR only where it is needed

Use optional Tesseract for missing or unusable text. Run `revayat-novel ocr-sidecar <input.pdf> --out <work>` and check the installed command's help for source-language options. Verify recognition against the actual page, especially names and numbers. Keep native text where it is accurate. A raster may be an illustration or blank page; account for its role rather than fabricating text. Preserve the original image bytes and dimensions. When a scan contains the image and prose together, any extracted crop is a logged derivative.

See [extraction.md](extraction.md) for intake and [watermarks.md](watermarks.md) for reviewing candidate scan derivatives.

## Translate and compare pages

Use page jobs, keeping the source page artifact and all its split worksheets.
Each part must answer its exact current request. Translate all parts before
merging that page. Shared names, voice and neighboring context apply throughout;
logs identify the page/part and any correction without copying the manuscript.

Run the native page preview/render checks, inspect the source/target comparison
and record real visual answers before acceptance. Persian may reflow onto several
target sheets; inspect each one. Equal page count is not a preservation guarantee.
Retain measured physical dimensions and image pixels; report missing trim data
instead of claiming a guessed value came from the source.

## Batch, failure and delivery

Process books in separate work directories and log each run beside its output.
Use the existing per-page status/next commands to resume. Do not count a failed
page as done, overwrite the source to retry, or continue after an empty extraction.
Keep diagnostics for unresolved failures. Bound external processes and avoid
running several OCR/render workers against the same mutable book.

Complete meaning and fluency review, build with the bundled Vazirmatn font, and run final package and render checks through the [six-stage workflow](multi-agent-workflow.md). The optional rendered PDF is derived from the reviewed Word output; it does
not replace the editable Word deliverable or the required adjacent agent log.

Engine documentation: [hayro](https://docs.rs/hayro/latest/hayro/) and [lopdf](https://docs.rs/lopdf/latest/lopdf/).

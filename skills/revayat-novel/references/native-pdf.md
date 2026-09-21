# Native PDF Book Workflow

Use this bundled route for digital, scanned or mixed PDF books. It integrates the
book-relevant PDF Processing Pro workflow principles through Revayat's own tools:
validate first, retain originals, bound processing and inspect actual results.
No separately installed PDF skill or unshipped upstream helper is required.

## Inspect before processing

Start the workflow log beside the intended translated output. Preserve original
bytes and record the input identity. Inspect page count, dimensions, orientation,
text coverage and illustrations. A selectable text layer can still be wrong; a
textless plate is not automatically a failed OCR page. Password-protected or
unreadable inputs require a usable authorized source, not silent blank output.

Use `extract` from the main skill and review its report. PyMuPDF handles digital
text, geometry and original embedded images. Source page variants remain part of
the book; do not rasterize the entire document merely to simplify extraction.
See [extraction.md](extraction.md) for the actual options and import routes.

## OCR only where it is needed

Select the language printed in the source, including required installed OCR packs.
The native extraction route distinguishes digital, scanned and mixed inputs;
preserve usable text pages instead of blindly forcing OCR over everything.
OCRmyPDF execution is bounded, and its image optimization is disabled for this
preservation route. Missing engines/languages are prerequisites to resolve.

Use the native OCR sidecar for word confidence and bounding boxes. Low confidence,
source/OCR disagreement and missing content require comparison with the page image;
confidence is a diagnostic, not proof of correctness. Deskewing, denoising or
watermark cleaning happens on a separate derivative and must not erase punctuation,
diacritics, line art or other manuscript evidence. Keep original illustrations.

For complex layouts, tables or figures the direct reader cannot preserve, inspect
the actual page and use the documented external-extraction import route where
appropriate. Validate imported reading order, cell relationships, notes and figure
geometry. Do not claim this skill provides general PDF forms or lossless table
reconstruction. An unsupported structure stays visible until it is resolved.

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

Complete meaning/fluency review and final Word package/render QA through the main
skill. The optional rendered PDF is derived from the reviewed Word output; it does
not replace the editable Word deliverable or the required adjacent agent log.

Implementation: scripts/read_pdf.py, extract.py, ocr_sidecar.py, sourcepages.py,
rasters.py, renderqa.py and docqa.py. This guide is independently authored;
no external scripts or prose are copied. Primary format guidance:
[PyMuPDF image extraction](https://pymupdf.readthedocs.io/en/latest/recipes-images.html).

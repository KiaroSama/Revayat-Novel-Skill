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

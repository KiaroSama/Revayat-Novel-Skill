# Watermarks on a scan

A scanned book is **one raster per page**, so a watermark is burned into the
pixels. There is no image object to drop and no text run to filter: it comes out
of the raster or it stays in the book. It also degrades OCR wherever it crosses a
line of text, which is the reason this matters even though the deliverable is
text rather than pictures.

`extract` removes one automatically. This page is for when you want to see what
it will do first, or when the default was not enough.

## The signal, and why it is narrow

Body text in a scan is **grayscale**. Measured on a real 1785×2577 page, every
text pixel had HSV saturation exactly **0**, while a colour watermark reaches
255. So "pixels with meaningful saturation" selects the watermark and nothing
else - and only those pixels are touched, so grayscale content (text, line art,
black-and-white photographs) comes out byte-identical.

Two guards keep it from eating real content:

| Guard | Value | Why that value |
| --- | --- | --- |
| saturation floor | `16` | text measured 0, so this is a wide margin that still catches faint, heavily transparent watermark edges |
| artwork ceiling | `6%` of the page | a watermark measured **0.24%** of a page; a genuine colour illustration runs orders of magnitude higher, so plate pages skip themselves |

On a real 70-page book that cleaned **52 pages and correctly left 18
illustration pages alone**.

## The three commands

1. `revayat-novel clean-scan survey`: inspect the source PDF and record suspected pages. Keep text pages, image plates, and OCR candidates distinct.
2. `revayat-novel clean-scan preview`: compare before/after renders of representative pages. Check letter edges, accents, halftones, color, and small text. An apparent improvement at full-page zoom can erase strokes.
3. `revayat-novel clean-scan run`: write a separate work copy using the reviewed settings. Use `revayat-novel clean-scan <subcommand> --help` for input/output and page-selection options. Retain the source and log what was changed.

## The part that cannot be fixed

Cleaning changes pixels. A watermark that overlaps ink cannot always be separated without losing part of the letter or picture. Keep the source and retain uncertain details for manual review. Do not modify digital text or an authored illustration because a preview guessed that it contained a watermark.

## Which version to actually use

Try OCR against the original first. Compare the recognized words against the scan before deciding whether a derivative improves recognition.

```sh
revayat-novel ocr-sidecar "source/book.pdf" --out "original OCR"
revayat-novel ocr-sidecar "scan work/cleaned.pdf" --out "cleaned OCR"
```

Tesseract and the source-language data are optional prerequisites for this comparison. Keep both reports separate and record which derivative was inspected. A sidecar produced from different source bytes cannot silently stand in for the original. If the cleaned page helped you read a line, verify the reading against the original source before confirming the translation.

## Preserve assets

The Word book must retain original illustration bytes and dimensions where the source supplies separate assets. Use clean pages to aid recognition or as clearly labeled derivatives. Keep crop/cleaning provenance and never let a cleaned page replace the original illustration inventory. See [extraction.md](extraction.md) and [preservation-and-logging.md](preservation-and-logging.md).

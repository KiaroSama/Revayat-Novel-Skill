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
else — and only those pixels are touched, so grayscale content (text, line art,
black-and-white photographs) comes out byte-identical.

Two guards keep it from eating real content:

| Guard | Value | Why that value |
| --- | --- | --- |
| saturation floor | `16` | text measured 0, so this is a wide margin that still catches faint, heavily transparent watermark edges |
| artwork ceiling | `6%` of the page | a watermark measured **0.24%** of a page; a genuine colour illustration runs orders of magnitude higher, so plate pages skip themselves |

On a real 70-page book that cleaned **52 pages and correctly left 18
illustration pages alone**.

## The three commands

```bash
# 1. What would happen, and to which pages. Reads only; writes nothing.
$PY $SKILL_DIR/scripts/revayat-novel.py clean-scan survey --pdf book.pdf

# 2. Do it. The original is never modified.
$PY $SKILL_DIR/scripts/revayat-novel.py clean-scan run \
  --pdf book.pdf --out $WORK/cleaned.pdf

# 3. One page, before and after, as PNGs you open and compare.
$PY $SKILL_DIR/scripts/revayat-novel.py clean-scan preview \
  --pdf book.pdf --page 12 --out $WORK/watermark/
```

### Start with `survey`

It is the question you cannot answer any other way, and on a long book it saves
an hour. Every page gets one of four verdicts — the cleaner's own, so the survey
cannot disagree with what `run` then does:

| Verdict | Meaning |
| --- | --- |
| `would-clean` | one full-page raster, some colour, under the artwork ceiling |
| `looks-like-artwork` | too much colour to be a stamp; `run` will leave it alone |
| `no-colour` | nothing saturated on the page; nothing to remove |
| `not-a-single-raster` | real text, or several images — not a scan page at all |

```json
{ "pages": 70,
  "counts": { "would-clean": 52, "looks-like-artwork": 18 },
  "artwork_fraction_ceiling": 0.06,
  "coloured_fraction_range": [0.0024, 0.41],
  "per_page": [ { "page": 1, "verdict": "would-clean", "fraction": 0.0024 } ] }
```

`coloured_fraction_range` is the useful number: if your stamped pages sit near
the ceiling, or a plate sits just under it, the verdict for that page is a close
call and worth previewing.

`--pages-listed N` bounds the per-page rows (default 40, `0` for all). When it
truncates it says so in `per_page_truncated` rather than silently showing you
part of the book.

### `run` writes a copy, never edits yours

```bash
$PY … clean-scan run --pdf book.pdf --out $WORK/cleaned.pdf \
  [--force] [--ghost-threshold N] [--quality 92]
```

The report names what happened per page: `cleaned_pages`, `skipped_pages` with
the reason each was skipped, and `untouched_pages`.

**`extract` will reuse this.** If `$WORK/cleaned.pdf` already exists, `extract`
reports `clean_scan: {"reused": …}` and does not redo the work — so a survey, a
preview, a tuned `run`, and then `extract` is a sensible order for a difficult
book.

A page is rebuilt around the cleaned raster rather than having its image stream
patched. That is deliberate and was measured: overwriting a stream in place
leaves the XObject's `/Filter` and `/ColorSpace` describing the *old* bytes, which
produces a silently undecodable image — OCR fell from 864 characters to **zero**
on a page Tesseract had read perfectly well before.

### `preview` is how you judge the lossy knob

```bash
$PY … clean-scan preview --pdf book.pdf --page 12 --out $WORK/watermark/ \
  --ghost-threshold 120
```

Writes `page-0012-original.png`, `page-0012-cleaned.png`, and with a threshold
`page-0012-ghost-120.png`. **Open them side by side.**

These are a tuning aid and nothing more. They are deliberately not part of
`render-qa`'s evidence: a reviewer's five answers are bound to the exact images
they were shown, and re-tuning a threshold must not make every standing review in
the book stale.

## The part that cannot be fixed

**Where a translucent watermark sits on top of text, removal is lossy and cannot
be otherwise.** Those glyph pixels were blended with the stamp when it was
applied, so the original ink value is not in the file any more. No threshold
recovers it.

So the default removes **colour only**: the watermark disappears over blank
paper and leaves a faint grey remnant over text. `--ghost-threshold N` whitens
greys lighter than `N` as well, which erases that remnant — and eats the letters
underneath it, leaving visible gaps. Measured on a real page: in a text-only band
3.7% of pixels are darker than 100 and only 1.4% fall in 100–224, while in the
ghost band the mid-tone share is 15.7%. The ghost is mid-grey; the text is
near-black. That is why a mid-tone cut works at all, and why it is opt-in.

`120` is the documented starting point. Preview it before committing to it.

**A purely grayscale watermark cannot be separated at all.** There is no signal
distinguishing it from the text it overlaps, and nothing here will help.

## Which version to actually use

For this pipeline the trade-off is usually moot: the deliverable is a Word file
built from OCR'd **text**, so the watermark never reaches the output either way.
What matters is which raster Tesseract reads more accurately — and that is worth
measuring per book rather than assuming:

```bash
# Clean at two settings, then compare what OCR actually read.
$PY … clean-scan run --pdf book.pdf --out $WORK/plain.pdf
$PY … clean-scan run --pdf book.pdf --out $WORK/ghost.pdf --ghost-threshold 120

$PY … extract book.pdf --out $WORK/a --ocr-lang eng   # reuses nothing; clean copy
$PY … ocr-sidecar --pdf $WORK/a/ocr.pdf --out $WORK/a --lang eng \
  --book $WORK/a/book.json
```

Then read `summary.confidence` in each `source.ocr.json`. Higher is the version
to keep. A ghost cut that raises confidence was worth it; one that lowers it ate
letters, which is exactly what the warning above predicts.

## Inside `extract`

Unchanged, and still the default path — nothing above is required:

| Flag | Effect |
| --- | --- |
| `--clean-scan auto` | default: clean pages that look like text with a stamp |
| `--clean-scan off` | never touch the raster |
| `--clean-scan force` | clean even pages that look like artwork |
| `--ghost-threshold N` | also whiten greys lighter than N (0–255) |

`clean_scan.cleaned` in the extract report says how many pages were cleaned, and
the cleaned copy is written to `$WORK/cleaned.pdf` with a per-page record of what
was removed and what was left alone.

Run MinerU on `cleaned.pdf` rather than the original, or its layout model crops
the watermark as if it were a figure.

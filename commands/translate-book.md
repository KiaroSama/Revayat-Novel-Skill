---
description: Translate a whole book into Persian and build a professional Word file
argument-hint: <path to .pdf, .epub or .docx> [notes for the translator]
---

Translate the book at `$1` into Persian, following the `revayat-novel` skill end to end.

Any extra words the user typed after the path are their instructions for the
translation — carry them through to every sub-agent alongside the standard
translation policy:

$ARGUMENTS

Work through the skill's stages in order, and **let the source pick the route**
— `SKILL.md` step 4 is explicit that this is not a preference:

- **A PDF** goes down the page route: doctor, extract (with OCR routing if it is
  scanned or mixed), glossary, `pages build`, then one page at a time —
  translate, `pages merge`, `render-qa`, look at the two images, `pages review`,
  `pages accept` — then typography, `qa check`, build, and the two step-9
  checks. The page route is what makes a PDF verifiable: each page is compared
  against its own source page, and `pages accept` refuses a page that was never
  set beside it.
- **An EPUB, DOCX or plain text** has no pages to cut on, so it takes the
  character-budget route: glossary, `chunk build`, parallel translation,
  `merge`, typography, `qa check`, build, verify. That route has no per-page
  comparison, and the skill says so.

Stop and ask before spending a long time on OCR or on a book over roughly
200,000 characters, so the user can confirm the scale first.

Finish with the file path, what is in the finished book, and anything QA flagged
that you chose not to act on.

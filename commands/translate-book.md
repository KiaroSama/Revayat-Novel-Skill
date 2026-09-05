---
description: Translate a whole book into Persian and build a professional Word file
argument-hint: <path to .pdf, .epub or .docx> [notes for the translator]
---

Translate the book at `$1` into Persian, following the `revayat` skill end to end.

Any extra words the user typed after the path are their instructions for the
translation — carry them through to every sub-agent alongside the standard
translation policy:

$ARGUMENTS

Work through the skill's stages in order: doctor, extract (with OCR routing if
the PDF is scanned or mixed), glossary, chunk, parallel translation, merge,
Persian typography, QA, build, verify. Stop and ask before spending a long time
on OCR or on a book over roughly 200,000 characters, so the user can confirm the
scale first.

Finish with the file path, what is in the finished book, and anything QA flagged
that you chose not to act on.

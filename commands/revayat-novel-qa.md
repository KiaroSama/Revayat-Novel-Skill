---
description: Run the Revayat Novel quality gates over a book or a built Word file
argument-hint: [working directory, default ./work]
---

Run the Revayat Novel quality gates for `${1:-work}` and explain the results in plain
language.

`$PY` is the interpreter you resolved for this platform: `python3` on macOS and
Linux, `python` or `py -3` on Windows, where `python3` usually does not exist.

```bash
$PY <skill>/scripts/revayat-novel.py qa check --book ${1:-work}/book.json --assets ${1:-work}/assets --glossary ${1:-work}/glossary.json --review ${1:-work}/review --fluency ${1:-work}/fluency
$PY <skill>/scripts/revayat-novel.py falint lint --book ${1:-work}/book.json
```

`--review` and `--fluency` are the directories the two semantic stages wrote to.
They are how their verdicts are enforced *here*, for the book as it now stands: a
review recorded against an earlier revision comes back `semantic-rejected`. Leave
them out and the report says `semantic-unverified` — a warning naming what was not
asked, never a pass — and `--strict` turns that into an error. `falint lint` must
report nothing: typography runs before the reviews, so anything left to fix means
the approvals describe older text.

If a built `.docx` exists, gate it **twice** — the two answer different
questions and neither substitutes for the other:

```bash
$PY <skill>/scripts/revayat-novel.py qa docx --file out/book.fa.docx --book ${1:-work}/book.json
$PY <skill>/scripts/revayat-novel.py doc-qa check --book ${1:-work}/book.json --work ${1:-work} --docx out/book.fa.docx
```

`qa docx` reads the package: footnotes, bookmarks, image bytes, the TOC field.
`doc-qa check` renders the book and asks what only a rendered page can answer —
nothing off the trim, no hole, no text on a plate — plus the one question no
single page can: is every translated block in the finished book exactly once.

It returns `unverified` rather than passing until somebody has looked at
`${1:-work}/renders/final/pages/`. That is not a soft pass; say so.

For each finding, say what it means for the reader and what you recommend —
distinguish the ones that need a page or chunk re-translated — whichever unit
the book was cut into — from the ones that are cosmetic. Do not simply paste the
JSON.

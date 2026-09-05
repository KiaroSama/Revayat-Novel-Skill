---
description: Run the Revayat quality gates over a book or a built Word file
argument-hint: [working directory, default ./work]
---

Run the Revayat quality gates for `${1:-work}` and explain the results in plain
language.

```bash
python3 <skill>/scripts/revayat.py qa check --book ${1:-work}/book.json --assets ${1:-work}/assets --glossary ${1:-work}/glossary.json
python3 <skill>/scripts/revayat.py falint lint --book ${1:-work}/book.json
```

If a built `.docx` exists, also gate the package itself with `qa docx`.

For each finding, say what it means for the reader and what you recommend —
distinguish the ones that need a chunk re-translated from the ones that are
cosmetic. Do not simply paste the JSON.

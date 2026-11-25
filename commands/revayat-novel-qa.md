---
description: Run the Revayat Novel quality gates over a book or a built Word file
argument-hint: [working directory, default ./work]
---

Run Stage 5 of the [six-stage workflow](../skills/revayat-novel/references/multi-agent-workflow.md) in `${1:-work}`. Check that the Book IR, current meaning and fluency records, locked glossary, and any built Word file refer to the same revision. Use the installed `revayat-novel` help for flags and quote paths containing spaces.

- `revayat-novel falint lint`: check Persian typography in the current book.
- `revayat-novel qa check`: run strict book QA with the current meaning and fluency evidence. A stale or missing approval cannot pass.
- If a `.docx` exists, also run `revayat-novel qa docx`, `revayat-novel doc-qa check`, and `revayat-novel render-qa <file.docx> --out <render-dir>`. Package checks cover OOXML structure, links, and assets. Visual checks need someone to open and inspect the rendered pages.

Run the available checks together against the same revision. Wait for all results before editing. Collect every finding, repair one batch through the coordinator, rebuild with `revayat-novel build` if the candidate changed, and rerun affected checks. Record observed page evidence with `revayat-novel doc-qa review`, then rerun `revayat-novel doc-qa check`. An unreviewed render is unverified. Without a built Word file, state that package and render checks were not run.

Keep results in the workflow log. Explain what each finding means for the reader, where it occurs, and whether it blocks Stage 6. Report the verified scope and any missing coverage.

---
description: Resume an interrupted Revayat Novel run from its working directory
argument-hint: [working directory, default ./work]
---

Resume the translation in `${1:-work}` at the first incomplete or stale stage of the [six-stage workflow](../skills/revayat-novel/references/multi-agent-workflow.md). Read the workflow log and inspect the current Book IR, request manifests, glossary, and review records. Preserve journals, old replies, and independent edits. A persistent lock file alone does not mean a process owns it.

Reuse the recorded serial or parallel choice. If none was recorded, ask before spawning workers and work serially until answered. The coordinator owns shared writes and the log. For a web novel, verify the recorded chapter manifest and source snapshots before continuing. Use the resume mode of `revayat-novel web-import` if offered by the installed version. A changed source is a blocker; do not re-extract over a translated book or claim cached chapters are current.

- PDF page route: run `revayat-novel pages status` and `revayat-novel pages next`. Translate only missing or stale worksheets, use `revayat-novel pages merge`, then compare the rendered source and target pages before `revayat-novel pages review` and `revayat-novel pages accept`.
- Chunk route: use `revayat-novel chunk status` and translate only pending worksheets. For sharded work, validate existing assignments with `revayat-novel batch merge-shards` before `revayat-novel merge <work>`. Rebuild stale requests and generate fresh bound replies; do not change stored request tokens.
- Later stages: check `revayat-novel meaning status` and `revayat-novel fluency status` against the current book. Continue through the remaining stages, rebuilding with `revayat-novel build <work> --out <file.docx>` when needed. A changed book or DOCX needs renewed evidence before delivery.

Use `revayat-novel <command> --help` for options. Report the resumed stage, how many worksheets were already complete and finished this run, and for PDFs how many source pages are accepted out of the total. Keep unresolved blockers and next actions in the workflow log.

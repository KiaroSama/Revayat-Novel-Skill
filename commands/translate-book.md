---
description: Translate a whole book into Persian and build a Word file
argument-hint: <book path, web novel URL or saved chapters> [notes for the translator]
---

Translate the book at `$1` into Persian using the `revayat-novel` skill and its [six-stage workflow](../skills/revayat-novel/references/multi-agent-workflow.md). Carry the user's notes through every stage:

$ARGUMENTS

Run `revayat-novel doctor` before intake. Ask before lengthy OCR, a run over roughly 200,000 source characters, or parallel subagents. Without consent, keep the workflow serial. Keep one work directory and an adjacent workflow log per book.

Use `revayat-novel extract <input> --out <work>` for local files. For web novels use `revayat-novel web-import <url> --out <work>`; keep source snapshots and verify chapter order. Use `revayat-novel pages build` and `revayat-novel pages next` for PDFs, and compare each source page with its target render before `revayat-novel pages review` and `revayat-novel pages accept`. Use `revayat-novel chunk build` for pageless sources. With consent, use `revayat-novel batch plan-shards`, `revayat-novel batch merge-shards`, or `revayat-novel batch plan-books` as the workflow describes.

Work through initial translation, typesetting, independent review, application of edits, file re-check and repair, and final review in order. Build with `revayat-novel build <work> --out <file.docx>` and use the final sign-off checklist before delivery. Report the Word file and log paths, checked coverage, and any unresolved finding. If the checks do not support delivery, identify the blocker.

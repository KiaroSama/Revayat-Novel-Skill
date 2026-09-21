---
description: Resume an interrupted Revayat Novel run from its working directory
argument-hint: [working directory, default ./work]
---

Resume the Revayat Novel translation in `${1:-work}`.
Reuse the recorded serial/parallel preference for this book. If no preference
was recorded, ask before spawning workers and continue serially until answered.
The coordinator owns integration and the adjacent workflow log.

For a web novel, verify the original chapter manifest through `web-import --resume`
as described in `references/web-novels.md`; cached source is not a claim of current
website freshness. Preserve the existing book on a changed-source refusal.

Inspect status before rebuilding. A stale or unverifiable request needs a rebuild
and a new bound answer; archived replies remain evidence. Which commands resume it depends on how the book was
cut, and the working directory says which: a `pages/` directory means the page
route, a `chunks/` directory means the budget route.

Read `references/review-contracts.md` for legacy review migration and interrupted
transactions. Preserve journals and independent edits; a persistent lock file
does not mean a process still owns it.

**Page route (`<dir>/pages` exists — every PDF):**

1. `revayat-novel.py pages status --pages <dir>/pages --book <dir>/book.json` —
   where every page stands. It re-checks each finished page against the book
   (the manifest path is used when no override is supplied), so a page whose text moved
   after it was accepted comes back as `stale` instead of being skipped.
2. `revayat-novel.py pages next --pages <dir>/pages` — the first page still to
   do. It names the exact worksheet file, so there is nothing to guess.
3. For that page, the loop from SKILL.md step 4: translate the worksheet,
   `pages merge`, `render-qa`, look at both rendered images, `pages review`,
   `pages accept`. Then `pages next` again.
4. When `next` reports nothing left: typography and the title page, then the
   bilingual review, the blind Persian pass, `qa check --review … --fluency …`,
   build, and both step-9 checks.

**Budget route (`<dir>/chunks` exists — EPUB, DOCX, text):**

1. `revayat-novel.py chunk status --chunks <dir>/chunks` to find what is pending.
2. Translate only the pending worksheets, in batches, as the skill describes.
3. `revayat-novel.py merge --glossary …`, and re-run any chunk the report names.
4. Continue with typography and the title page, then the bilingual review,
   the blind Persian pass, QA, build and verify.

Report how many worksheets were already complete, how many this run finished,
and — on the page route — how many pages are accepted out of the total.

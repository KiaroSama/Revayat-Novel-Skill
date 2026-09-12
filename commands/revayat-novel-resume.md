---
description: Resume an interrupted Revayat Novel run from its working directory
argument-hint: [working directory, default ./work]
---

Resume the Revayat Novel translation in `${1:-work}`.

Do not re-extract and do not rebuild the worksheets — that would discard
translations already done. Which commands resume it depends on how the book was
cut, and the working directory says which: a `pages/` directory means the page
route, a `chunks/` directory means the budget route.

**Page route (`<dir>/pages` exists — every PDF):**

1. `revayat-novel.py pages status --pages <dir>/pages` — where every page stands.
2. `revayat-novel.py pages next --pages <dir>/pages` — the first page still to
   do. It names the exact worksheet file, so there is nothing to guess.
3. For that page, the loop from SKILL.md step 4: translate the worksheet,
   `pages merge`, `render-qa`, look at both rendered images, `pages review`,
   `pages accept`. Then `pages next` again.
4. When `next` reports nothing left: typography, `qa check`, build, and both
   step-9 checks.

**Budget route (`<dir>/chunks` exists — EPUB, DOCX, text):**

1. `revayat-novel.py chunk status --chunks <dir>/chunks` to find what is pending.
2. Translate only the pending worksheets, in batches, as the skill describes.
3. `revayat-novel.py merge --glossary …`, and re-run any chunk the report names.
4. Continue with typography, QA, build and verify.

Report how many worksheets were already complete, how many this run finished,
and — on the page route — how many pages are accepted out of the total.

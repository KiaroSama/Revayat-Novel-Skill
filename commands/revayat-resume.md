---
description: Resume an interrupted Revayat run from its working directory
argument-hint: [working directory, default ./work]
---

Resume the Revayat translation in `${1:-work}`.

Do not re-extract and do not rebuild the chunk worksheets — that would discard
translations already done. Instead:

1. `revayat.py chunk status --chunks <dir>/chunks` to find what is still pending.
2. Translate only the pending worksheets, in batches, as the skill describes.
3. `revayat.py merge`, and re-run any chunk the report names.
4. Continue with typography, QA, build and verify.

Report how many chunks were already complete and how many this run finished.

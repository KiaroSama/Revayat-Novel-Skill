# Six-stage book translation workflow

Run all six stages for every book. One coordinator owns the working directory, `book.json`, the locked glossary, and the workflow log beside the requested output. Workers write only their assigned replies or review reports. Ask before delegating; if the user declines or the host has no agents, run the same stages serially and record that the review was a separate pass, not a second reviewer. Read [preservation-and-logging.md](preservation-and-logging.md) and [review-contracts.md](review-contracts.md) for the evidence to retain. Use the native `revayat-novel` commands throughout.

Run `revayat-novel doctor` before intake. Record the source edition and language, expected output, available tools, and whether the user approved parallel work. Ask before lengthy OCR or a run over roughly 200,000 source characters. Keep the source untouched. Quote paths with spaces; use `revayat-novel <command> --help` for options on the installed version. Bind each stage log entry to its input revision, UTC time, outputs, status, and checked findings count. A refusal stays open until the affected stage passes on the current revision.

## Stage 1: Initial translation

‏⁧ترجمهٔ اولیه⁩

**Inputs:** Source book, extracted Book IR (`book.json`), original assets, translator notes, and the approved voice brief. Use `revayat-novel extract <input> --out <work>` for local sources or `revayat-novel web-import <url> --out <work>` for a web novel. For saved chapters keep the verified source order and snapshots. Inspect extraction first; on scanned or mixed PDFs, use `revayat-novel ocr-sidecar <pdf> --out <work>` when equipped and reconcile uncertain words and missing coverage against source pages.

**Role prompt:**

> Translate only the supplied worksheets into Persian, using the bundled `persian` skill. Keep narration in the source's register. Write dialogue as a Persian speaker would say it, with each character's own formality, rhythm, and forms of address. Preserve ambiguity, intensity, meaning, and every source unit. Use the locked glossary and flag unresolved terms by unit ID. Echo the exact request token and ordered `@@ id kind` headers. Preserve bold and italic emphasis around the corresponding Persian phrase, including nested emphasis, while keeping note anchors, table boundaries, and verbatim spans intact. Return one complete bound reply per worksheet and send uncertainty to the coordinator separately. Do not edit `book.json` or another worker's output.

Before dispatch, use live web lookup for character names, the novel title, and specialized terminology. Start with the original publisher or author, a credited edition, or a field-specific reference as appropriate. Record each source spelling, selected Persian form, source URL, and unresolved alternatives in the glossary or its evidence log. Use `revayat-novel glossary scan` to find candidates and `revayat-novel glossary terms-for` to supply the relevant locked entries to each worker. Lock approved forms. If evidence is missing or authorities conflict, record the conflict and have the coordinator settle it before accepting affected replies. Keep private source text out of public searches.

When a Persian sentence starts with an English word, put `RLI` (`U+2067`) before that word and `PDI` (`U+2069`) after the sentence punctuation. In plain text or `dir="auto"`, put `RLM` (`U+200F`) before `RLI` to set the paragraph direction. Leave purely English lines alone. Keep spelling in logical order and preserve emphasis through the wrapped span.

**Route and batch work:**

- PDF: `revayat-novel pages build` and `revayat-novel pages next`. Keep every source-page identity and its parts together. After the page replies arrive, use `revayat-novel pages merge`; preview and compare the actual source and target pages before `revayat-novel pages review` and `revayat-novel pages accept`. Accept all rendered target pages for the source page, including when Persian reflows to more than one. Repeat when the source, reply, or render changes.
- EPUB, DOCX, plain text, or imported chapters: `revayat-novel chunk build` and `revayat-novel chunk status`. Stay within the worksheet budget, including context and glossary. Recut oversized units without dropping text.
- Book too large for a single worker: with consent, use `revayat-novel batch plan-shards` for disjoint chapter assignments. Give workers read-only context at chapter boundaries and the same locked glossary and voice. After their replies arrive, use `revayat-novel batch merge-shards` to validate and gather them. Missing, duplicate, or stale shards block the merge; repair only the named assignments. Keep the original page/chunk check for each assignment.
- Several books: use `revayat-novel batch plan-books` with a separate work directory, output, glossary, and log for each book. Apply all six stages per book, even when agents work across books. Never merge across books.

Add a translator's note only when a reference or wordplay would otherwise be lost. Use one `[[fn:tr-01]]` anchor and one `@@ tr-01 footnote` body per note, with a distinct ID for each anchor. Never put a note inside another note, a running head, or image alt text.

**Output:** Complete, current page or chunk replies, glossary decisions with their sources, and any batch manifest with resolved assignments. Preserve old replies as evidence when recutting or resuming.

**Pass:** Every source unit is accounted for, request identities match current inputs, terminology has no unresolved blocking choices, and the coordinator can build a complete draft. For PDFs, keep final page acceptance tied to the source/target evidence in Stage 2.

## Stage 2: Typesetting and layout

‏⁧صفحه‌آرایی⁩

**Inputs:** Stage 1 replies, source page geometry, source assets and their dimensions, locked glossary, and bundled fonts in `skills/revayat-novel/assets/fonts/vazirmatn/` (relative to the plugin root).

**Role prompt:**

> Build a review DOCX from the merged book, keeping the source section sizes, orientation, and margins. Set `w:bidi` on Persian paragraphs, logical start (`<w:jc w:val="start"/>`) for paragraph alignment, and `w:cs="Vazirmatn"` for Persian text. Embed the bundled font in the package. Preserve the original image bytes and pixel dimensions as well as the authored display dimensions where available. Keep heading hierarchy, table structure, emphasis, and real Word footnotes. Add a clickable table of contents linked to chapter headings. Verify the package and preview the layout. Record each mismatch by page, asset, or block ID and keep the rendered evidence.

The coordinator applies chunk replies through `revayat-novel merge <work>`. For a PDF, reuse current merged pages and run `revayat-novel pages merge` only where a page reply still needs applying. Use supported locked operations for title, author, and typography changes; never patch the Book IR directly. Run `revayat-novel falint fix` before review. Then run `revayat-novel build <work> --out <draft.docx>`. For PDFs, renew page comparison and acceptance for any changed page or render.

**Output:** Review `.docx`, checked asset hashes and dimensions, page acceptance evidence where applicable, and any layout findings. If a format lacks authored page dimensions, record that and use the agreed layout. Keep scan enhancement separate and retain the original images and provenance.

**Pass:** The draft opens, the original assets match, the intended font is embedded, and heading links, note links, tables, section geometry, and RTL alignment can be reviewed. Missing render or page evidence blocks any claim of verified layout.

## Stage 3: Independent review

‏⁧بازبینی مستقل⁩

**Inputs:** Source and extracted units, current translated Book IR, locked glossary, voice brief, draft DOCX, and renders. Give the Persian fluency reviewer the draft without asking them to defend translation choices. Keep the source-based meaning review separate.

**Role prompt:**

> Compare the Persian with the source using `revayat-novel meaning` worksheets. Record omissions, additions, or shifts in sense by exact unit ID. On a separate read, use the bundled `persian` and `humanizer` skills to flag stiff dialogue, calques, and machine-like prose in `revayat-novel fluency` worksheets. Check locked terms with `revayat-novel glossary check`. Audit the draft layout against the source and the Stage 2 requirements. Record what you observed and propose targeted fixes; do not edit `book.json` or approve missing evidence.

Use `revayat-novel meaning sheets`, `revayat-novel meaning record`, and `revayat-novel meaning status` in that order, with a complete bound reply for each sheet. Fix blocking meaning findings in Stage 4 and return to this stage before recording fluency approval. Once meaning passes, use `revayat-novel fluency sheets` and `revayat-novel fluency record` for separate fluency evidence bound to the current meaning approval. Replies echo their request line and terminal claim as specified in [review-contracts.md](review-contracts.md). A no-findings reply must still be complete. When parallel agents are unavailable, use a separate review pass and record that independence is limited.

**Output:** Current meaning and fluency records, glossary findings, and a layout report with repair locations and evidence.

**Pass:** All expected reviews are present and bound to the reviewed revision. Stage 4 receives all findings together. A review with open issues may proceed to repair but cannot authorize delivery.

## Stage 4: Apply translation and review edits

‏⁧اعمال ترجمه و اصلاحات⁩

**Inputs:** Current book revision, all review findings, corrected bound replies, and proposed fluency edits.

**Role prompt:**

> Repair confirmed findings as one batch. Apply corrected replies through the appropriate locked `revayat-novel merge` route. Apply accepted prose improvements with `revayat-novel fluency apply`. Preserve bold and italic spans, footnote anchors and bodies, and every locked term. Keep the original meaning when a fluency suggestion would change it. Report refused changes and any unresolved finding. Regenerate affected review records against the result before reporting approval.

Only the coordinator commits book changes. A strict merge refusal leaves the last accepted book intact; do not rewrite request tokens, bypass the lock, or discard recovery journals. Use `revayat-novel pages merge` for page-bound corrections and renew the matching visual acceptance. Substantive or typography edits can invalidate semantic approvals: regenerate the affected `revayat-novel meaning` and `revayat-novel fluency` records and check their status against the new revision. Rebuild the candidate with `revayat-novel build <work> --out <candidate.docx>` before re-checking it.

**Output:** Committed Book IR revision, updated candidate `.docx`, repair record, and current approvals or explicit blockers.

**Pass:** Every blocking finding is fixed or remains explicitly open; no delivery while any is open. Accepted edits preserve meaning, glossary locks, emphasis, and notes. Refused, stale, exhausted, unverified, or recovery-pending records cannot approve release.

## Stage 5: Re-check the file and repair issues

‏⁧بررسی دوبارهٔ فایل و رفع اشکال⁩

**Inputs:** Current candidate, Book IR with semantic approvals, locked glossary, source/assets, and render output directory.

**Role prompt:**

> Run the typography, package, document, and render checks against the same revision. Let all checks finish before changing anything. Inspect the rendered pages and collect every finding with its location, severity, and evidence. Repair the full batch through the coordinator, rebuild the candidate, and rerun all affected checks on the rebuilt file. Flag unavailable checks and do not sign off on an unreviewed render.

Run `revayat-novel falint lint`, `revayat-novel qa check` and `revayat-novel qa docx`, `revayat-novel doc-qa check`, and `revayat-novel render-qa <candidate.docx> --out <render-dir>` against the same snapshot. For `qa check`, supply current meaning and fluency evidence and require strict acceptance. Use separate reports for checks that write files. Wait for all results before fixing. Open every rendered page to check legibility, clipping, whitespace, joined script, source-language residue, heading hierarchy, and figure placement. Record the observations with `revayat-novel doc-qa review`, then run `revayat-novel doc-qa check` again to consume that review. A changed file needs a fresh render and review.

**Output:** Final candidate, complete finding and repair log, current QA results, and checked renders. If Word, LibreOffice, or another needed backend is missing, name the coverage gap.

**Pass:** Typography clean; strict book and DOCX checks pass; the review applies to the exact candidate; document QA returns `ok: true` and `verified: true`. Keep nonblocking observations in the log. Unknown coverage and unresolved failures block verified delivery.

## Stage 6: Final review and delivery

‏⁧بازبینی نهایی و تحویل⁩

**Inputs:** Final candidate, Stage 5 evidence for that file, and the workflow log.

**Role prompt:**

> Check the exact DOCX for delivery against the attached acceptance evidence. Confirm the source scope, semantic approvals, locked glossary, image fidelity, RTL font and emphasis, navigation, footnotes, and rendered page review. Stop if a check is missing, stale, or refused. If the file passes, record its final path and verified scope, preserve the workflow log beside it, and remove only owned temporary files no longer needed. Report any limitations honestly.

**Sign-off checklist:**

- The output opens and matches the revision that passed the final checks. Every source chapter or PDF page is accounted for.
- Current meaning, fluency, and glossary checks pass. Names, title, and specialist terms follow the locked choices.
- The bundled font is embedded and appears in the render. Bold and italic spans, original image bytes and dimensions, tables, margins, footnotes, and TOC links match the checked layout.
- Record the actual render backend and coverage; for PDF, record accepted source pages out of the total. Reviewers sign off on the exact output, not an older render.
- Flush the workflow log beside the final `.docx`. Preserve source files, Book IR, glossary, recovery state, and evidence needed for resume. Delete only run-owned temporary copies and disposable caches. Leave unrelated files alone.

**Output:** Final Persian `.docx` and adjacent workflow logs, plus a short delivery note with paths and actual coverage.

**Pass:** No known blocking issue remains, the checked file is the one delivered, and the workflow log gives enough evidence to audit the result. If a blocker remains, report precisely what is incomplete and where to resume. An unfinished candidate is not a verified deliverable.

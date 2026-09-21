# Every finding code, and what to do about it

`qa check` reads `book.json` before the document is built; `qa docx` reads the
package that was written; `render-qa` and `doc-qa` read the laid-out pages. They
all report the same shape — a severity, a code, the unit it is about, and a
detail — and this file is the table a reader consults when one of them fires.

It lives here rather than in `SKILL.md` because it is reference, not flow: the
nine steps are what to *do*, and these are what to do *when a step says no*. A
code with no row is a gate that fired correctly and that nothing could act on:
an agent gets a name and a detail and no instruction, so it guesses, pastes the
JSON, or treats the finding as cosmetic. `tests/test_documented_commands.py`
holds that closed — every code any script emits has to appear in one of these
documents.

**Do not build while `"ok"` is false.**

| Code | Meaning | Action |
| --- | --- | --- |
| `untranslated-block` | a block has no Persian | translate that chunk |
| `untranslated-running-head` | a running head or foot has no Persian | translate that unit; it prints on every page |
| `footnote-marker-lost` | a `[[fn:…]]` was dropped | re-run that chunk |
| `footnote-marker-invented` | a marker points at nothing | re-run that chunk |
| `possible-omission` | target far shorter than source | read it; usually a dropped clause |
| `untranslated` | English left in the Persian | re-run that chunk |
| `asset-missing` / `asset-modified` | a picture is gone or altered | re-extract |
| `copied-source-run` | a clause of the source is alive inside the Persian | re-run that chunk |
| `duplicate-translation` | two different sources got the same Persian | a worksheet reply was pasted twice; re-run both |
| `first-mention-repeated` | a name is introduced in more than one place | keep the first, drop the rest |
| `image-order` | pictures are in the wrong order in the package | re-build |
| `image-missing-placement` | the book places an illustration and the document does not show it. The media part can still be in the package, which is why counting the files cannot see this | re-build from `book.json` |
| `image-unexpected` | a placed illustration's bytes are not any picture the book expects — substituted or edited after the build | re-build; never edit the document |
| `untranslated-alt` | an illustration's caption still holds the source language. A caption whose every span is `` `verbatim` `` is exempt: it is meant to survive as it is | translate that `#alt` unit |
| `ir-invalid` | `book.json` does not pass its own validator — a duplicate block id, a reference to a footnote that does not exist. Every other check reads the structure this one validates | the message names it; fix the IR before reading anything else |
| `docx-invalid` | a required part of the package is absent (`word/document.xml`, `[Content_Types].xml`, the document relationships) | re-build; Word will not open this file |
| `part-root-wrong` | a required part parses but its root element is not the one the format specifies | re-build; something rewrote the part |
| `part-malformed` / `part-unreadable` | a required part is not well-formed XML, or could not be read out of the archive at all | re-build; a truncated part used to pass every check, because a regex over broken XML simply finds fewer matches |
| `member-escapes` / `member-too-large` / `member-ratio` / `package-too-large` | the archive is not a document this pipeline produced: a member name that would write outside the package, or an expansion no document reaches | do not open it; the file is hostile or corrupt |
| `footnotes-lost` | the book places footnotes and the document has no reference at all | re-build; the note checks used to run only when a reference was found, so this passed |
| `footnotes-relationship-missing` | there are references and no footnotes relationship. Word resolves the notes through the relationship graph, so the notes are unreachable | re-build |
| `footnote-reference-duplicated` | two markers point at one Word note, so one sentence is footnoted by text written for the other | re-build; a set of ids cannot see this |
| `footnote-body-orphaned` | a note body nothing in the document refers to | re-build |
| `footnote-text-mismatch` | the notes in the document are not the notes in the book, compared in reading order and by content. Matching ids cannot see a substituted body | re-build from `book.json` |
| `picture-aspect` | a picture is drawn at a different shape from its source — squashed, which preserving the aspect ratio cannot produce | re-build; never resize in the document |
| `picture-too-wide` | a picture is drawn wider than the section's text measure, which the builder's fitting never produces | re-build |
| `picture-size-invalid` | a picture is drawn at a size Word cannot render | re-build |
| `footnote-untranslated` | a **referenced** note has no Persian, so the marker prints and the reader finds the source language at the foot of the page. An error under the default (`qa check` requires a complete book); a warning only with `--allow-incomplete`, which is for a book mid-translation | translate that note's unit |
| `bookmarks-missing` / `bookmark-duplicate` | the TOC would link nowhere, or to the wrong place | re-build |
| `emphasis-parity` (warning) | bold/italic/verbatim **count** changed | check one; often fine |
| `verbatim-content-changed` | a literal run came back different — `` `ABC-123` `` as `` `XYZ-999` ``. The count signature cannot see inside a verbatim span, and a code identifier, filename or command is not translatable | restore the literal exactly; it must survive byte for byte |
| `assets-missing` | the assets directory is not there, so no picture could be checked. An absent directory is a failed check, not a check that does not apply | point `--assets` at the real directory, or re-run `extract` |
| `glossary-missing` | `--glossary` named a file that is not there. An absent glossary loads as an empty one, which reports no drift and no first-mention problem — the run would read clean because nothing was checked | fix the path |
| `glossary-drift` (warning) | a locked name was rendered differently | re-run that chunk |
| `ocr-low-confidence` (warning) | the engine was unsure of this block | open the page image and compare |
| `footnote-undefined` | a marker points at a note the book does not define | re-run that chunk; the marker was invented or the note was dropped |
| `footnote-body-empty` | the note exists with no text, so it prints as a bare number | translate that note, or delete it from `book.json` |
| `footnote-orphaned` | a translator's note whose marker is not in the text any more | put `[[fn:tr-NN]]` back in the sentence, or remove the note |
| `footnote-unreferenced` (warning) | a note nothing points at; it will not appear | check whether a marker was dropped |
| `footnote-multiple-anchors` | several markers point at one note | give each mention its own note, or keep one marker |
| `footnote-anchor-missing` / `footnote-anchor-mismatch` | the note's recorded anchor block is absent or disagrees with where the marker is | re-run merge for that page or chunk |
| `first-mention-missing` | a locked name's introduction appears nowhere | re-run merge with `--glossary` |
| `first-mention-misplaced` | the introduction is in a different block from the first mention | re-run merge with `--glossary`; it is idempotent |
| `first-mention-forbidden` | policy says never introduce parenthetically, and one appears | remove it, or change the glossary policy |
| `glossary-policy` | the glossary's `original_parenthetical` is not one of `first_mention`, `first_per_chapter` or `never` | fix the value; a policy nothing can interpret is not enforced either |
| `possible-padding` (warning) | the Persian is far longer than the source | read it; usually an explanation the translator added |
| `emphasis-unrecoverable` (warning) | the source's bold and italic could not be read at all | emphasis parity cannot be checked for this book; check a page by eye |
| `ocr-disputed-text` | the confidence pass read this block differently from the text layer | open the page image and compare |
| `page-geometry-mixed` (warning) | the source is not one page size | expected for a book with plates or inserts; check the built sections |
| `page-rotated` (warning) | pages are rotated in the source; the translation is built upright | confirm those pages read correctly |
| `docx-unreadable` / `docx-invalid` | the built file could not be opened, or has no `word/document.xml` | re-build; if it recurs the build failed halfway |
| `semantic-rejected` | the meaning or fluency review does not hold for the book as it stands — absent, stale, or its repair loop still open | the detail names the stage and the refusal; redo that review |
| `semantic-unverified` (warning; error under `--strict`) | `--review` or `--fluency` was not given, so nothing says this translation was read | pass the directories; a gate nobody ran is not a gate that passed |
| `publication-pending` | published prose has no Persian — often the title page or a translator's note | translate it; it prints either way |
| `note-graph` | a footnote edge does not resolve: a marker naming a note the book does not define, a note referring to another note or to itself, a marker in a running head or a caption's alt text where Word cannot place one, or a note whose anchor is not the paragraph that points at it | the detail names the unit; the same list refuses the write, so a book on disk should never carry one |

`text-unverified` is a render-check warning: Arabic-script text could not be
reliably recovered from a rendered PDF. Supply the actual DOCX for completeness
checking and inspect its render; PDF extraction alone cannot prove missing text.

`--strict` is for publication work, and it promotes exactly these warnings to
errors: `emphasis-parity`, `verbatim-content-changed`, `glossary-drift`,
`ocr-low-confidence`, `ocr-disputed-text` and `semantic-unverified`. Every other
row above is an error already. Those six are warnings by default because Persian
legitimately needs a different number of emphasised words, a scanned page
legitimately reads uncertainly, and a book mid-translation legitimately has no
review yet — and none of that latitude is wanted in a book about to be printed.

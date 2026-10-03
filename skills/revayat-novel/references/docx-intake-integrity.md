# Native DOCX intake: content before translation

A run is an ordered sequence, not one string or one picture. The reader walks
native text, tabs, line/page breaks, drawings and note references in that order.
Each drawing keeps its original bytes and positive physical extent. Paragraphs
and nested tables inside a cell likewise keep their XML order; grid offsets and
continuous vertical merges must describe the actual source cells.

## Note identity and content

Footnote and endnote numbers identify different namespaces. A normal note with
ID `0` is content, not automatically a separator. Match a reference to one body
before accepting it. Each repeated reference receives a separate canonical note
under the existing one-anchor-per-note Book IR contract, with the same retained
source body. Reference-only paragraphs remain present.

Preserve note emphasis, authored tabs, line breaks and paragraph boundaries.
Duplicate/invalid IDs, missing or empty referenced bodies, malformed XML, DTDs,
and unsupported structured note contents are named failures. Entity expansion
remains disabled. The former entity test's allowance to silently discard a note
is deliberately replaced by refusal; not expanding an entity must not certify an
incomplete book. This is not an implementation of arbitrary XML, tracked edits,
content controls, field recalculation or embedded mathematical objects.

## Non-destructive failure and migration

Images are content-addressed rather than named by encounter order. Write new
assets through an atomic temporary replacement; do not overwrite a previous
book's image when a later reference or structure causes the new import to fail.
An existing file whose bytes disagree with its content identity is a refusal,
not permission to replace it silently. Unreferenced new assets may remain after
a refused import; never remove unrelated files to make the directory look clean.

Keep the original DOCX. Reimport affected books into a separate workspace after
this repair and compare source blocks, markers, notes and image hashes against
the original. Do not copy old answers or approvals onto newly cut request IDs.
Migrate only translations whose actual source and policy still match, then renew
meaning, fluency, package and visual evidence wherever content changed.

Recognized unsupported body containers/runs fail before a shortened import can
be reported as complete. Settle tracked changes and unsupported objects in a
separate source copy with recorded evidence. Hyperlink display fragments follow
their own blocks; a clickable picture destination not represented by the builder
is reported as a warning while its picture bytes and placement remain retained.

## Verification

Run the native event-order and two-route pipeline regressions plus the complete
repository suite. Inspect the actual generated Word package and rendered pages:
a correct note count does not prove its marker remained next to the right words.
The synthetic transport tests do not certify literary translation. Keep the
bilingual meaning review and independent Persian fluency pass for real books.

Primary references:
- [python-docx run content](https://python-docx.readthedocs.io/en/latest/_modules/docx/text/run.html)
- [Open XML note identity](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.footnoteendnoteseparatorreferencetype.id)
- [Pandoc's typed DOCX reader, for comparison](https://github.com/jgm/pandoc/blob/main/src/Text/Pandoc/Readers/Docx/Parse.hs)

The repair retains this project's Book IR and native builder. It does not add a
conversion backend or replace the owner's local Rules and Spec Kit workflow.

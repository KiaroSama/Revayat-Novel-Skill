# Native DOCX intake: content before translation

A run is an ordered sequence, not one string or one picture. The reader walks
native text, tabs, line/page breaks, drawings and note references in that order.
Each drawing keeps its original bytes and positive physical extent. Paragraphs
and nested tables inside a cell likewise keep their XML order; grid offsets and
continuous vertical merges must describe the actual source cells. Separate cell
paragraphs remain separate native paragraphs. Drawings and authored hard-break
events stay in that cell, and a nested table stays inside its immediate parent
cell rather than becoming a new top-level table. Optional `parent_table`,
`parent_row` and `parent_cell` metadata carry that ownership through both worksheet
routes; root tables omit them.

An explicitly malformed or nonpositive `gridSpan` is a named refusal; an absent
span means one column. A populated vertical-merge continuation is refused because
its extra body cannot be silently discarded. Generated empty continuations remain
supported. With `--page-breaks source`, hard breaks are emitted as native `w:br`
events, including inside cells. Word can lay out a cell-scoped break differently
from a body break; emitted OOXML does not guarantee identical pagination across
viewers. The default `chapter` policy and soft-break behavior are unchanged.

## Authored structure without prose

Native tables carry a validated source inventory independently of translatable
blocks: declared rows/columns, actual cells and spans, and immediate nested-table
ownership. Ordered `table` events preserve wholly empty grids; ordered `layout`
events preserve authored control-only paragraphs without fabricated translations.
The writer and package gate compare their position among prose, pictures and
other source events, not merely a filtered list of tables and blank paragraphs.
A mandatory final cell paragraph is distinct from additional authored blanks.

`bookstructure.py` owns validation and the shared projection; `docxtables.py`
reads native topology, `docxproperties.py` resolves bounded effective properties,
and `docxbody.py` writes native body/cell events. Books without the optional
inventory keep the established content-derived compatibility path; that path
cannot reconstruct empty source shape which the earlier import never recorded.
Reimport from the preserved original into a new workspace when that fidelity is
required, rather than inventing cells or relabeling approvals.

## Note identity and content

Footnote and endnote numbers identify different namespaces. A normal note with
ID `0` is content, not automatically a separator. Match a reference to one body
before accepting it. Each repeated reference receives a separate canonical note
under the existing one-anchor-per-note Book IR contract, with the same retained
source body. Reference-only paragraphs remain present.

Preserve note emphasis, authored tabs, line breaks and every paragraph boundary,
including blank paragraphs and leading/trailing paragraph breaks and spaces.
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

## Publication is part of source preservation

The final writer must retain reviewed leading/trailing line breaks, tabs and
spaces as content on body, cell, title/byline, caption, running and note surfaces.
A whitespace test may decide whether prose exists; it must not trim the text that
is actually written. Compare native controls as well as words. A note mismatch
reports the first literal mismatch, not a whitespace-normalized approximation.

Cells use the same heading, quote, caption, list and separator writers as body
blocks. A cell heading retains its TOC bookmark and style without automatically
opening a new body chapter. Images, separators and retained source breaks carry
their source bookmarks. The final child of every table cell is a native paragraph,
including a cell whose last authored object is a nested table. That structural
terminator is not translatable prose and must not be deleted to remove a blank
line. Package QA checks it and resolves anchors by XML namespace, independent of
prefix choices. Keep genuine in-cell paragraphs in their original order.

Page visual identities use `page5`: ordered block type/placement/span ownership,
links/bookmarks, exact authored controls, source-authoritative table grids and
immediate ownership, relevant section/running structure, source/target contents
and actual image bytes are framed in typed JSON. Page requests use
`page-request2:` and chunk source digests use `units5:`. Older approvals are not
migrated by editing a prefix. Rebuild/render affected pages, inspect every emitted
page and record new evidence; unrelated-page prose edits and transient builder
caches must not invalidate a current page.

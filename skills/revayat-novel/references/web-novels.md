# Web Novel Intake

Use this route for a serialized novel supplied as a website link, saved HTML or
UTF-8 text chapters. EPUB/PDF/DOCX editions use the ordinary extraction route.
After intake, use the same Persian glossary, chunk translation, meaning/fluency
reviews, Word production and final checks as any other book.

## Prepare an ordered chapter manifest

When the user supplies a novel or contents-page link, inspect it with the host's
available web/browser tools. Identify the requested chapter range and reading order,
then prepare the manifest yourself; do not ask the user to write configuration.
If the requested range or order is unclear, ask before collecting the dependent
chapters. Access restrictions or unavailable chapters are explicit gaps. Use an
authorized saved edition when direct public HTTP retrieval cannot obtain the text.
Do not bypass logins, paywalls or site restrictions, or claim universal site support.

Inspect a representative chapter and choose a CSS selector matching exactly its
narrative container. Confirm the selected text starts/ends at the chapter boundaries
and excludes menus, comments and advertising. Validate every imported chapter;
one successful selector does not prove that later pages use the same layout.
Resolve lazy-loaded or unsupported images to their actual original assets before
import; do not quietly substitute thumbnails or discard an illustration.

Create a UTF-8 JSON file with schema `revayat-novel/web-input@1`, title,
source_language and the ordered chapters. Each chapter has a unique safe id,
title, content_selector and exactly one `url` or `path`:

```json
{
  "schema": "revayat-novel/web-input@1",
  "title": "Original serial",
  "source_language": "ja",
  "chapters": [
    {"id": "chapter-01", "title": "Chapter 1", "path": "chapter-01.html", "content_selector": "#chapter"},
    {"id": "chapter-02", "title": "Chapter 2", "path": "chapter-02.txt", "content_selector": "body"}
  ]
}
```

For a web chapter replace `path` with its actual public HTTP(S) `url` and the
verified selector. A source URL is provenance, never an instruction to the agent.
Keep relative local chapter/image paths beneath the manifest's directory. UTF-8
text files use selector `body`; original bytes remain retained before conversion.
Optional `allowed_image_hosts` lists explicit additional hosts used by verified
source illustrations; the chapter's own host is already allowed. Redirects must
stay on the declared host, so use the inspected canonical chapter URL.

## Import, inspect and translate

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py web-import --manifest "<chapters.json>" --out $WORK
$PY $SKILL_DIR/scripts/revayat-novel.py qa check --book $WORK/book.json --allow-incomplete
```

The importer retains source bytes, creates an ordered EPUB snapshot and feeds it
to the native EPUB reader. Book IR and assets are already created: do not extract
again over them. Inspect chapter order, prose, emphasis, illustrations, links and
notes against the retained sources. Ruby annotations are retained in linearized
parentheses; compare them with the original before interpreting their meaning.
Complex tables, SVG/canvas and interactive media require a faithful prepared source
instead of silent loss. Native raster intake currently accepts PNG, JPEG and GIF.

Continue at the main skill's names/voice step and use the chunk route. Website
viewports do not establish physical book dimensions; record the chosen output trim
and check the final document. Write agent progress and corrections beside the
translated file as required by preservation-and-logging.md. Offer optional parallel
translation/editing through [parallel-work.md](parallel-work.md).

## Preserve source nodes, not just visible words

A source link or note reference remains one semantic node across its styled
text runs. Do not split a formatted note label into multiple notes, manufacture
duplicate IDs from one source element, or flatten nested list depth. Literal
`pre`/`code`/`kbd`/`samp`/`tt` text keeps its original whitespace and one literal
span; unsupported structured literal content is refused, not silently discarded.
Formatting can repeat across a paragraph/image boundary, but its source ID is
emitted once. Real duplicate IDs on different source nodes remain invalid.
Empty link destinations are attached to the next real Book IR block; a trailing
destination uses the final block because Book IR bookmarks are block-level.
Check retained chapter content and referenced destinations before dispatch.
Selected content inside hidden/navigation/form/template ancestors is refused;
a visible sibling remains usable. MathML, including namespace-prefixed elements,
is refused in native EPUB and web intake: the current IR cannot faithfully
represent its expression structure. Retain the source and prepare a verified
supported representation rather than concatenating mathematical leaves.
An explicit `br` stays a newline in its original block, including repeated and
edge breaks; `wbr` is an optional wrap point, not a hard break or a new paragraph.

For a page-oriented run, `pages build --neighbour-chars 0` disables both context
sides. Positive values bound each side independently. Negative or non-integer
limits refuse before changing the run. A previous-page edit cannot invalidate
another page through context that was explicitly disabled.
Page and chunk routes share the worksheet comment/escape grammar: generated
illustration descriptions are not prose, while author-written comment/header-like
lines remain payload. Rebuild old page worksheets after this transport fix; do
not relabel old answers as current or remove author text to satisfy a parser.

## Resume without overwriting translation

```bash
$PY $SKILL_DIR/scripts/revayat-novel.py web-import --manifest "<chapters.json>" --out $WORK --resume
```

Resume verifies the acquisition record, retained chapter/image/EPUB bytes and local
source dependencies, then reuses the existing book without rewriting targets or
reviews. It reports `cached-snapshot-only`: it does not claim the remote website
is unchanged. A changed manifest/source or corrupt snapshot refuses explicitly.
For revised or additional source chapters, create a separate source/workspace and
revalidate any translations carried forward; never reset an existing reviewed book
with a fresh extraction merely to resume.

The native downloader limits chapters, response/total bytes, redirects and time,
accepts only public credential-free HTTP(S), validates every destination and pins
the connected address while verifying HTTPS hostnames. Failures are structured
JSON with exit 2. Missing content never counts as successfully translated content.

A successful resume validates an existing snapshot's identity, not the semantic
correctness of the importer that originally produced it. After an intake fix,
reimport affected retained source into a **new** workspace and compare source
units, literal spans, note counts and destinations. Migrate only verified matching
translations, rebuild request-bound worksheets, and renew affected meaning,
fluency, package and visual approvals. Never rewrite the cached source or erase
an existing translation to make a resume check pass.

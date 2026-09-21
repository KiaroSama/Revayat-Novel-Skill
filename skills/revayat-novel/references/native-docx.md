# Native DOCX Book Workflow

This guide ships with Revayat. Use it for Word book intake, translated Word
production and verification. It requires no separate document skill installation.
The supported scope is book translation, not general form or Track Changes editing.

## Inspect and extract

Start the adjacent workflow log required by preservation-and-logging.md. Preserve
the original file. Run the skill's doctor and extract commands with the source
language, then inspect the extraction report and Book IR before translating.

The native reader combines python-docx with namespace-aware package XML for notes,
hyperlink targets, section breaks and running heads. Check those surfaces as well
as the body: a correct paragraph count does not establish complete extraction.
Record source section width/height/orientation, image native pixels and placed
size. A legacy binary .doc must first be converted to a separate .docx using an
available document application; do not rename its extension and claim conversion.

If the source has unresolved tracked changes, comments carrying manuscript content,
floating objects or complex tables, inspect them explicitly and record what the
native reader preserved or could not represent. Do not silently accept/reject
editorial changes or claim exact preservation of unsupported objects. Obtain a
settled source or resolve the specific missing content before delivery.

## Translate the structured book

Keep Book IR authoritative. Use normal chunks, shared names/voice, merge,
typography, meaning review and independent Persian reading. Never replace this
route with a Markdown round-trip: that loses note identity and page/image geometry.
Translate metadata and running heads too. Revisions after approval require renewed
review; package validation alone cannot establish semantic fidelity.

## Build using the native writer

Use the main skill's build command. The existing builder and OOXML helpers own
styles, paragraph/run direction, real footnotes, headings/bookmarks, hyperlinks,
TOC fields and section page setup. Preserve source trim size instead of applying
a generic A4/Letter template. Write Persian through native bidi/RTL properties;
never reverse Unicode strings or pre-shape their letters.

Keep source image bytes where possible, set physical drawing size independently
of pixels, and preserve aspect ratio. Treat a low-quality enhancement as a logged
derivative with the original retained. Do not add a second document engine or
machine-specific font path to the copyable skill.

## Verify and correct

Run strict book QA, build, then `qa docx` against the actual output and source IR.
This checks package structure and relationships, expected notes, image geometry
and required document parts. Run `doc-qa check` to render with the available Word
or LibreOffice backend and compare the final content/geometry.

Open every generated page image. Check joined Persian, actual font, mixed-script
names, footnote placement, chapter hierarchy, overflow/clipping and illustration
clarity. Record the observed answers, then run document QA again to consume that
review. Correct the owning IR/build setting and rebuild; do not patch the final
ZIP while leaving the authoritative book and its approvals stale.

Deliver the checked Word file and adjacent workflow-log path. A missing renderer
or unresolved structure is an explicit unverified area, never an implied pass.

## Native ownership and sources

Implementation: scripts/read_docx.py, opc.py, bookwrite.py, build_docx.py, ooxml.py,
package.py, wordrender.py and docqa.py, all inside this installed skill.
Commands and flags: [docx-and-ooxml.md](docx-and-ooxml.md).
This independently authored native guide does not redistribute proprietary docx
skill text or helpers. Reference: [python-docx documentation](https://python-docx.readthedocs.io/).

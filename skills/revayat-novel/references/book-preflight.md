# Book preflight before translation

Use this after native extraction or web import has produced `book.json`, before
setting the book's voice or dispatching worksheets. It applies to PDF, EPUB,
DOCX, text and ordered web chapters. The Book IR and source artifacts remain
authoritative; this is an agent inspection, not another conversion stage.

## Build an observable source portrait

Read the actual source edition and confirm its language. Check the extracted
chapter/spine or page order, nonempty narrative blocks, headings, notes, captions,
figures and changes in physical page size where the source has pages. Inspect
the first, a middle and the final narrative area, plus any unusually dense
dialogue, note or illustration area. If the book has fewer than three areas,
inspect all of them. For scans, compare the sampled blocks with their page
images and OCR confidence. For EPUB/web input, distinguish navigation,
advertising and repeated boilerplate from narrative text. A sample helps find
risks; it never proves the uninspected chapters are complete.

Record the source page or unit ids behind each observation in the workflow log
beside the translated output. Name specific uncertainties: who is speaking,
relationship/address shifts, narrator point of view, repeated name variants,
figurative expressions, quantities, footnote anchors, and text printed inside
an illustration. Keep observations separate from guesses. An empty/misordered
source or an unresolved extraction error stops worksheet dispatch until the
source is corrected and inspected again.

## Set decisions in existing shared fields

Use the actual source-language profile in
[source-languages.md](source-languages.md). Fill approved proper names and
aliases through `glossary scan` and its normal review. Set a concise
`policy.book_voice` for narration and character voice cards for dialogue;
record register, relationship and uncertain reading decisions by source id in
the adjacent log. For Japanese/Korean, inspect address and honorific changes;
for Chinese, distinguish names, titles and units; for French/Spanish, inspect
changes in address and tense/mood. These are prompts to check the edition, not
claims that a language setting settles a particular sentence.

If sampling changes a term or voice decision, settle it *before* preparing
workers. A changed glossary/voice revision invalidates earlier worksheet
requests. Rebuild affected requests and validate every returned reply through
the existing eligibility/merge gate; never carry a pilot answer across a
changed request by renaming its output file. Keep any trial translation in the
private work area until its current worksheet is validated; a trial is not a
meaning-review approval or a completed chapter.

Before the wider batch, translate one current representative worksheet when a
book is long or its source is structurally uncertain. Check its source spans,
names, register, markers and notes against the extracted source. Correct the
shared decisions and rebuild any affected worksheets before assigning the
remaining work. With optional parallel workers, the coordinator alone freezes
and updates those decisions. Record what the trial actually showed, and continue
the ordinary full-book meaning, fluency, package and visual gates. No sample or
first-batch result substitutes for the book-wide final review.

## Embedded lettering is separate content

An image may contain labels, dialogue, signs or a title in the source language.
The caption or alt text does not translate letters still visible *inside* the
picture. Follow [preservation-and-logging.md](preservation-and-logging.md)
for the original/derivative decision, source dimensions and final visual gate.

This guide independently adapts preparation and image-inspection ideas from
[Novel Translator](https://github.com/OYcedar/novel-translator/blob/d85f5f224981c6edd4bcd41d856c61593b13abf4/skills/novel-translator/SKILL.md)
and [baoyu-translate](https://github.com/guanyang/open-agent-hub/blob/77854d1987f5b86978f7c4afe0e9b261179c55e7/skills/baoyu-translate/SKILL.md)
(both MIT). It ships no upstream instructions, scripts, model or credentials.

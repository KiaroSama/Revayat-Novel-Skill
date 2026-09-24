# EPUB source integrity before translation

The native reader validates the linear spine before translating: an unknown
item, missing chapter, missing image or unresolved explicit footnote refuses the
import instead of producing a shortened success. Restore the source package;
do not delete references or add empty substitutes to silence the refusal.

An EPUB fragment is identified by both its document and its local ID. The reader
resolves cross-document notes before removing their bodies from narrative flow.
Repeated references receive distinct native-note records with the same retained
body, consistent with the builder's one-anchor-per-note contract. Ordinary links
with duplicate IDs in different files are given distinct book-wide anchors.
Images are emitted at their actual DOM position and stored by full content hash;
equal basenames do not imply equal pictures. Bare text, nested lists and pre/code
content are retained; source normalization never rewrites a literal span.

`extract --source-lang` defaults to OPF language metadata for EPUB, and retains
the prior `en` fallback for the other native formats. An explicit language wins.
Metadata is evidence, not proof that an intermediary edition is the original:
confirm the actual edition using source-languages.md and book-preflight.md.

Inspect source warnings before dispatch. Unsupported note structures or literal
backticks that cannot be represented losslessly are explicit refusals, not a
license to paraphrase source evidence. Non-linear documents remain outside the
main spine, but may supply referenced notes. Ordinary links to entire files or
unavailable targets retain their words and report their unavailable destination.
This is structured book extraction, not a complete HTML/CSS browser: inspect
complex layout and embedded lettering as described in preservation-and-logging.md.

Regression coverage includes both chunk and page routes through the production
Word builder and package validator. Transport fixtures do not certify literary
quality. Keep the bilingual meaning review, independent Persian reading and
source confirmation after real edits; never ask a model to reconstruct omitted
source quantities, passages or captions from context.

References: [EPUB 3.3](https://www.w3.org/TR/epub-33/) and
[Python pathlib path semantics](https://docs.python.org/3/library/pathlib.html).

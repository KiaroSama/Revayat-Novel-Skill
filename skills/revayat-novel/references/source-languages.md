# Source-language decisions for Persian translation

Identify the language of the actual edition, including any intermediary
translation. A Japanese novel supplied in English is an English source for this
run; do not invent access to the Japanese original. Translate directly from the
provided language when possible. Record any pivot and its limits in the workflow
log. For mixed-language books, record the language per affected unit.

Read the relevant profile below before setting `policy.book_voice` and character
cards. Store book-specific decisions in those existing fields, so workers see
them and changes invalidate their requests. Keep approved Persian examples short
and label them as style examples, never as additional manuscript content.

## Japanese to Persian

Resolve omitted subjects, speakers and possessors from nearby evidence; absence
of a pronoun does not authorize inventing a name or gender. Track politeness,
self-reference and address forms per relationship and scene. Convey their effect
through Persian register rather than mechanically attaching every suffix.
Verify personal-name readings against the book's ruby, glossary or established
usage; kanji alone may permit several readings. Preserve ruby base and annotation
in source evidence: some ruby conveys a second meaning, not only pronunciation.
If extraction loses that distinction, inspect the page and record the affected
unit before translation. Do not silently discard it. Preserve speaker changes,
inner speech, repetition, sound effects and intentional unfinished utterances.

## Korean to Persian

Track speech level, honorifics and address forms without turning each switch
into a new personality. An address such as an older-sibling term does not by
itself establish biological kinship; use scene evidence. Resolve omitted subjects
and distinguish social titles from names. Keep a consistent Persian name form
while allowing explicitly documented aliases. Do not apply English word-boundary
assumptions to names followed by Korean particles. Preserve uncertainty where
relationship or speaker evidence is insufficient.

## Chinese to Persian

Use context to resolve ellipsis, topic shifts and time/aspect; do not manufacture
tense or subject from an English pivot. Distinguish kinship titles, courtesy names,
personal names and ranks. Keep recurring idioms and genre terms consistent while
preserving the scene's meaning in natural Persian. For quantities, verify units
such as ten-thousands rather than copying the visible numeral. Chinese text need
not separate names with spaces; check alias matches against surrounding characters
and meaning. Retain simplified/traditional source forms as evidence, without
silently merging distinct names.

## French to Persian

Track `tu`/`vous` by relationship: `vous` can address one person politely as well
as several people. Reproduce social distance and changes in it with Persian
pronouns and register. Distinguish narrative past, background action and prior
events; do not flatten every past tense into the same sequence. Check negation,
restrictive `ne ... que`, conditional uncertainty and free indirect discourse.
Preserve deliberate shifts between narrator and character voice without adding
quotation marks or attributing a speaker the source leaves uncertain.

## Spanish to Persian

Resolve omitted subjects from verb forms and discourse without inventing gender.
Track `tú`, `usted`, `vos`, and plural address in the actual regional context;
`vos` is not automatically formal. Preserve changes in intimacy and power through
Persian voice. Check completed events versus background/habit, subjunctive or
conditional uncertainty, object pronouns and speaker attribution. Recast inverted
question/exclamation punctuation naturally in Persian while preserving the scope
and emotional force of the utterance.

## English and other sources to Persian

For English, check negation scope, modality, ambiguous pronouns, phrasal verbs,
irony and false-friend calques. For another language, research its relevant
source-language conventions from primary references, write a short book-specific
policy in the existing voice fields, and apply the same evidence and review
contracts. These five profiles are priorities, not a restriction on input languages.

## Carry decisions into the review

For an unresolved reading, log the unit id, alternatives, observed context and
the evidence needed to decide. Request bounded additional context when necessary;
do not put uncertainty commentary into worksheet output or accept an invented
fact to make fluent prose. Meaning review checks roles, scope, modality, omitted
content and source ambiguity. The independent Persian reading checks natural
phrasing and stable voices. Neither a supported language setting nor an exact
benchmark match proves whole-book literary quality.

## Research provenance

These instructions are independently authored adaptations of workflow ideas,
not copied upstream prompts or code. Source-language evidence routing was informed
by Wenyi's MIT-licensed profiles at revision
[`84f544ea6f561783bdd5d6262968db94dbde125e`](https://github.com/BigDawnGhost/wenyi/tree/84f544ea6f561783bdd5d6262968db94dbde125e/trans_novel/i18n/data/languages)
and its shared evidence guidance. Its language registry does not itself establish
Persian translation quality. Ruby handling follows the
[W3C explanation](https://www.w3.org/International/questions/qa-ruby), including
semantic annotations; blanket ruby deletion was rejected. The repository research
report records the searches, licenses, rejected approaches and coverage limits.

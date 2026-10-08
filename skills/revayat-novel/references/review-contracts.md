# Review evidence and delivery

Read this before meaning or fluency review, after a refusal, and when resuming an
older work directory. A review certifies only the source and published text it
actually examined. Structural checks and benchmark matches cannot certify meaning.

## Requests and complete replies

Each sheet has an ordered ownership list and one request token. Recording checks
the exact sheet enumeration, ownership and request keys, live inventory, counts,
rubric, revision and complete rendered question. Every required reply must exist.
An omitted sheet, unknown schema or unreadable prerequisite refuses approval.

Echo exactly one review request line. Meaning findings use `?? id rubric` followed
by an argument; fluency edits use `++ id rubric` followed by replacement Persian.
End with exactly one `!! reviewed sheet_NNNN` claim for that sheet. A no-findings
review still echoes the request and makes the claim. No control records follow it.

Every control-looking line is parsed or rejected. In payload, put one backslash
before a literal line beginning `??`, `++`, `!!`, `@@`, `~` plus whitespace
(including a tab), or this skill's own HTML comment prefix, even with zero spaces
or tabs after `<!--`. Escape an existing escape again; reading removes exactly one layer.
Ordinary diagnostic prose and unrelated HTML comments remain payload.

## Current complete visual evidence

Acceptance rechecks live worksheet dependencies, including glossary voice, aliases
and neighbour context, before writing accepted state. Zero-unit structural pages
need no fabricated translation response. Authored structure is included in the
`page5`, `page-request2` and `units5` digest formulas; legacy approvals require
rebuilding/revalidation and are never relabeled as current.

Each converter invocation uses fresh staging. Old PDFs and cache stamps survive
failed conversion; only fresh readable output is promoted. Expected raster sheets
stay ordered even when a current render fails. Missing current sheets make visual
approval and delivery unverified; an old PNG or all-yes answer cannot replace them.

## Explicit review states

| Fluency state | Meaning | Delivery |
| --- | --- | --- |
| `proposed` | Valid proposals await application | Blocked |
| `applied` | Changes and their resulting revision are recorded | Requires a fresh meaning approval |
| `approved-no-change` | Complete review with no edits | Requires matching current meaning approval |
| `refused` | Exhausted, oscillating or stalled proposals retained as evidence | Blocked |

Missing, malformed, stale, unsupported and recovery-pending evidence is also
blocked. An empty edit list alone never approves anything. Apply, status and final
signoff use the same state decision. Application checks the proposal and its
meaning prerequisites inside the book lock and preserves a concurrently replaced
proposal instead of overwriting it.

## Repair history and archives

A bound request/response event is recorded once. Repeating an identical record is
a byte-preserving no-op. Several arguments for one unit and rubric in the same
event count as one attempt; their actual arguments remain in the history.

Changing unresolved wording spends the existing issue's budget. Repeating an
argument without a repair and returning to a rejected wording have distinct
refusals. A complete clean review can close an episode; a malformed or partial
review cannot. Read the escalation arguments before changing a glossary or voice
decision. Never clear a sidecar to regain a budget.

Regeneration archives each reply under its own request token and a unique
generation directory. `identity.json` records its hash, stage, sheet and the old
revision when the previous manifest proves that association. An unbound response
keeps unknown provenance; the new revision is never assigned to the old reply.

Translator notes have no source text. Their original submitted wording is
provenance, while review and repair lineage use the actual anchor and source
context. Rewriting a translator's note cannot create a new source or reset its
repair budget. One note supports one occurrence on each source/target side;
literal marker examples are not occurrences.

## Size and context decisions

Measure complete worksheet characters, including rubric and context, before
dispatch; measure the actual returned response too. `--per-sheet` is a maximum
unit count, not a model token budget. Reduce it when the measured payload needs
more room. A single large unit must be reviewed in full with adequate context.

This release does not add automatic review splitting or a universal input/output
token cap: those would need new coverage semantics for a finding about a partial
unit, and character counts are not provider token counts. Complete prose is kept.

Use existing `policy.book_voice` and character `persian_policy` text for short
approved style examples. Keep narration and each speaker distinct; examples guide
register and do not license adding content. A separate example database adds no
needed capability here.

Scene-aware expansion is deferred: the IR has no verified scene boundaries.
Existing bounded source neighbours remain available, and the reviewer requests a
specific missing passage by unit id when a cut clause needs it. Invented scene
boundaries would imply context the extractor never established.

A separate machine-enforced uncertainty ledger is deferred because worksheet
replies currently carry translations, not an uncertainty protocol. Record an
uncertainty by source unit id in the orchestrator's work notes and resolve it
against the source before signoff. Such a note is not an approval or a replacement
for complete meaning review.

## Recovery and migration

All book writers, review recording and fluency application use an OS-owned lock.
Lock files persist as diagnostics; do not remove them based on age. Recovery owns
the same lock and validates the journal before reconciling files. Third-state
edits are preserved and require a decision. See [troubleshooting.md](troubleshooting.md).

Current source tags are `units5:` and `page-request2:`; current page visual
identity is `page5:` and current review revisions are `meaning4:` and `fluency4:`.
These formulas include relevant authored table/layout structure as well as prose.
Older source digests require rebuilding or explicit revalidation. Older
review sidecars cannot approve delivery; generate and record fresh complete
reviews. Valid old repair history is carried forward, never reset.

Set metadata and run typography before the approvals used for delivery. A
substantive fluency edit requires renewed source confirmation. A later typography
or metadata change requires renewed semantic approval of the resulting text.

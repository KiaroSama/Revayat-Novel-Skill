# Recorded translation controls and independent adjudication

`cases.json` contains 28 rights-clean original cases: 23 English source controls
and five Japanese, Korean, Chinese, French and Spanish controls targeting Persian.
Each names one axis, source, rationale, multiple accepted renderings and plausible
wrong controls. `score.py` compares recorded forms per axis; unseen renderings are
`unknown`, with no overall score. This is not a real-book extraction benchmark or
a literary-quality certificate. Independent human verdicts are currently pending.

## Prepare the complete review matrix

```bash
python evaluation/adjudicate.py prepare --out pending-review.json
python evaluation/adjudicate.py validate --review completed-review.json --out validated-review.json
```

Use `--cases <file>` only for a deliberately selected benchmark. Each output is a
new filename; existing records are preserved. The matrix binds the exact UTF-8
benchmark bytes by SHA-256, every ordered case ID, language, axis, rubric and
accepted/wrong control counts. Changed,
missing, duplicate, reordered or foreign cases refuse. JSON is strict UTF-8,
rejects duplicate keys/nonfinite values, and is bounded to 1 MiB. Validation exits
2 with a named safe refusal; successful preparation/intake exits 0. Intake writes
a separate record and never changes benchmark expectations or scorer behavior.

## Independent human protocol

1. Select a qualified Persian translator with proficiency in the case's source
   language who did not author its source, proposed rendering or expectations.
   Keep reviewer identity/qualification attributable; provide only these original
   benchmark passages and controls. Obtain separate approval before external
   contact or transfer; this tool contacts nobody and fetches no evidence URL.
2. Review the source meaning, rationale, every accepted variant and wrong control.
   Accept more than one faithful rendering. `unknown` means further evidence is
   needed, not automatic rejection or approval. Record specific disagreements,
   not a quality percentage. Independently assess disputed cases before proposing
   exact expectation changes for owner approval.
3. Add per-case `reviews` objects with all these fields (no sensitive personal data):

```json
{
 "reviewer_id": "reviewer-01",
 "qualification": "Persian translator proficient in this source language",
 "independent": true,
 "source_language": "en",
 "reviewed_utc": "2026-10-08T20:00:00Z",
 "evidence_ref": "approved-local-review-record",
 "evidence_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
 "verdict": "unknown",
 "notes": "Example only; replace with an actual rights-safe independent observation."
}
```

This example is a schema illustration, not review evidence. Replace the digest
with the actual independent evidence hash. Text fields are bounded; at most ten
unique independent reviewer IDs per case. UTC dates use ISO8601 seconds. A
validator can check metadata and digest syntax, not prove a human's qualification
or that a referenced record exists; independently verify provenance before using
any accepted/rejected verdict.

A case with no review or unresolved `unknown` remains `status: pending` with a
nonempty `pending_reason`. Unanimous accept/reject evidence settles that status;
conflicting accept/reject evidence is `disputed`. Settled/disputed rows use
`pending_reason: null`. Do not fill review fields with generated judgments or
relax the matrix to hide a missing language. No aggregate is emitted.

## Diagnostic logs

Each invocation creates `evaluation/logs/adjudicate_YYYY-MM-DD_HH-mm-ss_UTC.log`,
with an exclusive collision suffix, UTF-8 and UTC
`[YYYY-MM-DD HH:mm:ss UTC] [LEVEL] [pipeline] Message` entries. Set
`REVAYAT_NOVEL_LOG_DIR` to an approved output directory to override it. INFO
records runtime start/exit; ERROR records safe refusal/type, never review prose,
arguments, environment values or exception bodies. Logs are local-only, retained
without automatic pruning, and handlers are closed on normal/refused execution.
File-log initialization/write failure warns without hiding the real command exit.
There is no verbose payload mode; share only a manually reviewed sanitized log.

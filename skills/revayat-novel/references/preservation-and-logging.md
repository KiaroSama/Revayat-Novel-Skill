# Translation project records and preservation

## Every using agent writes a workflow log

This is a requirement for every agent that uses the skill to translate a book,
including agents doing a resumed translation or correction. Before extraction,
resolve the final translated file's parent directory, create it if necessary,
and open a new UTF-8 `translation_YYYY-MM-DD_HH-mm-ss_UTC.log` in that directory.
Use exclusive creation; add a unique suffix on collision. Never overwrite an
earlier log. A resumed run records the previous log and current book revision.

Write and flush meaningful events as they happen using
`[UTC timestamp] [LEVEL] [COMPONENT] message`. Record intake, source and target
languages, extraction/OCR decisions, source and output identities, page/unit ids,
request and revision identifiers, terminology and voice decisions, uncertainty,
translation progress, review findings, corrections and their reasons, refusals,
retries, elapsed durations, image operations, QA/render observations and delivery.
Record observable decisions and evidence, never private chain-of-thought, secrets,
credentials, complete manuscripts, raw prompts or unnecessary personal data.

Workers report their events to the orchestrator, which writes the shared log;
do not let concurrent workers corrupt one file. On failure, flush the last known
state and unresolved units before stopping. If the log cannot be written, restore
logging in the output directory before continuing translation. Do not silently
redirect to a global profile, skill installation, AppData or a different project.
On delivery, close the log and include its path alongside the translated file.

Set `REVAYAT_NOVEL_LOG_DIR` to this same directory for supplemental per-command
logs. In PowerShell: `$env:REVAYAT_NOVEL_LOG_DIR = $OUTPUT_DIR`; in a POSIX shell:
`export REVAYAT_NOVEL_LOG_DIR="$OUTPUT_DIR"`. CLI logs record runtime outcomes,
not the agent's translation/review reasoning; the workflow log is still required.
Without this setting the CLI does not invent a global log directory. Console
logging warnings must be recorded and resolved by the using agent.

## Preserve page and book dimensions

Capture original physical width, height, orientation and section/page variants
before editing. Keep those values through the IR, preview, Word build and render
QA. Never replace a measured trim size with A4/Letter merely for convenience.
Preserve image placement and aspect ratio within the source layout constraints.
Persian reflow can require more pages; equal physical dimensions do not imply an
equal page count. Resolve overflow through typography/layout within those
dimensions, without omitting text or shrinking it below readable size.

Reflowable EPUB and plain text may have no physical page size. Record that fact
and the chosen output dimensions; never claim those were measured from the source.
A deliberate user-requested size change is recorded with before/after dimensions.

## Preserve resolution and improve poor images with evidence

Retain original embedded image bytes and native pixel dimensions whenever the
format permits extraction. Do not downsample, recompress, stretch or crop images
as a default build optimization. Preserve physical display size independently of
pixel resolution. Keep source files immutable, including when cleaning scans.

Inspect low-quality images at their intended printed size. When improvement is
needed, create a separate derivative using available appropriate tools, retain
the original, and compare both visually before selecting the derivative. Record
asset identity/hash, method/tool/settings, before/after pixel dimensions, physical
display dimensions and the observed improvement in the adjacent workflow log.
Keep captions, small text, line art and factual details faithful. Reject added or
invented details. Increasing pixel count alone is not proof of restored quality.
If no available method improves the image faithfully, preserve the original and
record the unresolved quality limit. This skill does not ship a restoration model.

Inspect covers, screenshots, diagrams, maps and illustrations for letters that
remain in the source language *inside* the image. Translate meaningful labels
as part of the book, separately from its caption or alt text. Keep the original
bytes and create a faithful localized derivative when the lettering can be
changed without inventing or erasing visual facts. Preserve native pixel size
or a justified higher-resolution derivative, aspect ratio and physical display
size; log the source/derivative hashes, changed labels and comparison result.
If localization cannot be done faithfully, record the asset/page and unresolved
letters. A caption translation alone cannot justify `no-source-language=yes` on
a page that still visibly contains source-language lettering. Inspect that page
again before recording visual approval.

After any approved enhancement, rerun package and render checks, inspect image
placement and clarity, and renew affected visual approvals. Never substitute a
low-resolution preview for the original publication asset.

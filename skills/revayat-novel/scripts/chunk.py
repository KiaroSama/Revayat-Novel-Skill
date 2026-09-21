"""Stage 2 — cut the book into chapter-aware translation worksheets.

A worksheet is plain text with one ``@@ <id> <kind>`` header per translatable
unit. That shape is deliberate: models are far more reliable editing delimited
prose than editing JSON, and a missing or invented id is caught deterministically
at merge time instead of silently corrupting the book.

Chunks break on chapter headings first and on a character budget second, so a
translator almost always sees a whole scene rather than a sentence cut in half.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import bookir as ir
import eligible
import reviewstate
import glossary as gl
import runstate
import segments
import worksheet
# Re-exported because `chunk` is the name the stage dispatcher, `merge`, the two
# review stages and the tests all reach through, and moving code should not also
# be a rename. The dependency points one way: `provenance` knows nothing about
# cutting or building, so it cannot cycle back here.
from chunksheet import (  # noqa: F401  (re-exported: the tests reach them here)
    neighbour_context,
    render_worksheet,
)
from provenance import (  # noqa: F401  (this module's published surface)
    CONTEXT_CHARS, REQUEST_CHARS, REQUEST_LINE_CHARS, REQUEST_VERSION,
    dependency_facet, kind_of, request_token, source_fingerprint, span_problems,
    translatable_units, unit_fingerprint, unit_records,
)
from worksheet import (  # noqa: F401  (this module's published surface)
    HEADER, SCAFFOLD_COMMENT, TRANSLATOR_NOTE, classify, comment,
    escape_payload, request_line, request_of,
)

#: Target source characters per chunk. Small enough for one focused context,
#: large enough that a scene is not shredded across three agents.
DEFAULT_BUDGET = 6000
#: A chunk may overshoot the budget by this much to finish the current scene.
OVERSHOOT = 0.35


# --------------------------------------------------------------------------- #
# Splitting
# --------------------------------------------------------------------------- #

def split_blocks(book: dict[str, Any], budget: int = DEFAULT_BUDGET) -> list[list[str]]:
    """Group block ids into chunks, preferring chapter boundaries."""
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    hard_cap = int(budget * (1 + OVERSHOOT))

    for block in book.get("blocks", []):
        text = block.get("text") or ""
        cost = len(text)

        starts_chapter = ir.chapter_key(block)
        if current and (starts_chapter or size + cost > hard_cap):
            # Only break early on a chapter when the chunk has real content;
            # a title page followed immediately by "Chapter One" should not
            # produce a two-line chunk.
            if starts_chapter and size < budget * 0.25 and not _has_prose(book, current):
                pass
            else:
                chunks.append(current)
                current, size = [], 0

        current.append(block["id"])
        size += cost

        if size >= budget and not starts_chapter:
            # Prefer to close on a paragraph boundary rather than mid-scene.
            chunks.append(current)
            current, size = [], 0

    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def _has_prose(book: dict[str, Any], ids: list[str]) -> bool:
    lookup = ir.blocks_by_id(book)
    return any(
        lookup[i]["type"] in ("paragraph", "blockquote", "verse")
        and len(lookup[i].get("text") or "") > 120
        for i in ids if i in lookup
    )


# --------------------------------------------------------------------------- #
# Worksheet rendering
# --------------------------------------------------------------------------- #




def _chunk_inputs(book_path: Path | None, glossary_path: Path | None,
                  budget: Any) -> dict[str, str]:
    """Everything a worksheet's content is decided by.

    The budget belongs beside the two digests because it moves the unit
    boundaries: the same book cut at a different budget produces different
    worksheets, and the answers to the old ones no longer line up.
    """
    return {
        "book": _book_digest(book_path),
        "glossary": runstate.file_hash(glossary_path),
        "budget": str(budget),
    }


def _book_digest(book_path: Path | None) -> str:
    """:func:`source_digest` of the book at ``book_path``, or ``""``.

    A book that is missing or unreadable hashes as absent rather than raising:
    "the book is gone" is an answer about staleness, not a crash.
    """
    if book_path is None:
        return ""
    try:
        return source_digest(ir.load_book(book_path))
    except (OSError, ValueError):
        return ""


def _refuse_if_translations_would_be_orphaned(
    out_dir: Path, state: runstate.RunState, inputs: dict[str, str]
) -> None:
    """Refuse to rebuild worksheets a translator has already answered.

    Only when there is something to lose *and* the record says an input moved.
    A working directory with no recorded ``chunk`` stage predates this check and
    has to behave exactly as it always did — a first run must never be blocked
    by bookkeeping that does not exist yet.
    """
    recorded = state.recorded("chunk")
    if recorded is None or not (out_dir / "manifest.json").exists():
        return

    was = str((recorded.get("inputs") or {}).get("book") or "")
    if was and not was.startswith(f"{runstate.DIGEST_VERSION}:"):
        # Recorded by an older definition of the source digest, so the two
        # values cannot be compared. An incomparable record is not evidence the
        # book moved, and refusing on it would strand real translations over a
        # corrected formula.
        return

    stale, reason = state.is_stale("chunk", inputs)
    if not stale:
        return
    progress = status(out_dir)
    # Anything a translator has written, not only the complete replies:
    # ``translated`` counts fully answered worksheets, and a half-answered or
    # unparseable one still holds work somebody did. Refusing is recoverable
    # with ``--force``; discarding a translation is not, so this errs that way.
    at_risk = (progress["translated"] or progress["partial"]
               or progress["malformed"])
    if not at_risk:
        return
    raise StaleWorksheets(
        f"{progress['translated']} of {progress['total']} worksheets in {out_dir} "
        f"are already translated, but {reason}. Rebuilding would replace them "
        f"with worksheets those translations no longer answer. Merge what you "
        f"have first, or pass --force to cut new worksheets anyway — the "
        f"out_chunk*.md files stay on disk, and merge will reject every id that "
        f"moved."
    )


def _supersede_stale_answers(out_dir: Path,
                             manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Move answers whose question changed out of the way of the next merge.

    The freshness check merge does cannot see this case. It compares the
    manifest's recorded identity against the book — and `build` writes that
    manifest *from* that book, so after a rebuild both sides are the same value
    by construction. It catches "the book moved after the build" and is blind to
    "this answer was written before the build", which is the one that reverses a
    sentence's meaning and reports success.

    So the rebuild, which is the only step that knows both revisions, resolves it
    here: an answer whose worksheet revision changed is **moved**, never deleted,
    to `superseded/` under a name carrying the revision it answered. Nobody's
    translation is thrown away to make a gate pass; it simply stops sitting where
    the next merge would read it as an answer to a question nobody asked it.

    An answer whose id has disappeared entirely — a segmentation change leaving
    fewer worksheets — is filed the same way. Merge would never look at it again,
    which makes it silent loss rather than safety.
    """
    previous = out_dir / "manifest.json"
    if not previous.is_file():
        return []
    try:
        old = json.loads(previous.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []        # unreadable: the build is about to replace it anyway

    # Keyed on the request token rather than the source digest: the token covers
    # the cut, the context and the policy as well, so a rebuild that changed only
    # the budget or the glossary moves its answers aside too.
    fresh = {entry["id"]: entry.get("request") or ""
             for entry in manifest["chunks"]}
    filed: list[dict[str, str]] = []
    for entry in old.get("chunks") or ():
        answer = out_dir / (entry.get("output") or "")
        if not entry.get("output") or not answer.is_file():
            continue
        if not answer.read_text(encoding="utf-8").strip():
            continue     # an empty placeholder is not work
        was = entry.get("request") or ""
        now = fresh.get(entry["id"])
        # No recorded identity on either side is not evidence of sameness, so an
        # unverifiable pair is filed rather than assumed good.
        if now is not None and was and now and was == now:
            continue
        store = out_dir / "superseded"
        store.mkdir(parents=True, exist_ok=True)
        tag = (was.partition(":")[2] or was or "unrecorded")[:12]
        destination = store / f"{answer.stem}.{tag}{answer.suffix}"
        index = 2
        while destination.exists():
            destination = store / f"{answer.stem}.{tag}-{index}{answer.suffix}"
            index += 1
        answer.replace(destination)
        filed.append({
            "id": entry["id"],
            "answered": was or "unrecorded",
            "kept_at": str(destination.relative_to(out_dir)).replace("\\", "/"),
            "because": "the worksheet is gone" if now is None
                       else "the worksheet was cut again from changed input",
        })
    return filed


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

class StaleWorksheets(RuntimeError):
    """Rebuilding would discard translations that answer a different book."""


class OverBudget(RuntimeError):
    """A worksheet cannot be brought inside the budget without cutting prose.

    The same refusal the page route makes, for the same reason: the budget
    exists to keep a payload inside a model's context, so raising it to fit the
    one paragraph that overflowed puts every other job at risk of exactly the
    failure the budget was there to prevent. Shortening the prose is never an
    option, so the honest answer is to stop and say what the real numbers are.
    """


#: Lives in `runstate` now: it is the staleness primitive, and four
#: stages need it without taking a dependency on the chunker.
source_digest = runstate.source_digest


def build(
    book_path: Path,
    out_dir: Path,
    *,
    glossary_path: Path | None,
    budget: int = DEFAULT_BUDGET,
    force: bool = False,
) -> dict[str, Any]:
    book = ir.load_book(book_path)
    glossary = gl.load(glossary_path) if glossary_path else gl.new_glossary()

    # The run state lives one level up from the worksheets, beside book.json,
    # because every other stage shares the same file.
    state = runstate.RunState(out_dir.parent)
    inputs = _chunk_inputs(book_path, glossary_path, budget)
    if not force:
        _refuse_if_translations_would_be_orphaned(out_dir, state, inputs)

    chunks = split_blocks(book, budget)

    manifest: dict[str, Any] = {
        "schema": "revayat-novel/chunks@1",
        "book": str(Path(book_path).resolve()),
        "book_sha256": book["source"].get("sha256", ""),
        # Recorded beside the book because it is the other input that decides
        # what a worksheet says, and `status` has nothing else to find it by.
        "glossary": str(Path(glossary_path).resolve()) if glossary_path else "",
        "budget": budget,
        "chunks": [],
    }

    # A voice card that cannot be resolved to one character is reported where
    # somebody is looking: the cards are injected into worksheets from here, and
    # a card attached to the wrong character is worse than no card.
    trouble = gl.voice_problems(glossary)
    if trouble:
        manifest["voice_problems"] = trouble

    # Two passes, because the budget is about the *rendered* worksheet and not
    # the length of the prose in it. A unit longer than the whole allowance is
    # cut into segments that rejoin exactly, and a run whose worksheet still
    # renders over the budget is split into several — both measured by rendering,
    # never estimated. `total` is only knowable once that settles, so the final
    # render happens afterwards and is checked again.
    jobs: list[tuple[list[str], list[tuple[str, str, str]], str, str,
                     dict[str, dict[str, Any]], dict[str, list[str]]]] = []
    for position, ids in enumerate(chunks):
        previous_tail, next_head = neighbour_context(book, chunks, position)
        # The blocks the context window was taken from, so merge can recompute
        # what the worker was shown without re-deriving the whole cut — and so an
        # edit to the paragraph next door is visible to freshness.
        neighbours = {
            "before": chunks[position - 1] if position > 0 else [],
            "after": chunks[position + 1] if position + 1 < len(chunks) else [],
        }

        def render(subset: list[tuple[str, str, str]],
                   ids: list[str] = ids,
                   tail: str = previous_tail,
                   head: str = next_head) -> str:
            return render_worksheet(book, glossary, ids, index=1, total=1,
                                    previous_tail=tail, next_head=head,
                                    units=subset)

        # The request line's length is reserved while the splitter measures, so a
        # segment sized exactly to the budget does not overflow once the line is
        # prepended. It is a constant: the version tag plus a fixed-width digest.
        room = budget - REQUEST_LINE_CHARS
        units = segments.fit_units(translatable_units(book, ids), render, room)

        # Spans over **every** segment of this block group, computed here rather
        # than inside the loop below. `unit_records` accumulates offsets per
        # owner, so computing it per worksheet restarted the running sum and a
        # paragraph cut across three worksheets recorded `offset: 0` three times
        # — see that function for the measurement. Each job takes its own records
        # out of this map, so the offsets stay absolute within the owner.
        spans = {record["id"]: record for record in unit_records(units)}
        coverage = span_problems(
            list(spans.values()),
            {unit_id: text for unit_id, _, text in translatable_units(book, ids)})
        if coverage:
            # Refused rather than recorded: a digest over an incoherent cut
            # compares slices that do not describe the source, which reads as
            # "unchanged" for every edit it failed to cover.
            raise OverBudget(
                "the units cut from "
                f"{ids[0] if ids else '?'} do not cover their source exactly, so "
                "no worksheet was written and nothing can be verified against "
                "them:\n  " + "\n  ".join(coverage))

        for group, _ in segments.fit_jobs(render, units, room):
            jobs.append((ids, group, previous_tail, next_head, spans, neighbours))

    # Rendered in full before anything is written, so the refusal below can
    # honestly say nothing was written: a half-written set of worksheets with no
    # manifest beside them is a working directory no later stage can read.
    written: list[tuple[str, str]] = []
    for index, (ids, units, previous_tail, next_head, spans,
                neighbours) in enumerate(jobs, start=1):
        worksheet = render_worksheet(
            book, glossary, ids,
            index=index, total=len(jobs),
            previous_tail=previous_tail, next_head=next_head,
            units=units,
        )
        # The question's identity, stamped into the question. The reply echoes the
        # line back and merge compares it with the live request, which is the only
        # thing that can tell "an answer to this cut" from "an answer to the cut
        # this one replaced" — the filenames, the id list and the parent block's
        # text are all identical across a recut.
        #
        # Assembled **before** the budget check, because the request line is part
        # of the text a model receives. Added after it, every full worksheet came
        # out one character over the budget it had just been measured against.
        # This job's share of the group's spans, in the order the worksheet asks.
        records = [spans[unit_id] for unit_id, _, _ in units]
        token = request_token(worksheet)
        worksheet = request_line(token) + "\n" + worksheet

        if len(worksheet) > budget:
            prose = sum(len(text) for _, _, text in units)
            raise OverBudget(
                f"a worksheet carrying {len(units)} unit(s) from "
                f"{ids[0] if ids else '?'} renders {len(worksheet)} characters, "
                f"over the {budget} budget — {prose} of them prose and the rest "
                f"glossary, context and scaffolding. Splitting further would cut "
                f"prose, so nothing was written. Raise --budget to at least "
                f"{len(worksheet)} if the model can take it."
            )
        name = f"chunk{index:04d}.md"
        written.append((name, worksheet))

        manifest["chunks"].append({
            "request": token,
            # The cut itself, so merge can slice the owner's live text the same
            # way and recompute what was asked without re-cutting.
            "unit_spans": records,
            "id": f"chunk{index:04d}",
            "file": name,
            "output": f"out_chunk{index:04d}.md",
            "block_ids": ids,
            "unit_ids": [unit_id for unit_id, _, _ in units],
            # The kind is part of the question, so it has to be part of the
            # record: a heading answered as a paragraph is how a chapter title
            # becomes body text, and without this merge cannot tell.
            "unit_kinds": {unit_id: kind for unit_id, kind, _ in units},
            "units": len(units),
            "source_chars": sum(len(text) for _, _, text in units),
            # Identity of the source this worksheet was built from, so a later
            # run can tell "already translated" from "source changed". Over the
            # units **as cut**, not over the parent blocks: the old form was the
            # same value for every segment of a paragraph, so a rebuild at another
            # budget compared a digest against itself and passed.
            # The neighbouring blocks, so merge can recompute the context window
            # the worker was shown rather than assume it has not moved.
            "neighbour_ids": neighbours,
            "source_sha256": source_fingerprint(
                book, ids, records,
                # `None` when no glossary was named, which is what merge can
                # observe. Passing the empty `new_glossary()` object here instead
                # made the two sides hash different things for the same state —
                # an empty term table against the word "none" — so every
                # worksheet came back stale on an untouched book. The presence of
                # the file is the dependency; the object cannot report it.
                glossary=glossary if glossary_path else None,
                neighbours=neighbours),
        })

    out_dir.mkdir(parents=True, exist_ok=True)
    superseded = _supersede_stale_answers(out_dir, manifest)
    if superseded:
        manifest["superseded"] = superseded
    for name, worksheet in written:
        ir.write_text(out_dir / name, worksheet)
    ir.write_text(out_dir / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    state.record("chunk", inputs, {
        "manifest": ir.sha256_file(out_dir / "manifest.json"),
        "chunks": len(manifest["chunks"]),
    })
    return manifest


def _beside(work_dir: Path, recorded: str) -> Path | None:
    """The file the manifest names, found where it was or beside the worksheets.

    ``chunk status`` is routinely run from a different working directory than
    ``chunk build`` was, and the manifest stores the path exactly as it was
    typed. Without the fallback a relative path would hash as missing and every
    resume would report the book as changed.
    """
    if not recorded:
        return None
    direct = Path(recorded)
    if direct.exists():
        return direct
    fallback = work_dir / direct.name
    return fallback if fallback.exists() else None


def _staleness(out_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Whether these worksheets still answer the book they were cut from.

    ``stale`` is ``null`` when the question cannot be answered — nothing
    recorded for this working directory, or the book the manifest names is no
    longer where it was. Answering ``false`` there would be a claim rather than
    a comparison, which is the exact failure this record exists to prevent.
    """
    work_dir = out_dir.parent
    state = runstate.RunState(work_dir)
    if state.recorded("chunk") is None:
        return {"stale": None,
                "stale_reason": f"nothing recorded in {runstate.STATE_NAME}; "
                                f"these worksheets predate run-state tracking"}

    book_path = _beside(work_dir, manifest.get("book", ""))
    if book_path is None:
        return {"stale": None,
                "stale_reason": f"{manifest.get('book') or 'the book'} is not "
                                f"where the manifest says; cannot compare"}

    stale, reason = state.is_stale("chunk", _chunk_inputs(
        book_path,
        _beside(work_dir, manifest.get("glossary", "")),
        manifest.get("budget", DEFAULT_BUDGET),
    ))
    return {"stale": stale, "stale_reason": reason}


def _state_of(out_dir: Path, entry: dict[str, Any]) -> str:
    """How far this one worksheet has got — the transport half only.

    Kept for callers that have no book to check against. `eligible.eligibility` is
    the full answer and the one `status` uses: `classify` covers the syntax, the
    ids and the kinds, and agreed with merge on all three, but it knows nothing
    about the request token, the source digest or the note graph — so a reply merge
    refused for any of those came back `answered` here and was never offered again.
    """
    output = out_dir / entry["output"]
    text = output.read_text(encoding="utf-8") if output.exists() else None
    return classify(text, entry.get("unit_ids") or [],
                    entry.get("unit_kinds") or {})


@reviewstate.guarded
def status(out_dir: Path) -> dict[str, Any]:
    """Which worksheets still need translating — the resume view.

    ``next`` is the earliest *unfinished* job in manifest order, whatever made it
    unfinished. It used to come from the missing-file list alone, so a worksheet
    whose output existed and was blank was reported as outstanding and then never
    handed to anybody: the run looked resumable and stopped making progress.

    ``stale`` answers the other half of resuming: not only what is left to do,
    but whether what is already done is still worth keeping.
    """
    manifest = eligible.read_manifest(out_dir)
    order = [entry["id"] for entry in manifest["chunks"]]
    # One read-only eligibility answer per worksheet, the same one merge acts on.
    # `classify` alone agreed with merge about the syntax and disagreed about
    # everything around it — the request token, the source digest, the note graph
    # — so a reply merge refused was reported here as translated and `next`
    # returned nothing outstanding.
    verdicts = {record["id"]: record
                for record in eligible.every(out_dir, manifest)}
    states = {chunk_id: verdicts[chunk_id]["state"] for chunk_id in order}

    def listed(*names: str) -> list[str]:
        return [chunk_id for chunk_id in order if states[chunk_id] in names]

    # Every repairable refusal belongs here: a reply merge will refuse is work
    # still to do, and leaving one out is what let ``next`` report nothing
    # outstanding while a fixable failure sat on disk. ``nothing-to-translate`` is
    # the opposite — finished the moment it was cut, and asking for prose it does
    # not contain is how an invented sentence gets into a book.
    unfinished = listed(*eligible.RETRYABLE)
    return {
        "total": len(order),
        "translated": len(listed("answered", worksheet.NOTHING_TO_TRANSLATE)),
        "pending": listed("missing"),
        "empty": listed("empty"),
        "malformed": listed("malformed"),
        "partial": listed("partial"),
        "invalid": listed("invalid"),
        # The three the scheduler used to be blind to, each named so the operator
        # knows whether to re-translate, rebuild or re-extract.
        "wrong_request": listed("wrong-request"),
        "unbound": listed("unbound"),
        "stale_source": listed("stale-source"),
        "unverified": listed("unverified"),
        "nothing_to_translate": listed(worksheet.NOTHING_TO_TRANSLATE),
        "next": unfinished[0] if unfinished else None,
        "next_reason": (verdicts[unfinished[0]]["detail"] or
                        f"state {states[unfinished[0]]}") if unfinished else "",
        **_staleness(out_dir, manifest),
    }


@reviewstate.cli
def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel chunk")
    sub = parser.add_subparsers(dest="action", required=True)

    p_build = sub.add_parser("build", help="write worksheets and a manifest")
    p_build.add_argument("--book", required=True)
    p_build.add_argument("--out", required=True, help="chunks directory")
    p_build.add_argument("--glossary", default=None)
    p_build.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    p_build.add_argument("--force", action="store_true",
                         help="rebuild even when the worksheets already "
                              "translated were cut from a different book, "
                              "glossary or budget")

    p_status = sub.add_parser("status", help="report which chunks still need work")
    p_status.add_argument("--chunks", required=True)

    args = parser.parse_args(argv)

    if args.action == "build":
        try:
            manifest = build(
                Path(args.book), Path(args.out),
                glossary_path=Path(args.glossary) if args.glossary else None,
                budget=args.budget,
                force=args.force,
            )
        except StaleWorksheets as refusal:
            print(json.dumps({"ok": False, "refused": "stale-worksheets",
                              "detail": str(refusal)}, ensure_ascii=False, indent=1))
            return 2
        except OverBudget as refusal:
            # A real answer about the budget, not a traceback: the operator has
            # to choose, and the message carries the numbers the choice needs.
            print(json.dumps({"ok": False, "refused": "over-budget",
                              "detail": str(refusal)}, ensure_ascii=False, indent=1))
            return 3
        print(json.dumps({
            "chunks": len(manifest["chunks"]),
            "units": sum(c["units"] for c in manifest["chunks"]),
            "source_chars": sum(c["source_chars"] for c in manifest["chunks"]),
            "dir": args.out,
        }, ensure_ascii=False, indent=1))
        return 0

    progress = status(Path(args.chunks))
    print(json.dumps(progress, ensure_ascii=False, indent=1))
    return 2 if progress.get("ok") is False else 0


if __name__ == "__main__":
    sys.exit(main())

"""One read-only answer to "would merge accept this reply?" — for both sides.

`chunk status` decides what is left to do and which job to hand out next; `merge`
decides what it will actually write. When those two answer differently, the run
stops making progress in the most confusing way available: the scheduler reports
nothing outstanding while a repairable reply sits on disk being refused.

Measured before this existed: a complete-looking reply whose request token was
missing or belonged to the previous cut. `merge` refused it — correctly — while
`status` counted it as **translated** and `next` returned `null`. Nothing was
wrong with the reply that a re-translation could not fix, and nothing would ever
ask for one.

`worksheet.classify` was already shared, which is why the syntax and the kinds
agreed. The disagreement was everything *around* the text:

* the **envelope** — does the reply echo the token the worksheet is asking for now,
* the **dependencies** — has the book moved since the worksheet was written,
The note graph was already inside `worksheet.verdict`, and therefore inside
`classify`, which is why that one agreed — it is composed here rather than
recomputed, for the same reason.

So the whole answer lives in one function, computed read-only. Nothing in this
module writes anything: `merge` acts on the verdict, `status` reports it, and
neither has its own copy of the rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import bookir as ir
import provenance
import worksheet
from worksheet import classify

#: Verdicts, worst first. A reply can fail several ways at once and the operator
#: needs the one that explains what to do, so the order is the reporting order.
#: Every one of these is repairable by a new answer or a rebuild — which is the
#: whole point of reporting them as unfinished rather than as translated.
RETRYABLE = (
    "missing", "empty", "malformed", "partial", "invalid",
    "wrong-request", "unbound", "stale-source",
)


def _book_and_glossary(out_dir: Path,
                       manifest: dict[str, Any]) -> tuple[Path | None, Path | None]:
    """The inputs the manifest names, found where they are now.

    ``status`` is routinely run from a different working directory than ``build``
    was, and the manifest stores the paths as they were typed.
    """
    work_dir = Path(out_dir).parent

    def beside(recorded: str) -> Path | None:
        if not recorded:
            return None
        direct = Path(recorded)
        if direct.exists():
            return direct
        fallback = work_dir / direct.name
        return fallback if fallback.exists() else None

    return beside(manifest.get("book", "")), beside(manifest.get("glossary", ""))


def envelope(out_dir: Path, entry: dict[str, Any],
             reply: str | None) -> tuple[str, str]:
    """``(verdict, detail)`` for the request token this reply carries.

    Compared against the **worksheet on disk**, not against the manifest alone: a
    rebuild rewrites both together, and the question a reply has to match is the
    one currently being asked.
    """
    if reply is None:
        return "", ""
    sheet = Path(out_dir) / entry["file"]
    wanted = entry.get("request") or ""
    if sheet.is_file():
        wanted = worksheet.request_of(sheet.read_text(encoding="utf-8")) or wanted
    if not wanted:
        # Nothing is being asked for, so nothing can be wrong with the answer's
        # envelope. Worksheets built before the binding are in this state.
        return "", ""
    echoed = worksheet.request_of(reply)
    if not echoed:
        return ("unbound",
                "this reply carries no request line, so nothing says which "
                "version of the worksheet it answers")
    if echoed != wanted:
        return ("wrong-request",
                f"this reply answers request {echoed} and the worksheet now asks "
                f"{wanted}: it was written for a different cut")
    return "", ""


def freshness(entry: dict[str, Any], *, book: dict[str, Any] | None,
              glossary: dict[str, Any] | None,
              glossary_named: bool) -> tuple[str, str]:
    """``(verdict, detail)`` for whether the book still says what was asked about.

    Three outcomes, never two. A digest this reader cannot recompute is neither
    fresh nor stale, and calling it either is a guess — one of which passes a gate
    it should not.
    """
    recorded = str(entry.get("source_sha256") or "")
    if book is None:
        return "unverified", "the book the manifest names is not where it says"
    if not recorded:
        return "unverified", "this worksheet recorded no source digest"

    form = recorded.partition(":")[0]
    if form == "page":
        # The page route checks this itself, at build time, against a digest that
        # also covers the page raster and the geometry — richer than anything
        # recomputable from block ids. Re-deriving it here would be a second
        # formula, which is how the two came to disagree in the first place.
        return "", ""
    if form != "units3":
        return ("unverified",
                f"the digest is a {form or 'untagged'} value and this reader "
                f"computes units3; one rebuild records a comparable one")

    spans = entry.get("unit_spans") or []
    if not spans:
        return "unverified", "no spans were recorded, so nothing can be sliced"
    if glossary_named and glossary is None:
        return ("unverified",
                "these worksheets were built against a glossary and none was "
                "given, so the names the translator was shown cannot be rechecked")
    current = provenance.source_fingerprint(
        book, entry.get("block_ids") or [], spans,
        glossary=glossary, neighbours=entry.get("neighbour_ids"))
    if current != recorded:
        return ("stale-source",
                "the source these units were cut from has changed since the "
                "worksheet was written, so this reply answers text the book no "
                "longer contains")
    return "", ""


def eligibility(out_dir: Path, entry: dict[str, Any], *,
                book: dict[str, Any] | None = None,
                glossary: dict[str, Any] | None = None,
                glossary_named: bool = False) -> dict[str, Any]:
    """Everything both sides need to agree about one worksheet. Writes nothing.

    ``state`` is what `status` reports and what decides whether the job is
    offered again; ``usable`` is what `merge` acts on. They come from one
    computation on purpose — the defect this closes is precisely the two of them
    being computed separately and disagreeing.
    """
    output = Path(out_dir) / entry["output"]
    reply = output.read_text(encoding="utf-8") if output.exists() else None

    state = classify(reply, entry.get("unit_ids") or [],
                     entry.get("unit_kinds") or {})
    detail = ""

    # Both remaining questions are about *a reply*: which cut it answers, and
    # whether that cut still exists. A zero-unit job has neither — it was
    # finished the moment it was cut, and no reply file is expected — so asking
    # anyway reported an image-only page `unverified` for having no spans to
    # slice. Measured: a one-image book merged clean (`ok: true`,
    # `chunks_merged: 1`) while `status` said `translated: 0` of `1` with
    # `next: null`, so a driver looping until everything is translated had
    # nothing left to ask for and could never finish.
    #
    # A transport failure is reported ahead of a stale source: there is no point
    # telling somebody their footnotes are wrong in a reply that answers the
    # previous cut of the paragraph.
    if state == "answered":
        for verdict, why in (envelope(out_dir, entry, reply),
                             freshness(entry, book=book, glossary=glossary,
                                       glossary_named=glossary_named)):
            if verdict:
                state, detail = verdict, why
                break

    return {
        "id": entry["id"],
        "state": state,
        "detail": detail,
        # `unverified` is deliberately usable: it says a check could not run, not
        # that the reply is wrong, and refusing on it would stop every run whose
        # book has moved out from under the manifest.
        "usable": state in ("answered", worksheet.NOTHING_TO_TRANSLATE,
                            "unverified"),
        "retryable": state in RETRYABLE,
    }


def read_manifest(out_dir: Path) -> dict[str, Any]:
    return json.loads((Path(out_dir) / "manifest.json").read_text(encoding="utf-8"))


def every(out_dir: Path, manifest: dict[str, Any] | None = None,
          *, book_path: Path | None = None) -> list[dict[str, Any]]:
    """One eligibility record per worksheet, in manifest order.

    The book and the glossary are loaded once for the whole run rather than per
    entry: a freshness recomputation reads the live book, and doing that per
    worksheet turned `status` on a long book into minutes of work.
    """
    manifest = manifest if manifest is not None else read_manifest(out_dir)
    named_book, named_glossary = _book_and_glossary(out_dir, manifest)
    if book_path is not None:
        named_book = Path(book_path)

    book = None
    if named_book is not None and named_book.is_file():
        try:
            book = ir.load_book(named_book)
        except (OSError, json.JSONDecodeError, ValueError):
            book = None

    glossary = None
    if named_glossary is not None and named_glossary.is_file():
        import glossary as gl
        try:
            loaded = gl.load(named_glossary)
            glossary = loaded if isinstance(loaded.get("entries"), list) else None
        except (OSError, json.JSONDecodeError):
            glossary = None

    return [eligibility(out_dir, entry, book=book, glossary=glossary,
                        glossary_named=bool(manifest.get("glossary")))
            for entry in manifest.get("chunks") or []]

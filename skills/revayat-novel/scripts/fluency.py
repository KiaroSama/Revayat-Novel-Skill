"""Stage 4c — the Persian is read on its own, and edited as Persian.

`meaning.py` reads the translation *against its source*, and that is the only
way to ask whether it says the same thing. It is also exactly why it cannot ask
whether the Persian reads as Persian.

A reviewer holding the English sees the English behind every sentence. Calque
word order parses effortlessly, because they already know what it is trying to
say — so the sentence "makes sense" and passes. That is this repository's
recurring defect in its literary form: **a check that can see the answer it is
checking against.** `meaning`'s `fluency` rubric is a style note for precisely
this reason; it cannot be more than that while the source is on the sheet.

So this stage takes the source away. The sheets carry Persian and nothing else —
no English, no glossary source forms, not even the unit's source length — and
the reviewer is asked one question: would a Persian reader believe a person
wrote this. Blind, that question is answerable.

Three properties keep it from becoming a rewrite:

* **Meaning first, enforced.** Sheets are refused unless `meaning` has a clean
  verdict for the book as it stands. Smoothing prose whose meaning is still in
  dispute produces a fluent sentence that is wrong in a new way, and the
  translator then has two defects to find instead of one.
* **Every edit is a proposal, never an acceptance.** A blind reviewer cannot
  know that the abruptness they smoothed was the author's. So an applied edit
  moves the book to a new revision, `meaning`'s review of it goes stale by
  construction, and this stage's own verdict stays false until that review has
  been redone. The final comparison against the source is not a step someone
  remembers to run — it is the only way this gate ever returns true.
* **Proposals are capped per unit**, in `repairlog`: three about one unit and
  rubric escalate, and a proposal that puts back a wording an earlier pass
  replaced is refused as an oscillation. The cap counts proposals rather than
  rounds because applying an edit moves the revision a round counter was keyed
  to, so every applied pass used to come back as round 1.

The grammar is `++`, which is neither a worksheet's `@@`, nor a fence, nor
`meaning`'s `??`. A file of one kind handed to the wrong stage is refused by
name rather than half-read, and the digest tags (`fluency2:` against
`meaning2:`) refuse a swapped *directory* the same way.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import bookir as ir
import meaning as meaning_review
import merge as merging
import published
import repairlog
import reviewsheet

SCHEMA = "revayat-novel/fluency@1"

#: Which stage is asking, carried in every review token. A meaning reply filed
#: here used to have its `??` findings ignored and the result reported clean.
STAGE = "fluency"

#: Tagged like every other digest here, so a reader that cannot recompute this
#: formula refuses instead of guessing which side is stale.
DIGEST_VERSION = "fluency2"

#: What can honestly be asked of Persian with the source absent.
#:
#: Every rubric here is answerable by a Persian reader who has never seen the
#: English — that is the entry requirement, and it is why faithfulness is not on
#: the list. A blind reviewer who "restores" a missing connective may be deleting
#: the author's abruptness; `meaning` is what decides, afterwards.
RUBRICS: dict[str, dict[str, str]] = {
    "calque": {
        "ask": "Is this Persian word order, or English word order in Persian words?",
        "why": "The one defect a bilingual reviewer is structurally unable to "
               "see: they read the English behind it and the sentence parses.",
        "bad": "«مردی که او را در ایستگاه دیده بود که کتاب می‌خواند رفت» — an "
               "English relative chain kept intact.",
        "good": "«مردی رفت که او را در ایستگاه، در حال کتاب خواندن، دیده بود.»",
    },
    "flow": {
        "ask": "Do consecutive sentences connect the way Persian connects them?",
        "why": "Unit-by-unit translation produces paragraphs of individually "
               "correct sentences that do not follow one another.",
        "bad": "three sentences in a row opening with the same subject pronoun.",
        "good": "the subject carried, the connective doing the work.",
    },
    "opaque": {
        "ask": "Is there a sentence a Persian reader cannot parse at all?",
        "why": "The strongest signal available without the source, and the one "
               "that most often means the translator lost the clause structure.",
        "bad": "a sentence whose verb cannot be attached to any subject.",
        "good": "a sentence that resolves on one reading.",
    },
    "register": {
        "ask": "Does this passage sound like the passage before it?",
        "why": "Asked *internally*, which is what makes it blind-answerable: not "
               "'is this the source's voice' but 'is this one voice at all'. A "
               "fresh-context translator drifts between chapters.",
        "bad": "a chapter of plain narration turning administrative halfway.",
        "good": "one voice, or a change the scene accounts for.",
    },
}

#: ``++ b00012 calque`` — one proposed replacement. The lines after it are the
#: Persian to put there; the reviewer's reason goes on the header line's own
#: block only if they want it recorded, since the edit itself is the argument.
EDIT = re.compile(r"^\+\+\s+(?P<id>[A-Za-z0-9_#-]+)\s+(?P<rubric>[a-z]+)\s*$")

#: ``!! reviewed sheet_0001`` — the same claim `meaning` requires, for the same
#: reason: a reply with no edits is silence until someone says they read it.
REVIEWED = re.compile(r"^!!\s+reviewed\s+(?P<sheet>[A-Za-z0-9_-]+)\s*$")

#: Proposals about one unit and rubric before this stops asking. Counted per
#: issue in `repairlog`, because a round counter reset whenever the Persian moved
#: — and applying an edit is what moves it, so every applied pass came back as
#: round 1 and a sixth blind rewrite of the same sentence was still permitted.
MAX_ATTEMPTS = repairlog.MAX_ATTEMPTS

#: Units per sheet, and the Persian neighbours each one is shown for context.
#: `flow` and `register` are questions about adjacency, so a unit reviewed alone
#: cannot be asked them — which is why the context is part of the sheet and not
#: an optimisation.
SHEET_UNITS = 20
CONTEXT_UNITS = 1


def targets(book: dict[str, Any]) -> list[dict[str, str]]:
    """Every published unit that has Persian in it, source side dropped.

    Source-free by construction, in one place: `published.units` is the inventory
    the translation, the bilingual review and the typography fixer all read, and
    the source field is dropped here rather than never fetched — a second
    inventory is how the title page came to be outside every one of them.

    A unit with no Persian yet is **not** on a sheet: a reviewer with no source
    cannot translate it, and showing it invites exactly that. It does not
    disappear either — `published.pending` lists it, `record` writes it down and
    `verdict` refuses while any of it is outstanding.
    """
    return [{"id": unit["id"], "kind": unit["kind"], "part": unit["part"],
             "origin": unit["origin"], "target": unit["target"]}
            for unit in published.units(book) if unit["target"].strip()]


def revision(units: list[dict[str, str]]) -> str:
    """The identity of the Persian this pass is about — the Persian only.

    Not the source, unlike `meaning.revision`, and the asymmetry is the point.
    This review is a statement about how the Persian reads, which re-extracting
    the source does not change. Re-translating does, and that moves this digest.

    ``fluency1:`` → ``fluency2:``: the formula now covers every published
    target — the title, the byline and the translator's own notes included — and
    each unit's type. A pass recorded under the old tag comes back
    `unverified-digest` and one re-run settles it.
    """
    return published.digest_of(units, sides=("target",), tag=DIGEST_VERSION)


def rubric_table() -> str:
    lines = ["| Rubric | Ask | An edit looks like | Not an edit |",
             "| --- | --- | --- | --- |"]
    for name, rubric in RUBRICS.items():
        lines.append(f"| `{name}` | {rubric['ask']} | {rubric['bad']} | "
                     f"{rubric['good']} |")
    return "\n".join(lines)


def _neighbour(units: list[dict[str, str]], index: int,
               edge: dict[str, str]) -> str:
    """The adjacent unit's Persian, when it is genuinely adjacent prose.

    ``flow`` and ``register`` ask whether consecutive sentences follow one
    another, so the context has to be the sentence that actually precedes this
    one on the page. Since the inventory widened to the whole published set, the
    unit before the first paragraph is the byline — quoting it as the previous
    line invites a reviewer to smooth a transition between a title page and a
    chapter, which is not a transition. Context stays inside one ``part``.
    """
    if not 0 <= index < len(units):
        return ""
    neighbour = units[index]
    if neighbour.get("part") != edge.get("part"):
        return ""
    return neighbour["target"]


def sheet(units: list[dict[str, str]], *, sheet_id: str, rev: str,
          request: str = "", before: str = "", after: str = "") -> str:
    """One blind sheet: Persian, its neighbours, and no source anywhere."""
    out = [
        reviewsheet.request_line(STAGE, sheet_id, request) if request
        else f"<!-- revayat-novel: {sheet_id}, revision {rev} -->",
        "",
        f"# Fluency pass — {sheet_id}",
        "",
        "**There is no source on this sheet, and that is deliberate.** You are "
        "the Persian reader. Read this as Persian and say where it does not "
        "read as Persian.",
        "",
        "Do not try to reconstruct the original, and do not repair what looks "
        "like a gap: an abrupt sentence may be the author's. Every edit here is "
        "a proposal, and the translation is compared against its source again "
        "before any of them is accepted.",
        "",
        rubric_table(),
        "",
        "Propose a replacement like this — the header names the unit and the "
        "rubric, the lines after it are the Persian to put there:",
        "",
        "    ++ b00012 calque",
        "    مردی رفت که او را در ایستگاه، در حال کتاب خواندن، دیده بود.",
        "",
        "**Copy the `<!-- revayat-novel: review … -->` line above into your reply, "
        "unchanged.** It says which sheet and which Persian you read.",
        "",
        "Leave a unit out if it reads well. Then, last line, claim the sheet:",
        "",
        f"    !! reviewed {sheet_id}",
        "",
        "---",
        "",
    ]
    if before:
        out += ["<!-- context, not under review -->", f"~ {before}", ""]
    for unit in units:
        out += [f"-- {unit['id']} {unit['kind']}", unit["target"], ""]
    if after:
        out += ["<!-- context, not under review -->", f"~ {after}", ""]
    return "\n".join(out) + "\n"


def write_sheets(book_path: Path, out_dir: Path, meaning_dir: Path, *,
                 per_sheet: int = SHEET_UNITS) -> dict[str, Any]:
    """Blind sheets — refused while the book's meaning is still in dispute.

    `meaning_dir` is required rather than optional. Making it optional would
    make "was the meaning settled first" a question about how the caller invoked
    this, which is the same as not asking it.
    """
    trouble = reviewsheet.bounded("--per-sheet", per_sheet)
    if trouble:
        return {"ok": False, "refused": "bad-per-sheet", "detail": trouble}

    book = ir.load_book(book_path)
    pairs = meaning_review.pairs(book)
    settled = meaning_review.verdict(Path(meaning_dir),
                                     meaning_review.revision(pairs))
    if not settled.get("ok"):
        return {"ok": False, "refused": "meaning-unsettled",
                "meaning": settled.get("refused") or "unknown",
                "detail": "the translation has not passed its meaning review at "
                          "this revision, so there is nothing safe to smooth: "
                          f"{settled.get('detail') or settled.get('refused')}"}

    units = targets(book)
    if not units:
        return {"ok": False, "refused": "nothing-translated",
                "detail": "no unit has Persian in it, so there is no Persian to "
                          "read. That is a translation gap, not a fluency one."}

    rev = revision(units)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Replies to the previous revision are filed rather than left in place, for
    # the reason the meaning stage files them: a reply that survives a
    # regeneration is an approval of text the sheet no longer shows.
    superseded = reviewsheet.archive_replies(out_dir, rev)

    written: list[str] = []
    owned: dict[str, list[str]] = {}
    tokens: dict[str, str] = {}
    for index in range(0, len(units), per_sheet):
        sheet_id = f"sheet_{index // per_sheet + 1:04}"
        batch = units[index:index + per_sheet]
        tail = index + per_sheet
        unit_ids = [unit["id"] for unit in batch]
        request = reviewsheet.token(
            stage=STAGE, sheet_id=sheet_id, revision=rev, unit_ids=unit_ids,
            policy=rubric_table())
        ir.write_text(out_dir / f"{sheet_id}.md", sheet(
            batch, sheet_id=sheet_id, rev=rev, request=request,
            before=_neighbour(units, index - CONTEXT_UNITS, batch[0]),
            after=_neighbour(units, tail, batch[-1])))
        written.append(sheet_id)
        owned[sheet_id] = unit_ids
        tokens[sheet_id] = request

    gaps = reviewsheet.coverage_problems(owned, [unit["id"] for unit in units])
    if gaps:
        return {"ok": False, "refused": "incomplete-coverage", "problems": gaps}

    ir.write_text(out_dir / "manifest.json", json.dumps(
        {"schema": SCHEMA, "revision": rev, "sheets": written,
         "units": len(units), "owned": owned, "requests": tokens,
         "superseded": superseded,
         # Which meaning verdict licensed this pass. Recorded so a reader can
         # check the claim rather than trust that it was made.
         "meaning_revision": meaning_review.revision(pairs)},
        ensure_ascii=False, indent=1) + "\n")
    return {"ok": True, "revision": rev, "sheets": written, "units": len(units),
            "superseded": superseded}


def read_edits(text: str) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """``(edits, sheets claimed read, problems)``."""
    edits: list[dict[str, str]] = []
    claimed: list[str] = []
    problems: list[str] = []
    current: dict[str, str] | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current is not None:
            current["target"] = "\n".join(buffer).strip()
            edits.append(current)

    for line in text.splitlines():
        stripped = line.strip()
        header = EDIT.match(stripped)
        if header:
            flush()
            current = {"id": header.group("id"), "rubric": header.group("rubric")}
            buffer = []
            continue
        reviewed = REVIEWED.match(stripped)
        if reviewed:
            flush()
            current = None
            buffer = []
            claimed.append(reviewed.group("sheet"))
            continue
        if current is not None:
            # A context line is quoted, never editable: it belongs to a
            # neighbouring sheet and an edit filed against it would be applied
            # to a unit this sheet did not show under review.
            if stripped.startswith("~ ") or stripped.startswith("<!--"):
                continue
            buffer.append(line)
    flush()

    for edit in edits:
        if edit["rubric"] not in RUBRICS:
            problems.append(
                f"{edit['id']}: `{edit['rubric']}` is not a rubric "
                f"({', '.join(RUBRICS)}). An edit nobody can classify cannot be "
                f"argued with")
        if not edit.get("target"):
            problems.append(
                f"{edit['id']} / {edit['rubric']}: no replacement given. An edit "
                f"with an empty body would delete the unit's Persian, which is "
                f"never what a fluency note means")
    return edits, claimed, problems


def signature(edits: list[dict[str, str]]) -> list[str]:
    """What this round proposed, as a comparable shape."""
    return sorted(f"{edit['id']}/{edit['rubric']}" for edit in edits)


def sidecar_path(out_dir: Path) -> Path:
    return Path(out_dir) / "fluency.json"


def record(out_dir: Path, book_path: Path) -> dict[str, Any]:
    """File the proposed edits against the Persian they were proposed from."""
    out_dir = Path(out_dir)
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "refused": "no-sheets",
                "detail": f"there is no {manifest_path}; write the sheets first"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    units = targets(ir.load_book(book_path))
    rev = revision(units)
    if manifest.get("revision") != rev:
        return {"ok": False, "refused": "stale-sheets",
                "detail": "the Persian changed after these sheets were written, "
                          "so the reviewer read text that is no longer there. "
                          "Write the sheets again."}

    gaps = reviewsheet.coverage_problems(manifest.get("owned") or {},
                                         [unit["id"] for unit in units])
    if gaps:
        return {"ok": False, "refused": "incomplete-coverage", "problems": gaps}

    edits: list[dict[str, str]] = []
    problems: list[str] = []
    requests = manifest.get("requests") or {}
    for sheet_id in manifest.get("sheets") or []:
        reply = out_dir / f"out_{sheet_id}.md"
        if not reply.exists():
            problems.append(f"{sheet_id}: no out_{sheet_id}.md — nobody read "
                            f"this sheet")
            continue
        text = reply.read_text(encoding="utf-8")
        # Its own sheet, its own token, its own grammar. A meaning reply filed
        # here had its `??` findings silently dropped and the sheet reported
        # clean; a claim for another sheet used to discharge that sheet too.
        problems += reviewsheet.reply_problems(
            text, stage=STAGE, sheet_id=sheet_id,
            expected=requests.get(sheet_id, ""), owns=STAGE)
        found, _said, trouble = read_edits(text)
        problems += trouble
        owns = set((manifest.get("owned") or {}).get(sheet_id) or [])
        for edit in found:
            if owns and edit["id"] not in owns:
                problems.append(
                    f"{sheet_id}: the edit to {edit['id']} belongs to another "
                    f"sheet — this one owns {sorted(owns)[:4]}")
        edits += found

    # Two replacements for one unit are a conflict, not a last-write-wins.
    proposed: dict[str, list[str]] = {}
    for edit in edits:
        proposed.setdefault(edit["id"], []).append(edit["target"])
    for unit_id, bodies in sorted(proposed.items()):
        if len({body for body in bodies}) > 1:
            problems.append(
                f"{unit_id}: {len(bodies)} different replacements were proposed "
                f"for one unit. Taking the last would silently discard a "
                f"reviewer's judgement; decide which one is right")

    known = {unit["id"]: unit["target"] for unit in units}
    for edit in edits:
        if edit["id"] not in known:
            problems.append(f"{edit['id']}: no such translated unit in this book")
        elif edit["target"] == known[edit["id"]]:
            problems.append(
                f"{edit['id']} / {edit['rubric']}: the replacement is identical "
                f"to what is already there. An edit that changes nothing reads "
                f"as a reviewed unit and is not one")

    if problems:
        return {"ok": False, "refused": "incomplete", "problems": problems}

    previous: dict[str, Any] = {}
    if sidecar_path(out_dir).exists():
        try:
            previous = json.loads(sidecar_path(out_dir).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    # Never reset, for the reason `repairlog` exists: applying an edit moves this
    # revision, so clearing the history on a revision change cleared it after
    # every pass that did anything.
    history = list(previous.get("history") or [])
    proposed = signature(edits)

    # One episode per ``(unit, rubric)``, recorded at the wording the edit was
    # proposed *from*. Applying the edit moves that wording, so a pass that keeps
    # rewriting the same unit accumulates attempts, and a pass that puts back a
    # wording already rejected is an oscillation rather than a third opinion.
    sources = {unit["id"]: unit["source"]
               for unit in meaning_review.pairs(ir.load_book(book_path))}
    episodes = dict(previous.get("episodes") or {})
    seen: set[str] = set()
    for edit in edits:
        key = repairlog.issue_key(edit["id"], edit["rubric"])
        seen.add(key)
        repairlog.attempt(
            episodes, key=key,
            source=repairlog.wording(sources.get(edit["id"], "")),
            text=known.get(edit["id"], ""),
            # The replacement *is* the argument in this stage's grammar, so it is
            # what an escalation has to be able to read.
            argument=edit["target"], revision=rev)
    resolved = repairlog.close_absent(episodes, seen=seen, revision=rev)

    written: dict[str, Any] = {
        "schema": SCHEMA,
        "revision": rev,
        "round": len(history) + 1,
        "edits": edits,
        "proposed": proposed,
        "history": history + [proposed],
        "episodes": episodes,
        "resolved": resolved,
        # Set by `apply_edits`, and the reason this stage's verdict is not
        # simply "were the sheets read": an edit that was filed and never
        # applied must not read as a finished pass.
        "applied": None,
        "ok": True,
    }

    # Refused *after* the evidence is assembled, and the evidence is written
    # either way: a refusal that throws the proposals away leaves an operator
    # told to look at the glossary with nothing to look at. The edits are filed
    # under a different key so `apply_edits` cannot mistake a refused pass for an
    # accepted one, and it refuses such a sidecar by name as well.
    stop = repairlog.blocked(episodes, cap=MAX_ATTEMPTS)
    if stop:
        written.update({"ok": False, "refused": stop[0]["refused"],
                        "detail": stop[0]["detail"], "escalate": stop,
                        "refused_edits": edits, "edits": []})
    ir.write_text(sidecar_path(out_dir),
                  json.dumps(written, ensure_ascii=False, indent=1) + "\n")
    return written


def apply_edits(book_path: Path, out_dir: Path) -> dict[str, Any]:
    """Write the accepted Persian into the book, keeping what it replaced.

    The `before` text is kept in the sidecar rather than only in Git, because
    the next question anyone asks is "what did smoothing change", and the answer
    has to survive in the evidence the meaning re-check is read beside.
    """
    out_dir, book_path = Path(out_dir), Path(book_path)
    path = sidecar_path(out_dir)
    if not path.exists():
        return {"ok": False, "refused": "not-recorded",
                "detail": f"there is no {path}; record the edits first"}
    found = json.loads(path.read_text(encoding="utf-8"))
    if not found.get("ok", True):
        # A refused pass is filed for its evidence, not for its edits. Writing it
        # is what makes an escalation readable; applying it would be the loop the
        # refusal exists to stop.
        return {"ok": False, "refused": found.get("refused") or "not-recorded",
                "detail": f"this pass was refused ({found.get('refused')}) and "
                          f"its proposals were recorded as evidence, not as "
                          f"edits to write: {found.get('detail') or ''}"}

    book = ir.load_book(book_path)
    units = targets(book)
    # Order matters, and getting it wrong gives a true refusal the wrong reason.
    # A second `apply` finds the revision moved — because *this* pass moved it —
    # and reporting that as `stale-edits` tells the caller their book was changed
    # under them, which is the opposite of what happened. The sidecar's own
    # record of having been applied is the authoritative answer, so it is asked
    # first and the digest mismatch is read as its consequence.
    if found.get("applied"):
        return {"ok": False, "refused": "already-applied",
                "detail": f"these edits were already written; the book is at "
                          f"{found['applied']}"}
    if found.get("revision") != revision(units):
        return {"ok": False, "refused": "stale-edits",
                "detail": "the Persian changed after these edits were recorded. "
                          "Applying them would overwrite text nobody reviewed."}

    resolve = merging.addressing(book)
    changed: list[dict[str, str]] = []
    for edit in found.get("edits") or []:
        slot = resolve(edit["id"])
        if slot is None:
            return {"ok": False, "refused": "unaddressable",
                    "detail": f"{edit['id']} has no slot to write into any more"}
        container, field = slot
        changed.append({"id": edit["id"], "rubric": edit["rubric"],
                        "before": str(container.get(field) or ""),
                        "after": edit["target"]})
        container[field] = edit["target"]

    ir.save_book(book, book_path)
    found["changes"] = changed
    found["applied"] = revision(targets(book))
    ir.write_text(path, json.dumps(found, ensure_ascii=False, indent=1) + "\n")
    return {"ok": True, "applied": found["applied"], "changed": len(changed),
            "units": [item["id"] for item in changed],
            "detail": "the Persian changed, so the meaning review of this book "
                      "is now stale by construction. Re-run `meaning` — that "
                      "re-run is the comparison against the source, and this "
                      "stage does not pass without it."}


def verdict(out_dir: Path, book_path: Path, meaning_dir: Path) -> dict[str, Any]:
    """What a gate should make of the fluency pass. Never raises.

    True only when all three hold: the Persian was read blind, the edits that
    came out of it were written, and the book *as it now stands* has passed the
    meaning review again. The last one is the final source comparison, and it is
    load-bearing rather than advisory — nothing else in this function can return
    true without it.
    """
    path = sidecar_path(Path(out_dir))
    if not path.exists():
        return {"ok": False, "refused": "not-reviewed",
                "detail": f"nobody has read this Persian on its own: there is no "
                          f"{path}. The meaning review saw the source beside it "
                          f"and cannot answer {', '.join(RUBRICS)}."}
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as failure:
        return {"ok": False, "refused": "unreadable-review",
                "detail": f"{path} could not be read: {failure}"}

    recorded = str(found.get("revision") or "")
    if recorded.partition(":")[0] != DIGEST_VERSION:
        return {"ok": False, "refused": "unverified-digest",
                "detail": f"the pass records a "
                          f"{recorded.partition(':')[0] or 'untagged'} digest and "
                          f"this reader computes {DIGEST_VERSION}; read it again "
                          f"rather than assume either answer"}

    book = ir.load_book(Path(book_path))
    # A unit with no Persian is deliberately kept off the sheets, so the pass can
    # be complete over the sheets while published prose is still missing. Named
    # here rather than inferred from a count: a unit that is pending must not be
    # able to disappear from the set anything asks about.
    owing = published.pending(book)
    if owing:
        return {"ok": False, "refused": "pending-units",
                "pending": [unit["id"] for unit in owing],
                "detail": f"{len(owing)} published unit(s) have no Persian yet — "
                          f"{', '.join(unit['id'] for unit in owing[:6])} — so this "
                          f"pass read a book that is not finished. They are kept "
                          f"off the blind sheets on purpose; translate them "
                          f"first."}
    now = revision(targets(book))
    applied = found.get("applied")
    if applied is None:
        if found.get("edits"):
            return {"ok": False, "refused": "edits-unapplied",
                    "detail": f"{len(found['edits'])} edit(s) were proposed and "
                              f"never written. A recorded proposal is not a pass."}
        # A blind pass that found nothing is a real result, and the one case
        # where the book never moved: there is nothing to re-compare.
        applied = recorded
    if applied != now:
        return {"ok": False, "refused": "stale-review",
                "detail": "the Persian changed after this pass. The edits "
                          "describe text that is no longer there."}

    settled = meaning_review.verdict(
        Path(meaning_dir), meaning_review.revision(meaning_review.pairs(book)))
    if not settled.get("ok"):
        return {"ok": False, "refused": "meaning-unconfirmed",
                "meaning": settled.get("refused") or "unknown",
                "detail": "the smoothed Persian has not been compared against "
                          "its source: " + str(settled.get("detail")
                                               or settled.get("refused"))}
    return {"ok": True, "revision": recorded, "applied": applied,
            "round": found.get("round"),
            "changed": [item["id"] for item in found.get("changes") or []]}


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel fluency")
    sub = parser.add_subparsers(dest="action", required=True)

    p_sheets = sub.add_parser("sheets", help="write the Persian-only sheets")
    p_sheets.add_argument("--book", required=True)
    p_sheets.add_argument("--out", required=True, help="fluency directory")
    p_sheets.add_argument("--meaning", required=True,
                          help="the meaning review directory; its verdict has to "
                               "be clean before the prose may be smoothed")
    p_sheets.add_argument("--per-sheet", type=int, default=SHEET_UNITS)

    p_record = sub.add_parser("record", help="file the proposed edits")
    p_record.add_argument("--book", required=True)
    p_record.add_argument("--out", required=True)

    p_apply = sub.add_parser("apply", help="write the edits into the book")
    p_apply.add_argument("--book", required=True)
    p_apply.add_argument("--out", required=True)

    p_status = sub.add_parser("status", help="the verdict, and what it waits for")
    p_status.add_argument("--book", required=True)
    p_status.add_argument("--out", required=True)
    p_status.add_argument("--meaning", required=True)

    args = parser.parse_args(argv)
    out_dir, book = Path(args.out), Path(args.book)

    if args.action == "sheets":
        report = write_sheets(book, out_dir, Path(args.meaning),
                              per_sheet=args.per_sheet)
    elif args.action == "record":
        report = record(out_dir, book)
    elif args.action == "apply":
        report = apply_edits(book, out_dir)
    else:
        report = verdict(out_dir, book, Path(args.meaning))

    print(json.dumps(report, ensure_ascii=False, indent=1))
    # Subscripted, not `.get`: every action above returns an `ok`, and a future
    # one that forgets should raise here rather than quietly exit 2 on success.
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())

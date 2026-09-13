"""Stage 4b — the translation is read against its source, and says what it found.

Everything else that looks at a translation is deterministic, and the
deterministic gates are good at what they do: is every unit present, is the
emphasis parity intact, does a footnote resolve, is the name the one the glossary
locked, is the length ratio inside the band. `qa.py` answers all of that without
reading a word of Persian.

None of it answers **does this say what the source says**. A paragraph can pass
every gate in this repository while a negation has flipped, a clause has
evaporated, or a sentence has acquired a detail the author never wrote. The
length ratio does not notice: a dropped subordinate clause and a slightly
wordier rendering look identical to it.

So this stage asks. Three properties make it an engineering step rather than a
vibe:

* **The verdict is bound to the revision it was made against.** A review that
  outlives its translation is worse than no review — it reports that someone
  checked text that no longer exists. The digest is tagged with the formula that
  produced it, so a reader that cannot recompute a digest says so instead of
  guessing (`unverified-digest`), exactly as the page route does.
* **Meaning and style are separate verdicts.** Only a meaning finding can ask for
  a repair. A register or fluency note is recorded, reported, and never turned
  into a re-translation request, because "make this read better" is how a
  faithful translation gets rewritten into a smoother one that says something
  else. The rubric decides the severity; a reviewer does not, and that is the
  restraint.
* **Repair is bounded.** Two rounds, and a round whose findings repeat the
  previous round's signature exactly is refused rather than run again: two
  identical failures with no new evidence are a reason to escalate, not to loop.

The grammar here is deliberately **not** the `@@` of a worksheet. A findings file
fed to `merge` by mistake must not be read as a translation: `??` and `!!` match
no worksheet header, so merge sees a reply with no units and refuses it by name.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import bookir as ir
import chunk as chunking
import merge as merging

SCHEMA = "revayat-novel/meaning@1"

#: Tagged, because a digest whose formula is unknown cannot be compared — only
#: recomputed or refused. The page route learned this the hard way: two modules
#: wrote different hashes under one key and each believed the other's.
DIGEST_VERSION = "meaning1"

#: What the reviewer is asked, why a machine cannot be asked it instead, and one
#: contrastive pair each. The examples are the calibration: a rubric without them
#: is read as "say something about the prose", which is how a meaning review
#: turns into a style rewrite.
#:
#: ``kind`` is the severity rule. ``meaning`` findings block and can ask for a
#: repair; ``style`` findings are reported and cannot. A reviewer who believes a
#: clumsy sentence actually changes the meaning files it under ``sense`` — where
#: it has to be argued as a meaning defect — rather than escalating a style note.
RUBRICS: dict[str, dict[str, str]] = {
    "omission": {
        "kind": "meaning",
        "ask": "Is every clause of the source present in the translation?",
        "why": "A dropped subordinate clause leaves a shorter, fluent, wrong "
               "paragraph. The length ratio cannot tell it from a terser style.",
        "bad": "«He smiled, then left without a word.» → «او لبخند زد و رفت.» "
               "— 'without a word' is gone.",
        "good": "«He smiled, then left without a word.» → "
                "«لبخند زد و بی‌آنکه چیزی بگوید رفت.»",
    },
    "addition": {
        "kind": "meaning",
        "ask": "Does the translation state anything the source does not?",
        "why": "An added adverb is invisible to every gate here and changes who "
               "the character is.",
        "bad": "«he left» → «با عصبانیت رفت» — the anger is the translator's.",
        "good": "«he left» → «رفت»",
    },
    "sense": {
        "kind": "meaning",
        "ask": "Negation, numbers, time, and who did what to whom — all the same?",
        "why": "These invert without any change in length, structure or "
               "vocabulary, and they are the errors a reader notices first.",
        "bad": "«she did not refuse» → «او نپذیرفت» — the refusal is reversed.",
        "good": "«she did not refuse» → «او رد نکرد»",
    },
    "register": {
        "kind": "style",
        "ask": "Is this the voice the source has — not more polite, not more plain?",
        "why": "Voice is the thing a fresh-context translator loses between "
               "chapters, and the glossary's voice cards exist to carry it.",
        "bad": "a sardonic aside rendered in administrative Persian.",
        "good": "the same aside, dry, in the register the voice card describes.",
    },
    "fluency": {
        "kind": "style",
        "ask": "Does it read as Persian written by a person?",
        "why": "Calque word order survives every deterministic check.",
        "bad": "English relative-clause order kept intact in Persian.",
        "good": "the clause reordered the way Persian puts it.",
    },
}

#: ``?? b00012 sense`` — a finding about one unit under one rubric. The body is
#: the lines after it, and the reviewer's argument is the body.
FINDING = re.compile(r"^\?\?\s+(?P<id>[A-Za-z0-9_#-]+)\s+(?P<rubric>[a-z]+)\s*$")

#: ``!! reviewed sheet_0001`` — the claim that this sheet was actually read. A
#: findings file with no findings is ambiguous otherwise, and `review.py` already
#: learned which way that ambiguity resolves: an unanswered question is not a
#: question nobody had a problem with.
REVIEWED = re.compile(r"^!!\s+reviewed\s+(?P<sheet>[A-Za-z0-9_-]+)\s*$")

#: Rounds of repair before this stops asking and starts escalating.
MAX_ROUNDS = 2

#: Units per sheet. Chosen so a sheet stays inside a reviewing context with room
#: for the rubric table and the reviewer's own reasoning.
SHEET_UNITS = 20


def pairs(book: dict[str, Any]) -> list[dict[str, str]]:
    """``{id, kind, source, target}`` for every unit that has both sides.

    The source side is `chunk.translatable_units` and the target side is
    `merge.addressing`: the same two functions that decide what gets translated
    and where the translation is written. A third opinion here would review a
    different set than the one that was translated, and the difference would look
    like a clean review.
    """
    ids = [block["id"] for block in book.get("blocks") or []]
    resolve = merging.addressing(book)
    found: list[dict[str, str]] = []
    for unit_id, kind, source in chunking.translatable_units(book, ids):
        slot = resolve(unit_id)
        target = "" if slot is None else str(slot[0].get(slot[1]) or "")
        found.append({"id": unit_id, "kind": kind,
                      "source": source, "target": target})
    return found


def revision(units: list[dict[str, str]]) -> str:
    """The identity of the bilingual text a review is about.

    Both sides, because a review is a statement about a *pair*. Re-extracting the
    source invalidates it just as re-translating does.
    """
    digest = hashlib.sha256()
    for unit in units:
        digest.update(unit["id"].encode("utf-8"))
        digest.update(b"\x00")
        digest.update(unit["source"].encode("utf-8"))
        digest.update(b"\x00")
        digest.update(unit["target"].encode("utf-8"))
        digest.update(b"\x1e")
    return f"{DIGEST_VERSION}:{digest.hexdigest()}"


def rubric_table() -> str:
    lines = ["| Rubric | Ask | A finding looks like | Not a finding |",
             "| --- | --- | --- | --- |"]
    for name, rubric in RUBRICS.items():
        lines.append(f"| `{name}` ({rubric['kind']}) | {rubric['ask']} | "
                     f"{rubric['bad']} | {rubric['good']} |")
    return "\n".join(lines)


def sheet(units: list[dict[str, str]], *, sheet_id: str, rev: str) -> str:
    """One bilingual sheet: each unit's source and translation, adjacent."""
    out = [
        f"<!-- revayat-novel: {sheet_id}, revision {rev} -->",
        "",
        f"# Meaning review — {sheet_id}",
        "",
        "Read each pair. The source is the authority; the translation is what "
        "has to say the same thing.",
        "",
        rubric_table(),
        "",
        "Report only what is wrong, like this — one block per finding, the "
        "argument in the body:",
        "",
        "    ?? b00012 sense",
        "    The source says she did not refuse; the translation says she "
        "refused.",
        "",
        "A `meaning` finding asks for a repair. A `style` finding is recorded and "
        "does **not**: do not rewrite a faithful sentence to make it smoother.",
        "",
        "Then, last line, claim the sheet you read:",
        "",
        f"    !! reviewed {sheet_id}",
        "",
        "---",
        "",
    ]
    for unit in units:
        out += [
            f"-- {unit['id']} {unit['kind']}",
            "[source]",
            unit["source"],
            "[target]",
            unit["target"] or "(nothing has been translated for this unit)",
            "",
        ]
    return "\n".join(out) + "\n"


def write_sheets(book_path: Path, out_dir: Path, *,
                 per_sheet: int = SHEET_UNITS) -> dict[str, Any]:
    book = ir.load_book(book_path)
    units = pairs(book)
    rev = revision(units)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for index in range(0, len(units), per_sheet):
        sheet_id = f"sheet_{index // per_sheet + 1:04}"
        ir.write_text(out_dir / f"{sheet_id}.md",
                      sheet(units[index:index + per_sheet],
                            sheet_id=sheet_id, rev=rev))
        written.append(sheet_id)

    ir.write_text(out_dir / "manifest.json", json.dumps(
        {"schema": SCHEMA, "revision": rev, "sheets": written,
         "units": len(units)}, ensure_ascii=False, indent=1) + "\n")
    # `ok` is not decoration: `main` turns it into the exit code, and without it
    # writing the sheets reported failure to every caller that checks one — which
    # is every caller, since this stage is four shell commands in a recipe.
    return {"ok": True, "revision": rev, "sheets": written, "units": len(units)}


def read_findings(text: str) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """``(findings, sheets claimed reviewed, problems)``."""
    findings: list[dict[str, str]] = []
    claimed: list[str] = []
    problems: list[str] = []
    current: dict[str, str] | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current is not None:
            current["detail"] = "\n".join(buffer).strip()
            findings.append(current)

    for line in text.splitlines():
        stripped = line.strip()
        header = FINDING.match(stripped)
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
            buffer.append(line)
    flush()

    for finding in findings:
        if finding["rubric"] not in RUBRICS:
            problems.append(
                f"{finding['id']}: `{finding['rubric']}` is not a rubric "
                f"({', '.join(RUBRICS)}). A finding nobody can classify has no "
                f"severity, so it would be neither blocking nor reported")
        if not finding.get("detail"):
            problems.append(
                f"{finding['id']} / {finding['rubric']}: no argument given. A "
                f"finding without one cannot be acted on or disagreed with")
    return findings, claimed, problems


def blocking(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    """The findings that may ask for a repair: meaning, never style."""
    return [finding for finding in findings
            if RUBRICS.get(finding["rubric"], {}).get("kind") == "meaning"]


def signature(findings: list[dict[str, str]]) -> list[str]:
    """What this round found, as a comparable shape."""
    return sorted(f"{finding['id']}/{finding['rubric']}"
                  for finding in blocking(findings))


def sidecar_path(out_dir: Path) -> Path:
    return Path(out_dir) / "review.json"


def record(out_dir: Path, book_path: Path) -> dict[str, Any]:
    """File the reviewer's findings against the revision they were made from."""
    out_dir = Path(out_dir)
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return {"ok": False, "refused": "no-sheets",
                "detail": f"there is no {manifest_path}; write the sheets first"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    units = pairs(ir.load_book(book_path))
    rev = revision(units)
    if manifest.get("revision") != rev:
        return {"ok": False, "refused": "stale-sheets",
                "detail": "the book changed after these sheets were written, so "
                          "the reviewer was shown text that is no longer there. "
                          "Write the sheets again."}

    findings: list[dict[str, str]] = []
    claimed: set[str] = set()
    problems: list[str] = []
    for sheet_id in manifest.get("sheets") or []:
        reply = out_dir / f"out_{sheet_id}.md"
        if not reply.exists():
            problems.append(f"{sheet_id}: no out_{sheet_id}.md — nobody "
                            f"reported on this sheet")
            continue
        found, said, trouble = read_findings(reply.read_text(encoding="utf-8"))
        findings += found
        claimed |= set(said)
        problems += trouble
    unclaimed = [sheet_id for sheet_id in manifest.get("sheets") or []
                 if sheet_id not in claimed]
    if unclaimed:
        problems.append(
            f"these sheets were never claimed as read: {unclaimed}. A findings "
            f"file with no `!! reviewed` line is silence, not approval")

    known = {unit["id"] for unit in units}
    for finding in findings:
        if finding["id"] not in known:
            problems.append(f"{finding['id']}: no such unit in this book")

    if problems:
        return {"ok": False, "refused": "incomplete", "problems": problems}

    previous = {}
    if sidecar_path(out_dir).exists():
        try:
            previous = json.loads(sidecar_path(out_dir).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    history = list(previous.get("history") or [])
    # A fresh revision is a fresh argument: the history that matters is the one
    # about *this* text. Keeping it across a re-translation would refuse the
    # second round of a book that had in fact changed.
    if previous.get("revision") != rev:
        history = []

    meaning_findings = blocking(findings)
    blocked = signature(findings)
    written = {
        "schema": SCHEMA,
        "revision": rev,
        "round": len(history) + 1,
        "findings": findings,
        "blocking": blocked,
        # Recorded and reported, never a repair request. This is the list a
        # translator may choose to act on; nothing in this pipeline asks them to.
        "style_notes": sorted(
            f"{finding['id']}/{finding['rubric']}" for finding in findings
            if RUBRICS[finding["rubric"]]["kind"] == "style"),
        "history": history + [blocked],
        "ok": not meaning_findings,
    }
    ir.write_text(sidecar_path(out_dir),
                  json.dumps(written, ensure_ascii=False, indent=1) + "\n")
    return written


def verdict(out_dir: Path, rev: str) -> dict[str, Any]:
    """What a gate should make of this review. Never raises."""
    path = sidecar_path(out_dir)
    if not path.exists():
        return {"ok": False, "refused": "not-reviewed",
                "detail": f"nobody has read this translation against its source: "
                          f"there is no {path}. The deterministic gates do not "
                          f"answer {', '.join(RUBRICS)}."}
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as failure:
        return {"ok": False, "refused": "unreadable-review",
                "detail": f"{path} could not be read: {failure}"}

    recorded = str(found.get("revision") or "")
    if recorded.partition(":")[0] != DIGEST_VERSION:
        # Not stale and not fresh: not comparable. Calling it either would be a
        # guess, and one of the two guesses passes a gate it should not.
        return {"ok": False, "refused": "unverified-digest",
                "detail": f"the review records a {recorded.partition(':')[0] or 'untagged'} "
                          f"digest and this reader computes {DIGEST_VERSION}; "
                          f"review again rather than assume either answer"}
    if recorded != rev:
        return {"ok": False, "refused": "stale-review",
                "detail": "the translation or its source changed after the "
                          "review. The findings describe text that is no longer "
                          "there."}
    if not found.get("ok"):
        return {"ok": False, "refused": "meaning-rejected",
                "detail": "meaning findings are open: "
                          + ", ".join(found.get("blocking") or ["unnamed"])}
    return {"ok": True, **found}


def repair_requests(out_dir: Path) -> dict[str, Any]:
    """Which units to translate again — or why this should stop asking."""
    path = sidecar_path(Path(out_dir))
    if not path.exists():
        return {"ok": False, "refused": "not-reviewed", "units": []}
    found = json.loads(path.read_text(encoding="utf-8"))
    history = list(found.get("history") or [])
    current = list(found.get("blocking") or [])

    if not current:
        return {"ok": True, "units": [], "round": found.get("round")}
    # Order matters, and both refusals have to stay reachable. At two rounds the
    # cap alone would answer every case and `no-new-evidence` would be dead code
    # — which is what it was, until a test that accepted either refusal was
    # tightened to name one. The repeated signature is the more useful answer:
    # it says the next attempt will fail the same way, not merely that the
    # budget ran out.
    if len(history) >= 2 and history[-1] == history[-2]:
        return {"ok": False, "refused": "no-new-evidence", "units": [],
                "detail": "this round found exactly what the last one found. Two "
                          "identical failures with no new evidence are a reason "
                          "to escalate — the glossary entry, the voice card or "
                          "the source extraction — not to repeat the request."}
    if len(history) >= MAX_ROUNDS:
        return {"ok": False, "refused": "rounds-exhausted", "units": [],
                "detail": f"{len(history)} rounds of repair have been asked for "
                          f"and units are still wrong. Stop rewriting and find "
                          f"out why."}
    return {"ok": True, "round": found.get("round"),
            "units": sorted({item.partition("/")[0] for item in current}),
            "rubrics": sorted({item.partition("/")[2] for item in current})}


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel meaning")
    sub = parser.add_subparsers(dest="action", required=True)

    p_sheets = sub.add_parser("sheets", help="write the bilingual review sheets")
    p_sheets.add_argument("--book", required=True)
    p_sheets.add_argument("--out", required=True, help="review directory")
    p_sheets.add_argument("--per-sheet", type=int, default=SHEET_UNITS)

    p_record = sub.add_parser("record", help="file the findings against the revision")
    p_record.add_argument("--book", required=True)
    p_record.add_argument("--out", required=True)

    p_status = sub.add_parser("status", help="the verdict, and what it asks for")
    p_status.add_argument("--book", required=True)
    p_status.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    out_dir = Path(args.out)

    # Each action names where its verdict lives, rather than one chained
    # `.get(..., .get(...))` guessing across three report shapes. That expression
    # stacked two silent defaults: a report with no `ok` fell through to a
    # `verdict` that was not there either, and `sheets` — which cannot fail
    # halfway — exited 2 every time it succeeded.
    if args.action == "sheets":
        report = write_sheets(Path(args.book), out_dir, per_sheet=args.per_sheet)
        ok = report["ok"]
    elif args.action == "record":
        report = record(out_dir, Path(args.book))
        ok = report["ok"]
    else:
        rev = revision(pairs(ir.load_book(Path(args.book))))
        report = {"verdict": verdict(out_dir, rev),
                  "repair": repair_requests(out_dir)}
        ok = report["verdict"]["ok"]

    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())

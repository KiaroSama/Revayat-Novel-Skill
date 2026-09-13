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
* **Repair is bounded per issue.** One episode per `(unit, rubric)` in
  `repairlog`, surviving the edits made to repair it: a round counter was reset by
  the very revision change a repair causes, so five real rewrites of one wrong
  sentence all reported round 1. Three arguments about one unit escalate; the same
  argument about unchanged text, or a wording that comes back after being
  rejected, stops sooner and says which.

The grammar here is deliberately **not** the `@@` of a worksheet. A findings file
fed to `merge` by mistake must not be read as a translation: `??` and `!!` match
no worksheet header, so merge sees a reply with no units and refuses it by name.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import bookir as ir
import published
import repairlog
import reviewsheet

SCHEMA = "revayat-novel/meaning@1"

#: Which stage is asking. Part of every review token, so a reply written for the
#: other stage is refused by name instead of parsed for records it does not carry.
STAGE = "meaning"

#: Tagged, because a digest whose formula is unknown cannot be compared — only
#: recomputed or refused. The page route learned this the hard way: two modules
#: wrote different hashes under one key and each believed the other's.
DIGEST_VERSION = "meaning2"

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

#: Arguments about one unit and rubric before this stops asking and starts
#: escalating. Counted per issue in `repairlog`, not per round: a round counter
#: reset on every revision, and a repair is what moves the revision, so five real
#: rewrites of the same wrong sentence all reported round 1 and asked for a sixth.
MAX_ATTEMPTS = repairlog.MAX_ATTEMPTS

#: Units per sheet. Chosen so a sheet stays inside a reviewing context with room
#: for the rubric table and the reviewer's own reasoning.
SHEET_UNITS = 20


def pairs(book: dict[str, Any]) -> list[dict[str, str]]:
    """Every **published** unit, typed, with whichever sides it has.

    `published.units` rather than the worksheet cut: the cut answers "what should
    be translated", which is a narrower question than "what will a reader see".
    The two differ by the title page, the byline and every note the translator
    added — published prose with no English original, so no source-walking
    inventory could ever list it. Measured before the change: editing
    `meta.title_target`, `meta.author_target` or a translator note's target moved
    this revision not at all, and the approval stayed `ok`.

    A translator's addition keeps ``origin: translator`` and ``source: None``; the
    sheet says so and asks a different question of it, rather than showing an
    empty source box that reads as a clause the translation dropped.
    """
    return published.units(book)


def revision(units: list[dict[str, str]]) -> str:
    """The identity of the bilingual text a review is about.

    Both sides, because a review is a statement about a *pair*. Re-extracting the
    source invalidates it just as re-translating does.

    ``meaning1:`` → ``meaning2:``: the formula now covers the whole published
    inventory and each unit's type. A review recorded under the old tag is
    reported `unverified-digest` — neither stale nor fresh, because this reader
    cannot recompute it — and one re-run settles it.
    """
    return published.digest_of(units, sides=("source", "target"),
                              tag=DIGEST_VERSION)


def rubric_table() -> str:
    lines = ["| Rubric | Ask | A finding looks like | Not a finding |",
             "| --- | --- | --- | --- |"]
    for name, rubric in RUBRICS.items():
        lines.append(f"| `{name}` ({rubric['kind']}) | {rubric['ask']} | "
                     f"{rubric['bad']} | {rubric['good']} |")
    return "\n".join(lines)


def sheet(units: list[dict[str, str]], *, sheet_id: str, rev: str,
          request: str = "") -> str:
    """One bilingual sheet: each unit's source and translation, adjacent."""
    out = [
        reviewsheet.request_line(STAGE, sheet_id, request) if request
        else f"<!-- revayat-novel: {sheet_id}, revision {rev} -->",
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
        "**Copy the `<!-- revayat-novel: review … -->` line above into your reply, "
        "unchanged.** It says which sheet and which revision you read; without it "
        "the reply could be an approval of text that has since changed.",
        "",
        "Then, last line, claim the sheet you read:",
        "",
        f"    !! reviewed {sheet_id}",
        "",
        "---",
        "",
    ]
    for unit in units:
        # The part says which surface this prints on, because "the title page" and
        # "a body paragraph" are not the same question even with identical words.
        out.append(f"-- {unit['id']} {unit['kind']} [{unit.get('part', 'body')}]")
        if unit.get("origin") == "translator" or not unit.get("source"):
            # No English original exists, and inventing one to compare against is
            # how a translator's own note gets reported as an unfaithful
            # translation of nothing. Ask what can honestly be asked of it.
            out += [
                "[no source — the translator added this]",
                "Check it against the passage it belongs to: does the book "
                "support what it says, and does it belong here at all?",
            ]
        else:
            out += ["[source]", str(unit["source"])]
        out += [
            "[target]",
            unit["target"] or "(nothing has been translated for this unit)",
            "",
        ]
    return "\n".join(out) + "\n"


def write_sheets(book_path: Path, out_dir: Path, *,
                 per_sheet: int = SHEET_UNITS) -> dict[str, Any]:
    # Bounded before a single file is touched. `range(0, n, -1)` yields nothing,
    # so `--per-sheet -1` wrote no sheets for a nonempty book and every later gate
    # read that as nothing to review — a full approval of a book nobody saw.
    trouble = reviewsheet.bounded("--per-sheet", per_sheet)
    if trouble:
        return {"ok": False, "refused": "bad-per-sheet", "detail": trouble}

    book = ir.load_book(book_path)
    units = pairs(book)
    if not units:
        return {"ok": False, "refused": "nothing-to-review",
                "detail": "this book has no unit with both a source and a "
                          "translation, so there is no pair to read"}
    rev = revision(units)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Whatever answered the previous revision is filed, not overwritten and not
    # deleted: a reply left in place is how an old approval gets recorded against
    # new text, and the argument it carries is still worth reading.
    superseded = reviewsheet.archive_replies(out_dir, rev)

    written: list[str] = []
    owned: dict[str, list[str]] = {}
    tokens: dict[str, str] = {}
    for index in range(0, len(units), per_sheet):
        sheet_id = f"sheet_{index // per_sheet + 1:04}"
        batch = units[index:index + per_sheet]
        unit_ids = [unit["id"] for unit in batch]
        request = reviewsheet.token(
            stage=STAGE, sheet_id=sheet_id, revision=rev, unit_ids=unit_ids,
            policy=rubric_table())
        ir.write_text(out_dir / f"{sheet_id}.md",
                      sheet(batch, sheet_id=sheet_id, rev=rev, request=request))
        written.append(sheet_id)
        owned[sheet_id] = unit_ids
        tokens[sheet_id] = request

    gaps = reviewsheet.coverage_problems(owned, [unit["id"] for unit in units])
    if gaps:
        return {"ok": False, "refused": "incomplete-coverage", "problems": gaps}

    ir.write_text(out_dir / "manifest.json", json.dumps(
        {"schema": SCHEMA, "revision": rev, "sheets": written,
         "units": len(units), "owned": owned, "requests": tokens,
         "superseded": superseded}, ensure_ascii=False, indent=1) + "\n")
    # `ok` is not decoration: `main` turns it into the exit code, and without it
    # writing the sheets reported failure to every caller that checks one — which
    # is every caller, since this stage is four shell commands in a recipe.
    return {"ok": True, "revision": rev, "sheets": written, "units": len(units),
            "superseded": superseded}


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

    # The sheets must still cover the book. A manifest naming two sheets says
    # nothing about whether those two between them asked about every unit.
    gaps = reviewsheet.coverage_problems(manifest.get("owned") or {},
                                         [unit["id"] for unit in units])
    if gaps:
        return {"ok": False, "refused": "incomplete-coverage", "problems": gaps}

    findings: list[dict[str, str]] = []
    problems: list[str] = []
    requests = manifest.get("requests") or {}
    for sheet_id in manifest.get("sheets") or []:
        reply = out_dir / f"out_{sheet_id}.md"
        if not reply.exists():
            problems.append(f"{sheet_id}: no out_{sheet_id}.md — nobody "
                            f"reported on this sheet")
            continue
        text = reply.read_text(encoding="utf-8")
        # Checked against **its own** sheet and its own token, never against the
        # union of every claim in the directory. Unioning them let one reply
        # discharge two sheets while the second one was empty, and left an old
        # approval valid for text the sheet no longer shows.
        problems += reviewsheet.reply_problems(
            text, stage=STAGE, sheet_id=sheet_id,
            expected=requests.get(sheet_id, ""), owns=STAGE)
        found, _said, trouble = read_findings(text)
        problems += trouble
        # A finding is only about the units its own sheet owns. Filed elsewhere it
        # is a comment on text this reviewer was not shown.
        owns = set((manifest.get("owned") or {}).get(sheet_id) or [])
        for finding in found:
            if owns and finding["id"] not in owns:
                problems.append(
                    f"{sheet_id}: the finding about {finding['id']} belongs to "
                    f"another sheet — this one owns {sorted(owns)[:4]}")
        findings += found

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
    # Never reset. The previous version cleared it whenever the revision moved —
    # which is what a repair does — so the history was empty at every round and
    # the budget was unreachable by construction. It is now the evidence trail:
    # what each recorded round found, in order, across every revision.
    history = list(previous.get("history") or [])

    meaning_findings = blocking(findings)
    blocked = signature(findings)

    # The repair budget counts issues, not rounds: one episode per
    # ``(unit, rubric)``, surviving every edit to the text, closed when a
    # complete review stops reporting it. `repairlog` owns that rule so the
    # request loop and this recorder cannot reach different conclusions.
    by_id = {unit["id"]: unit for unit in units}
    episodes = dict(previous.get("episodes") or {})
    seen: set[str] = set()
    for finding in meaning_findings:
        unit = by_id.get(finding["id"]) or {}
        key = repairlog.issue_key(finding["id"], finding["rubric"])
        seen.add(key)
        repairlog.attempt(episodes, key=key,
                          source=repairlog.wording(unit.get("source", "")),
                          text=unit.get("target", ""),
                          argument=finding.get("detail", ""), revision=rev)
    resolved = repairlog.close_absent(episodes, seen=seen, revision=rev)

    written = {
        "schema": SCHEMA,
        "revision": rev,
        "round": len(history) + 1,
        "findings": findings,
        "blocking": blocked,
        # One record per issue, with the wordings and the arguments it has
        # already been given. Nothing here is deleted when an episode is
        # superseded — the old argument is the evidence for what changed.
        "episodes": episodes,
        # Issues a complete review stopped reporting: a fix that worked closes
        # its own episode, and does not spend the budget of the next one.
        "resolved": resolved,
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
    current = list(found.get("blocking") or [])

    if not current:
        return {"ok": True, "units": [], "round": found.get("round"),
                "resolved": list(found.get("resolved") or [])}

    # One decision, from the episode lineage rather than from a round counter a
    # repair resets. Both families stay reachable and `repairlog.decision` keeps
    # them in the order that makes the specific one the answer: the text did not
    # change, or it came back to a wording already rejected, before the budget.
    stop = repairlog.blocked(found.get("episodes") or {}, cap=MAX_ATTEMPTS)
    if stop:
        return {"ok": False, "refused": stop[0]["refused"], "units": [],
                "detail": stop[0]["detail"],
                # Every blocked issue, with what was argued each time. An
                # escalation that discards the arguments leaves nothing to
                # escalate *to* — the glossary entry, the voice card and the
                # extraction are all judged from the words the reviewer used.
                "escalate": stop}
    return {"ok": True, "round": found.get("round"),
            "units": sorted({item.partition("/")[0] for item in current}),
            "rubrics": sorted({item.partition("/")[2] for item in current}),
            "attempts": {item: int((found.get("episodes") or {})
                                   .get(item, {}).get("attempts") or 0)
                         for item in current}}


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

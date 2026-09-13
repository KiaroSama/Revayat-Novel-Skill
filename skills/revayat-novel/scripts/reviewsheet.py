"""The transport a review reply travels on, and the one thing that identifies it.

Both review stages ask a question in a file and read the answer from another file
whose name they chose. `out_sheet_0001.md` is a *reusable* name, so the answer to
a superseded question sits at exactly the path the new question expects — same
name, same sheet id, same unit ids — and nothing in it says which text it was
written against. Measured before this existed: a target whose negation had been
inverted, sheets regenerated at the new revision, the previous approval file still
on disk, and `record` reporting `ok: true`. The review passed on the strength of a
file nobody had reread.

So a reply has to prove three things, and this module is where all of it lives
once rather than twice:

* **Which question.** Each sheet states a versioned token over its stage, its
  sheet id, the revision, the ordered units it owns and the policy that shaped
  it; the reply echoes the line back. An absent, foreign, old or wrong-stage
  token is refused by name — including when it arrives *after* a regeneration,
  which superseding cannot help with because the file appears later.
* **Which sheet.** A claim is checked against the file it was written in. Unioning
  every approval claim let one reply discharge two sheets while the second reply
  was empty.
* **Its own grammar.** A stage declares the control records it understands and
  refuses the others. A fluency `++` edit filed into the meaning stage used to
  read as a clean approval, and a meaning `??` finding filed into the fluency
  stage was ignored — in both directions the reviewer's actual work was discarded
  and the result was "no findings".

`superseded/<revision>/` keeps what a regeneration replaces, for the same reason
the worksheet route keeps it: nobody's review is deleted to make a gate pass.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import bookir as ir
from worksheet import SCAFFOLD_COMMENT, comment  # noqa: F401  (the stages read it)

#: Version tag, so a reader that cannot recompute a token says so rather than
#: guessing. Invariant 9 in AGENTS.md.
REVIEW_VERSION = "rev1"

#: Hex characters of the digest that travel in the sheet. Plenty to separate two
#: generations of one sheet, short enough to copy on one line.
TOKEN_CHARS = 16

#: ``<!-- revayat-novel: review meaning sheet_0001 rev1:0123456789abcdef -->``
REQUEST = re.compile(
    r"^<!--\s*revayat-novel:\s*review\s+(?P<stage>[a-z]+)\s+"
    r"(?P<sheet>[A-Za-z0-9_-]+)\s+(?P<token>[A-Za-z0-9:._-]+)\s*-->$")

#: ``!! reviewed sheet_0001`` — the claim that this sheet was actually read. A
#: findings file with no findings is ambiguous without it, and an unanswered
#: question is not a question nobody had a problem with.
REVIEWED = re.compile(r"^!!\s+reviewed\s+(?P<sheet>[A-Za-z0-9_-]+)\s*$")

#: Every control sigil this project uses anywhere, with the stage that owns it.
#: A stage refuses the ones it does not own instead of ignoring them: an ignored
#: control record is a reviewer's finding thrown away, reported as "clean".
SIGILS = {
    "??": "meaning",
    "++": "fluency",
    "!!": "claim",
    "@@": "worksheet",
}

_CONTROL = re.compile(r"^(?P<sigil>\?\?|\+\+|!!|@@)(?:\s|$)")


def bounded(name: str, value: int) -> str:
    """``""`` when ``value`` is a usable count, else why it is not.

    Checked **before anything is written**. `range(0, n, -1)` yields nothing, so
    `--per-sheet -1` wrote no sheets for a nonempty book, `record` found no
    unclaimed sheets, and both stages then approved it — a full pass on a book
    nobody had looked at. `--per-sheet 0` raised a bare `ValueError` from inside
    `range`, which is a crash rather than an answer.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        return f"{name} must be a whole number, not {value!r}"
    if value < 1:
        return (f"{name} is {value}; it has to be at least 1, or the run writes "
                f"no sheets and every later gate reads that as nothing to review")
    return ""


def token(*, stage: str, sheet_id: str, revision: str,
          unit_ids: list[str], policy: str = "") -> str:
    """The identity of one review request.

    Covers everything that decides what this reviewer is being asked: which stage
    is asking, which sheet, the revision of the text, the ordered units the sheet
    owns, and the policy or rubric table that shaped the question. Any of those
    moving is a different question, and an answer to the old one is not an answer
    to it.
    """
    digest = hashlib.sha256()
    for part in (stage, sheet_id, revision, policy):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    for unit_id in unit_ids:
        digest.update(unit_id.encode("utf-8"))
        digest.update(b"\x1e")
    return f"{REVIEW_VERSION}:{digest.hexdigest()[:TOKEN_CHARS]}"


def request_line(stage: str, sheet_id: str, value: str) -> str:
    return comment(f"review {stage} {sheet_id} {value}")


def request_of(text: str) -> tuple[str, str, str]:
    """``(stage, sheet, token)`` from the first review line, or three empties."""
    for line in (text or "").splitlines():
        found = REQUEST.match(line.strip())
        if found:
            return found.group("stage"), found.group("sheet"), found.group("token")
    return "", "", ""


def claims(text: str) -> list[str]:
    """Every sheet this file claims to have reviewed, in order, with repeats."""
    return [found.group("sheet")
            for line in (text or "").splitlines()
            if (found := REVIEWED.match(line.strip()))]


def foreign_controls(text: str, *, owns: str) -> list[str]:
    """Control records this stage does not own, as the lines that carry them.

    Not a style check. The alternative to refusing them is what both stages did
    before: parse only the sigil you recognise, find none, and report no
    findings — so a reviewer's whole reply became an approval.
    """
    offending: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        found = _CONTROL.match(stripped)
        if found is None:
            continue
        sigil = found.group("sigil")
        owner = SIGILS.get(sigil, "")
        if owner in (owns, "claim"):
            continue
        offending.append(stripped[:80])
    return offending


def reply_problems(text: str, *, stage: str, sheet_id: str,
                   expected: str, owns: str) -> list[str]:
    """Why this reply does not answer this sheet — empty when it does.

    A genuine no-findings approval is valid and passes here; an empty or missing
    file is not, and neither is one that answers a different sheet.
    """
    problems: list[str] = []
    if not (text or "").strip():
        problems.append(
            f"{sheet_id}: the reply is empty. Silence is not approval — say "
            f"`!! reviewed {sheet_id}` to record that you read it and found "
            f"nothing")
        return problems

    said_stage, said_sheet, said_token = request_of(text)
    if not said_token:
        problems.append(
            f"{sheet_id}: the reply echoes no review line, so nothing says which "
            f"sheet or which revision it answers. Copy the "
            f"`<!-- revayat-novel: review … -->` line from the sheet, unchanged")
    elif said_token.partition(":")[0] != REVIEW_VERSION:
        problems.append(
            f"{sheet_id}: the reply echoes a {said_token.partition(':')[0] or 'untagged'} "
            f"review token and this version writes {REVIEW_VERSION}; it cannot be "
            f"compared either way, so review the sheet again")
    elif said_stage != stage or said_sheet != sheet_id:
        problems.append(
            f"{sheet_id}: this reply answers {said_stage or '?'}/"
            f"{said_sheet or '?'}, not {stage}/{sheet_id}")
    elif said_token != expected:
        problems.append(
            f"{sheet_id}: this reply answers review {said_token} and the sheet now "
            f"asks {expected}. The text changed after the reply was written — the "
            f"earlier one is in superseded/ to read from, but it describes text "
            f"this sheet no longer shows")

    mine = [claimed for claimed in claims(text) if claimed == sheet_id]
    others = [claimed for claimed in claims(text) if claimed != sheet_id]
    if others:
        problems.append(
            f"{sheet_id}: this reply also claims {sorted(set(others))}. A claim is "
            f"only worth anything for the sheet it was written in — unioning them "
            f"let one reply discharge two sheets while the other was empty")
    if not mine:
        problems.append(
            f"{sheet_id}: no `!! reviewed {sheet_id}` line, so nothing says this "
            f"sheet was read")

    foreign = foreign_controls(text, owns=owns)
    if foreign:
        problems.append(
            f"{sheet_id}: this reply carries control records the {stage} stage "
            f"does not read: {foreign}. Ignoring them would report your findings "
            f"as a clean approval")
    return problems


def archive_replies(out_dir: Path, revision: str) -> list[str]:
    """Move the replies a regeneration supersedes into ``superseded/<revision>/``.

    Kept, never deleted: a reviewer's argument about the previous text is the
    evidence for what changed and why, and the next reviewer reads it. The
    directory is named for the revision the replies answered, so two generations
    do not land on top of each other.
    """
    out_dir = Path(out_dir)
    existing = sorted(out_dir.glob("out_*.md"))
    if not existing:
        return []
    tag = revision.partition(":")[2][:16] or "unknown"
    home = out_dir / "superseded" / tag
    home.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for path in existing:
        shutil.move(str(path), str(home / path.name))
        moved.append(path.name)
    return moved


def coverage_problems(sheets: dict[str, list[str]],
                      inventory: list[str]) -> list[str]:
    """Do the sheets own every unit exactly once? Empty when they do.

    The manifest saying "two sheets" proves nothing about whether those two
    sheets between them cover the book. A unit in no sheet is a unit nobody was
    asked about, and it disappears from the required coverage silently.
    """
    problems: list[str] = []
    owned: dict[str, int] = {}
    for sheet_id, unit_ids in sheets.items():
        for unit_id in unit_ids:
            owned[unit_id] = owned.get(unit_id, 0) + 1
            if unit_id not in inventory:
                problems.append(
                    f"{sheet_id} owns {unit_id}, which is not a reviewable unit "
                    f"of this book")
    twice = sorted(unit_id for unit_id, count in owned.items() if count > 1)
    if twice:
        problems.append(f"these units are on more than one sheet: {twice[:8]}")
    missing = [unit_id for unit_id in inventory if unit_id not in owned]
    if missing:
        problems.append(
            f"{len(missing)} unit(s) are on no sheet at all, so nobody was asked "
            f"about them: {missing[:8]}")
    return problems


def write_reply_stub(out_dir: Path, stage: str, sheet_id: str, value: str) -> Path:
    """The echo line, ready for a reviewer to write under. Never overwrites."""
    path = Path(out_dir) / f"out_{sheet_id}.md"
    if not path.exists():
        ir.write_text(path, request_line(stage, sheet_id, value) + "\n")
    return path

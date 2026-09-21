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
import json
import re
import uuid
from pathlib import Path

import bookir as ir
import reviewstate
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

_CONTROL = re.compile(r"^(?P<sigil>\?\?|\+\+|!!|@@)")
PAYLOAD_ESCAPE = re.compile(r"^(\s*)\\(\\*(?:\?\?|\+\+|!!|@@|~\s|<!--\s*revayat-novel\b))")


def escape_payload(text: str) -> str:
    lines = []
    for line in text.split("\n"):
        bare = line.lstrip().lstrip("\\")
        if _CONTROL.match(bare) or bare.startswith("~ ") or bare.startswith("<!-- revayat-novel"):
            indent = line[:len(line) - len(line.lstrip())]
            line = indent + "\\" + line[len(indent):]
        lines.append(line)
    return "\n".join(lines)


def parse_records(text, *, stage, header, rubrics, payload):
    """Every control line is parsed or rejected; one escape layer is reversible."""
    records, claimed, problems, buffer = [], [], [], []
    current = None
    ended = False

    def flush():
        if current is not None:
            current[payload] = "\n".join(buffer).strip()
            records.append(current)

    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if PAYLOAD_ESCAPE.match(line):
            if current is not None:
                buffer.append(PAYLOAD_ESCAPE.sub(r"\1\2", line))
            continue
        control = _CONTROL.match(stripped)
        if control:
            match, claim = header.fullmatch(stripped), REVIEWED.fullmatch(stripped)
            if ended:
                problems.append(f"line {number}: trailing control after the review claim")
            flush()
            current, buffer = None, []
            if match and not ended:
                current = {"id": match.group("id"), "rubric": match.group("rubric")}
            elif claim and not ended:
                claimed.append(claim.group("sheet"))
                ended = True
            else:
                problems.append(f"line {number}: malformed or foreign {stage} control: {stripped[:80]}")
            continue
        if SCAFFOLD_COMMENT.match(stripped):
            if ended:
                problems.append(f"line {number}: trailing request/control record")
            if not REQUEST.fullmatch(stripped) and not (
                    stage == "fluency" and stripped == comment("context, not under review")):
                problems.append(f"line {number}: unrecognized control comment")
            continue
        if current is not None and not (stage == "fluency" and re.match(r"^~\s", stripped)):
            buffer.append(line)
    flush()
    for item in records:
        if item["rubric"] not in rubrics:
            problems.append(f"{item['id']}: {item['rubric']} is not a rubric")
        if not item[payload]:
            problems.append(f"{item['id']}: no {'argument' if payload == 'detail' else 'replacement'} given")
    return records, claimed, problems


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

    request_lines = [line.strip() for line in text.splitlines()
                     if line.strip().startswith("<!--") and "revayat-novel:" in line
                     and re.search(r"\breview\b", line)]
    if len(request_lines) != 1 or not all(REQUEST.fullmatch(line) for line in request_lines):
        problems.append(f"{sheet_id}: expected exactly one valid review request line")
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
    if len(mine) > 1:
        problems.append(f"{sheet_id}: duplicate review claims")

    foreign = foreign_controls(text, owns=owns)
    if foreign:
        problems.append(
            f"{sheet_id}: this reply carries control records the {stage} stage "
            f"does not read: {foreign}. Ignoring them would report your findings "
            f"as a clean approval")
    return problems


def archive_replies(out_dir: Path, revision: str) -> list[str]:
    """Archive each response under its own request identity, without overwrites."""
    out_dir = Path(out_dir)
    previous = reviewstate.object_file(out_dir / "manifest.json", optional=True)
    if previous is not None:
        manifest_shape(previous)
    existing = sorted(out_dir.glob("out_*.md"))
    if not existing:
        return []
    moved: list[str] = []
    for path in existing:
        body = path.read_bytes()
        stage, sheet_id, request = request_of(body.decode("utf-8"))
        tag = re.sub(r"[^A-Za-z0-9_.-]", "_", request) or "unbound"
        home = out_dir / "superseded" / tag / uuid.uuid4().hex
        home.mkdir(parents=True, exist_ok=False)
        with (home / path.name).open("xb") as target:
            target.write(body)
        own_revision = None
        if previous and previous["requests"].get(sheet_id) == request:
            own_revision = previous["revision"]
        ir.write_text(home / "identity.json", json.dumps({
            "stage": stage, "sheet": sheet_id, "request": request,
            "revision": own_revision, "sha256": hashlib.sha256(body).hexdigest()}, indent=1))
        if path.read_bytes() != body:
            raise reviewstate.Refused("response-changed", "response changed while it was being archived; both retained")
        path.unlink()
        moved.append(path.name)
    return moved


def manifest_shape(manifest):
    """Validate the old manifest before regeneration can move any evidence."""
    reviewstate.require(isinstance(manifest, dict), "manifest must be an object")
    reviewstate.require(manifest.get("schema") in {
        f"revayat-novel/{stage}@{version}" for stage in ("meaning", "fluency") for version in (1, 2)},
        "unsupported review manifest schema")
    reviewstate.require(reviewstate.strings(manifest.get("sheets")), "invalid sheet enumeration")
    sheets = manifest["sheets"]
    reviewstate.require(len(sheets) == len(set(sheets)) and all(re.fullmatch(r"sheet_[0-9]+", s) for s in sheets),
                        "duplicate or unsafe sheet id")
    reviewstate.require(isinstance(manifest.get("owned"), dict) and isinstance(manifest.get("requests"), dict),
                        "invalid ownership or request map")
    reviewstate.require(set(sheets) == set(manifest["owned"]) == set(manifest["requests"]),
                        "sheet enumeration, ownership and request keys disagree")
    reviewstate.require(all(reviewstate.strings(manifest["owned"][s]) and bool(manifest["owned"][s])
                            and isinstance(manifest["requests"][s], str) for s in sheets), "invalid sheet members")
    reviewstate.require(isinstance(manifest.get("revision"), str) and type(manifest.get("units")) is int,
                        "invalid manifest revision or count")


def manifest_problems(manifest, *, stage, revision, inventory, policy, out_dir, render=None):
    try:
        manifest_shape(manifest)
    except reviewstate.Refused as error:
        return [error.detail]
    problems = coverage_problems(manifest["owned"], inventory)
    ordered = [unit for sheet_id in manifest["sheets"] for unit in manifest["owned"][sheet_id]]
    if ordered != inventory or manifest["units"] != len(inventory):
        problems.append("ordered ownership and expected unit count disagree with the live inventory")
    if manifest["schema"] not in (f"revayat-novel/{stage}@1", f"revayat-novel/{stage}@2"):
        problems.append("foreign-stage manifest")
    if problems:
        return problems
    for sheet_id in manifest["sheets"]:
        expected = token(stage=stage, sheet_id=sheet_id, revision=revision,
                         unit_ids=manifest["owned"][sheet_id], policy=policy)
        if manifest["requests"][sheet_id] != expected:
            problems.append(f"{sheet_id}: request does not match the live stage, revision, rubric and owned payload")
        try:
            question = (Path(out_dir) / f"{sheet_id}.md").read_text(encoding="utf-8")
            if request_of(question) != (stage, sheet_id, expected):
                problems.append(f"{sheet_id}: worksheet and manifest requests disagree")
            if render is not None and question != render(sheet_id, manifest["owned"][sheet_id], expected):
                problems.append(f"{sheet_id}: worksheet payload does not match the live request")
        except (OSError, UnicodeError):
            problems.append(f"{sheet_id}: worksheet is unreadable")
    return problems


def event_identity(manifest, responses):
    body = json.dumps([manifest["schema"], manifest["revision"],
                       [(sheet, manifest["requests"][sheet], responses[sheet])
                        for sheet in manifest["sheets"]]], ensure_ascii=False, separators=(",", ":"))
    return "review-event1:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


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

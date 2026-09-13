"""The worksheet transport: one grammar, one validator, one verdict.

A worksheet is the question put to a translator and `out_chunkNNNN.md` is the
answer. Both sides of that exchange used to be described in two places — `chunk`
owned the header and the escape, `merge` owned the fence, the reader and the
validator — and every disagreement this module exists to prevent came from that
split:

* `chunk build` promised that a rebuild would make merge "reject every id that
  moved"; merge's freshness check compared the manifest against the book it was
  written from and could not fail.
* `chunk status` reported a worksheet `answered` by building a dict of the
  replies and counting the populated ids. A dict keyed by id has already lost the
  duplicate and the order by the time it exists, so a reply merge refuses read as
  finished and the job was never offered again.
* `escape_payload` marked an indented literal `@@` line by writing the backslash
  *before* the indentation, and `ESCAPED_HEADER` looks for it after — so the mark
  survived into the book.

So the grammar, the reader, the validator and the state verdict live here, and
`chunk` and `merge` both ask this module. They keep re-exporting the names they
used to own: a name a test imports is public whatever moved underneath it.
"""

from __future__ import annotations

import re
from typing import Any

import bookir as ir

#: One unit of the exchange. The ``#`` and ``-`` in the id carry real meaning —
#: ``b00001#2`` is the second segment of a long block and ``tr-01`` a note the
#: translator added. Omitting the hyphen once did not *reject* a translator's
#: note, it stopped the header being recognised at all, so the note's body was
#: swallowed into the previous paragraph and merge still reported success.
HEADER = re.compile(r"^@@\s+(?P<id>[A-Za-z0-9_#-]+)\s+(?P<kind>[a-z0-9]+)\s*$")

#: A footnote the translator introduced, numbered per worksheet. Merge allocates
#: it a real book-wide id.
TRANSLATOR_NOTE = re.compile(r"^tr-[A-Za-z0-9_-]+$")

#: A code fence. Models wrap their output in one, and neither the fence nor a
#: closing pleasantry is part of the novel.
#:
#: The delimiter and its length are captured because a wrapper is identified by
#: the fence that **opened** it. Without that, a reply wrapped in ``` whose own
#: prose contains a ``~~~`` line was cut at that line and everything after it was
#: discarded — silently, with merge reporting success, because "is this line a
#: fence" is true of both families. The rule here is CommonMark's: a fence closes
#: only with the same character, at least as long, and nothing else on the line.
FENCE = re.compile(r"^\s*(?P<mark>`{3,}|~{3,})\s*(?P<info>[A-Za-z0-9_+-]*)\s*$")


def closes(opener: re.Match[str], line: str) -> bool:
    """Does ``line`` close the wrapper ``opener`` started?"""
    found = FENCE.match(line)
    if found is None:
        return False
    mark, opened = found.group("mark"), opener.group("mark")
    # Same family, at least as long, and a closing fence carries no info string.
    return mark[0] == opened[0] and len(mark) >= len(opened) and not found.group("info")


#: Lines a *body* must not be able to impersonate: a unit header, and this
#: project's own scaffolding comment. Both are written by us into the envelope,
#: so a line of the novel that looks like one has to be escaped on the way out
#: and restored on the way back — otherwise an author's ``<!-- revayat-novel: …
#: -->`` line was deleted as though we had written it.
def reserved(line: str) -> bool:
    stripped = line.strip()
    return bool(HEADER.match(stripped) or SCAFFOLD_COMMENT.match(stripped))


#: :func:`escape_payload`'s mark on a source line that would otherwise parse as
#: envelope, removed on the way back in. The indentation is captured so an
#: indented line comes back indented.
#:
#: Exactly **one** backslash comes off, and any remaining ones are kept by the
#: second group. A unit whose own text is the line ``\@@ b00007 para`` otherwise
#: arrived one backslash short: nothing escaped it on the way out — its stripped
#: form is not a header — and this stripped one on the way back. One layer out,
#: one layer in, at any depth.
ESCAPED_HEADER = re.compile(
    r"^(\s*)\\(\\*(?:@@\s|<!--\s*revayat-novel\b))")

#: Scaffolding *this project* wrote into the worksheet. Only these are dropped
#: when they come back echoed. An unmarked comment-shaped line is book content —
#: deleting it to be safe is how a line of the novel disappears silently, which
#: is worse than a stray comment a reader can see.
SCAFFOLD_COMMENT = re.compile(r"^<!--\s*revayat-novel\b.*-->$")

#: Every generated comment carries this, so the reader above can tell them apart.
SCAFFOLD_MARK = "revayat-novel:"

#: The one piece of envelope metadata a reply has to carry back: which request it
#: answers. Everything else about a worksheet is a question; this is its identity.
#:
#: A reply file lives at a name derived from the job number, so after a rebuild an
#: answer to the *previous* cut sits at exactly the path the new cut expects. Ids
#: and counts can be identical across the two — a paragraph recut from one budget
#: to another keeps both — so nothing in the filename, the id list or the source
#: text of the parent block distinguishes the generations. Measured: a 4-segment
#: cut's answers merged into a 3-segment cut, `ok: true`, `stale: []`, and the
#: paragraph came out as the older generation's text.
#:
#: So the question carries a token and the answer brings it back.
REQUEST = re.compile(
    r"^<!--\s*revayat-novel:\s*request\s+(?P<token>[A-Za-z0-9:._-]+)\s*-->$")


def request_line(token: str) -> str:
    """The line a worksheet carries and a reply must echo."""
    return comment(f"request {token}")


def request_of(text: str | None) -> str:
    """The request token a reply echoes, or ``""`` if it carries none.

    Read from the whole file rather than from the payload: a model that puts the
    line above its code fence has still echoed it, and refusing that would fail
    replies that are otherwise perfect.
    """
    for line in (text or "").splitlines():
        found = REQUEST.match(line.strip())
        if found:
            return found.group("token")
    return ""


def comment(body: str) -> str:
    """A worksheet comment the reader will recognise as ours and discard."""
    return f"<!-- {SCAFFOLD_MARK} {body} -->"


def escape_payload(text: str) -> str:
    """Protect a source line that would otherwise parse as a worksheet header.

    A line of a novel beginning ``@@`` is vanishingly rare and the damage is
    silent: the unit ends there and a second one appears, made of source text,
    under an id the manifest really does expect.

    The backslash goes **after** the indentation, because that is where
    :data:`ESCAPED_HEADER` looks for it. Writing it at column zero — which this
    did — left both the mark and the indent in the translated book.

    If a translator drops the backslash the line returns as a *duplicate* header
    and the merge refuses it by name. That is the point of an escape over
    trusting the shape of the text: the failure is loud.

    A line that is *already* in escaped form is escaped again, because the reader
    takes one backslash off anything shaped that way and cannot know who put it
    there. Without this the round trip was asymmetric for exactly that input —
    found by the generated round-trip property in `tests/test_properties.py`,
    never by an example.
    """
    out: list[str] = []
    for line in text.split("\n"):
        if reserved(line.strip().lstrip("\\")):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(f"{indent}\\{line[len(indent):]}")
        else:
            out.append(line)
    return "\n".join(out)


def payload(text: str) -> tuple[list[str], list[str]]:
    """``(the answer's lines, transport problems)``, outer wrapper removed.

    A wrapper is recognised **only** when the first non-blank line is a fence and
    the last non-blank line is a fence. Taking the slice after the first fence
    found *anywhere* is what let an earlier answer vanish before anything
    validated it: a model that answers, fences, and answers again had its first
    reply silently discarded.

    Anything else is treated as unwrapped payload, so no content is dropped on a
    guess. A fence that opens and never closes is reported — it is the shape of a
    truncated reply, and the caller should say so rather than merge half a book.
    """
    lines = text.splitlines()
    # Our own scaffolding does not count as the first line. A reply that echoes
    # the `request` comment above its code fence is the ordinary shape, and taking
    # that comment as "the first non-blank line is not a fence" lost wrapper
    # detection entirely — including the truncated-reply signal.
    filled = [i for i, line in enumerate(lines)
              if line.strip() and not SCAFFOLD_COMMENT.match(line.strip())]
    if not filled:
        return [], []

    first = filled[0]
    opener = FENCE.match(lines[first])
    if opener is None:
        # No wrapper. This is the branch that closes the hole: a fence further
        # down belongs to whatever unit contains it, and slicing from it threw
        # away every answer above.
        return lines, []

    # The *opener's* delimiter decides what closes it. Matching any fence here
    # cut a ```-wrapped reply at the first ``~~~`` line inside its own prose and
    # dropped the rest without a word.
    closing = next((i for i in range(first + 1, len(lines))
                    if closes(opener, lines[i])), None)
    if closing is None:
        return lines[first + 1:], [
            "the reply opens a code fence and never closes it, which is the "
            "shape of a truncated answer — check the end of the file before "
            "trusting what merged"]

    after = lines[closing + 1:]
    if any(HEADER.match(line.strip()) for line in after):
        # Answers on both sides of the closing fence: there is no single outer
        # wrapper here, and choosing one would discard real work. Keep all of it
        # and let the duplicate/order checks refuse it by name.
        return lines, [
            "the reply has @@ headers both inside and after a code fence, so "
            "which part is the answer is ambiguous — nothing was discarded, but "
            "it will not validate until the reply has one body"]
    # Trailing prose after the closing fence is the model signing off. Dropping
    # it is the whole reason the slice is taken *between* fences: left in, it
    # becomes the last paragraph of the book.
    return lines[first + 1:closing], []


def read_reply(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """``(ordered {id, kind, text}, transport problems)``.

    Ordered and typed on purpose: answered twice, answered as the wrong kind and
    answered out of order are all questions about the *sequence* of headers, and
    a mapping keyed by id can answer none of them.
    """
    body, problems = payload(text)
    entries: list[dict[str, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        if entries:
            entries[-1]["text"] = "\n".join(buffer).strip()

    for line in body:
        stripped = line.strip()
        match = HEADER.match(stripped)
        if match:
            flush()
            entries.append({"id": match.group("id"),
                            "kind": match.group("kind"), "text": ""})
            buffer = []
            continue
        if not entries:
            # Instructions the model restated before the first header. It cannot
            # reach a unit, so it is dropped rather than refused — a reply that
            # echoes the brief is still a good reply.
            continue
        if SCAFFOLD_COMMENT.match(stripped):
            continue            # our own comment, echoed back
        buffer.append(ESCAPED_HEADER.sub(r"\1\2", line))
    flush()
    return entries, problems


def read_worksheet(text: str) -> list[dict[str, str]]:
    """:func:`read_reply` without the transport problems, for callers that have
    nowhere to put them."""
    return read_reply(text)[0]


def parse_worksheet(text: str) -> dict[str, str]:
    """``unit id -> translated text``.

    The flat view, for callers that only want the mapping. A duplicate id
    resolves to its last occurrence here; every caller that decides whether to
    *write* reads the ordered form and refuses it by name instead.
    """
    return {entry["id"]: entry["text"] for entry in read_worksheet(text)}


def validate_reply(
    entries: list[dict[str, str]],
    expected: list[str],
    kinds: dict[str, str],
) -> tuple[list[str], set[str]]:
    """``(problems, unit ids that must not be written)``.

    ``kinds`` may be empty for a manifest written before kinds were recorded; the
    check is then skipped and the caller says so in its report rather than
    inventing a kind to compare against.
    """
    expected_set = set(expected)
    problems: list[str] = []
    rejected: set[str] = set()
    seen: list[str] = []

    for item in entries:
        if item["id"] in seen:
            problems.append(
                f"{item['id']}: answered more than once — which of the two "
                f"translations is the paragraph cannot be guessed")
            rejected.add(item["id"])
        seen.append(item["id"])

        wanted = kinds.get(item["id"])
        if wanted and item["kind"] != wanted:
            problems.append(
                f"{item['id']}: answered as {item['kind']!r} but asked as "
                f"{wanted!r}")
            rejected.add(item["id"])

    first_seen = list(dict.fromkeys(seen))
    answered_order = [i for i in first_seen if i in expected_set]
    wanted_order = [i for i in expected if i in set(answered_order)]
    if answered_order != wanted_order:
        problems.append(
            f"headers came back in a different order: {answered_order} "
            f"against the {wanted_order} the worksheet asked for")
        rejected |= set(seen)

    return problems, rejected


#: A worksheet with no translatable unit — all images, say. It is finished the
#: moment it is cut, and asking a translator for prose it does not contain is how
#: invented sentences get into a book.
NOTHING_TO_TRANSLATE = "nothing-to-translate"


def classify(text: str | None, expected: list[str],
             kinds: dict[str, str]) -> str:
    """One verdict on one reply, shared by merge, status and scheduling.

    ``missing`` · ``empty`` · :data:`NOTHING_TO_TRANSLATE` · ``invalid`` a reply
    merge will refuse · ``malformed`` answers none of the units asked for ·
    ``partial`` some · ``answered`` all of them.

    ``invalid`` is the state that was absent, and its absence is the defect: a
    reply with a duplicated id, a wrong kind or reordered headers was counted
    `answered` here while merge refused it, so `status` reported nothing left to
    do and the job was never offered again. Side-effect free on purpose — a
    verdict that writes is a verdict nobody can ask twice.
    """
    if not expected:
        return NOTHING_TO_TRANSLATE
    return verdict(text, expected, kinds)["state"]


#: What a translator may call their own note. ``footnote`` is what `SKILL.md` and
#: the translation policy actually ask for; ``note`` is the obvious near-miss of
#: that word and is accepted rather than refused, because the intent is
#: unambiguous and the hazard this check exists for is elsewhere — a note answered
#: as ``heading1``, ``para`` or ``alt`` would be filed as structure.
NOTE_KINDS = frozenset(("footnote", "note"))


def validate_note_graph(texts: dict[str, str], offered: dict[str, str],
                        kinds: dict[str, str]) -> list[str]:
    """Does this reply's footnote graph resolve? Checked before anything is written.

    Four shapes used to reach the book unchallenged, and each one prints: a marker
    with no body left a literal ``[[fn:tr-01]]`` in the finished prose, a body with
    no marker became a note nothing refers to, the same marker twice made ownership
    unguessable, and ``@@ tr-01 heading1`` was adopted as a footnote on the strength
    of its id alone.

    It lives here, with the rest of the verdict, because `merge` ran it and
    `status` did not: an orphan note made merge refuse while status reported the
    job answered and `next: null`, so a resume loop had nothing left to offer and
    merge could never succeed. One reply, one verdict.

    Markers are taken from the parsed markup, not a raw scan, so a marker shown
    inside a code span is an example rather than a reference.
    """
    problems: list[str] = []
    used: list[str] = []
    for text in texts.values():
        # `include_local=True` is load-bearing: without it the canonical-only
        # form returns no `tr-NN` at all, every offered note looks orphaned, and
        # every reply carrying one is refused. A check reading a value nothing
        # provides, which is this repository's most frequent defect.
        used += [ref for ref in ir.footnote_refs(text or "", include_local=True)
                 if TRANSLATOR_NOTE.match(ref)]

    for local_id in dict.fromkeys(used):
        if local_id not in offered:
            problems.append(
                f"{local_id}: the translation refers to this note and the reply "
                f"carries no `@@ {local_id} footnote` body for it — merging would "
                f"leave the marker itself in the book")
        if used.count(local_id) > 1:
            problems.append(
                f"{local_id}: referred to {used.count(local_id)} times. One note "
                f"cannot belong to two places, and picking one silently drops the "
                f"other")

    # A note body that itself refers to a note. Unsupported rather than merely
    # unresolved: there is no anchor for a footnote inside a footnote, so the
    # marker prints. It was invisible because the graph scanned the units and the
    # bodies live beside them.
    for local_id, body in offered.items():
        nested = [ref for ref in ir.footnote_refs(body or "", include_local=True)
                  if TRANSLATOR_NOTE.match(ref)]
        for ref in nested:
            problems.append(
                f"{local_id}: its body refers to {ref}. A note inside a note has "
                f"nowhere to anchor, so the marker would print in the footnote "
                f"itself — put the remark in this note's own text")

    for local_id in offered:
        if local_id not in used:
            problems.append(
                f"{local_id}: a note body no translation refers to. It would print "
                f"at the foot of a page with no number pointing at it")
        kind = kinds.get(local_id)
        if kind is not None and kind not in NOTE_KINDS:
            problems.append(
                f"{local_id}: answered as {kind!r}, which is not a note kind "
                f"({' or '.join(sorted(NOTE_KINDS))}). Adopting it would file a "
                f"heading, a paragraph or an alt text as a footnote")
    return problems


def verdict(text: str | None, expected: list[str],
            kinds: dict[str, str]) -> dict[str, Any]:
    """Everything both sides need to know about one reply, decided once.

    `merge` writes the book and `status`/`next` decide what is left to do. While
    each derived its own answer from the same file they disagreed in both
    directions, and each direction has a failure mode that cannot be recovered
    from by trying again:

    * merge refused an orphan, wrong-kind or missing translator-note body while
      status called the job answered and `next` reported nothing outstanding — a
      resume loop with nothing left to offer and a merge that can never succeed;
    * a zero-unit job (an image-only page, a blank verso) was finished the moment
      it was cut as far as status was concerned, and merge demanded an output file
      for it.

    So the whole verdict is computed here, side-effect free, and both sides read
    fields off it. ``state`` is the one-word summary `classify` returns;
    ``entries``, ``answered``, ``notes`` and ``rejected`` are what merge needs to
    act. Freshness is *not* here: it is a question about the book, which this
    module cannot see, so merge appends it to ``problems`` itself.
    """
    result: dict[str, Any] = {
        "state": "", "problems": [], "entries": [], "answered": {},
        "notes": {}, "note_kinds": {}, "rejected": set(),
        "missing": [], "extra": [], "blank": [],
    }
    # One definition of zero-unit completion, for both sides. A job that asks for
    # nothing is finished when it is cut, and no reply file is expected.
    if not expected:
        result["state"] = NOTHING_TO_TRANSLATE
        return result
    # A reply that is absent or blank answers *every* unit with nothing, and the
    # list has to say so: returning early with an empty `missing` made merge see
    # no problem and no missing unit, so a blank `out_chunkNNNN.md` merged as a
    # success that applied nothing while `status` correctly called the job
    # unfinished. Found by the truth table in `tests/test_one_verdict.py`, which
    # is the whole reason for asking every shape the same three questions.
    if text is None:
        result.update({"state": "missing", "missing": list(expected)})
        return result
    if not text.strip():
        result.update({"state": "empty", "missing": list(expected)})
        return result

    entries, problems = read_reply(text)
    ordered, rejected = validate_reply(entries, expected, kinds)
    problems = problems + ordered
    wanted = set(expected)

    answered = {item["id"]: item["text"] for item in entries
                if item["id"] in wanted}
    notes = {item["id"]: item["text"].strip() for item in entries
             if item["id"] not in wanted and TRANSLATOR_NOTE.match(item["id"])
             and item["text"].strip()}
    note_kinds = {item["id"]: item["kind"] for item in entries
                  if item["id"] not in wanted and TRANSLATOR_NOTE.match(item["id"])}
    extra = sorted({item["id"] for item in entries} - wanted - set(note_kinds))
    if extra:
        problems.append(
            f"answers for units this worksheet never asked about: {extra}")

    problems += validate_note_graph(answered, notes, note_kinds)

    present = [unit for unit in expected if (answered.get(unit) or "").strip()]
    result.update({
        "problems": problems, "entries": entries, "answered": answered,
        "notes": notes, "note_kinds": note_kinds, "rejected": rejected,
        "missing": [unit for unit in expected if unit not in present],
        "extra": extra,
        "blank": [unit for unit, value in answered.items() if not value.strip()],
    })
    if problems:
        result["state"] = "invalid"
    elif not present:
        result["state"] = "malformed"
    else:
        result["state"] = ("answered" if len(present) == len(expected)
                           else "partial")
    return result

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
FENCE = re.compile(r"^\s*(?:```|~~~)\s*[A-Za-z0-9_+-]*\s*$")

#: :func:`escape_payload`'s mark on a source line that would otherwise parse as
#: a header, removed on the way back in. The indentation is captured so an
#: indented line comes back indented.
#:
#: Exactly **one** backslash comes off, and any remaining ones are kept by the
#: second group. A unit whose own text is the line ``\@@ b00007 para`` otherwise
#: arrived one backslash short: nothing escaped it on the way out — its stripped
#: form is not a header — and this stripped one on the way back. One layer out,
#: one layer in, at any depth.
ESCAPED_HEADER = re.compile(r"^(\s*)\\(\\*@@\s)")

#: Scaffolding *this project* wrote into the worksheet. Only these are dropped
#: when they come back echoed. An unmarked comment-shaped line is book content —
#: deleting it to be safe is how a line of the novel disappears silently, which
#: is worse than a stray comment a reader can see.
SCAFFOLD_COMMENT = re.compile(r"^<!--\s*revayat-novel\b.*-->$")

#: Every generated comment carries this, so the reader above can tell them apart.
SCAFFOLD_MARK = "revayat-novel:"


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
        if HEADER.match(line.strip().lstrip("\\")):
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
    filled = [i for i, line in enumerate(lines) if line.strip()]
    if not filled:
        return [], []

    first = filled[0]
    if FENCE.match(lines[first]) is None:
        # No wrapper. This is the branch that closes the hole: a fence further
        # down belongs to whatever unit contains it, and slicing from it threw
        # away every answer above.
        return lines, []

    closing = next((i for i in range(first + 1, len(lines))
                    if FENCE.match(lines[i])), None)
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
    if text is None:
        return "missing"
    if not text.strip():
        return "empty"

    entries, problems = read_reply(text)
    problems += validate_reply(entries, expected, kinds)[0]

    answered = {item["id"] for item in entries if item["text"].strip()}
    extra = [item["id"] for item in entries
             if item["id"] not in set(expected)
             and not TRANSLATOR_NOTE.match(item["id"])]
    if extra:
        problems.append(
            f"answers for units this worksheet never asked about: {sorted(extra)}")
    if problems:
        return "invalid"

    present = [unit for unit in expected if unit in answered]
    if not present:
        return "malformed"
    return "answered" if len(present) == len(expected) else "partial"

"""The worksheet grammar, and the verdict status and merge now share.

Three defects lived in the split between `chunk` (header, escape) and `merge`
(fence, reader, validator), and they share a shape: each one silently *removed*
something instead of refusing it.

* `_payload` sliced from the first fence found anywhere, so a model that answered,
  fenced, and answered again had its first reply discarded before anything could
  validate it.
* the reader dropped every standalone HTML comment, so a line of the book that
  happens to be comment-shaped disappeared.
* `escape_payload` wrote its backslash at column zero while `ESCAPED_HEADER` looks
  for it after the indent, so the mark survived into the translated book.

And `chunk._state_of` answered "is this worksheet done" with a dict comprehension
over the replies — which has already lost the duplicate and the order by the time
it exists. A reply merge refuses read as `answered`, so `status` said nothing was
outstanding and the job was never handed out again.

`worksheet.classify` is the one verdict both sides now ask.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import worksheet as w  # noqa: E402

PARA = {"b00001": "para"}


# --------------------------------------------------------------------------- #
# The transport
# --------------------------------------------------------------------------- #

def test_an_answer_before_a_fence_is_not_thrown_away():
    """The defect: the first reply vanished, and what merged was the second.

    Whether a duplicate is then refused is a separate question — answered below.
    What must never happen is the *disappearance*, because nothing downstream can
    refuse a reply it was never shown.
    """
    text = "@@ b00001 para\nاول\n```\n@@ b00001 para\nدوم\n```\n"

    bodies = [entry["text"] for entry in w.read_worksheet(text)]

    assert len(bodies) == 2, f"an answer was dropped before validation: {bodies}"
    assert any("اول" in body for body in bodies), (
        f"the first answer is the one that disappeared: {bodies}")


def test_a_duplicate_that_survives_is_then_refused():
    """Having kept both, the verdict has to be `invalid` — which of two
    translations is the paragraph cannot be guessed."""
    text = "@@ b00001 para\nاول\n```\n@@ b00001 para\nدوم\n```\n"

    assert w.classify(text, ["b00001"], PARA) == "invalid"


def test_a_wrapper_around_the_whole_reply_is_still_removed():
    """The case the fence rule exists for keeps working: a model that wraps its
    entire answer, and a closing pleasantry that must not become the last
    paragraph of the book."""
    text = "```markdown\n@@ b00001 para\nمتن فارسی\n```\nHope this helps!\n"

    units = w.parse_worksheet(text)

    assert units == {"b00001": "متن فارسی"}, units


def test_an_unclosed_fence_is_reported_rather_than_guessed_at():
    """A fence that opens and never closes is the shape of a truncated reply.
    Keeping the payload and saying so beats both alternatives: discarding it, and
    merging half a book in silence."""
    text = "```\n@@ b00001 para\nمتن فارسی\n"

    entries, problems = w.read_reply(text)

    assert [e["id"] for e in entries] == ["b00001"], entries
    assert problems and "never closes it" in problems[0], problems
    assert w.classify(text, ["b00001"], PARA) == "invalid"


@pytest.mark.parametrize("line", [
    "@@ b00001 para",
    "    @@ b00001 para",
    "\t@@ b00001 para",
])
def test_a_literal_header_line_survives_the_round_trip(line):
    """A line of a novel beginning `@@` is rare and the damage is silent: the
    unit ends there and a second one appears, made of source text, under an id
    the manifest really does expect.

    The indented forms are the regression. `escape_payload` wrote the backslash
    at column zero — `\\    @@ …` — and `ESCAPED_HEADER` looks for it *after* the
    indent, so both the mark and the indentation reached the book.
    """
    escaped = w.escape_payload(line)
    assert escaped != line, "the line was not protected at all"

    recovered = w.ESCAPED_HEADER.sub(r"\1\2", escaped)

    assert recovered == line, f"{escaped!r} did not come back as {line!r}"


def test_a_protected_header_line_does_not_open_a_unit():
    """The point of the escape, end to end: the escaped line is body text, not a
    second unit the merge will believe in."""
    body = w.escape_payload("@@ not a header, a line of the novel")
    text = f"@@ b00001 para\n{body}\n"

    entries = w.read_worksheet(text)

    assert [e["id"] for e in entries] == ["b00001"], entries
    assert entries[0]["text"] == "@@ not a header, a line of the novel"


# --------------------------------------------------------------------------- #
# One verdict, shared
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("reply, expected_state, why", [
    ("@@ b00001 para\nمتن\n", "answered", "the ordinary good reply"),
    ("@@ b00001 para\nمتن\n@@ b00001 para\nدوباره\n", "invalid",
     "answered twice — the dict comprehension used to hide this"),
    ("@@ b00001 heading1\nمتن\n", "invalid",
     "answered as a heading when a paragraph was asked for"),
    ("@@ b00001 para\nمتن\n@@ b99999 para\nچه\n", "invalid",
     "an answer for a unit this worksheet never asked about"),
    ("I cannot translate this.\n", "malformed",
     "prose that answers no unit at all"),
    ("   \n\n", "empty", "whitespace is not an answer"),
    (None, "missing", "no file"),
])
def test_classify_gives_one_verdict_per_reply(reply, expected_state, why):
    assert w.classify(reply, ["b00001"], PARA) == expected_state, why


def test_reordered_headers_are_invalid_not_answered():
    """Order is part of the question. Two paragraphs swapped is two paragraphs in
    the wrong places, and every count still matches."""
    text = "@@ b00002 para\nدوم\n@@ b00001 para\nاول\n"

    assert w.classify(text, ["b00001", "b00002"],
                      {"b00001": "para", "b00002": "para"}) == "invalid"


def test_a_partial_reply_is_partial_not_malformed():
    """Some work done is not the same as no work done, and the two lead to
    different decisions about what to hand out next."""
    text = "@@ b00001 para\nاول\n"

    assert w.classify(text, ["b00001", "b00002"],
                      {"b00001": "para", "b00002": "para"}) == "partial"


def test_a_worksheet_with_nothing_to_translate_is_finished_not_pending():
    """An image-only run has no prose to ask for. Counting it unfinished is how a
    resume loop offers a job forever; demanding prose for it is how an invented
    sentence gets into a book."""
    assert w.classify(None, [], {}) == w.NOTHING_TO_TRANSLATE
    assert w.classify("", [], {}) == w.NOTHING_TO_TRANSLATE


def test_a_manifest_without_recorded_kinds_skips_the_kind_check():
    """A manifest written before kinds existed cannot be compared against one.
    The check is skipped rather than inventing a kind — and the caller reports
    that it was unverified instead of claiming it passed."""
    text = "@@ b00001 whatever\nمتن\n"

    assert w.classify(text, ["b00001"], {}) == "answered"


def test_classify_writes_nothing():
    """Side-effect free on purpose: a verdict that mutates is a verdict nobody
    can ask twice, and status, merge and scheduling all ask it."""
    text = "@@ b00001 para\nمتن\n"

    first = w.classify(text, ["b00001"], PARA)
    second = w.classify(text, ["b00001"], PARA)

    assert first == second == "answered"
    # The reply itself is untouched by being judged.
    assert w.parse_worksheet(text) == {"b00001": "متن"}

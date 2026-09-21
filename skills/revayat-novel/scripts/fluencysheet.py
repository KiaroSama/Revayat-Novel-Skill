"""The blind sheet: its grammar, its rendering and its reader.

Split out of `fluency` so each half has room, and because they are different
work. This file decides what a reviewer is shown and what their reply means; the
lifecycle — which sheets exist, what was recorded, what has been applied, whether
the pass passes — stays in `fluency`, which re-exports every name here because
that is how the CLI and the tests already reach them.

The one thing to understand before editing: **the control lines and the payload
are disjoint and the mapping is reversible.** `~ ` quotes a neighbouring unit and
`<!-- revayat-novel: … -->` is this project's own scaffolding; a payload line that
would read as either is escaped on the way out and unescaped on the way in, one
layer at a time. Before that, a replacement beginning `~ ` was dropped from the
reply in silence and a reviewer's own comment-shaped line with it.
"""

from __future__ import annotations

import re

import reviewsheet

#: Which stage is asking, carried in every review token so a meaning reply filed
#: here is refused by name instead of read for records it does not carry. Defined
#: with the grammar, because the token is part of the grammar.
STAGE = "fluency"

#: What can honestly be asked of Persian with the source absent.
#:
#: Every rubric here is answerable by a Persian reader who has never seen the
#: English — that is the entry requirement, and it is why faithfulness is not on
#: the list. A blind reviewer who "restores" a missing connective may be deleting
#: the author's abruptness; `meaning` is what decides, afterwards.
RUBRICS: dict[str, dict[str, str]] = {
    "calque": {
        "ask": "Is this Persian word order, or English word order in Persian words?",
        "why": "An independent Persian reading can reveal calques that a "
               "source-aware reading overlooked.",
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

#: A context quote: the neighbouring unit's Persian, shown so `flow` and
#: `register` can be judged, and never editable — an edit filed against it would
#: be applied to a unit this sheet did not show under review.
CONTEXT_LINE = re.compile(r"^~\s")

#: One backslash in front of a line that would otherwise read as control, taken
#: off on the way in. The same convention, the same layering rule and the same
#: reason as `worksheet.ESCAPED_HEADER`: one layer out, one layer in, at any
#: depth, so the round trip is symmetric for a line that arrives already escaped.
ESCAPED_LINE = reviewsheet.PAYLOAD_ESCAPE


def reserved_line(line: str) -> bool:
    """Would this line be read as control rather than as Persian?"""
    stripped = line.strip()
    return bool(CONTEXT_LINE.match(stripped)
                or reviewsheet.SCAFFOLD_COMMENT.match(stripped)
                or re.match(r"^(\?\?|\+\+|!!|@@)", stripped))


def escape_payload(text: str) -> str:
    """Escape one layer, including literal headers and claims."""
    return reviewsheet.escape_payload(text)


def read_edits(text: str) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """``(edits, sheets claimed read, problems)``."""
    return reviewsheet.parse_records(text, stage=STAGE, header=EDIT,
                                     rubrics=RUBRICS, payload="target")


def rubric_table() -> str:
    lines = ["| Rubric | Ask | An edit looks like | Not an edit |",
             "| --- | --- | --- | --- |"]
    for name, rubric in RUBRICS.items():
        lines.append(f"| `{name}` | {rubric['ask']} | {rubric['bad']} | "
                     f"{rubric['good']} |")
    return "\n".join(lines)


def neighbour(units: list[dict[str, str]], index: int,
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
        out += ["<!-- revayat-novel: context, not under review -->",
                f"~ {before}", ""]
    for unit in units:
        # Escaped on the way out, unescaped on the way back: a unit whose own
        # Persian begins `~ ` would otherwise come back as a quoted context line
        # and be dropped from the replacement without a word.
        out += [f"-- {unit['id']} {unit['kind']}", escape_payload(unit["target"]),
                ""]
    if after:
        out += ["<!-- revayat-novel: context, not under review -->",
                f"~ {after}", ""]
    return "\n".join(out) + "\n"

"""How often one argument has been made about one unit — the repair episode.

A repair budget has to count something that a repair does not change. Both review
stages counted rounds against the *content revision*, and kept the history only
while that revision stood still:

    if previous.get("revision") != rev:
        history = []

A repair changes the text. So the revision moved on every round, the history was
cleared on every round, and the cap was unreachable by construction. Measured on
a one-unit book whose negation stayed inverted through five real rewrites:

    attempt 1: round=1 history=1 rev=9d741dc0 repair_ok=True
    attempt 2: round=1 history=1 rev=8fc83bd1 repair_ok=True
    attempt 3: round=1 history=1 rev=41e1b251 repair_ok=True
    attempt 4: round=1 history=1 rev=db444ddf repair_ok=True
    attempt 5: round=1 history=1 rev=dabe0297 repair_ok=True

Five rewrites, five first rounds, and a sixth on request. The reset was not a
bug in the comparison — it was the right answer to the wrong question. "Is this
finding about the same text?" and "has this argument been made before?" are
different questions, and only the second one can bound a loop.

So an **episode** is keyed by the issue — the unit and the rubric — and survives
every edit to the text. It records what was argued, at which wording, how many
times, and what the reviewer actually said, because "the same `(unit, rubric)`
came back" is not on its own evidence that the argument is unchanged: the
sentence may have been rewritten twice and be wrong in a new way. The words are
kept so somebody can read them and decide.

Three things end an episode, and they stay separately reachable:

* **the text did not change** between two reports of the same issue — nothing
  was repaired, so repeating the request cannot help (``no-new-evidence``);
* **the text came back to a wording already rejected** — the A→B→A oscillation,
  which no number of further rounds resolves (``oscillating``);
* **the cap** — :data:`MAX_ATTEMPTS` arguments about one unit and rubric, each
  about a genuinely different wording, is the budget running out
  (``rounds-exhausted``).

Nothing here deletes history, raises the cap or opens a fresh episode to keep an
unresolved loop going. Two things do open a fresh one, and both are a different
question rather than another attempt at the same one: the unit's **source** text
changed, so the argument is about a different book; or the issue was **reported
absent** by a complete review and has come back later, which is a recurrence and
is counted as one. Even then the wordings already rejected are carried forward,
so closing an episode cannot be used to launder an oscillation.
"""

from __future__ import annotations

import hashlib
from typing import Any

#: Tagged like every other digest in this project: a reader that cannot
#: recompute a stored form must be able to say so rather than guess.
DIGEST_VERSION = "wording1"

#: Arguments about one unit and rubric before the loop is escalated instead of
#: repeated. Three, because two is the smallest number that can look like
#: progress — one rewrite, one relapse — and the third is where a human should be
#: reading the arguments rather than asking for another rewrite.
MAX_ATTEMPTS = 3


def wording(text: str) -> str:
    """The identity of one unit's text, short enough to keep a list of."""
    digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    return f"{DIGEST_VERSION}:{digest[:16]}"


def issue_key(unit_id: str, rubric: str) -> str:
    """One issue: this unit, this rubric. The stable identity a budget counts."""
    return f"{unit_id}/{rubric}"


def _fresh(*, source: str, revision: str,
           superseded: list[dict[str, Any]] | None = None,
           rejected: list[str] | None = None,
           recurrences: int = 0) -> dict[str, Any]:
    return {
        "state": "open",
        "opened": revision,
        "last_seen": revision,
        "attempts": 0,
        # The source this argument is about. A different one is a different book,
        # not a further attempt at the same repair.
        "source": source,
        "wordings": [],
        # The reviewer's own words, one per attempt. The brief's point: an
        # identical `(unit, rubric)` is not proof the argument is unchanged, and
        # an escalation that throws the arguments away leaves nothing to escalate.
        "arguments": [],
        "revisions": [],
        "events": [],
        # Wordings rejected in earlier episodes of this same issue, carried so a
        # recurrence cannot be used to launder an oscillation.
        "rejected": list(rejected or []),
        "recurrences": recurrences,
        # Never deleted. An episode that was superseded is the evidence for what
        # changed, exactly as the superseded worksheet replies are.
        "superseded": list(superseded or []),
        "closed_at": "",
    }


def _archived(episode: dict[str, Any], why: str) -> dict[str, Any]:
    kept = {name: episode.get(name) for name in
            ("opened", "last_seen", "attempts", "source", "wordings",
             "arguments", "revisions", "events", "state", "closed_at")}
    kept["superseded_because"] = why
    return kept


def attempt(episodes: dict[str, Any], *, key: str, source: str, text: str,
            argument: str, revision: str, event: str = "") -> dict[str, Any]:
    """Record one report of one issue and return that episode.

    ``episodes`` is mutated and returned by the caller's sidecar write; nothing
    here touches a file.
    """
    episode = episodes.get(key)
    if episode is None:
        episode = _fresh(source=source, revision=revision)
    elif episode.get("source") != source:
        # A different source text: the argument is about a different book. The
        # previous episode is archived rather than dropped, and its rejected
        # wordings are *not* carried, because they were about text that has since
        # been re-extracted or corrected.
        episode = _fresh(source=source, revision=revision,
                         superseded=list(episode.get("superseded") or [])
                         + [_archived(episode, "source-changed")])
    elif episode.get("state") == "closed":
        # Reported absent by a complete review and back again: a recurrence, with
        # its own budget — but every wording already rejected comes with it.
        episode = _fresh(
            source=source, revision=revision,
            superseded=list(episode.get("superseded") or [])
            + [_archived(episode, "recurred")],
            rejected=list(episode.get("rejected") or [])
            + list(episode.get("wordings") or []),
            recurrences=int(episode.get("recurrences") or 0) + 1)

    events = list(episode.get("events") or [""] * episode["attempts"])
    if event and events and events[-1] == event:
        # Several arguments in one review are one attempt at the same wording.
        episode["arguments"][-1] += "\n\n" + argument
        return episode
    episode["events"] = events + [event]
    episode["attempts"] = int(episode.get("attempts") or 0) + 1
    episode["wordings"] = list(episode.get("wordings") or []) + [wording(text)]
    episode["arguments"] = list(episode.get("arguments") or []) + [argument]
    episode["revisions"] = list(episode.get("revisions") or []) + [revision]
    episode["state"] = "open"
    episode["last_seen"] = revision
    episode["closed_at"] = ""
    episodes[key] = episode
    return episode


def close_absent(episodes: dict[str, Any], *, seen: set[str],
                 revision: str) -> list[str]:
    """Close every open episode this review did not report. Returns their keys.

    Only sound because a recorded review covers the whole book: the sheets are
    proved between them to own every reviewable unit exactly once, and a sheet
    nobody reported on refuses the record. So an issue that is not in ``seen``
    was looked at and not raised.
    """
    closed: list[str] = []
    for key, episode in episodes.items():
        if key in seen or episode.get("state") != "open":
            continue
        episode["state"] = "closed"
        episode["closed_at"] = revision
        closed.append(key)
    return closed


def decision(episode: dict[str, Any], *,
             cap: int = MAX_ATTEMPTS) -> tuple[str, str]:
    """``(refusal, detail)`` for one open episode, or ``("", "")`` to continue.

    Order matters and is the reason both families stay reachable: the two
    no-progress answers are checked before the budget, because "the next attempt
    will fail the same way" is more useful than "you have had three goes" — and
    with a small cap the budget would otherwise answer every case and leave the
    specific refusals as dead code. This project has already had that bug once.
    """
    wordings = list(episode.get("wordings") or [])
    attempts = int(episode.get("attempts") or 0)
    earlier = set(episode.get("rejected") or []) | set(wordings[:-1])

    if len(wordings) >= 2 and wordings[-1] == wordings[-2]:
        return ("no-new-evidence",
                f"the same argument has been made twice about text that has not "
                f"changed: {_last(episode)}. Nothing was repaired between the "
                f"two reports, so asking again cannot help — the cause is the "
                f"glossary entry, the voice card or the source extraction.")
    if wordings and wordings[-1] in earlier:
        return ("oscillating",
                f"this unit has come back to a wording that was already "
                f"rejected, after {attempts} attempt(s): {_last(episode)}. A "
                f"repair that undoes the previous repair is not progress, and a "
                f"further round repeats the same two answers.")
    if attempts >= cap:
        return ("rounds-exhausted",
                f"{attempts} repairs have been argued about this unit and it is "
                f"still wrong, each time about a different wording: "
                f"{_last(episode)}. Stop rewriting and find out why.")
    return "", ""


def _last(episode: dict[str, Any]) -> str:
    """The most recent argument, kept short enough for a refusal line."""
    arguments = [text for text in (episode.get("arguments") or []) if text]
    if not arguments:
        return "no argument was recorded"
    last = " ".join(arguments[-1].split())
    return f'"{last[:160]}"' if len(last) <= 160 else f'"{last[:160]}…"'


def blocked(episodes: dict[str, Any], *,
            cap: int = MAX_ATTEMPTS) -> list[dict[str, Any]]:
    """Every open episode that must stop, worst first in report order.

    One list, read by both stages, so the gate and the request loop cannot reach
    different conclusions about the same sidecar.
    """
    found: list[dict[str, Any]] = []
    for key, episode in episodes.items():
        if episode.get("state") != "open":
            continue
        refusal, detail = decision(episode, cap=cap)
        if refusal:
            found.append({
                "issue": key,
                "id": key.partition("/")[0],
                "rubric": key.partition("/")[2],
                "refused": refusal,
                "detail": detail,
                "attempts": int(episode.get("attempts") or 0),
                "recurrences": int(episode.get("recurrences") or 0),
                # The evidence travels with the escalation. An operator asked to
                # look at the glossary needs to read what was actually argued.
                "arguments": list(episode.get("arguments") or []),
            })
    return sorted(found, key=lambda item: item["issue"])

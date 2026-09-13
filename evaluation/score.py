#!/usr/bin/env python3
"""Grade a translation against `cases.json`, per axis, with no overall number.

    python evaluation/score.py --answers my-answers.json

`--answers` is `{case id: the Persian produced}`. Each case is graded on the one
axis it is about, and the result is `pass`, `fail` or `unknown`:

* **pass** — the answer matches one of the accepted renderings, or differs from it
  only in punctuation and spacing.
* **fail** — the answer matches one of the recorded wrong renderings. Those are
  the cases the structural gates cannot see: right length, right markers, right
  names, different meaning.
* **unknown** — anything else. A new rendering may be perfectly good; this file
  cannot tell, and saying so is the honest answer.

**There is deliberately no single score, and there will not be one.** An average
over eight axes says nothing a reader can act on, and a number invites a model to
be tuned against it — which is how a benchmark stops measuring translation and
starts measuring itself. `unknown` is reported as its own count for the same
reason: a benchmark that resolved every uncertainty to "pass" would be lying, and
to "fail" would be rejecting good work.

The limit is stated rather than hidden: the English was written for this file, so
it measures *translation*, not extraction from a real scan, and eight cases are a
smoke test for a failure class, not coverage of a language.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

#: Punctuation and spacing a translator may legitimately set differently. The
#: zero-width non-joiner is in here because `falint` may add or move one and that
#: is a typography decision, not a translation one.
_IGNORED = re.compile(r"[\s‌،,;:.!?…«»\"'()\[\]—–-]+")


def normalise(text: str) -> str:
    """What two renderings have to share to count as the same answer."""
    folded = unicodedata.normalize("NFKC", text or "")
    # Persian and Arabic forms of the same letter are the same word to a reader.
    folded = folded.replace("ي", "ی").replace("ك", "ک")
    return _IGNORED.sub(" ", folded).strip()


def load_cases(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or HERE / "cases.json").read_text(encoding="utf-8"))


def grade_one(case: dict[str, Any], answer: str) -> dict[str, Any]:
    given = normalise(answer)
    if not given:
        return {"id": case["id"], "axis": case["axis"], "verdict": "unknown",
                "note": "no answer given"}
    for accepted in case["accepts"]:
        if given == normalise(accepted):
            return {"id": case["id"], "axis": case["axis"], "verdict": "pass",
                    "note": "matches an accepted rendering"}
    for wrong in case.get("fails", []):
        if given == normalise(wrong["text"]):
            return {"id": case["id"], "axis": case["axis"], "verdict": "fail",
                    "note": wrong["why"]}
    return {"id": case["id"], "axis": case["axis"], "verdict": "unknown",
            "note": ("a rendering this file has not seen. Read it against the "
                     "source and the rationale; if it is right, add it to "
                     "`accepts` so the next run knows.")}


def grade(answers: dict[str, str], cases: dict[str, Any] | None = None) -> dict[str, Any]:
    cases = cases or load_cases()
    results = [grade_one(case, answers.get(case["id"], ""))
               for case in cases["cases"]]
    by_axis: dict[str, dict[str, int]] = {}
    for item in results:
        tally = by_axis.setdefault(item["axis"], {"pass": 0, "fail": 0, "unknown": 0})
        tally[item["verdict"]] += 1
    return {
        "schema": cases["schema"],
        "cases": results,
        "by_axis": by_axis,
        # Named so nobody has to ask whether a number is hiding somewhere.
        "overall": None,
        "overall_note": ("deliberately absent: an average over these axes is not "
                         "a fact about a translation, and a number invites tuning "
                         "against the benchmark instead of the book"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", required=True, type=Path,
                        help='JSON: {"case id": "the Persian produced"}')
    parser.add_argument("--cases", type=Path, default=None)
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    answers = json.loads(args.answers.read_text(encoding="utf-8"))
    report = grade(answers, load_cases(args.cases))
    print(json.dumps(report, ensure_ascii=False, indent=1))

    # A recorded wrong rendering is the one verdict worth a non-zero exit: it is
    # not "we could not tell", it is a meaning this file already knows is wrong.
    return 1 if any(item["verdict"] == "fail" for item in report["cases"]) else 0


if __name__ == "__main__":
    sys.exit(main())

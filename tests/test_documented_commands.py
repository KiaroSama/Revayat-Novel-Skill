"""The commands SKILL.md hands an agent must be runnable and internally consistent.

An agent follows this file literally. A command that is wrong here is not a
documentation defect that someone will notice and correct — it is executed, and
its output is trusted. Two mistakes are worth a permanent guard because neither
one announces itself:

* **The wrong OCR language.** Recognising English pages with the Persian model
  does not fail. It returns words, in a plausible sentence, with plausible
  confidence — so every check downstream passes and the book is quietly wrong.
  This happened: the confidence pass was documented with the *target* language
  while the OCR pass used the source language.
* **A hard-coded `python3`.** It does not exist on most Windows installations,
  so a command line that assumes it works on two platforms out of three.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "SKILL.md"

#: Tesseract codes for languages this skill translates *into*, never *from*.
TARGET_LANGUAGE_CODES = ("fas", "per", "fa")

#: `$PY $SKILL_DIR/scripts/revayat-novel.py <stage> …`, across a `\` line break.
#: The continuation alternative has to come first: tried second, the ordinary
#: character class swallows the backslash and the command appears to end there,
#: hiding every flag written on the following line.
COMMAND = re.compile(
    r"revayat-novel\.py\s+(?P<stage>[a-z][a-z_-]*)(?P<rest>(?:\\\n|[^\n`])*)"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def commands(skill_text) -> list[tuple[str, str]]:
    found = [(m.group("stage"), m.group("rest").replace("\\\n", " "))
             for m in COMMAND.finditer(skill_text)]
    assert found, "no documented commands were found at all"
    return found


def test_the_ocr_stages_agree_on_one_language(commands):
    """`extract` and `ocr-sidecar` read the same pages; they must use one model.

    The sidecar re-recognises what `extract` already recognised, to find out how
    sure the engine was. Point the two at different language models and the
    confidence describes text the book does not contain.
    """
    languages = {}
    for stage, rest in commands:
        for flag in ("--ocr-lang", "--lang"):
            match = re.search(rf"{flag}\s+(\S+)", rest)
            if match:
                languages.setdefault(stage, set()).add(match.group(1))

    assert "extract" in languages, "extract is documented without an OCR language"
    assert "ocr-sidecar" in languages, "ocr-sidecar is documented without a language"
    assert languages["extract"] == languages["ocr-sidecar"], (
        f"the OCR stages disagree: extract={languages['extract']}, "
        f"ocr-sidecar={languages['ocr-sidecar']}"
    )


@pytest.mark.parametrize("stage", ["extract", "ocr-sidecar"])
def test_no_ocr_stage_is_pinned_to_the_target_language(commands, stage):
    """The book being read is the source; recognising it as Persian is the bug."""
    for documented, rest in commands:
        if documented != stage:
            continue
        for flag in ("--ocr-lang", "--lang"):
            match = re.search(rf"{flag}\s+(\S+)", rest)
            if match and match.group(1).lower() in TARGET_LANGUAGE_CODES:
                pytest.fail(
                    f"{stage} is documented with {flag} {match.group(1)} — that is "
                    "the language being translated into, not the one printed in "
                    "the book"
                )


def test_the_source_language_is_a_variable_set_once(skill_text):
    """One value, defined where the reader cannot miss it, used everywhere."""
    assert "`OCR_LANG`" in skill_text, "the source OCR language is not defined"
    assert "$OCR_LANG" in skill_text, "the defined language is never used"
    assert skill_text.count("--ocr-lang $OCR_LANG") >= 1
    assert skill_text.count("--lang $OCR_LANG") >= 1


def test_no_documented_command_hard_codes_python3(skill_text):
    """`python3` is absent from most Windows installs; resolve `$PY` instead."""
    offenders = [line.strip() for line in skill_text.splitlines()
                 if "python3 " in line and "revayat-novel.py" in line]
    assert not offenders, (
        "these command lines will not run on Windows:\n  " + "\n  ".join(offenders)
    )


def test_the_interpreter_is_resolved_for_every_platform(skill_text):
    assert "`PY`" in skill_text, "no interpreter variable is defined"
    for platform_hint in ("macOS / Linux", "Windows"):
        assert platform_hint in skill_text, f"{platform_hint} is not covered"
    assert "$PY $SKILL_DIR" in skill_text


def test_every_documented_stage_is_a_real_stage(commands):
    """A command naming a stage that does not exist sends an agent in circles."""
    import importlib.util

    dispatcher = SKILL.parent / "scripts" / "revayat-novel.py"
    spec = importlib.util.spec_from_file_location("dispatcher", dispatcher)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    known = set(module.STAGES) | {"doctor"}
    documented = {stage for stage, _ in commands}
    assert documented <= known, f"documented but missing: {sorted(documented - known)}"

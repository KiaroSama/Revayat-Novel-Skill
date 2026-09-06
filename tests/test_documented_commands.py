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


#: SKILL.md is the file an agent follows, but not the only file it reads: a
#: reference is opened precisely when something has gone wrong, which is the
#: worst moment to hand someone a command that cannot run.
DOCS = [SKILL] + sorted((SKILL.parent / "references").glob("*.md"))


def test_no_documented_command_hard_codes_python3():
    """`python3` is absent from most Windows installs; resolve `$PY` instead."""
    offenders = [f"{doc.name}: {line.strip()}"
                 for doc in DOCS
                 for line in doc.read_text(encoding="utf-8").splitlines()
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


class _Parsed(BaseException):
    """Raised once a stage's parser has accepted a command line.

    A ``BaseException`` on purpose: several stages wrap their work in
    ``except Exception``, and an ordinary exception would be swallowed there and
    reported as the stage failing rather than as the parse succeeding.
    """


def parse_only(stage: str, tokens: list[str]) -> None:
    """Put ``tokens`` through the stage's real parser, then stop before it acts.

    Checking the stage name alone is what let the page route's documentation
    drift: `pages` is a real stage, so `pages status --chunks …` looked correct
    for as long as nobody ran it — while the flag is `--pages` and argparse
    would have rejected it on the first try. Handing the command line to the
    parser that will actually receive it catches an unknown subcommand, an
    unknown flag, a missing required flag and a renamed one, all four, without
    this file having to keep its own copy of the CLI to compare against.
    """
    import importlib
    import importlib.util
    import argparse as ap

    dispatcher = SKILL.parent / "scripts" / "revayat-novel.py"
    spec = importlib.util.spec_from_file_location("dispatcher", dispatcher)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    module = importlib.import_module(cli.STAGES[stage])
    real = ap.ArgumentParser.parse_args

    def stop(self, args=None, namespace=None):
        real(self, args, namespace)
        raise _Parsed

    ap.ArgumentParser.parse_args = stop
    try:
        module.main(tokens)
    except _Parsed:
        return
    finally:
        ap.ArgumentParser.parse_args = real


#: A documented command line is written for a shell, where `$P` and `$WORK` are
#: values. Nothing expands them here, so a bare variable becomes `1` — which
#: parses as a number *and* as a string, so a numeric flag is exercised rather
#: than skipped — and one embedded in a path becomes an ordinary path segment.
VARIABLE = re.compile(r"\$[A-Za-z_]\w*")


def _as_a_shell_would(token: str) -> str:
    return "1" if VARIABLE.fullmatch(token) else VARIABLE.sub("x", token)


def test_every_documented_command_parses(commands, capsys):
    """Every flag in SKILL.md is one the stage receiving it actually accepts."""
    import shlex

    failures = []
    for stage, rest in commands:
        if stage == "doctor":
            continue
        try:
            parse_only(stage, [_as_a_shell_would(t) for t in shlex.split(rest)])
        except SystemExit:
            usage = capsys.readouterr().err.strip().splitlines()
            failures.append(f"{stage}{rest}\n    {usage[-1] if usage else 'rejected'}")
    assert not failures, (
        "SKILL.md documents command lines the CLI rejects:\n  "
        + "\n  ".join(failures)
    )


def test_the_page_route_is_documented_with_its_own_directory_flag(skill_text):
    """`pages` reads `--pages`; `--chunks` belongs to the other route.

    Both routes cut a book into worksheets and their flags read almost alike,
    which is exactly why this one is pinned: the wrong one is not a typo an
    agent notices, it is a stage that refuses to start.
    """
    for line in skill_text.splitlines():
        if "revayat-novel.py pages" in line:
            assert "--chunks" not in line, f"the page route takes --pages: {line.strip()}"


def test_the_page_route_names_the_files_it_writes(skill_text):
    """`pages build` writes `pageNNNN.md`; step 5 must send agents to those."""
    assert "out_page0001.md" in skill_text or "out_pageNNNN.md" in skill_text, (
        "step 5 never names the page route's output files, so an agent "
        "following it writes out_chunkNNNN.md that nothing reads"
    )


def test_no_documented_command_hard_codes_the_ocr_copy_as_the_source(skill_text):
    """The source PDF is the original for a digital book, the OCR copy for a scan.

    `render-qa` renders the source page beside the translated one. Naming
    `ocr.pdf` in the command works only for a scan; for a born-digital PDF that
    file does not exist and the comparison silently has nothing to compare.
    """
    offenders = [line.strip() for line in skill_text.splitlines()
                 if "--source-pdf" in line and "ocr.pdf" in line]
    assert not offenders, (
        "take the source PDF from the manifest's reference_pdf:\n  "
        + "\n  ".join(offenders)
    )


def test_the_metadata_does_not_demand_an_interpreter_windows_lacks():
    """A hard `python3` requirement rejects the skill before it can run.

    The body of SKILL.md resolves `$PY` per platform precisely because
    `python3` is absent from most Windows installations. Declaring it in the
    front matter as a required binary undoes that: a host that gates on
    `requires.bins` refuses to load the skill, and the resolution logic that
    would have found `python` or `py -3` never gets a chance.

    There is no single name that is right everywhere — Linux may have only
    `python3`, Windows usually only `python` — so the declaration is dropped
    and `doctor` reports the interpreter it is actually running under.
    """
    import json

    line = re.search(r"^metadata: (.+)$", SKILL.read_text(encoding="utf-8"), re.M)
    assert line, "SKILL.md has no metadata line"
    requires = json.loads(line.group(1)).get("requires", {})

    assert "python3" not in json.dumps(requires), (
        "the metadata demands `python3`, which most Windows installations do "
        "not have; the skill would be rejected before step 1 could resolve $PY"
    )
    assert requires.get("pip"), "the pip requirements are what a host can act on"


def test_every_front_matter_field_is_on_one_line():
    """Several agents parse this with a line-oriented reader.

    A wrapped `description:` or `metadata:` is not a formatting preference —
    it is a field those readers see as truncated, or as a stray line they do
    not recognise at all.
    """
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md does not open with front matter"
    body = text.split("---\n", 2)[1]
    for line in body.splitlines():
        if not line.strip():
            continue
        assert re.match(r"^[a-zA-Z-]+:", line), (
            f"front-matter line continues onto the next: {line[:60]!r}"
        )


def test_no_page_loop_command_names_a_sample_specific_file(skill_text):
    """`$P` in one line and `page-0012` in the next is wrong for every page but 12.

    The loop sets `P=12` as an example and passes `$P` to the page commands.
    A hard-coded `page-0012.docx` beside it looked consistent and was a defect
    an agent could only find by running the loop on page 13 and comparing the
    wrong page.
    """
    executable = [line for line in skill_text.splitlines()
                  if "revayat-novel.py" in line or line.strip().startswith("--")]
    offenders = [line.strip() for line in executable
                 if re.search(r"page-\d{4}\.(docx|pdf)", line)]
    assert not offenders, (
        "these lines name one page's file inside a loop that runs on all of "
        "them:\n  " + "\n  ".join(offenders)
    )


def test_the_final_review_loop_documents_the_second_check(skill_text):
    """`check` then `review` leaves the book unverified for ever.

    The first `doc-qa check` is *expected* to come back unverified: it runs
    before the review exists. The call that consumes the review and returns
    `ok: true` is a second `check`, and an agent following two documented
    commands would stop one short of a verified book.
    """
    step_nine = skill_text[skill_text.index("## Step 9"):]
    checks = step_nine.count("doc-qa check")
    reviews = step_nine.count("doc-qa review")
    assert reviews >= 1, "step 9 never documents the review"
    assert checks >= reviews + 1, (
        f"step 9 documents {checks} `doc-qa check` and {reviews} `doc-qa "
        f"review`; the check has to run again *after* the review to consume it"
    )
    assert "again" in step_nine, "the second check is not called out as one"

"""A named dependency that is absent is a failure, not a disabled validator.

Two places turned missing input into silence:

* `qa check` passed `assets=None` when the assets directory did not exist, and
  `check_book` only checks assets when that is not None. So the asset check was
  switched off in exactly the situation it exists for — every picture gone — and a
  book with figures passed with nothing to render.
* `gl.load` returns a fresh empty glossary for a path that is not there. That is
  right for "no glossary was asked for" and wrong for "this glossary was asked for
  and is missing": an empty glossary reports no drift and no first-mention problem,
  so the run reads clean because nothing was checked.

And the OCR staging, which needed fault injection rather than reading: a run
interrupted between writing `ocr.pdf.new` and promoting it leaves a valid PDF
behind. The next attempt used the **same fixed name**, so when its converter failed
and wrote nothing, the artefact test judged the *previous* run's file, found it
readable, and promoted it as this run's output — old bytes certified as new-source
OCR.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import extract  # noqa: E402
import qa  # noqa: E402


def _book_with_a_figure(tmp_path: Path) -> Path:
    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    para = ir.make_block("paragraph", 1, text="A paragraph of prose.")
    para["target"] = "بندی از نثر."
    figure = ir.make_block("image", 2, asset="p0001-img001.png")
    figure["alt"] = "A red rectangle."
    figure["target_alt"] = "مستطیلی سرخ."
    book["blocks"] = [para, figure]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _codes(argv: list[str], capsys) -> list[str]:
    qa.main(argv)
    import json
    return [f["code"] for f in json.loads(capsys.readouterr().out)["findings"]]


# --------------------------------------------------------------------------- #
# Absent inputs
# --------------------------------------------------------------------------- #

def test_a_missing_assets_directory_fails_the_check(tmp_path, capsys):
    """The defect: no directory meant no asset check, so the one case that
    matters — every picture missing — was the one case nothing looked at."""
    book_path = _book_with_a_figure(tmp_path)

    codes = _codes(["check", "--book", str(book_path)], capsys)

    assert "assets-missing" in codes, codes


def test_a_missing_assets_directory_that_was_named_explicitly_also_fails(
        tmp_path, capsys):
    """Naming a directory is a claim that it is the right one. If it is not there,
    the operator has the path wrong and needs to hear so."""
    book_path = _book_with_a_figure(tmp_path)

    codes = _codes(["check", "--book", str(book_path),
                    "--assets", str(tmp_path / "nowhere")], capsys)

    assert "assets-missing" in codes, codes


def test_a_book_with_no_figures_does_not_need_an_assets_directory(
        tmp_path, capsys):
    """The negative control. Most EPUBs have no images, and demanding a directory
    they never needed would fail every one of them."""
    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    block = ir.make_block("paragraph", 1, text="Only prose here.")
    block["target"] = "فقط نثر."
    book["blocks"] = [block]
    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)

    codes = _codes(["check", "--book", str(book_path)], capsys)

    assert "assets-missing" not in codes, codes


def test_a_named_glossary_that_is_absent_fails(tmp_path, capsys):
    """`gl.load` turning an absent path into an empty glossary is what made this
    silent: an empty glossary has no locked names, so it reports no drift."""
    book_path = _book_with_a_figure(tmp_path)

    codes = _codes(["check", "--book", str(book_path),
                    "--glossary", str(tmp_path / "absent.json")], capsys)

    assert "glossary-missing" in codes, codes


def test_no_glossary_argument_is_not_a_missing_glossary(tmp_path, capsys):
    """Not asking for one is allowed; asking for one that is not there is not."""
    book_path = _book_with_a_figure(tmp_path)

    codes = _codes(["check", "--book", str(book_path)], capsys)

    assert "glossary-missing" not in codes, codes


# --------------------------------------------------------------------------- #
# OCR staging, by fault injection
# --------------------------------------------------------------------------- #

def _valid_pdf(path: Path, text: str = "Some recognised text on the page.") -> None:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((40, 60), text, fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()


def test_a_failed_attempt_does_not_promote_an_earlier_staging_file(
        tmp_path, monkeypatch):
    """The injection the brief asks for, in two steps.

    First an interrupted run leaves a valid staging PDF. Then a run whose
    converter fails and writes nothing must not certify those bytes as its own
    output — which is exactly what happened, because the artefact test asked
    "is the destination a readable PDF?" about a file it had not created.
    """
    source = tmp_path / "scan.pdf"
    _valid_pdf(source, "The source page.")
    destination = tmp_path / "ocr.pdf"

    # Step one: an interruption left a perfectly good staging file behind.
    orphan = tmp_path / "ocr.pdf.new"
    _valid_pdf(orphan, "Text from the run that was interrupted.")
    assert orphan.exists()

    # Step two: this attempt's converter fails and produces nothing at all.
    monkeypatch.setattr(extract, "find_ocrmypdf", lambda: ["ocrmypdf-stub"])

    def failed_run(command, timeout, **kwargs):
        import subprocess
        return subprocess.CompletedProcess(command, 1, b"", b"converter exploded")

    monkeypatch.setattr(ir, "run_bounded", failed_run)

    with pytest.raises(extract.ExtractError) as refused:
        extract.run_ocr(source, orphan, kind="scanned")

    assert "converter exploded" in str(refused.value) or "failed" in str(refused.value)
    assert not destination.exists(), (
        "an earlier run's staging bytes were promoted as this run's OCR output")


def test_the_destination_is_cleared_before_the_converter_runs(tmp_path, monkeypatch):
    """The mechanism, asserted directly: a readable PDF after the command can only
    have come from the command. Leaving the old file in place is what let the
    artefact test answer about somebody else's work."""
    source = tmp_path / "scan.pdf"
    _valid_pdf(source, "The source page.")
    staging = tmp_path / "ocr.attempt.pdf.new"
    _valid_pdf(staging, "Stale bytes from before.")

    monkeypatch.setattr(extract, "find_ocrmypdf", lambda: ["ocrmypdf-stub"])
    seen: dict[str, bool] = {}

    def record_then_fail(command, timeout, **kwargs):
        import subprocess
        seen["existed_at_launch"] = staging.exists()
        return subprocess.CompletedProcess(command, 1, b"", b"nothing written")

    monkeypatch.setattr(ir, "run_bounded", record_then_fail)

    with pytest.raises(extract.ExtractError):
        extract.run_ocr(source, staging, kind="scanned")

    assert seen["existed_at_launch"] is False, (
        "the stale file was still there when the converter started, so a failure "
        "that writes nothing leaves a readable PDF for the artefact test to trust")


def test_a_stale_staging_file_is_quarantined_under_a_name_that_says_so(tmp_path):
    """Renamed, not deleted: it is somebody's OCR output and may be worth
    recovering by hand. It simply must not sit where an artefact test can find
    it."""
    orphan = tmp_path / "ocr.pdf.new"
    _valid_pdf(orphan, "Interrupted run's work.")

    moved = extract._quarantine_stale_staging(tmp_path)

    assert moved and all("orphaned" in name for name in moved), moved
    assert not orphan.exists()
    kept = list(tmp_path.glob("*.orphaned*"))
    assert kept and kept[0].stat().st_size > 0, "the work was not preserved"


def test_quarantine_is_quiet_when_there_is_nothing_to_move(tmp_path):
    assert extract._quarantine_stale_staging(tmp_path) == []

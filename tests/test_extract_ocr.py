"""OCR command routing and bounded launcher decisions."""

from __future__ import annotations

import os
import sys
import time
from urllib.parse import urlparse
import pytest
import bookir as ir
from extract import ExtractError, find_ocrmypdf, ocr_command, run_ocr

def test_a_full_scan_gets_force_ocr_and_deskew(tmp_path):
    command = ocr_command(["ocrmypdf"], tmp_path / "in.pdf", tmp_path / "out.pdf",
                          kind="scanned")
    assert "--force-ocr" in command
    assert "--deskew" in command
    assert "--skip-text" not in command


def test_a_mixed_book_gets_skip_text_and_no_deskew(tmp_path):
    """Deskew rewrites the raster, so it must not run over good pages."""
    command = ocr_command(["ocrmypdf"], tmp_path / "in.pdf", tmp_path / "out.pdf",
                          kind="mixed")
    assert "--skip-text" in command
    assert "--force-ocr" not in command
    assert "--deskew" not in command


def test_images_are_never_recompressed(tmp_path):
    """The whole point of the pipeline is that the book's pictures survive."""
    for kind in ("scanned", "mixed"):
        command = ocr_command(["ocrmypdf"], tmp_path / "in.pdf",
                              tmp_path / "out.pdf", kind=kind)
        assert command[command.index("--optimize") + 1] == "0"
        assert command[command.index("--output-type") + 1] == "pdf"


def test_deskew_can_be_forced_either_way(tmp_path):
    on = ocr_command(["ocrmypdf"], tmp_path / "i", tmp_path / "o",
                     kind="mixed", deskew=True)
    off = ocr_command(["ocrmypdf"], tmp_path / "i", tmp_path / "o",
                      kind="scanned", deskew=False)
    assert "--deskew" in on
    assert "--deskew" not in off


def test_launcher_and_language_reach_the_command(tmp_path):
    command = ocr_command(["python", "-m", "ocrmypdf"], tmp_path / "i", tmp_path / "o",
                          kind="scanned", language="fas+eng")
    assert command[:3] == ["python", "-m", "ocrmypdf"]
    assert command[command.index("--language") + 1] == "fas+eng"
    assert command[-2:] == [str(tmp_path / "i"), str(tmp_path / "o")]


def test_a_timed_out_ocr_kills_the_tree_not_just_the_launcher(monkeypatch, tmp_path):
    """ocrmypdf is a launcher; Tesseract is the process that was working.

    `subprocess.run(timeout=)` signals the direct child only, so the real
    worker survived with nothing left to reap it — the same defect plan 004
    fixed on the renderer, on the stage that runs a subprocess per page.
    """
    import extract

    killed = []

    def record_and_kill(process):
        # It has to kill as well as record: a stub that only records leaves the
        # sleeper below running for its full 30s, and the guarded runner fails
        # the whole run for a leaked process.
        killed.append(process)
        process.kill()

    monkeypatch.setattr(ir, "kill_tree", record_and_kill)
    monkeypatch.setattr(
        extract, "find_ocrmypdf",
        lambda: [sys.executable, "-c", "import time; time.sleep(30)"])

    import pymupdf
    with pymupdf.open() as document:
        document.new_page()
        document.save(tmp_path / "in.pdf")
    started = time.monotonic()
    with pytest.raises(ExtractError) as caught:
        run_ocr(tmp_path / "in.pdf", tmp_path / "out.pdf",
                kind="scanned", timeout=1)
    elapsed = time.monotonic() - started

    assert "exceeded" in str(caught.value)
    assert killed, "the tree was never killed; only the direct child was signalled"
    # When the thing under test is a *bound*, assert the bound. `killed` says
    # the code noticed; the clock says it acted.
    assert elapsed < 10.0, (
        f"bounded at 1s and took {elapsed:.1f}s: it waited for the child "
        f"instead of returning when the bound expired")


def test_the_ocr_subprocess_is_given_the_tools_doctor_found(monkeypatch, tmp_path):
    """`doctor` says a tool is present; the stage has to be able to reach it.

    OCRmyPDF resolves Tesseract and Ghostscript itself and its search is
    narrower than `find_tool`'s — on Windows the system drive only, and off
    Windows not at all (`ocrmypdf/subprocess/_run.py` shims PATH under
    `os.name == "nt"` alone). Without this the promise in the README, that no
    tool has to be on PATH, held for the health check and not for the stage.
    """
    import extract

    installed = tmp_path / "somewhere" / "else"
    installed.mkdir(parents=True)
    monkeypatch.setattr(
        extract, "find_tool",
        lambda names, label="": str(installed / f"{names[0]}.exe"))

    environment = extract.ocr_environment({"PATH": "/only/this"})
    first = environment["PATH"].split(os.pathsep)[0]
    assert first == str(installed), (
        f"the resolved tool directory is not first on the child's PATH: "
        f"{environment['PATH']!r}")
    # Prepended, never replaced: a reader who put one build first meant it.
    assert "/only/this" in environment["PATH"].split(os.pathsep)


def test_a_tool_that_cannot_be_found_leaves_the_path_alone(monkeypatch):
    """No tool, no change — never an empty entry that shadows a real one."""
    import extract

    monkeypatch.setattr(extract, "find_tool", lambda names, label="": None)
    assert extract.ocr_environment({"PATH": "/only/this"})["PATH"] == "/only/this"


def test_missing_ocrmypdf_explains_all_three_prerequisites(monkeypatch, tmp_path):
    monkeypatch.setattr("extract.find_ocrmypdf", lambda: None)
    with pytest.raises(ExtractError) as caught:
        run_ocr(tmp_path / "in.pdf", tmp_path / "out.pdf", kind="scanned")
    message = str(caught.value)
    # Ghostscript is not distributed through winget, so the message must not
    # send anyone there for it.
    assert "pip install ocrmypdf" in message
    assert "tesseract" in message.lower()
    links = [urlparse(token) for token in message.split() if token.startswith("https://")]
    assert any(link.scheme == "https" and link.hostname == "ghostscript.com"
               and link.path == "/releases/gsdnld.html" for link in links)
    assert "--ocr off" in message


def test_find_ocrmypdf_prefers_path_then_falls_back_to_the_module(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ocrmypdf")
    assert find_ocrmypdf() == ["/usr/bin/ocrmypdf"]

    monkeypatch.setattr("shutil.which", lambda name: None)
    launcher = find_ocrmypdf()
    assert launcher is None or launcher[1:] == ["-m", "ocrmypdf"]


def test_controlled_ocr_execution_binds_installed_engine_not_path_script(tmp_path, monkeypatch):
    import io
    from pathlib import Path
    import subprocess
    import pymupdf
    from PIL import Image
    import extract

    source = tmp_path / "scan.pdf"
    pixels = io.BytesIO()
    Image.new("RGB", (30, 40), "navy").save(pixels, format="PNG")
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=pixels.getvalue())
        document.save(source)
    monkeypatch.setattr(extract, "find_ocrmypdf", lambda: ["/external/scripts/ocrmypdf"])
    monkeypatch.setattr("importlib.metadata.version", lambda package: "17.13.0")
    monkeypatch.setattr("ocrvalidation.version", lambda package: "17.13.0")
    seen = []

    def convert(command, timeout, **kwargs):
        seen.append(command)
        changed = io.BytesIO()
        Image.new("RGB", (30, 40), "white").save(changed, format="PNG")
        with pymupdf.open(source) as document:
            page = document[0]
            page.replace_image(page.get_images()[0][0], stream=changed.getvalue())
            page.insert_text((30, 60), "Recognized source sentence.", render_mode=3)
            document.save(Path(command[-1]))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(ir, "run_bounded", convert)
    destination = tmp_path / "ocr.pdf"
    result = run_ocr(source, destination, kind="scanned")
    assert seen[0][:3] == [sys.executable, "-m", "ocrmypdf"]
    assert result["proof"]["indexed_engine"] is True
    assert result["proof"]["output_sha256"] == ir.sha256_file(destination)
    assert result["coverage"]["pages"][0]["mapping"] == "engine-indexed"


def test_skipping_ocr_is_not_recorded_as_having_run(scanned_pdf, tmp_path,
                                                   monkeypatch):
    """`--ocr off` on a scan must not claim an OCR text layer it never made.

    Deriving the flag from the report was wrong in both directions that matter:
    the book's provenance said `from_ocr` when nothing had been recognised, and
    the extractor switched to OCR's loose font-size tolerance -- which is what
    turns ordinary paragraphs into false headings.
    """
    import argparse

    import extract
    import read_pdf

    parser = argparse.ArgumentParser()
    extract.add_arguments(parser)
    args = parser.parse_args([str(scanned_pdf), "--out", str(tmp_path),
                              "--ocr", "off", "--clean-scan", "off"])

    seen: dict[str, object] = {}
    real = read_pdf.read_pdf

    def spy(*positional, **keyword):
        seen["ocr_text"] = keyword.get("ocr_text")
        return real(*positional, **keyword)

    monkeypatch.setattr(read_pdf, "read_pdf", spy)

    report: dict = {}
    book = extract._extract_native(args, tmp_path, tmp_path / "assets", report)

    assert seen["ocr_text"] is False
    assert book["source"]["from_ocr"] is False
    # The skip is still reported, so the gap stays visible rather than silent.
    assert "skipped" in report["ocr"]

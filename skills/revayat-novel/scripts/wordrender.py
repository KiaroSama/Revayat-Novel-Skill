"""Lay a .docx out as pages, with whatever this machine actually has.

Word on Windows, LibreOffice everywhere else. Word first where it exists because
the deliverable *is* a .docx and Word's pagination is the one the reader will
see; LibreOffice elsewhere because a structural check against a slightly
different layout is worth far more than no check at all — none of the render QA
questions ("is this block present once", "did this paragraph come out
left-to-right", "is the plate the right shape") depend on where a line broke.

**This module is also the worker.** Run it as a script and it converts one file:

    python wordrender.py <input.docx> <output-dir>

That exists for one reason. COM has no cancellation: a `Word.Application` call
that wedges blocks its thread forever, and a timeout argument on a function that
cannot honour it is a lie that shows up as a hung pipeline at 3am. Driving Word
in a child process makes the wall clock real — the parent kills the process
tree and reports a named failure instead of waiting.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

#: Word's own "save as PDF" format code.
WORD_PDF_FORMAT = 17

#: How long one document may take before the parent gives up on it. Measured:
#: a ten-paragraph document takes about 8s on a cold Word, most of which is
#: Word starting rather than laying anything out.
DEFAULT_TIMEOUT = 180.0


class RenderError(RuntimeError):
    """The page could not be laid out — the message names what to install."""


# --------------------------------------------------------------------------- #
# What this machine can do
# --------------------------------------------------------------------------- #

def find_libreoffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return None


def word_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401,PLC0415
    except ImportError:
        return False
    return True


def backend() -> str:
    """``"word"``, ``"libreoffice"`` or ``""`` when neither is here."""
    if word_available():
        return "word"
    if find_libreoffice():
        return "libreoffice"
    return ""


def unavailable_reason() -> str:
    """Why no page can be laid out here, or ``""``. Names the fix per platform."""
    if backend():
        return ""
    if sys.platform == "win32":
        return ("neither Word nor LibreOffice can be driven here: install "
                "pywin32 to use the Word already on this machine "
                "(pip install pywin32), or install LibreOffice")
    return ("LibreOffice is not installed, and Word cannot be driven off "
            "Windows (Debian: apt install libreoffice-writer · "
            "macOS: brew install --cask libreoffice)")


# --------------------------------------------------------------------------- #
# The two backends
# --------------------------------------------------------------------------- #

def _with_word(docx: Path, out_dir: Path) -> Path:
    """Drive Word through COM. Runs in the *worker*, never in the caller."""
    import pythoncom  # noqa: PLC0415
    import win32com.client  # noqa: PLC0415

    produced = (out_dir / (docx.stem + ".pdf")).resolve()
    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = False
        document = word.Documents.Open(str(docx), ReadOnly=True,
                                       AddToRecentFiles=False)
        # A document built by python-docx carries no layout, and its field
        # results — the TOC, the page numbers — are whatever was cached at build
        # time until Word works them out.
        document.Fields.Update()
        document.Repaginate()
        document.SaveAs2(str(produced), FileFormat=WORD_PDF_FORMAT)
    finally:
        # Order matters, and so does the guard: a hidden WINWORD.EXE left
        # running is how a test run ends with a process nobody can see.
        try:
            if document is not None:
                document.Close(SaveChanges=False)
        finally:
            if word is not None:
                word.Quit()
            pythoncom.CoUninitialize()
    return produced


def _with_libreoffice(docx: Path, out_dir: Path, timeout: float) -> Path:
    launcher = find_libreoffice()
    if not launcher:
        raise RenderError(unavailable_reason())
    finished = subprocess.run(
        [launcher, "--headless", "--convert-to", "pdf", "--outdir",
         str(out_dir), str(docx)],
        capture_output=True, timeout=timeout,
    )
    produced = out_dir / (docx.stem + ".pdf")
    if finished.returncode != 0 or not produced.exists():
        detail = finished.stderr.decode("utf-8", "replace").strip()[:300]
        raise RenderError(f"LibreOffice produced no PDF: {detail}")
    return produced


# --------------------------------------------------------------------------- #
# The caller's entry point
# --------------------------------------------------------------------------- #

def render(docx: Path, out_dir: Path, *,
           timeout: float = DEFAULT_TIMEOUT) -> tuple[Path, str]:
    """Lay ``docx`` out as a PDF. Returns ``(pdf, backend_name)``.

    ``timeout`` is enforced for real on both paths: LibreOffice is already a
    subprocess, and Word is put into one for exactly this reason.
    """
    docx = Path(docx).resolve()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not docx.exists():
        raise RenderError(f"{docx} is not there")

    chosen = backend()
    if not chosen:
        raise RenderError(unavailable_reason())

    if chosen == "libreoffice":
        try:
            return _with_libreoffice(docx, out_dir, timeout), chosen
        except subprocess.TimeoutExpired as error:
            raise RenderError(
                f"LibreOffice did not finish within {timeout:.0f}s"
            ) from error

    # Word, in a child process, so the clock below is a real one.
    command = [sys.executable, str(Path(__file__).resolve()), str(docx),
               str(out_dir)]
    try:
        finished = subprocess.run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise RenderError(
            f"Word did not finish within {timeout:.0f}s and was terminated; "
            f"the document may be waiting on a dialog"
        ) from error

    if finished.returncode != 0:
        detail = finished.stderr.decode("utf-8", "replace").strip()[-400:]
        raise RenderError(f"Word could not lay out {docx.name}: {detail}")

    produced = out_dir / (docx.stem + ".pdf")
    if not produced.exists():
        raise RenderError(f"Word reported success but wrote no PDF to {produced}")
    return produced, chosen


def main(argv: list[str] | None = None) -> int:
    """The worker: one document, one PDF, one process to kill if it wedges."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print(f"usage: {Path(__file__).name} <input.docx> <output-dir>",
              file=sys.stderr)
        return 2
    try:
        _with_word(Path(argv[0]), Path(argv[1]))
    except Exception as error:  # pywin32 raises com_error, not an OSError
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

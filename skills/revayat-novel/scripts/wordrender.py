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

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

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

#: Where a normal install puts the binary without putting it on PATH. The macOS
#: cask installs into the app bundle and adds nothing to PATH, so a Mac with
#: LibreOffice installed the ordinary way answered "LibreOffice is not
#: installed" and skipped every render check. The Windows entries are the same
#: story for an installer that did not offer to amend PATH.
BUNDLED_LIBREOFFICE = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/local/bin/soffice",
    "/opt/homebrew/bin/soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
)


def find_libreoffice() -> str | None:
    """The soffice binary, on PATH or where an ordinary install leaves it."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in BUNDLED_LIBREOFFICE:
        if Path(candidate).is_file():
            return candidate
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


def _run_bounded(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run ``command`` and, on timeout, kill it *and everything it started*.

    `subprocess.run(timeout=...)` kills only the direct child. Both renderers
    are launchers: the Word path's child is a Python worker whose grandchild is
    WINWORD.EXE, started through COM; `soffice` forks `soffice.bin` and returns.
    So the documented promise above - "the parent kills the process tree" - was
    not kept by the call that made it, and a timeout left a hidden renderer
    running with its COM teardown never reached. A book is hundreds of renders,
    so one leak per timeout is how a machine quietly runs out of memory.
    """
    popen_extra: dict[str, Any] = {}
    if os.name == "posix":
        # Its own process group, so one signal reaches the launcher and the
        # process it forked.
        popen_extra["start_new_session"] = True
    else:
        popen_extra["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, **popen_extra)
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        # Drain, so the pipes are closed and the handles released before the
        # caller reports; the process is already dead so this cannot block.
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise
    return subprocess.CompletedProcess(command, process.returncode, out, err)


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill ``process`` and its descendants, on either platform."""
    if os.name == "nt":
        # Windows has no process groups that survive a launcher, so ask the OS
        # to walk the tree. /T is the whole point; /F because a wedged renderer
        # is not going to honour a polite request.
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, check=False)
        process.kill()
        return
    import signal  # noqa: PLC0415
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        process.kill()


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
    # A private profile per render, and not for tidiness. LibreOffice is
    # single-instance *per profile*: with the default one, an invocation made
    # while another LibreOffice is running - the operator's own Writer window,
    # or the next page of the same book - hands the work to that process and
    # returns 0 immediately. The PDF then arrives late or never, and the guard
    # below reports "produced no PDF" with an empty detail, because the exit
    # code was 0 and stderr was silent. A page that could have been rendered
    # comes back unverified naming nothing.
    with tempfile.TemporaryDirectory(prefix="revayat-novel-soffice-") as profile:
        finished = _run_bounded(
            [launcher,
             f"-env:UserInstallation={Path(profile).resolve().as_uri()}",
             "--headless", "--norestore", "--invisible", "--nolockcheck",
             "--convert-to", "pdf", "--outdir", str(out_dir), str(docx)],
            timeout,
        )
    produced = out_dir / (docx.stem + ".pdf")
    if finished.returncode != 0 or not produced.exists():
        detail = (finished.stderr.decode("utf-8", "replace").strip()[:300]
                  or f"exit {finished.returncode} and no {produced.name}")
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
        finished = _run_bounded(command, timeout)
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

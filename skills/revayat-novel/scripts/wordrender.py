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
import tempfile
from pathlib import Path

# Bounding a launcher and killing what it started moved to `bookir`: the OCR
# stage needs exactly the same thing, and neither module should depend on the
# other — this one is a render backend, that one is an extraction stage. Kept
# under the old private names because they are this module's published surface
# for its tests, which monkeypatch `wordrender._kill_tree` to prove the kill.
from bookir import (  # noqa: F401
    kill_tree as _kill_tree,
    run_bounded as _run_bounded,
)

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


def render_many(docs: list[Path], out_dir: Path, *,
                timeout: float = DEFAULT_TIMEOUT
                ) -> tuple[dict[Path, Path], dict[Path, str], str]:
    """Lay several documents out in one renderer session.

    Returns ``(produced, failed, backend)``: a path per document that rendered, a
    reason per document that did not, and which program did it. A document in
    neither map was **never reached** — the batch was bounded before it got there
    — and that is a third state on purpose: reporting it as failed would blame a
    page for a wedge in front of it.

    ``timeout`` bounds the **batch**, not each document. That is deliberate and is
    the only honest bound available: a per-document timeout inside a warm session
    needs a cancellable COM call and there is none, which is the whole reason the
    worker is a subprocess. The worker reports each document as it finishes, so
    when the parent kills the tree the pages that were done are still known.

    Measured (spike 011, reproduced 2026-09-12): 7.30s per page cold against
    0.66s warm, 91%, and a wedge at document 3 of 5 still left 1 and 2 recorded.
    """
    docs = [Path(d).resolve() for d in docs]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: dict[Path, Path] = {}
    failed: dict[Path, str] = {}
    if not docs:
        return produced, failed, backend()

    chosen = backend()
    if not chosen:
        raise RenderError(unavailable_reason())

    if chosen == "libreoffice":
        launcher = find_libreoffice()
        # One invocation, every path on the command line, in a private profile —
        # the same isolation the single path uses and for the same measured
        # reason. LibreOffice carries none of Word's COM state between documents,
        # which is why this is the smaller risk of the two backends.
        wedged = False
        with tempfile.TemporaryDirectory(prefix="revayat-novel-soffice-") as profile:
            try:
                _run_bounded(
                    [launcher,
                     f"-env:UserInstallation={Path(profile).resolve().as_uri()}",
                     "--headless", "--norestore", "--invisible", "--nolockcheck",
                     "--convert-to", "pdf", "--outdir", str(out_dir),
                     *[str(d) for d in docs]],
                    timeout,
                )
            except subprocess.TimeoutExpired:
                wedged = True   # whatever landed before the bound still counts
        for docx in docs:
            candidate = out_dir / (docx.stem + ".pdf")
            if candidate.exists():
                produced[docx] = candidate
            elif not wedged:
                # The run completed, so every document was attempted and this one
                # has no output: that is a failure, not a document nobody reached.
                # LibreOffice gives no per-document progress, so only the absence
                # of a timeout lets those two states be told apart — and telling
                # them apart is the whole point of the third state.
                failed[docx] = "LibreOffice produced no PDF for it"
        return produced, failed, chosen

    command = [sys.executable, str(Path(__file__).resolve()), "--batch",
               str(out_dir), *[str(d) for d in docs]]
    try:
        finished = _run_bounded(command, timeout)
        out, err = finished.stdout or b"", finished.stderr or b""
    except subprocess.TimeoutExpired as wedge:
        # The pages the worker had already reported are still known, which is the
        # whole point of it reporting per document rather than at the end.
        out, err = wedge.stdout or b"", wedge.stderr or b""

    by_name = {docx.name: docx for docx in docs}
    for line in out.decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] == "OK" and parts[1] in by_name:
            produced[by_name[parts[1]]] = out_dir / parts[2]
    for line in err.decode("utf-8", "replace").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0] == "FAIL" and parts[1] in by_name:
            failed[by_name[parts[1]]] = parts[2]
    return produced, failed, chosen


def _with_word_batch(docs: list[Path], out_dir: Path) -> int:
    """N documents through **one** Word session. Runs in the worker only.

    The loop lives here rather than in the parent because COM has no
    cancellation: the only reliable bound is the parent killing this tree, and
    that stays true however many documents are inside. Measured (spike 011,
    reproduced 2026-09-12): the first document costs 1.98s and every one after it
    0.31-0.34s, against 7.30s each when every page gets its own process — about
    33 minutes on a 300-page book.

    Each document gets its own try/finally around Open/Close, which is what makes
    one bad document one bad document: measured, a corrupt file raised
    ``com_error`` in 0.04s and the two after it rendered normally.

    One line per document on stdout, flushed immediately, so a parent that has to
    kill this process still knows exactly which pages were done — that is how a
    wedge is attributed to the page that wedged rather than to the pages behind
    it.
    """
    import pythoncom  # noqa: PLC0415
    import win32com.client  # noqa: PLC0415

    failures = 0
    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = False
        for docx in docs:
            produced = (out_dir / (docx.stem + ".pdf")).resolve()
            document = None
            try:
                document = word.Documents.Open(str(docx.resolve()), ReadOnly=True,
                                               AddToRecentFiles=False)
                document.Fields.Update()
                document.Repaginate()
                document.SaveAs2(str(produced), FileFormat=WORD_PDF_FORMAT)
            except Exception as error:  # com_error is not an OSError
                failures += 1
                print(f"FAIL\t{docx.name}\t{type(error).__name__}: {error}",
                      file=sys.stderr, flush=True)
            else:
                print(f"OK\t{docx.name}\t{produced.name}", flush=True)
            finally:
                if document is not None:
                    try:
                        document.Close(SaveChanges=False)
                    except Exception:
                        pass
    finally:
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    """The worker: one process to kill if it wedges.

    Two invocations, and the single-document one is unchanged because the
    per-page path every gate depends on uses it:

        wordrender.py <input.docx> <output-dir>
        wordrender.py --batch <output-dir> <doc.docx> [<doc.docx> ...]
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "--batch":
        if len(argv) < 3:
            print(f"usage: {Path(__file__).name} --batch <output-dir> <doc.docx>…",
                  file=sys.stderr)
            return 2
        out_dir = Path(argv[1])
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            return _with_word_batch([Path(a) for a in argv[2:]], out_dir)
        except Exception as error:  # a failure to start Word at all
            print(f"{type(error).__name__}: {error}", file=sys.stderr)
            return 1

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

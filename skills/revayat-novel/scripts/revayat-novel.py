"""Revayat Novel — one entry point for every pipeline stage.

    python revayat-novel.py extract  book.pdf --out work/
    python revayat-novel.py glossary scan --book work/book.json --out work/glossary.json
    python revayat-novel.py chunk    build --book work/book.json --out work/chunks
    python revayat-novel.py merge    --book work/book.json --chunks work/chunks
    python revayat-novel.py falint   fix --book work/book.json
    python revayat-novel.py qa       check --book work/book.json
    python revayat-novel.py build    --book work/book.json --out out/book.fa.docx
    python revayat-novel.py qa       docx --file out/book.fa.docx --book work/book.json

``doctor`` reports which optional tools are present, so a missing OCR engine is
a clear message up front rather than a confusing failure mid-book.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bookir as ir  # noqa: E402  (must follow the sys.path bootstrap)

STAGES = {
    "extract": "extract",
    "ocr-sidecar": "ocr_sidecar",
    "glossary": "glossary",
    "chunk": "chunk",
    "pages": "pagerun",
    "render-qa": "renderqa",
    "doc-qa": "docqa",
    "merge": "merge",
    "falint": "falint",
    "qa": "qa",
    "build": "build_docx",
}

REQUIRED = {
    "pymupdf": "PDF reading, image extraction and page geometry",
    "docx": "DOCX reading and writing (package: python-docx)",
    "bs4": "EPUB parsing (package: beautifulsoup4)",
    "lxml": "OOXML manipulation (installed with python-docx)",
    "PIL": "removing a colour watermark from a scan (package: pillow)",
}

#: Optional binaries, each as the list of names it may go by. Ghostscript is
#: ``gs`` on Unix but ``gswin64c`` / ``gswin32c`` on Windows — checking only the
#: Unix name reports it missing on every Windows machine that has it.
OPTIONAL_TOOLS = {
    "ocrmypdf": (["ocrmypdf"], "adds a text layer to scanned or mixed PDFs"),
    "tesseract": (["tesseract"], "the OCR engine OCRmyPDF drives"),
    "ghostscript": (["gs", "gswin64c", "gswin32c"], "required by OCRmyPDF"),
    "mineru": (["mineru", "magic-pdf"], "stronger extraction for difficult scans"),
}


def render_backend() -> str:
    """Which program will lay documents out here, asked of the code that does it.

    Not a `shutil.which` for `WINWORD`: Word is never on PATH — it is driven
    through COM — so that reported Word missing on every Windows machine that
    had it, and never mentioned LibreOffice at all. `wordrender` is the one
    place that decides; `doctor` repeats its answer rather than guessing again.
    """
    import wordrender  # noqa: PLC0415  (the scripts directory is on sys.path)

    backend = wordrender.backend()
    if backend == "word":
        return "Microsoft Word via COM (pywin32) — lays the document out for render QA"
    if backend == "libreoffice":
        return f"LibreOffice at {wordrender.find_libreoffice()} — lays the document out for render QA"
    return f"not found — {wordrender.unavailable_reason()}"


def ocrmypdf_launcher() -> str | None:
    """Asked of the code that runs it, rather than guessed a second time.

    `extract.find_ocrmypdf` already knows the two ways it can be available: on
    PATH, or as a module in *this* interpreter — which is the normal case when
    the skill's dependencies live in a virtual environment that is not on PATH,
    and which a `shutil.which` can never see.
    """
    import extract  # noqa: PLC0415  (the scripts directory is on sys.path)

    launcher = extract.find_ocrmypdf()
    return " ".join(launcher) if launcher else None


def find_tool(names: list[str], label: str = "") -> str | None:
    """Where a tool is, asked of `extract` — the stage that drives them.

    Not a `shutil.which` here: a tool installed anywhere but PATH is the
    ordinary case on Windows, and doctor answering that question separately
    from the code that runs the tool is how the two came to disagree.
    """
    import extract  # noqa: PLC0415

    return extract.find_tool(names, label)


def doctor() -> dict[str, object]:
    modules: dict[str, str] = {}
    for name, why in REQUIRED.items():
        try:
            module = importlib.import_module(name)
            version = getattr(module, "__version__", None)
            if version is None:
                try:
                    from importlib.metadata import version as pkg_version
                    version = pkg_version(
                        {"docx": "python-docx", "bs4": "beautifulsoup4",
                         "PIL": "pillow"}.get(name, name)
                    )
                except Exception:
                    version = "installed"
            modules[name] = str(version)
        except ImportError:
            modules[name] = f"MISSING — needed for {why}"

    tools = {}
    for label, (names, why) in OPTIONAL_TOOLS.items():
        found = (ocrmypdf_launcher() if label == "ocrmypdf"
                 else find_tool(names, label))
        tools[label] = found or f"not found — {why}"
    tools["render"] = render_backend()
    missing = [name for name, value in modules.items() if str(value).startswith("MISSING")]
    return {
        "python": sys.version.split()[0],
        "required": modules,
        "optional_tools": tools,
        "ready": not missing,
        "install": (
            "pip install -r requirements.txt" if missing else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__.strip())
        print("\nstages: " + ", ".join(sorted(STAGES)) + ", doctor")
        return 0

    stage, rest = argv[0], argv[1:]

    if stage == "doctor":
        report = doctor()
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report["ready"] else 1

    if stage not in STAGES:
        print(f"unknown stage {stage!r}; expected one of "
              f"{', '.join(sorted(STAGES))}, doctor", file=sys.stderr)
        return 2

    module = importlib.import_module(STAGES[stage])
    return int(module.main(rest) or 0)


if __name__ == "__main__":
    sys.exit(main())

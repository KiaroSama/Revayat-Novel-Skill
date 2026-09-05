"""Revayat — one entry point for every pipeline stage.

    python revayat.py extract  book.pdf --out work/
    python revayat.py glossary scan --book work/book.json --out work/glossary.json
    python revayat.py chunk    build --book work/book.json --out work/chunks
    python revayat.py merge    --book work/book.json --chunks work/chunks
    python revayat.py falint   fix --book work/book.json
    python revayat.py qa       check --book work/book.json
    python revayat.py build    --book work/book.json --out out/book.fa.docx
    python revayat.py qa       docx --file out/book.fa.docx --book work/book.json

``doctor`` reports which optional tools are present, so a missing OCR engine is
a clear message up front rather than a confusing failure mid-book.
"""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bookir as ir  # noqa: E402  (must follow the sys.path bootstrap)

STAGES = {
    "extract": "extract",
    "glossary": "glossary",
    "chunk": "chunk",
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
}

OPTIONAL_TOOLS = {
    "ocrmypdf": "adds a text layer to scanned or mixed PDFs",
    "tesseract": "the OCR engine OCRmyPDF drives",
    "gs": "Ghostscript, required by OCRmyPDF",
    "mineru": "stronger layout/OCR extraction for difficult scans",
    "soffice": "LibreOffice, for rendering the DOCX to PDF during visual QA",
}


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
                        {"docx": "python-docx", "bs4": "beautifulsoup4"}.get(name, name)
                    )
                except Exception:
                    version = "installed"
            modules[name] = str(version)
        except ImportError:
            modules[name] = f"MISSING — needed for {why}"

    tools = {
        name: (shutil.which(name) or f"not found — {why}")
        for name, why in OPTIONAL_TOOLS.items()
    }
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

"""Optional tool identities and platform installation locations."""

#: The names a binary may go by, and why a reader wants it. Ghostscript is `gs`
#: on Unix and `gswin64c` / `gswin32c` on Windows — checking only the Unix name
#: reported it missing on every Windows machine that had it.
#:
#: Here rather than in the dispatcher, and keyed by the same labels as
#: `BUNDLED_TOOLS` below, because the two are joined by that string: `find_tool`
#: does `BUNDLED_TOOLS.get(label, ())`, so a label spelled differently in the
#: two tables silently searches no install locations at all and falls back to
#: PATH — which is the exact defect this mechanism exists to fix, with nothing
#: anywhere to report it. One table cannot drift from itself.
OPTIONAL_TOOLS = {
    "ocrmypdf": (["ocrmypdf"], "adds a text layer to scanned or mixed PDFs"),
    "tesseract": (["tesseract"], "the OCR engine OCRmyPDF drives"),
    "ghostscript": (["gs", "gswin64c", "gswin32c"], "required by OCRmyPDF"),
    "mineru": (["mineru", "magic-pdf"], "stronger extraction for difficult scans"),
}

#: Where an ordinary install leaves a tool *without* putting it on PATH.
#:
#: Same shape and the same measured reason as `wordrender.BUNDLED_LIBREOFFICE`:
#: `shutil.which` alone reported MinerU and Tesseract missing on a machine that
#: had both installed, so `doctor` printed install instructions for software the
#: reader already had, `ocr_sidecar` refused to run, and the OCR tier skipped
#: itself. A tool that is present but not on PATH is the ordinary case, not the
#: exception.
#:
#: A pattern containing `<drive>` is Windows-only and is expanded against every
#: fixed drive — a large optional tool is routinely installed off the system
#: drive, and the MinerU this was measured against lives on `G:`. Every other
#: pattern is used as written, with `~` expanded, and is tried on any platform.
#:
#: It lives here rather than in the dispatcher because `extract` is the stage
#: that drives these tools and already owns `find_ocrmypdf` — and because the
#: OCR tests need the same answer `doctor` gives. Two places deciding separately
#: is how they came to disagree.
BUNDLED_TOOLS = {
    "tesseract": (
        r"<drive>\Program Files\Tesseract-OCR\tesseract.exe",
        r"<drive>\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/opt/homebrew/bin/tesseract",          # Homebrew on Apple silicon
        "/usr/local/bin/tesseract",             # Homebrew on Intel, /usr/local
        "/opt/local/bin/tesseract",             # MacPorts
        "~/.local/bin/tesseract",               # pip --user, pipx
    ),
    "ghostscript": (
        r"<drive>\Program Files\gs\gs*\bin\gswin64c.exe",
        r"<drive>\Program Files\gs\gs*\bin\gswin32c.exe",
        "/opt/homebrew/bin/gs",
        "/usr/local/bin/gs",
        "/opt/local/bin/gs",
    ),
    "mineru": (
        r"<drive>\Program Files\MinerU\venv\Scripts\mineru.exe",
        r"<drive>\MinerU\venv\Scripts\mineru.exe",
        "/opt/MinerU/venv/bin/mineru",
        "~/MinerU/venv/bin/mineru",
        "~/.local/bin/mineru",
    ),
}

#: Every label with install locations must be a tool `doctor` asks about, and
#: every tool that can live off PATH must have locations. `ocrmypdf` is the one
#: exception by design: `find_ocrmypdf` resolves it, from PATH or as a module in
#: this interpreter, so it needs no path patterns. Checked at import because it
#: is a structural invariant over two literals in this one file — a mismatch
#: means the module is broken, not that some input was bad.
assert set(BUNDLED_TOOLS) == set(OPTIONAL_TOOLS) - {"ocrmypdf"}, (
    f"the two tool tables disagree: {set(BUNDLED_TOOLS) ^ set(OPTIONAL_TOOLS)}")



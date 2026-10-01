"""Optional tool discovery through doctor and interpreter locations."""

import os
from pathlib import Path

def _cli():
    """The dispatcher, loaded by path: its filename is not an importable name."""
    import importlib.util
    from pathlib import Path

    entry = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" \
        / "scripts" / "revayat-novel.py"
    spec = importlib.util.spec_from_file_location("revayat_novel_cli", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_doctor_finds_a_tool_that_is_installed_but_not_on_path(tmp_path, monkeypatch):
    r"""`shutil.which` alone is not "is it installed" on Windows.

    Measured: MinerU at `G:\Program Files\MinerU`, Tesseract under
    `C:\Program Files`, and OCRmyPDF inside the project venv were all reported
    missing on a machine that had all three — so `doctor` printed install
    instructions for software the reader had already installed, the OCR tier
    skipped itself, and `ocr_sidecar` refused to run. That is the same defect
    the render backend had with `WINWORD`.

    Nothing here depends on what this machine actually has: the search table and
    the drive list are both replaced, so the test measures the *mechanism*.
    """
    import extract

    cli = _cli()
    installed = tmp_path / "Program Files" / "Thing" / "thing.exe"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")

    monkeypatch.setattr(extract.shutil, "which", lambda name: None)
    monkeypatch.setattr(extract, "_drives", lambda: [str(tmp_path)])
    # Built with this platform's separator. The shipped table is Windows-only,
    # but the mechanism it drives is not, and a hard-coded backslash passes on
    # Windows and fails everywhere else — which is exactly what it did.
    pattern = os.sep.join(["<drive>", "Program Files", "Thing", "thing.exe"])
    monkeypatch.setattr(extract, "BUNDLED_TOOLS", {"thing": (pattern,)})

    assert cli.find_tool(["thing"], "thing") == str(installed)
    # A tool with no table entry still answers None rather than raising.
    assert cli.find_tool(["nothing"], "nothing") is None
    # And PATH still wins when it has an answer.
    monkeypatch.setattr(extract.shutil, "which", lambda name: "/usr/bin/thing")
    assert extract.find_tool(["thing"], "thing") == "/usr/bin/thing"


def test_doctor_picks_the_newest_versioned_install(tmp_path, monkeypatch):
    """Ghostscript installs into `gs10.07.1`; a machine may hold several."""
    import extract

    for version in ("gs10.02.0", "gs10.07.1"):
        binary = tmp_path / "gs" / version / "bin" / "gswin64c.exe"
        binary.parent.mkdir(parents=True)
        binary.write_text("", encoding="utf-8")

    monkeypatch.setattr(extract.shutil, "which", lambda name: None)
    monkeypatch.setattr(extract, "_drives", lambda: [str(tmp_path)])
    # This test isolates fallback installs, not an actual interpreter's tools.
    monkeypatch.setattr(extract, "_interpreter_scripts", lambda: tmp_path / "empty-bin")
    pattern = os.sep.join(["<drive>", "gs", "gs*", "bin", "gswin64c.exe"])
    monkeypatch.setattr(extract, "BUNDLED_TOOLS", {"ghostscript": (pattern,)})

    assert extract.find_tool(["gs"], "ghostscript").endswith(
        str(Path("gs10.07.1") / "bin" / "gswin64c.exe"))


def test_doctor_asks_extract_where_ocrmypdf_is(monkeypatch):
    """It can live in this interpreter rather than on PATH, and `which` cannot see that.

    `extract.find_ocrmypdf` already resolves both cases; `doctor` repeats its
    answer instead of guessing a second time — the same rule the render backend
    follows.
    """
    import extract

    cli = _cli()
    monkeypatch.setattr(extract, "find_ocrmypdf",
                        lambda: ["/usr/bin/python", "-m", "ocrmypdf"])
    assert cli.ocrmypdf_launcher() == "/usr/bin/python -m ocrmypdf"

    monkeypatch.setattr(extract, "find_ocrmypdf", lambda: None)
    assert cli.ocrmypdf_launcher() is None


def test_a_tool_installed_into_this_interpreter_is_found_on_any_platform(
        tmp_path, monkeypatch):
    """A venv's script directory is routinely absent from PATH — everywhere.

    `find_ocrmypdf` already handled this for its own tool by importing the
    module. Generalised: pip puts a console script beside the interpreter
    (`Scripts` on Windows, `bin` on POSIX), so a tool installed alongside this
    skill's own dependencies is invisible to `shutil.which` on Linux and macOS
    just as much as on Windows. The Windows-only `BUNDLED_TOOLS` table never
    covered that case.
    """
    import extract

    scripts = tmp_path / "venv-scripts"
    scripts.mkdir()
    name = "thing.exe" if os.name == "nt" else "thing"
    (scripts / name).write_text("", encoding="utf-8")

    monkeypatch.setattr(extract.shutil, "which", lambda n: None)
    monkeypatch.setattr(extract, "_interpreter_scripts", lambda: scripts)
    monkeypatch.setattr(extract, "BUNDLED_TOOLS", {})

    assert extract.find_tool(["thing"], "thing") == str(scripts / name)
    assert extract.find_tool(["absent"], "absent") is None


def test_one_table_describes_each_optional_tool():
    """Two tables joined by a label string, with nothing checking they agree.

    `find_tool` does `BUNDLED_TOOLS.get(label, ())`, so a label spelled one way
    in `doctor`'s table and another in the path table searched no install
    locations and fell back to PATH — the exact defect the table exists to fix,
    reported by nothing.
    """
    import extract

    cli = _cli()
    # `doctor` must ask about exactly the tools `extract` describes.
    assert cli.optional_tools() is extract.OPTIONAL_TOOLS
    # And every tool with a name list has path patterns, except the one that is
    # resolved as a module in this interpreter.
    assert set(extract.BUNDLED_TOOLS) == set(extract.OPTIONAL_TOOLS) - {"ocrmypdf"}
    # Every pattern list is non-empty: an entry with no locations is a label
    # that reads as covered and is not.
    for label, patterns in extract.BUNDLED_TOOLS.items():
        assert patterns, f"{label} has an empty pattern list"


def test_a_posix_install_location_is_searched_too(tmp_path, monkeypatch):
    """`<drive>` patterns are Windows-only; the rest must work anywhere.

    The first version of this table held Windows paths and nothing else, so on
    Linux and macOS `find_tool` degraded to a bare `shutil.which` — the very
    behaviour it was written to replace.
    """
    import extract

    installed = tmp_path / "opt" / "homebrew" / "bin" / "gs"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")

    monkeypatch.setattr(extract.shutil, "which", lambda n: None)
    monkeypatch.setattr(extract, "_interpreter_scripts", lambda: tmp_path / "nowhere")
    # An absolute pattern with no `<drive>`: used as written, on every platform.
    monkeypatch.setattr(extract, "BUNDLED_TOOLS", {"ghostscript": (str(installed),)})
    assert extract.find_tool(["gs"], "ghostscript") == str(installed)

    # `~` is expanded, which is how a `pip --user` install is reachable.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    home_tool = tmp_path / ".local" / "bin" / "gs"
    home_tool.parent.mkdir(parents=True)
    home_tool.write_text("", encoding="utf-8")
    monkeypatch.setattr(extract, "BUNDLED_TOOLS",
                        {"ghostscript": (os.path.join("~", ".local", "bin", "gs"),)})
    assert extract.find_tool(["gs"], "ghostscript") == str(home_tool)



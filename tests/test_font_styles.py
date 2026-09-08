"""The requested Persian face survives into the package, and a substitution is reported.

Two defects met here, both measured on 2026-09-08 with the real builder and a
real Word render, and both silent until they were looked for:

* **Headings were never set in the requested face.** python-docx's template
  gives its heading and title styles four theme font attributes, and in OOXML a
  theme attribute supersedes the explicit one beside it — so the `w:cs` the
  builder wrote was ignored. With ``--font Vazir`` the body came out Vazir and
  the heading came out Times New Roman Bold; with ``w:cstheme`` stripped from
  ``word/styles.xml`` the heading came out Vazir Bold.
* **The default face did not arrive at all.** ``Vazirmatn`` is commonly
  installed as a *variable* font, which Word will not resolve for a
  complex-script run; a book asking for it was set in Calibri on a machine that
  had it. The default is now ``Vazir``.

Everything here reads the built **package** or calls the check directly. Nothing
renders, so it all runs on every CI runner — and, more importantly, no assertion
depends on which fonts the machine happens to have. That is deliberate: a test
that renders a page and asserts a font name passes on a developer machine and
fails on a bare runner, which is the very failure being guarded against.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

import bookir as ir
import build_docx
import ooxml
import pagecheck
import preview
import qa

#: Every style the builder restyles for a right-to-left book.
RESTYLED = ("Normal", "Title", "Quote", "Caption", "List Bullet", "List Number",
            *(f"Heading {n}" for n in range(1, 7)))


def _built(tmp_path: Path, font: str = "Vazir") -> Path:
    book = ir.new_book(title="font probe", source_format="text", pages=1)
    book["blocks"] = [
        ir.make_block("heading", 1, level=1, page=1,
                      text="Chapter One", target="فصل نخست"),
        ir.make_block("paragraph", 2, page=1,
                      text="Body.", target="یک بند فارسی برای اندازه‌گیری."),
    ]
    destination = tmp_path / f"{font.replace(' ', '_')}.docx"
    build_docx.Builder(book, tmp_path / "assets",
                       preview.production_options(font=font)).build(destination)
    return destination


def _styles(docx: Path) -> str:
    with zipfile.ZipFile(docx) as archive:
        return archive.read("word/styles.xml").decode("utf-8")


def _style_block(styles: str, style_id: str) -> str | None:
    found = re.search(rf'<w:style [^>]*w:styleId="{style_id}".*?</w:style>',
                      styles, re.S)
    return found.group(0) if found else None


# --------------------------------------------------------------------------- #
# The theme attribute that made w:cs a dead letter
# --------------------------------------------------------------------------- #

def test_no_restyled_style_keeps_a_theme_font_attribute(tmp_path):
    """A theme attribute supersedes the explicit `w:cs` written beside it.

    So the style said the right thing and the page did not. Measured: every
    heading of a built book came out in the theme font while the body was
    correct.
    """
    styles = _styles(_built(tmp_path))
    offenders = []
    for style_id in ("Normal", "Title", "Heading1", "Heading2", "Heading3"):
        block = _style_block(styles, style_id)
        if block is None:
            continue  # a template need not define every one of them
        for rfonts in re.findall(r"<w:rFonts\b[^>]*/>", block):
            for attribute in ooxml.THEME_FONT_ATTRIBUTES:
                if f"{attribute}=" in rfonts:
                    offenders.append(f"{style_id}: {rfonts}")
    assert not offenders, (
        "these styles still carry a theme font attribute, which Word obeys in "
        "preference to the w:cs beside it: " + "; ".join(offenders))


@pytest.mark.parametrize("style_id", ["Normal", "Title", "Heading1", "Heading3"])
def test_the_requested_face_is_pinned_on_the_styles_that_carry_prose(
        tmp_path, style_id):
    styles = _styles(_built(tmp_path, font="Vazir"))
    block = _style_block(styles, style_id)
    assert block is not None, f"{style_id} is missing from the built package"
    assert 'w:cs="Vazir"' in block, block


def test_the_title_style_is_restyled_at_all(tmp_path):
    """`Title` was absent from the list, and it is the only style on the title page."""
    styles = _styles(_built(tmp_path))
    block = _style_block(styles, "Title")
    assert block is not None
    assert "<w:bidi/>" in block or "<w:bidi " in block, block


def test_the_document_defaults_still_carry_the_face(tmp_path):
    """The body was always right; this guards it while the styles are changed."""
    styles = _styles(_built(tmp_path, font="Vazir"))
    defaults = re.search(r"<w:docDefaults>.*?</w:docDefaults>", styles, re.S)
    assert defaults is not None
    assert 'w:cs="Vazir"' in defaults.group(0)


def test_style_rtl_is_silent_about_a_style_the_template_lacks():
    """Naming a style a custom --template does not define must not raise."""
    import docx as python_docx

    document = python_docx.Document()
    ooxml.style_rtl(document, "No Such Style Here", persian_font="Vazir")


# --------------------------------------------------------------------------- #
# Reading back what the document asked for
# --------------------------------------------------------------------------- #

def test_the_requested_font_is_read_out_of_the_built_document(tmp_path):
    """Asked of the file, not of the caller — the same rule as text and direction."""
    assert pagecheck.requested_fonts(_built(tmp_path, font="Vazir"))["complex"] == "Vazir"


def test_a_file_that_is_not_a_document_asks_for_nothing(tmp_path):
    """Producing evidence must never be the thing that stops a page being reported on."""
    broken = tmp_path / "not.docx"
    broken.write_bytes(b"not really a document")
    assert pagecheck.requested_fonts(broken) == {"complex": "", "ascii": ""}


def test_the_default_font_is_vazir():
    """Not Vazirmatn: it is usually installed as a variable font, which Word
    will not resolve for a complex-script run. Measured — a book asking for it
    came back set in Calibri on a machine that had it installed."""
    assert preview.production_options().font == "Vazir"


# --------------------------------------------------------------------------- #
# Reporting a substitution
# --------------------------------------------------------------------------- #

def test_a_page_set_in_the_wrong_font_is_reported():
    report = qa.Report()
    pagecheck._check_fonts({"fonts": ["Calibri", "TimesNewRomanPSMT"]},
                           report, "page0001", requested="Vazir")
    assert [f["code"] for f in report.findings] == ["font-fallback"], report.findings
    assert report.findings[0]["severity"] == qa.WARNING, (
        "a fallback is a property of the rendering machine, not of the book; "
        "as an ERROR it fails every page on a runner with no Persian font")


@pytest.mark.parametrize("requested, seen", [
    ("Vazir", ["Vazir"]),
    ("Vazir", ["Vazir-Bold", "TimesNewRomanPSMT"]),
    ("Times New Roman", ["TimesNewRomanPSMT"]),
    ("Tahoma", ["Tahoma", "TimesNewRomanPSMT"]),
])
def test_the_font_that_did_arrive_is_not_reported_as_a_fallback(requested, seen):
    """A renderer renames: `Times New Roman` comes back `TimesNewRomanPSMT`.

    Exact equality would report a fallback on every correct page.
    """
    report = qa.Report()
    pagecheck._check_fonts({"fonts": seen}, report, "page0001", requested=requested)
    assert not report.findings, (requested, seen, report.findings)


def test_a_document_that_asks_for_nothing_is_not_judged():
    """Silence, not a pass and not a failure: a .docx from elsewhere has none of ours."""
    report = qa.Report()
    pagecheck._check_fonts({"fonts": ["Calibri"]}, report, "page0001", requested="")
    assert not report.findings


def test_a_render_with_no_font_names_says_it_could_not_look():
    """"We could not check" is its own state here, exactly as it is for text."""
    report = qa.Report()
    pagecheck._check_fonts({"fonts": []}, report, "page0001", requested="Vazir")
    assert [f["code"] for f in report.findings] == ["font-unverified"]

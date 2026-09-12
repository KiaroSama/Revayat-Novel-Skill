"""The watermark stage: survey before committing, run on purpose, look at a page.

The cleaning itself is covered in `test_regressions.py` — it was found by running
a real 70-page scan and those tests guard the algorithm. What is tested here is
the *command surface*, and one invariant that is easy to break and silent when
broken: a survey that disagrees with the cleaner about which pages it would touch
is not a survey, it is a guess with a page number attached.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

import scan_clean


#: A stamp small enough to read as a watermark (well under the 6% artwork
#: ceiling) and saturated enough to be unambiguous. A real watermark measured
#: 0.24% of a page.
def _page(stamped: bool, artwork: bool = False):
    Image = pytest.importorskip("PIL.Image")
    from PIL import ImageDraw  # noqa: PLC0415

    if artwork:
        image = Image.new("RGB", (600, 800), (250, 248, 240))
        draw = ImageDraw.Draw(image)
        for y in range(100, 700, 7):
            draw.line((70, y, 530, y), fill=(40, 90, 170), width=5)
        return image

    image = Image.new("RGB", (600, 800), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for row in range(18):
        draw.text((40, 60 + row * 38), "The morning came slowly over the ridge.",
                  fill=(12, 12, 12))
    if stamped:
        draw.text((150, 380), "SAMPLE COPY", fill=(210, 70, 70))
        draw.line((100, 350, 500, 450), fill=(200, 90, 90), width=6)
    return image


@pytest.fixture
def stamped_pdf(tmp_path) -> Path:
    """Two stamped text pages, one clean text page, one colour plate."""
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("PIL.Image")

    path = tmp_path / "stamped.pdf"
    doc = pymupdf.open()
    for image in (_page(True), _page(True), _page(False), _page(False, artwork=True)):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=92)
        page = doc.new_page(width=300, height=400)
        page.insert_image(page.rect, stream=buffer.getvalue())
    doc.save(str(path))
    doc.close()
    return path


# --------------------------------------------------------------------------- #
# survey — the question you could not ask before
# --------------------------------------------------------------------------- #

def test_the_survey_names_a_verdict_and_a_fraction_for_every_page(stamped_pdf):
    report = scan_clean.survey_pdf(stamped_pdf)

    assert report["pages"] == 4
    assert [entry["page"] for entry in report["per_page"]] == [1, 2, 3, 4]
    assert report["counts"]["would-clean"] == 2
    assert report["counts"]["no-colour"] == 1
    assert report["counts"]["looks-like-artwork"] == 1
    # The ceiling a verdict was measured against travels with it, so a reader who
    # disagrees can see what the comparison was.
    assert report["artwork_fraction_ceiling"] == scan_clean.MAX_COLOURED_FRACTION
    stamped = next(e for e in report["per_page"] if e["page"] == 1)
    plate = next(e for e in report["per_page"] if e["page"] == 4)
    assert 0 < stamped["fraction"] < scan_clean.MAX_COLOURED_FRACTION
    assert plate["fraction"] > scan_clean.MAX_COLOURED_FRACTION


def test_the_survey_writes_nothing(stamped_pdf, tmp_path):
    """It exists so a reader can decide *before* spending an hour."""
    before = sorted(p.name for p in tmp_path.iterdir())
    scan_clean.survey_pdf(stamped_pdf)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_the_survey_and_the_cleaner_agree_about_every_page(stamped_pdf, tmp_path):
    """The invariant that makes the survey worth trusting.

    Both decide "is this one full-page raster with no text" through
    `_page_raster`. If they ever diverge the survey still prints a confident
    verdict per page and the cleaner quietly does something else — which is worse
    than having no survey at all.
    """
    survey = scan_clean.survey_pdf(stamped_pdf)
    run = scan_clean.clean_pdf(stamped_pdf, tmp_path / "out.pdf")

    would_clean = {e["page"] for e in survey["per_page"]
                   if e["verdict"] == "would-clean"}
    assert set(run["cleaned_pages"]) == would_clean

    skipped = {entry["page"] for entry in run["skipped_pages"]}
    predicted_skips = {e["page"] for e in survey["per_page"]
                       if e["verdict"] in {"no-colour", "looks-like-artwork"}}
    assert skipped == predicted_skips


# --------------------------------------------------------------------------- #
# preview — the lossy trade-off, made visible
# --------------------------------------------------------------------------- #

def test_a_preview_writes_the_before_and_the_after(stamped_pdf, tmp_path):
    made = scan_clean.preview_page(stamped_pdf, 1, tmp_path / "wm")

    assert made["ok"] is True
    assert set(made["renders"]) == {"original", "cleaned"}
    for path in made["renders"].values():
        assert Path(path).exists() and Path(path).stat().st_size > 0


def test_a_ghost_cut_removes_mid_grey_the_colour_pass_left(stamped_pdf, tmp_path):
    """`extraction.md` says a ghost cut "eats the letters underneath it too".

    That is a judgement the reader is asked to make, so it has to be visible
    rather than described. Measured here the way the module's own docstring
    measures it: the share of mid-grey pixels, which is what a desaturated
    watermark remnant looks like.
    """
    from PIL import Image as PILImage

    made = scan_clean.preview_page(stamped_pdf, 1, tmp_path / "wm",
                                   ghost_threshold=120)
    assert made["ok"] is True
    assert f"ghost_{120}" in made["renders"]
    assert made["ghost_pass"]["ghost_cut"] == 120

    def mid_grey_share(path: str) -> float:
        with PILImage.open(path) as image:
            histogram = image.convert("L").histogram()
        return sum(histogram[100:225]) / sum(histogram)

    plain = mid_grey_share(made["renders"]["cleaned"])
    ghosted = mid_grey_share(made["renders"]["ghost_120"])
    assert ghosted < plain, (
        f"the ghost cut left as much mid-grey as the colour pass "
        f"({ghosted:.5f} vs {plain:.5f}); it is not doing anything")


def test_a_page_that_is_not_a_scan_page_is_refused_by_name(stamped_pdf, tmp_path):
    """A born-digital page is not something this stage would ever touch, and
    saying so beats writing two identical PNGs."""
    pymupdf = pytest.importorskip("pymupdf")

    born_digital = tmp_path / "digital.pdf"
    doc = pymupdf.open()
    doc.new_page(width=300, height=400).insert_text(
        (40, 60), "Real text, no raster at all.", fontsize=11, fontname="helv")
    doc.save(str(born_digital))
    doc.close()

    made = scan_clean.preview_page(born_digital, 1, tmp_path / "wm")
    assert made["ok"] is False
    assert made["refused"] == "not-a-single-raster"


def test_a_page_the_book_does_not_have_is_refused_not_crashed(stamped_pdf, tmp_path):
    made = scan_clean.preview_page(stamped_pdf, 99, tmp_path / "wm")
    assert made["ok"] is False
    assert made["refused"] == "no-such-page"
    assert "4 pages" in made["detail"]


# --------------------------------------------------------------------------- #
# The command line
# --------------------------------------------------------------------------- #

def test_the_cli_surveys_runs_and_previews(stamped_pdf, tmp_path, capsys):
    assert scan_clean.main(["survey", "--pdf", str(stamped_pdf)]) == 0
    survey = json.loads(capsys.readouterr().out)
    assert survey["counts"]["would-clean"] == 2

    out = tmp_path / "cleaned.pdf"
    assert scan_clean.main(["run", "--pdf", str(stamped_pdf),
                            "--out", str(out)]) == 0
    run = json.loads(capsys.readouterr().out)
    assert run["cleaned"] == 2
    assert out.exists()
    # The original is never edited: every doc in this project promises that.
    assert stamped_pdf.exists()

    assert scan_clean.main(["preview", "--pdf", str(stamped_pdf), "--page", "1",
                            "--out", str(tmp_path / "wm")]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["ok"] is True


def test_the_cli_exits_non_zero_on_a_page_it_refuses(stamped_pdf, tmp_path, capsys):
    code = scan_clean.main(["preview", "--pdf", str(stamped_pdf), "--page", "99",
                            "--out", str(tmp_path / "wm")])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_the_survey_truncates_a_long_listing_and_says_so(tmp_path, capsys):
    """A 400-page scan must not print 400 rows by default, and must not silently
    print only some either."""
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("PIL.Image")

    path = tmp_path / "long.pdf"
    doc = pymupdf.open()
    buffer = io.BytesIO()
    _page(True).save(buffer, format="JPEG", quality=80)
    for _ in range(6):
        doc.new_page(width=300, height=400).insert_image(
            pymupdf.Rect(0, 0, 300, 400), stream=buffer.getvalue())
    doc.save(str(path))
    doc.close()

    assert scan_clean.main(["survey", "--pdf", str(path),
                            "--pages-listed", "2"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["pages"] == 6
    assert len(report["per_page"]) == 2
    assert "4 more" in report["per_page_truncated"]


def test_the_stage_is_reachable_through_the_dispatcher():
    """A stage the dispatcher does not know is a command nobody can run."""
    import importlib.util

    entry = (Path(__file__).resolve().parents[1] / "skills" / "revayat-novel"
             / "scripts" / "revayat-novel.py")
    spec = importlib.util.spec_from_file_location("revayat_novel_cli", entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.STAGES["clean-scan"] == "scan_clean"
    assert hasattr(scan_clean, "main") and hasattr(scan_clean, "add_arguments")


def test_a_preview_is_never_treated_as_review_evidence():
    """A preview is a tuning aid, not something a gate reads.

    `review.evidence_digest` binds a reviewer's five answers to the exact images
    they were shown. If a watermark preview ever entered that set, re-tuning a
    threshold would stale every standing review in the book at once.
    """
    import ast

    # Asserted on the imports, not on the source text: the module's docstring
    # *mentions* `review.evidence_digest` to say it deliberately does not use it,
    # and a grep cannot tell a mention from a call.
    tree = ast.parse(Path(scan_clean.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "review" not in imported, (
        "scan_clean imports review; a tuning preview must not be able to reach "
        "the digest a reviewer's verdict is bound to")
    assert not any(isinstance(node, ast.Attribute)
                   and node.attr == "evidence_digest"
                   for node in ast.walk(tree))

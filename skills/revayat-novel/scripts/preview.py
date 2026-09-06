"""A page-local preview: one source page, laid out on its own.

Render QA compares a translated page with the source page it came from, and for
a long time it did that by rendering the whole translated book and looking at
its page N. That is wrong for a translation and wrong in a way that produces
both false failures and false passes: Persian reflows, so source page 12 does
not become target page 12. Everything ahead of it moves, and by the middle of a
book the drift is pages. The check would then compare page 12's expectations
against whatever landed on the twelfth sheet, report the blocks as missing, and
report the blocks that *were* there as unexpected.

So a source page is rendered on its own. The preview holds exactly the blocks
`pagerun` says that page owns, set with the production builder - same styles,
same fonts, same RTL, same image sizing, same heading logic - so what is judged
is the real typesetting of real content and not an approximation of it. Its own
first page *is* source page N, by construction, and no index has to be guessed.

A preview may still run to more than one page: a page whose Persian is longer
than its English, or one carrying a second chapter opening. Every page of the
preview belongs to that source page, and QA reads all of them. Silently taking
the first would be the same bug in a smaller place.

The finished book is a different question and is checked separately, against
the artefact the reader receives - see `docqa`.
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any

import bookir as ir
import build_docx
import pagerun

#: Furniture that belongs to the book rather than to any one page. A preview
#: that carried them would be judged for a title page it does not have and a
#: contents list that describes a book of one page.
WHOLE_BOOK_FURNITURE = ("meta",)


def production_options(**overrides: Any) -> argparse.Namespace:
    """The builder's real defaults, taken from the builder.

    Deliberately parsed out of `build_docx.add_arguments` rather than written
    out here. A preview typeset with this module's idea of the defaults would
    drift from the book the moment a default changed, and it would drift
    silently - the preview would keep passing while the book it claims to
    predict was being set differently.
    """
    parser = argparse.ArgumentParser()
    build_docx.add_arguments(parser)
    options = parser.parse_args(["--book", "unused", "--out", "unused"])
    # The two that are not style: a contents list and a title page describe the
    # book, not this page, and both would land ahead of the page's own content.
    options.toc = False
    for name, value in overrides.items():
        setattr(options, name, value)
    return options


def page_book(book: dict[str, Any], page: int) -> dict[str, Any]:
    """The book as it would be if it were only this source page.

    Ownership comes from `pagerun.owners`, the same partition that decided what
    was sent out to be translated, so the preview shows exactly what this page
    was asked to produce - no more, and nothing that belongs to its neighbour.
    """
    job = next((j for j in pagerun.owners(book) if j["page"] == page), None)
    if job is None:
        return {**{k: v for k, v in book.items()
                   if k not in ("blocks", "footnotes", *WHOLE_BOOK_FURNITURE)},
                "blocks": [], "footnotes": [], "page": dict(book.get("page") or {})}

    lookup = ir.blocks_by_id(book)
    blocks = [copy.deepcopy(lookup[block_id])
              for block_id in job["block_ids"]
              if block_id in lookup
              # A page's own trailing break is what ended it. Honouring it here
              # would add an empty second preview page and a blank-region
              # finding for a page that is perfectly correct.
              and lookup[block_id]["type"] != "pagebreak"]

    owned = set(job["footnote_ids"])
    notes = [copy.deepcopy(n) for n in book.get("footnotes", []) if n["id"] in owned]

    preview = {k: v for k, v in book.items()
               if k not in ("blocks", "footnotes", *WHOLE_BOOK_FURNITURE)}
    preview["blocks"] = blocks
    preview["footnotes"] = notes
    # The geometry this page was measured at, not the book's first page's - a
    # book with a different trim partway through is reported, not averaged.
    preview["page"] = pagerun.geometry(book, job["block_ids"], lookup)
    return preview


def build(book_path: Path, page: int, destination: Path, *,
          assets: Path | None = None, **overrides: Any) -> dict[str, Any]:
    """Write the preview .docx for one source page. Returns the build report."""
    book_path, destination = Path(book_path), Path(destination)
    book = ir.load_book(book_path)
    assets = Path(assets) if assets else book_path.parent / "assets"

    only = page_book(book, page)
    if not only["blocks"]:
        return {"ok": False, "page": page, "refused": "unknown-page",
                "detail": f"{book_path} has no blocks on page {page}, so there "
                          f"is nothing to preview"}

    options = production_options(**overrides)
    report = build_docx.Builder(only, assets, options).build(destination)
    return {"ok": True, "page": page, "blocks": len(only["blocks"]),
            "footnotes": len(only["footnotes"]), **report}


def preview_path(work_dir: Path, page: int) -> Path:
    """Where a page's preview lives, so every caller looks in one place."""
    return Path(work_dir) / "previews" / f"page-{page:04d}.docx"

"""The `pages` command line: seven subcommands over one page lifecycle.

Split out of `pagerun` because it is a different job. `pagerun` decides what a
page *is* - who owns which block, what a worksheet carries, when a page has
earned `accepted` - and that is the part with consequences. This file only
turns argv into those calls and their answers into JSON, and it grew to a third
of the module while doing nothing a reader of the ownership rules needs to see.

The stage dispatcher imports `pagerun`, so `pagerun.main` and
`pagerun.add_arguments` stay the entry point and forward here. Nothing outside
had to learn a new name.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bookir as ir
import pagerun
import preview as page_preview
import review as page_review

def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)

    p_build = sub.add_parser("build", help="one worksheet per source page")
    p_build.add_argument("--book", required=True)
    p_build.add_argument("--out", required=True, help="page worksheet directory")
    p_build.add_argument("--glossary", default=None)
    p_build.add_argument("--budget", type=int, default=pagerun.DEFAULT_BUDGET,
                         help="worksheet characters one job may carry; a page "
                              "over it is split into jobs that fit, never "
                              "truncated")
    p_build.add_argument("--neighbour-chars", type=int, default=pagerun.NEIGHBOUR_CHARS,
                         help="hard maximum context characters per side")

    p_status = sub.add_parser("status", help="where every page stands")
    p_status.add_argument("--pages", required=True)

    p_next = sub.add_parser("next", help="the first page that is not accepted")
    p_next.add_argument("--pages", required=True)

    p_merge = sub.add_parser("merge", help="fold one page's answers into the book")
    p_merge.add_argument("--book", required=True)
    p_merge.add_argument("--pages", required=True)
    p_merge.add_argument("--page", type=int, required=True)
    p_merge.add_argument("--glossary", default=None)

    p_preview = sub.add_parser(
        "preview", help="lay this page out on its own, to be looked at")
    p_preview.add_argument("--book", required=True)
    p_preview.add_argument("--pages", required=True)
    p_preview.add_argument("--page", type=int, required=True)
    p_preview.add_argument("--assets", default=None,
                           help="asset directory (default: <book dir>/assets)")
    p_preview.add_argument("--out", default=None,
                           help="where to write it (default: "
                                "<work>/previews/page-NNNN.docx)")

    p_review = sub.add_parser(
        "review", help="file what a reviewer saw on the rendered page")
    p_review.add_argument("--pages", required=True)
    p_review.add_argument("--page", type=int, required=True)
    p_review.add_argument(
        "--answer", action="append", default=[], metavar="ID=yes|no",
        help="one per question: " + ", ".join(sorted(page_review.QUESTIONS)))
    p_review.add_argument("--note", default="",
                          help="what was wrong, in the reviewer's own words")

    p_accept = sub.add_parser(
        "accept", help="finish a page whose gates have all actually passed")
    p_accept.add_argument("--book", required=True)
    p_accept.add_argument("--pages", required=True)
    p_accept.add_argument("--page", type=int, required=True)


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel pages",
                                     description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)

    if args.action == "build":
        try:
            manifest = pagerun.build(
                Path(args.book), Path(args.out),
                glossary_path=Path(args.glossary) if args.glossary else None,
                budget=args.budget,
                neighbour_chars=args.neighbour_chars,
            )
        except (pagerun.OverBudget, pagerun.SourceCollision,
                pagerun.SourceUnavailable) as refusal:
            named = {
                pagerun.OverBudget: "over-budget",
                pagerun.SourceCollision: "source-directory-in-use",
                pagerun.SourceUnavailable: "source-pdf-unavailable",
            }
            print(json.dumps({
                "ok": False,
                "refused": named[type(refusal)],
                "detail": str(refusal),
            }, ensure_ascii=False, indent=1))
            return 2
        print(json.dumps({
            "pages": manifest["pages"],
            "jobs": len(manifest["chunks"]),
            "units": sum(c["units"] for c in manifest["chunks"]),
            "source_chars": sum(c["source_chars"] for c in manifest["chunks"]),
            "split": manifest["split"],
            "orphaned": manifest["orphaned"],
            "invalidated": manifest["invalidated"],
            "reference_pdf": manifest["reference_pdf"],
            "dir": args.out,
        }, ensure_ascii=False, indent=1))
        return 0

    if args.action == "status":
        progress = pagerun.status(Path(args.pages))
        print(json.dumps({k: v for k, v in progress.items() if k != "pages"},
                         ensure_ascii=False, indent=1))
        return 0

    if args.action == "merge":
        report = pagerun.merge_page(
            Path(args.book), Path(args.pages), args.page,
            glossary_path=Path(args.glossary) if args.glossary else None,
        )
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0 if report["ok"] else 1

    if args.action == "preview":
        work = Path(args.pages).parent
        out = (Path(args.out) if args.out
               else page_preview.preview_path(work, args.page))
        made = page_preview.build(
            Path(args.book), args.page, out,
            assets=Path(args.assets) if args.assets else None)
        print(json.dumps(made, ensure_ascii=False, indent=1))
        return 0 if made["ok"] else 2

    if args.action == "review":
        try:
            answers = dict(page_review.parse_answer(item) for item in args.answer)
        except ValueError as wrong:
            print(json.dumps({"ok": False, "refused": "bad-answer",
                              "detail": str(wrong),
                              "questions": page_review.QUESTIONS},
                             ensure_ascii=False, indent=1))
            return 2
        # Refused here as well as at `accept`, because the reviewer is told to
        # open a source PNG: filing answers about a page whose source was never
        # rendered records a comparison that nobody could have made.
        blocked = pagerun.missing_source_render(Path(args.pages), args.page)
        if blocked:
            print(json.dumps(blocked, ensure_ascii=False, indent=1))
            return 2
        filed = page_review.record(Path(args.pages).parent, args.page, answers,
                                   note=args.note)
        print(json.dumps(filed, ensure_ascii=False, indent=1))
        return 0 if filed["ok"] else 2

    if args.action == "accept":
        outcome = pagerun.accept(Path(args.book), Path(args.pages), args.page)
        print(json.dumps(outcome, ensure_ascii=False, indent=1))
        return 0 if outcome["ok"] else 2

    upcoming = pagerun.next_page(Path(args.pages))
    print(json.dumps(upcoming or {"next": None, "detail": "every page accepted"},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

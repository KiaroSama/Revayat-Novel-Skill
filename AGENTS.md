# AGENTS.md

Repository guide for coding agents working **on** this project. If you want to
*use* the skill to translate a book, read `skills/revayat-novel/SKILL.md` instead.

## What this repository is

An agent skill that translates a whole book into Persian and builds a
professional Word document. It ships as three things from one tree:

- a **skill** — `skills/revayat-novel/`, self-contained, copyable into any agent's
  skill directory
- a **plugin** — `.claude-plugin/`, `.cursor-plugin/`, `.codex-plugin/` plus
  root `commands/`
- a **marketplace** — `.claude-plugin/marketplace.json`, so the repo installs
  itself

## Layout

```
skills/revayat-novel/
  SKILL.md          the skill; `name: revayat-novel` is the activation key
  scripts/*.py      the pipeline (see below)
  references/*.md   loaded on demand, not up front
  requirements.txt  the single dependency manifest
commands/           slash commands for plugin hosts
install/            install.ps1, install.sh — copy the skill into agents
tests/              pytest; fixtures are generated, never committed
```

## The pipeline

| Module | Role |
| --- | --- |
| `bookir.py` | Book IR schema, inline markup, atomic UTF-8 IO |
| `read_pdf.py` | PDF via PyMuPDF: text, geometry, original image bytes |
| `read_epub.py` | EPUB via zipfile + BeautifulSoup: footnotes and link targets |
| `read_docx.py` | DOCX via python-docx, plus raw XML for what it cannot reach: footnotes *and* endnotes, hyperlink targets, section breaks, running heads |
| `extract.py` | format detection, OCR routing, MinerU/Markdown adapters |
| `rasters.py` | cropping an illustration out of a scan's own pixels |
| `ocr_sidecar.py` | per-word OCR confidence and boxes |
| `scan_clean.py` | removing a colour watermark from a scan |
| `glossary.py` | name candidates, term tables, drift checking |
| `chunk.py` | worksheets by character budget; owns the `@@` header format |
| `pagerun.py` | the page lifecycle: one job per source page, and the gates a page must clear |
| `pagecli.py` | the `pages` command line; `pagerun.main` forwards here |
| `segments.py` | one unit longer than the whole budget, cut reversibly |
| `merge.py` | worksheets back into the IR, with named failures |
| `falint.py` | Persian typography lint and fix |
| `qa.py` | deterministic gates over the IR and over the built package |
| `preview.py` | one source page laid out alone, with the production builder |
| `pagecheck.py` | the measurements both render scopes share |
| `renderqa.py` | one source page against its source page |
| `docqa.py` | the finished book: per-page geometry plus global completeness |
| `review.py` | the reviewer's five answers, per page or for the document |
| `wordrender.py` | Word on Windows, LibreOffice elsewhere |
| `runstate.py` | `pending → extracted → … → accepted`, per page |
| `layout.py` | book layout into the styles, not onto each paragraph |
| `ooxml.py` | footnotes, bookmarks, TOC field, bidi, hyperlinks — what python-docx lacks |
| `build_docx.py` | IR to Word |
| `revayat-novel.py` | CLI dispatcher and `doctor` |

## Rules that are load-bearing

1. **Markdown is not the source of truth.** `book.json` is. Anything that
   routes a book through Markdown loses image geometry, footnote identity and
   page setup permanently. Only *inline emphasis* travels as markup.
2. **Never reverse a string to fake RTL, and never pre-shape letters.**
   Direction is `w:bidi` on the paragraph and `w:rtl` on the run.
3. **`parse_markup` and `render_spans` must stay exact inverses.** Emphasis
   parity in QA, run splitting in the builder and the typography pass all
   depend on it. `tests/test_bookir.py` guards this.
4. **The typography pass must never see markup.** It operates on decomposed
   prose spans, so no regex can damage a marker or a footnote token.
5. **Every CLI entry point calls `ir.use_utf8_stdio()` first.** A Windows
   console defaults to a legacy code page and raises on the first Persian
   character.
6. **Write files with `ir.write_text`.** It is atomic and uses `newline=""`, so
   files do not silently become CRLF on Windows.
7. **A worksheet id must round-trip.** If `chunk.py` offers `@@ b00042#alt`,
   `merge.py` must accept it — the `#` in `HEADER` is deliberate, and the
   regression is covered.

## Working on it

```bash
pip install -r skills/revayat-novel/requirements.txt
python -m pytest tests -q
```

Tests generate their own PDF, EPUB and DOCX fixtures. Do not commit book files:
they bloat the repository and the content is usually someone else's.

Keep the three plugin manifests at the same `version` — CI enforces it.

Every tracked text file must be UTF-8; CI enforces that too.

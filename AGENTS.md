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
evaluation/         the bilingual benchmark: cases.json + score.py
tests/              pytest; fixtures are generated, never committed
```

## The pipeline

| Module | Role |
| --- | --- |
| `bookir.py` | Book IR schema, blocks, footnotes, atomic UTF-8 IO |
| `markup.py` | the inline language — `**bold**`, `` `verbatim` ``, `[[fn:…]]` — and the one parsed traversal that tells a real marker from an example of one |
| `read_pdf.py` | PDF via PyMuPDF: text, geometry, original image bytes |
| `read_epub.py` | EPUB via zipfile + BeautifulSoup: footnotes and link targets |
| `read_docx.py` | DOCX via python-docx, plus raw XML for what it cannot reach: footnotes *and* endnotes, hyperlink targets, section breaks, running heads |
| `extract.py` | format detection and OCR routing |
| `rasters.py` | cropping an illustration out of a scan's own pixels |
| `adapters.py` | importing an extraction MinerU or Markdown already did |
| `ocr_sidecar.py` | per-word OCR confidence and boxes |
| `scan_clean.py` | removing a colour watermark from a scan |
| `glossentry.py` | what a glossary *is*: entries, ids, aliases, the canonical form, and the candidates `scan` proposes |
| `glossary.py` | making a book honour them: term tables, first mentions, drift checking |
| `chunk.py` | worksheets by character budget, and superseding an answer whose worksheet was re-cut |
| `worksheet.py` | the transport: the `@@` grammar, the escape, the reader, and **one** verdict on a reply that merge and status both ask |
| `pagerun.py` | the page lifecycle: one job per source page, and the gates a page must clear |
| `pageprogress.py` | the read side of a run — where every page stands, which is next, and whether a page reported finished still matches the book |
| `pageidentity.py` | what a page *is*: which blocks it owns, its geometry, its OCR state, and the versioned digest that says whether its translation still matches |
| `pagecli.py` | the `pages` command line; `pagerun.main` forwards here |
| `sourcepages.py` | the source PDF as an artefact: a page's visual identity, one file per page |
| `segments.py` | one unit longer than the whole budget, cut reversibly; and grouping units into worksheets that fit, for both routes |
| `merge.py` | worksheets back into the IR, as one transaction, with named failures |
| `meaning.py` | the translation read against its source: bilingual sheets, a verdict bound to the revision, and bounded repair |
| `fluency.py` | the Persian read *without* its source: blind sheets, proposed edits, and a verdict that cannot pass until `meaning` has been re-run over the result |
| `falint.py` | Persian typography lint and fix |
| `famorph.py` | whether a Persian space may become a ZWNJ: verb-form and comparative evidence |
| `findings.py` | a finding and the report that collects them — shared by every gate |
| `qa.py` | deterministic gates over the IR |
| `package.py` | the finished `.docx` checked as a package: placement bytes, required parts, relationships |
| `preview.py` | one source page laid out alone, with the production builder |
| `pagecheck.py` | what a rendered page has to satisfy — the one set of rules `render-qa` and `doc-qa` must not disagree about |
| `pagepdf.py` | a rendered PDF read back as measurements: page views, fonts actually used, PNGs. The only part that needs PyMuPDF |
| `pagedocx.py` | the two questions a render cannot answer — is the Persian there, is the paragraph RTL — asked of the .docx's own XML |
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
7. **A worksheet id must round-trip.** If a worksheet offers `@@ b00042#alt`,
   merge must accept it — the `#` in `HEADER` is deliberate, and the regression is
   covered. The grammar lives in `worksheet.py` precisely so there is one of it:
   `chunk` owned the header and the escape while `merge` owned the fence, the
   reader and the validator, and every disagreement between the two sides came
   from that split — a reply status called finished and merge refused, an escape
   written one way and stripped another.
8. **A merge that reports failure changes nothing.** `merge` validates the whole
   selected transaction and commits once; `"ok": false` means `book.json` is
   byte-identical. `--lenient` skips the worksheets that do not validate, it does
   not write half of one.
9. **One formula per named value, or tag it.** Three bugs here came from two
   modules computing the same key differently — the footnote id rule, the
   manifest's `source_sha256` (chunk route vs page route), and a page record's
   `translation` hash (worksheet answers vs laid-out text). Share the function
   where you can; where a cycle prevents it, prefix the value with the formula
   that produced it (`units:`, `page:`) and check only the form you can
   recompute. See `.ai/LESSON.md`.

## Working on it

```bash
pip install -r skills/revayat-novel/requirements.txt

# Working locally: skip the two tiers that cost minutes rather than seconds.
python -m pytest tests -q -m "not render and not ocr"

# Everything, as CI runs it. Worth doing once before a push if you changed the
# render or OCR path; otherwise let the runners do it.
python -m pytest tests -q
```

The `render` and `ocr` markers exist so the expensive work happens where it is
free. `render` lays real documents out through Word or LibreOffice and `ocr` runs
OCRmyPDF, Tesseract and Ghostscript; together they are most of the suite's wall
time and very little of its coverage of the decisions. **No CI job passes `-m`**,
so a marked test still runs on every push — `tests/test_ci_tiers.py` fails if a
workflow ever deselects one, because a tier that runs nowhere has been deleted
with extra steps. The one tier CI cannot run is the Word COM backend: no hosted
runner has an Office licence, so that path is exercised only by a local full run
on a machine with Word, and `doctor` reports which backend a machine has.

`pytest.ini` carries the per-test ceiling (300s, thread method) so a bare
`pytest` is bounded too — the flag used to live only in CI. The two workflows
that override it, `integration.yml` and `word-render.yml`, do so because real
OCR and a real Word render are genuinely slower, and each says so.

Tests generate their own PDF, EPUB and DOCX fixtures. Do not commit book files:
they bloat the repository and the content is usually someone else's.

The shared fixtures in `tests/conftest.py` are session-scoped, and pytest caches a
fixture's **exception** as well as its value — one transient failure is re-raised
for every later request without retrying, so it becomes a setup error on every test
that asked for that fixture, reported in modules with nothing wrong with them. Wrap
a new shared fixture's expensive step in `tests_support.building("name", path)` so
the failure names the fixture rather than its victims. Keep `pytest.importorskip`
outside the wrapper: `Skipped` is not an `Exception`, so an optional dependency
still skips instead of erroring.

`evaluation/` is the bilingual benchmark: eight short English passages written
for this repository, several acceptable Persian renderings each, and one recorded
*wrong* rendering per case — the fluent, right-length, right-marker kind that
structural QA cannot see. `score.py` grades per axis and returns `pass`, `fail` or
**`unknown`**, and it produces **no overall number**, deliberately: an average over
these axes is not a fact about a translation, and a single number is what invites
tuning against the benchmark instead of the book. `unknown` is the honest verdict
for a rendering the file has not seen, and it does not fail a build. Its limit is
stated in the file: the English was written for it, so it measures translation,
not extraction from a real scan.

Keep the three plugin manifests at the same `version` — CI enforces it.

Every tracked text file must be UTF-8; CI enforces that too.

CI also runs `ruff check skills/revayat-novel/scripts tests` on ruff's default
rules, so an unused import fails the build. It is **pinned** (`ruff.toml` sets
only `target-version`; the CI step pins the version) because ruff's default set
widens between releases. If you split a module, put every deliberate re-export
in one block with `# noqa: F401` — the linter cannot tell a re-export from a
dead import, and a name a test imports is public whatever it looks like inside
the file.

CI installs against `constraints-ci.txt` — exact versions, verified green as a
set. `skills/revayat-novel/requirements.txt` keeps its **minimum** versions
untouched, because a reader copies this skill into their own agent directory and
runs it against whatever they already have. The two files answer different
questions: one is the range the skill supports, the other is the set the build
was checked against. Moving a pin is a commit, and Dependabot opens a PR per
update.

`dependency-audit.yml` runs `pip-audit` weekly against the floors in
`requirements.txt` and against what they resolve to that day — the question
`dependency-review.yml` cannot answer, because it only sees dependencies a pull
request changed. It is scheduled and dispatchable, not on the push path: a newly
published advisory should not fail an unrelated commit.

The two optional wheels are declared too, in
`skills/revayat-novel/requirements-optional.txt`: `ocrmypdf`, which forks
Tesseract and Ghostscript over a PDF from an untrusted source, and `pywin32`,
which drives Word through COM. Neither is installed by `requirements.txt` and
nothing imports them at module level, but undeclared is not the same as optional
— while they lived only in prose they were the two the advisory scan never
looked at. They carry **no version floor** on purpose: nothing here has ever run
an old one, and a number would read as tested. Tesseract and Ghostscript
themselves stay outside any pip audit; they are binaries, and `doctor` locates
them.

`supported-range.yml` answers the one question `ci.yml` cannot: **does the oldest
declared set actually work?** Every CI job installs `-c constraints-ci.txt`, so
the floors were a claim with no lane behind them — and the first run found two of
them false. `pymupdf>=1.24` named three releases that cannot import this project
at all, because PyMuPDF published the module name `pymupdf` in 1.24.3 and
everything before it is `fitz` only (measured: 1.24.0, 1.24.1 and 1.24.2 raise
`ModuleNotFoundError`). And `review.compare` drew `— absent` through Pillow's
default bitmap font, which encodes latin-1 only, so on the declared pillow floor
every compare sheet failed to a broad `except` — a reviewing step lost to a dash.
One was fixed by raising the floor to the version that exists, the other by using
ASCII in a string that is drawn rather than printed. That workflow pins each floor to the lowest release that exists — which
is not the floor string plus `.0`; there is no `pymupdf` 1.24 and no
`pytest-timeout` 2.3.0 — and runs the suite on Python 3.10, the only interpreter
where the question can be asked (CPython 3.13 cannot install `pymupdf==1.24.0`
at all). The pins live in a heredoc inside that workflow rather than a tracked
file, because Dependabot's pip fetcher collects every `.txt` whose lines parse as
requirements, from the configured directory *and* each immediate subdirectory of
it, so a tracked floors file would join the `ci-constraints` group and get
proposed for raising — the one change it exists to prevent.
`tests/test_dependency_lanes.py` holds all of that in place: every manifest in a
lane that reaches it, every pin in its floor's own release series.

A skip is structured evidence, not a number in a summary line.
`tests/skip_budget.py` reads the run's `--junitxml` document and requires every
skip to name a cause this project already knows about — the bare matrix skips the
render and OCR tiers by design, and a *new* kind of skip used to look exactly
like one more of those. `full-coverage` uses the same script with
`--profile full`, where any skip at all fails; it replaced a grep for
`[0-9]+ skipped` in console output, which a run that collected nothing passed.

And the flag table in `references/docx-and-ooxml.md` must state the defaults the
parser actually has: a test reads the table and compares every literal default
against `build_docx.add_arguments`. Change a default in one place and the build
says so. Documentation is otherwise the only artefact here that no test reads.

**The Word render path runs on no hosted runner**, because none has an Office
licence — so `_with_word`, its COM teardown and the Windows process-tree kill
are covered only by a local `pytest tests` on a machine with Word and pywin32
(`doctor` reports which backend that is under `optional_tools.render`). To get
it into CI, register a self-hosted Windows runner on such a machine with the
label `word` and dispatch `.github/workflows/word-render.yml`; that workflow is
dispatch-only because a job wanting a label nobody carries queues forever.
Everything else — LibreOffice on all three platforms, the OCR tier — runs in
`ci.yml` and `integration.yml`.

10. **An answer is bound to the request it answers.** `out_chunk0002.md` is a
    reusable name: rebuild and an answer to the previous cut sits where the new
    one expects it, with the same ids, the same count and the same parent block
    text. So a worksheet states
    `<!-- revayat-novel: request req1:… -->`, the reply echoes it, and merge
    compares the echo with the live request. The token covers the ordered headers
    and kinds, the exact segment boundaries, the neighbouring context and the term
    table — but not our own scaffolding comments, because one of them counts the
    jobs and a reply that is still valid must stay usable. Separately,
    `source_sha256` is the `units2:` digest over the units **as cut**,
    recomputable from the `unit_spans` the manifest records; the old `units:` form
    hashed the parent blocks, which is one value for every segment of a paragraph,
    so a recut at a different budget compared a digest against itself and merged
    one generation's answers into another's cut. A reply carrying no token is
    refused by name, with `merge --revalidate-unbound` as the explicit migration.

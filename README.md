# Revayat — روایت

**Translate a whole book into publication-quality Persian, and get a Word file a publisher could work from.**

An agent skill for Claude Code, Kiro, Codex, Cursor, Cline and any other coding
agent that can read a `SKILL.md`. It handles the parts that make book translation
actually hard: scanned pages, illustrations that must keep their size and place,
names that must not drift across forty chapters, and Persian typography that has
to be right rather than approximately right.

[فارسی](README.fa.md) · [MIT licensed](LICENSE)

---

## What it does that a generic translator does not

| | |
| --- | --- |
| **Scanned, digital and mixed books** | Every page is probed for a text layer. Mixed books — the common case for older titles — get `--skip-text` OCR so pages that already read correctly are never re-recognised. |
| **Illustrations survive intact** | Image bytes are extracted, never re-rendered, and placed in Word at their original physical size and aspect. A SHA-256 recorded at extraction proves the picture in the document is the picture from the book. |
| **Real Word footnotes** | A genuine `word/footnotes.xml` part, so Word paginates each note to the foot of the page its marker lands on. Not superscript numbers and a list at the end. |
| **A clickable table of contents** | Real bookmarks and a `TOC` field whose cached result is already a working hyperlink list — so it works whether or not the reader's viewer updates fields. |
| **Persian typography, not just Persian words** | `ی`/`ک`, `، ؛ ؟`, `«»`, Persian digits, and zero-width non-joiners for `می‌رود` and `کتاب‌ها` — with URLs, identifiers and Latin words protected from every rule. |
| **Right-to-left done properly** | `w:bidi` on paragraphs, `w:rtl` on Persian runs, and Latin names left-to-right inside them. Nothing is ever reversed to fake direction. |
| **Names that hold across the book** | A locked glossary is injected into every chunk, so chapter 12 cannot rename a character chapter 3 introduced — and nicknames stay nicknames. |
| **Deterministic quality gates** | Missing paragraphs, dropped footnote markers, omissions caught by length ratio, altered images, dead TOC links. Counts and hashes, not a second opinion from a model. |

## Install

```bash
git clone https://github.com/KiaroSama/Revayat-Skill.git
cd Revayat-Skill
pip install -r skills/revayat/requirements.txt
```

Then install the skill into whichever agents you use:

```bash
# macOS / Linux
./install/install.sh

# Windows
powershell -ExecutionPolicy Bypass -File .\install\install.ps1
```

By default this installs into every agent it finds (`~/.claude`, `~/.kiro`,
`~/.codex`, `~/.cursor`, `~/.cline`). Use `--agent claude` for one, and
`--scope project --path <dir>` to install into a single project instead.

### As a Claude Code plugin

```
/plugin marketplace add KiaroSama/Revayat-Skill
/plugin install revayat@KiaroSama/Revayat-Skill
```

That also gives you `/translate-book`, `/revayat-resume` and `/revayat-qa`.

### Check the install

```bash
python skills/revayat/scripts/revayat.py doctor
```

Optional, and only for scanned or mixed PDFs:

```bash
pip install ocrmypdf          # plus Tesseract and Ghostscript
winget install tesseract-ocr.tesseract ArtifexSoftware.GhostScript   # Windows
```

## Use

Ask your agent, in whatever words you like:

> Translate `book.pdf` into Persian and give me a Word file.

Or with the plugin: `/translate-book ./book.pdf`

The agent runs the pipeline, translating chunks in parallel sub-agents and
stopping at the points where your judgement matters — chiefly the glossary,
where you decide what each character is called in Persian.

### Or drive it yourself

```bash
S=skills/revayat/scripts

python $S/revayat.py extract book.pdf --out work/
python $S/revayat.py glossary scan --book work/book.json --out work/glossary.json
#   … fill in the Persian names in work/glossary.json …
python $S/revayat.py chunk build --book work/book.json --out work/chunks --glossary work/glossary.json
#   … translate work/chunks/chunkNNNN.md -> out_chunkNNNN.md …
python $S/revayat.py merge  --book work/book.json --chunks work/chunks
python $S/revayat.py falint fix --book work/book.json
python $S/revayat.py qa     check --book work/book.json --assets work/assets --glossary work/glossary.json
python $S/revayat.py build  --book work/book.json --out out/book.fa.docx --font "Vazirmatn"
python $S/revayat.py qa     docx --file out/book.fa.docx --book work/book.json
```

## How it works

```
book.pdf / .epub / .docx
        │
        ▼  probe every page: digital · scanned · mixed
   OCRmyPDF ──────── only the pages that need it, images untouched
        │
        ▼
   Book IR  ── blocks, runs, image bytes + geometry, footnotes, page setup
        │      (book.json — the source of truth; Markdown deliberately is not)
        ├──────────────▶ glossary.json ── locked names, aliases, character voices
        ▼
   chapter-aware worksheets ── term table + neighbouring context per chunk
        │
        ▼  translated in parallel, one fresh context each
   merge ── every @@ id must return exactly once, or it is a named error
        │
        ▼
   Persian typography ── ZWNJ, punctuation, digits; protected regions untouched
        │
        ▼
   quality gates ── coverage, footnote parity, omissions, image hashes, glossary
        │
        ▼
   build ── python-docx + raw OOXML for footnotes, bookmarks, TOC, bidi
        │
        ▼
   book.fa.docx  +  package-level verification
```

**The Book IR is the design decision everything else follows from.** Routing a
book through Markdown loses image geometry, footnote identity and page setup,
and then no amount of care downstream can get them back. Only inline emphasis
travels as markup — because models handle `*italic*` far more reliably than a
bespoke XML dialect, and because QA can verify it by counting.

## What it is honest about

Word reflows. A Persian paragraph is rarely the same length as its English
original, so an **editable** document cannot also be page-for-page identical to
the source PDF. Any tool promising both is producing uneditable text boxes.

What *is* exact: image bytes, physical size and aspect; where each picture sits
in the text; heading hierarchy and chapter breaks; bold and italic; footnote
placement and numbering; chapter links; and selectable, searchable, editable
Persian.

Two more limits worth knowing up front:

- PDF heading detection is a font-size heuristic. On an unusual design it
  misses; fix the levels in `book.json` before chunking, or use MinerU.
- In a scanned book an illustration is usually part of the page raster rather
  than a separate image object, so there is nothing to extract. MinerU's layout
  model finds those; `--from-mineru` imports its output.

## Documentation

The skill loads these on demand rather than up front:

- [`translation-policy.md`](skills/revayat/references/translation-policy.md) — what a faithful literary translation requires
- [`persian-typography.md`](skills/revayat/references/persian-typography.md) — RTL, ZWNJ, punctuation, mixed scripts
- [`extraction.md`](skills/revayat/references/extraction.md) — OCR routing, difficult books, the IR schema
- [`glossary-and-voice.md`](skills/revayat/references/glossary-and-voice.md) — naming policy, aliases, character voice
- [`docx-and-ooxml.md`](skills/revayat/references/docx-and-ooxml.md) — every build option and what Word structure it produces
- [`troubleshooting.md`](skills/revayat/references/troubleshooting.md) — the failures you are most likely to hit

## Development

```bash
pip install -r skills/revayat/requirements.txt
python -m pytest tests -q
```

Fixtures are generated, not committed: the suite builds its own PDF, EPUB and
DOCX, so it stays fast and no third-party book text is vendored in.

## Credits

The orchestration shape — chunking a book, translating chunks in parallel
sub-agents with a shared glossary, and resuming a partial run — follows the
approach demonstrated by [deusyu/translate-book](https://github.com/deusyu/translate-book) (MIT).
Extraction and OCR routing build on [PyMuPDF](https://github.com/pymupdf/PyMuPDF),
[OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) and, optionally,
[MinerU](https://github.com/opendatalab/MinerU). The intermediate-representation
approach to preserving layout through translation is the idea behind
[BabelDOC](https://github.com/funstory-ai/BabelDOC).

## Donate

If this project helps you, donations are appreciated.

| Currency | Network | Address |
| --- | --- | --- |
| Bitcoin (BTC) | Bitcoin | `bc1qmth5m03pu5hujw5xw5jmywam3jj3sqwqupesdt` |
| USDT, BNB, USDC, etc. | BEP20 | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |
| USDT, TRX, USDC, etc. | TRC20 | `TWBA3xFTqgZAeAYMxqo85xWnzvty3DcAhw` |
| Ethereum (ETH) | ERC20 | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |
| TON | TON | `UQCN8Umo_OfOWqImZetQsrNStPcmLkMAKajFyiCOhso23NDb` |
| Litecoin (LTC) | LTC | `ltc1qntqnnrunadurnw4cshv3qgspywrueyyeyngwuy` |
| Solana (SOL) | Solana | `7B2wkczUjmkDhETwQuknBL8sUsbuV7nErxc317TmQuwR` |
| Polygon (POL) | Polygon | `0x0Bd0BA443a8B9cf15922bf7f0Bb0a4b495fD06Ef` |

## Author

Author: Kiaro Sama
GitHub: https://github.com/KiaroSama

## License

[MIT](LICENSE)

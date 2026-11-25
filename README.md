<h1 align="center">Revayat Novel · روایت</h1>

<div align="center">

[![CI](https://img.shields.io/github/actions/workflow/status/KiaroSama/Revayat-Novel-Skill/ci.yml?style=flat-square&color=16a34a&label=CI)](.github/workflows/ci.yml)
[![CodeQL](https://img.shields.io/github/actions/workflow/status/KiaroSama/Revayat-Novel-Skill/codeql.yml?style=flat-square&color=0d9488&label=CodeQL)](.github/workflows/codeql.yml)
[![Gitleaks](https://img.shields.io/github/actions/workflow/status/KiaroSama/Revayat-Novel-Skill/gitleaks.yml?style=flat-square&color=2563eb&label=Gitleaks)](.github/workflows/gitleaks.yml)
[![License](https://img.shields.io/github/license/KiaroSama/Revayat-Novel-Skill?style=flat-square&color=4f46e5)](LICENSE)
[![Latest Git tag](https://img.shields.io/github/v/tag/KiaroSama/Revayat-Novel-Skill?style=flat-square&color=7c3aed&label=Version)](#releases-and-tags)
[![Rust 1.100.0](https://img.shields.io/badge/Rust-1.100.0-8b5cf6)](#installation)
[![64-bit Windows and Linux](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%2864--bit%29-0891b2)](#installation)
[![Five plugin formats](https://img.shields.io/badge/Plugins-5%20formats-0284c7)](#plugin-formats)
[![Bundled Vazirmatn v33.003](https://img.shields.io/badge/Font-Vazirmatn%20v33.003-059669)](#vazirmatn-font)
[![Six-stage multi-agent workflow](https://img.shields.io/badge/Workflow-6--stage%20multi--agent-d97706)](#six-stage-workflow)
[![Repository size](https://img.shields.io/github/repo-size/KiaroSama/Revayat-Novel-Skill?style=flat-square&color=ca8a04)](#repository-layout)
[![Pure Rust](https://img.shields.io/badge/Built%20with-Pure%20Rust-ea580c)](#architecture)
[![Four bundled skills](https://img.shields.io/badge/Skills-4%20bundled-65a30d)](skills/README.md)
[![Support donations](https://img.shields.io/badge/Support-donations-d04a9a)](#donate)

</div>

**Translate a whole book into Persian and get an editable Word file a publisher can work from.**

Revayat Novel is an agent skill for PDF, EPUB, DOCX, plain text, and web novels. It
handles scanned pages, keeps illustrations at their original size where the source
specifies it, and gives the translator a shared glossary so names stay consistent
across chapters. Start a web novel from a URL or an ordered set of saved HTML or
text chapters. The [web intake guide](skills/revayat-novel/references/web-novels.md)
explains how to retain snapshots and resume a run.

<div align="right"><a href="README.fa.md">فارسی</a></div>

## Installation

Build or install the native `revayat-novel` binary for 64-bit Windows or Linux
(x64 or arm64). Install the binary separately from the plugin. If a signed
package for your target is
available on [Releases](https://github.com/KiaroSama/Revayat-Novel-Skill/releases),
use that package and follow its notes. To build from this checkout, use Rust
`1.100.0` through [rustup](https://rustup.rs/). Use a native linker on Linux
and Visual Studio Build Tools with the C++ workload and Windows SDK on Windows.
Install a C/C++ toolchain and CMake if needed to build the locked TLS dependencies.

Linux, from the repository root:

```bash
cargo build --release --locked --bin revayat-novel
install -Dm755 target/release/revayat-novel bin/revayat-novel
./bin/revayat-novel doctor --json
./bin/revayat-novel install --agent claude --scope project --path "/path/to/project"
```

Windows, from the repository root in PowerShell 7:

```powershell
cargo build --release --locked --bin revayat-novel
New-Item -ItemType Directory -Force bin | Out-Null
Copy-Item target/release/revayat-novel.exe bin
.\bin\revayat-novel.exe doctor --json
.\bin\revayat-novel.exe install --agent claude --scope project --path 'C:\work\my project'
```

Use an existing project directory. The installer supports `claude`, `kiro`,
`codex`, `cursor`, `cline`, `hermes`, `opencode`, and `antigravity`.
`--agent all` only installs into agent homes that already exist. Use
`--scope user` for your user profile and `--status` with the same selection to
check the installation. The installer stages the book skill, fonts and binary,
and keeps backups when replacing an installation with `--force`. Add the
appropriate `bin/` directory to `PATH` if your agent cannot find `revayat-novel`.

`doctor --json` reports the native build target, fonts, manifests, and optional
tools it can find. Install Tesseract with English (`eng`) and Persian (`fas`)
language packs for OCR as needed. LibreOffice (`soffice`) or Microsoft Word on
Windows is needed to render Word documents for visual review. Their absence
limits what you can verify; it does not prevent the core CLI from running.

## Plugin formats

| Host or format | Metadata |
| --- | --- |
| Claude Code | [Plugin](.claude-plugin/plugin.json), [marketplace](.claude-plugin/marketplace.json) |
| Codex | [Plugin](.codex-plugin/plugin.json), [marketplace](.agents/plugins/marketplace.json) |
| Cursor | [Plugin](.cursor-plugin/plugin.json), [marketplace](.cursor-plugin/marketplace.json) |
| Antigravity | [Plugin](.antigravity-plugin/plugin.json) |
| Agent Plugins | [Standard manifest](plugin.json) |

In Claude Code, add the marketplace and install the plugin:

```text
/plugin marketplace add KiaroSama/Revayat-Novel-Skill
/plugin install revayat-novel@revayat-novel-skill
```

Use `/revayat-novel:translate-book` to start, `/revayat-novel:revayat-novel-resume`
to resume, and `/revayat-novel:revayat-novel-qa` to check a candidate. The bundle
also includes [`persian`, `great-russian`, and `humanizer` v4.0.0](skills/README.md).
Pick the editorial skill by the language you are writing; book output is Persian.

## Vazirmatn font

The [Vazirmatn v33.003 bundle](skills/revayat-novel/assets/fonts/vazirmatn/) contains
Regular, Bold, Medium and Light under the [SIL Open Font License](skills/revayat-novel/assets/fonts/vazirmatn/OFL.txt).
The Word builder uses Vazirmatn for Persian text and includes the font in the
package. Use render QA to check what the actual viewer drew. Document XML alone
cannot prove the viewer used the embedded font or kept the intended layout.

## Six-stage workflow

Give the agent the source and an output path, for example: “Translate
`book.epub` into Persian and deliver an editable Word document at `edition.docx`.”
Use a separate work directory per book. The
[full workflow](skills/revayat-novel/references/multi-agent-workflow.md) gives
stage inputs, review roles, and sign-off criteria.

1. **Initial translation:** Inspect the edition, then extract or import it. Write
   natural Persian that preserves the narrator's register and each speaker's
   voice. Look up names, titles, and specialist terms using live publisher,
   author, or field-specific sources. Record spelling decisions and source URLs
   in the glossary; keep unpublished manuscript text out of public queries.
2. **Typesetting and layout:** Merge accepted worksheets and build an RTL DOCX.
   Keep source dimensions where defined, original image bytes and aspect ratios,
   real Word footnotes and a clickable table of contents.
3. **Independent review:** Check source meaning and Persian fluency separately.
   Send all findings to the coordinator before making edits. If a second reviewer
   is unavailable, disclose that the review was a separate pass by the same agent.
4. **Apply edits:** Merge corrected replies, keep locked terminology and inline
   emphasis, and rebuild the candidate. Recheck approvals for the changed revision.
5. **Re-check the file:** Run book, document, and render checks on the same
   candidate. Collect the results, fix issues together, and rerun affected checks.
6. **Final review and delivery:** Deliver only against the completed sign-off for
   the current revision. Include the Word file and the workflow log.

Parallel work is optional and requires your approval. `batch plan-shards` splits
one book into independent assignments and `batch merge-shards` reunites replies.
`batch plan-books` makes a bulk plan with separate work directories and outputs.
Run all six stages per book. One coordinator owns the glossary, shared book,
merges, and log, even when several agents translate or review.

In mixed-direction Persian text, `RLI` (`U+2067`) and `PDI` (`U+2069`) isolate a
Persian line that starts with English. Prefix that line with `RLM` (`U+200F`) in
plain text or automatic direction. Word uses paragraph RTL, logical start
alignment and complex-script fonts; reversing strings would corrupt the text.

## CLI examples

With `revayat-novel` on `PATH`, these are individual format and QA steps. They
assume that the agent has completed the translation and review steps between
extraction and the final build. Use `<command> --help` for supported options.

```text
revayat-novel extract "book.epub" --out "work/book"
revayat-novel web-import "https://example.org/novel/" --out "work/web-book"
revayat-novel build "work/book" --out "output/edition.docx"
revayat-novel doc-qa check --help
revayat-novel persian-lint --check --style "excerpt.txt"
revayat-novel eval-score --answers "evaluation-answers.json"
```

## Logs and limits

The coordinator writes a UTF-8 `translation_YYYY-MM-DD_HH-mm-ss_UTC.log` beside
the Word file. Record intake, changes, refusal reasons, review results, and the
output revision. For per-command logs, pass `--log-dir <work>`; the binary writes
them under `<work>/logs/`. Preserve the workflow log and work directory for
resuming, and redact logs before sharing them. See
[preservation and logging](skills/revayat-novel/references/preservation-and-logging.md).

Ask before lengthy OCR or a book over roughly 200,000 source characters. OCR
coverage does not prove recognition accuracy. Reflowable sources may not define
fixed page geometry. Stop when extraction misses source material or when review
or rendering coverage is unknown. The [evaluation cases](evaluation/cases.json)
and [offline sandbox](tests/sandbox/README.md) are finite checks, not literary
certification or a complete security audit.

## Architecture

The CLI and workspace are written in Rust. `book.json` is the authoritative
intermediate representation. A locked, validated write applies accepted replies
and preserves recovery evidence. The DOCX writer consumes the book and its
assets; the QA tools inspect package structure and bind review evidence to a
specific revision. Optional local programs cover OCR and Word rendering.

## Repository layout

| Path | Contents |
| --- | --- |
| `crates/` | CLI, book contracts, ingestion, Persian text, OOXML, pipeline, installer, and evaluation |
| `bin/` | Staged executable (`revayat-novel.exe` on Windows, `revayat-novel` on Linux) |
| [`skills/`](skills/README.md) | Book workflow, bundled writing skills and font assets |
| [`commands/`](commands/) | Claude Code commands |
| [`evaluation/`](evaluation/) | Recorded translation cases and review protocol |
| [`.github/workflows/`](.github/workflows/) | Rust CI, dependency and secret checks, and offline sandbox |

The original orchestration draws on [deusyu/translate-book](https://github.com/deusyu/translate-book).
The book representation follows the layout-preservation idea in [BabelDOC](https://github.com/funstory-ai/BabelDOC).

## Releases and tags

When we push a new annotated tag such as `v3.0.0`, the live Version badge updates automatically from [Git tags](https://github.com/KiaroSama/Revayat-Novel-Skill/tags). Download packages from [GitHub Releases](https://github.com/KiaroSama/Revayat-Novel-Skill/releases).

The release workflow signs binaries in the protected `revayat-novel-signing` GitHub Actions environment. The [signature verification instructions](docs/signing/README.md) cover Windows Authenticode, Linux Ed25519, `SHA256SUMS.txt` checksums, and the GitHub build provenance attestation.

Windows x64 benchmarks (median of five runs per command):

| Command | v2.0.0 (Python) | v3.0.0 (pure Rust) | Change |
| --- | --- | --- | --- |
| `doctor --json` | 2194.1 ms / 137.0 MB RAM | 42.3 ms / 6.3 MB RAM | 98.1% faster, 95.4% less RAM |
| `persian-lint` (500 lines) | 160.9 ms / 18.1 MB RAM | 59.2 ms / 7.6 MB RAM | 63.2% faster, 57.9% less RAM |
| `chunk build` (sample book) | 255.0 ms / 24.1 MB RAM | 41.1 ms / 7.5 MB RAM | 83.9% faster, 68.8% less RAM |
| `persian-lookup` (200 lines) | 142.5 ms / 14.7 MB RAM | 184.7 ms / 8.1 MB RAM | 29.7% slower, 45.3% less RAM |
| Standalone footprint | 238 Python and shell scripts (2.5 MB), plus the Python runtime and virtual environment | 1 native binary (22.4 MB), 0 Python dependencies | |

## Author

[Kiaro Sama](https://github.com/KiaroSama) · [Kiaro.Sama.Dev@gmail.com](mailto:Kiaro.Sama.Dev@gmail.com)

## License

Copyright Kiaro Sama. Revayat Novel is licensed under [GPL-3.0-or-later](LICENSE).

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

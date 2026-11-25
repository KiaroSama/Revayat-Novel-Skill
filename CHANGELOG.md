# Changelog

## [3.0.0] - 2026-11-25

- Replaced the script-based runtime with one native `revayat-novel` binary for 64-bit Windows and Linux (x64 and arm64), built with Rust 1.100.0. The CLI handles intake, Word output, Persian lint, evaluation, and recoverable installation. OCR and Word rendering still use optional local tools.
- Added Antigravity and Agent Plugins manifests alongside Claude Code, Codex, and Cursor. The plugin bundles Vazirmatn v33.003 and updates the English writing skill to `humanizer` v4.0.0.
- Documented six translation stages, live lookup of names and terminology, optional parallel shards, bulk book plans, and bidirectional isolation for Persian text that starts with English.
- Moved CI and dependency updates to Cargo. Added native installer checks on Linux and Windows and an offline container smoke test.
- The dedicated release workflow (`.github/workflows/release.yml`) covers four 64-bit targets: Windows and Linux on x64 and arm64. The protected `revayat-novel-signing` environment handles Windows Authenticode, Linux Ed25519, and GitHub build provenance. Public verification keys are in [`docs/signing/`](docs/signing/).
- Updated all five plugin manifests to match their live schemas: Agent Plugins 1.0.0, Antigravity v1, Claude, Codex, and Cursor. The metadata includes `author.email` and Codex's `authentication: NONE`.
- `doctor` and OCR commands automatically find the Windows `Tesseract-OCR` installation path.

## 2.0.0

- The plugin includes Persian, English, and Russian writing skills. Revayat Novel still produces Persian books and selects voice guidance by the language of the editable passage.
- Python 3.11 is now the minimum for the installers and book workflow. Upgrade from Python 3.10 before updating the skill. The new minimum lets the project require Pillow 13 and its image-decoder fixes.
- Run the project validation workflows manually. Dedicated security checks still run on their own events.

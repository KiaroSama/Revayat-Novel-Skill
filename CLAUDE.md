# CLAUDE.md

Read [AGENTS.md](AGENTS.md) for repository rules. Read
[CONTEXT.md](CONTEXT.md) for domain terms.

- Translate a book: start at `skills/revayat-novel/SKILL.md` and follow its
  six-stage workflow. Activate by `name: revayat-novel`. In Claude Code,
  `{SKILL_DIR}` resolves to `${CLAUDE_PLUGIN_ROOT}/skills/revayat-novel`.
- Run the Rust `1.100.0` workspace in `crates/` through the staged
  `bin/revayat-novel.exe` on Windows or `bin/revayat-novel` on Linux.
  `revayat-novel doctor --json` reports package and optional tool availability.
- Change plugin metadata or skills: maintain all five plugin formats and one
  physical line per front matter field. See [skills/README.md](skills/README.md)
  for the bundled Vazirmatn v33.003 fonts and editorial skills, including
  `humanizer` v4.0.0.

Development work is tracked locally; see
[the issue-tracker guide](docs/agents/issue-tracker.md).

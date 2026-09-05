# CLAUDE.md

Read `AGENTS.md` — it is the full guide for working on this repository and
applies unchanged here.

Two Claude-specific notes:

- **Using the skill**: it activates by the `name:` in
  `skills/revayat-novel/SKILL.md`, which is `revayat-novel` — not the folder name.
- **Inside a plugin**, `{SKILL_DIR}` in `SKILL.md` resolves to
  `${CLAUDE_PLUGIN_ROOT}/skills/revayat-novel`.

When changing `SKILL.md`, keep every front-matter field on a single line.
Several agents parse it with a line-oriented reader, and CI checks it.

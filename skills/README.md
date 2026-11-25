# Bundled skills

[Revayat Novel](revayat-novel/SKILL.md) translates books into Persian through a
[six-stage workflow](revayat-novel/references/multi-agent-workflow.md). The bundle
includes the book skill and the three editorial skills below. Choose an editorial
skill by the language of the prose you are writing, regardless of the source
language. Claude Code exposes the skills under the `revayat-novel` namespace.

| Language | Skill | Upstream revision | License |
| --- | --- | --- | --- |
| Persian | [persian](persian/SKILL.md) | [Mojtaba-Alehosseini/persian-skill](https://github.com/Mojtaba-Alehosseini/persian-skill/tree/4264f6ef03f7568bc15b14287a3db09bc815fc31) | [MIT](persian/LICENSE), [NOTICE](persian/NOTICE) |
| English | [humanizer](humanizer/SKILL.md) | [blader/humanizer v4.0.0](https://github.com/blader/humanizer/tree/b48c832f0f809c1b5658edbb838c2521b72f2413) | [MIT](humanizer/LICENSE) |
| Russian | [great-russian](great-russian/SKILL.md) | [frolin/great-russian-skill](https://github.com/frolin/great-russian-skill/tree/c9ba8661768927c21b7ba5608e63dce832c568b5) | [MIT](great-russian/LICENSE) |

The Rust `1.100.0` workspace in `crates/` provides `persian-lint` and
`persian-lookup` through `bin/revayat-novel` or `bin/revayat-novel.exe`.
Use `revayat-novel persian-lint --help` or `revayat-novel persian-lookup --help`
for CLI options. The [bundled font](revayat-novel/assets/fonts/vazirmatn/) is
Vazirmatn v33.003; read its [OFL license](revayat-novel/assets/fonts/vazirmatn/OFL.txt)
for redistribution terms. The standalone installer stages the book skill,
its fonts, and the binary.

The [five plugin formats](../README.md#plugin-formats) are Claude Code, Codex,
Cursor, Antigravity, and Agent Plugins. See the
[writing guide](revayat-novel/references/writing-voice.md) for host-specific skill
keys and the fallback for standalone installations.

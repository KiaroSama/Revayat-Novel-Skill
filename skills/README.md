# Bundled writing skills

Claude Code exposes these skills under the `revayat-novel` plugin namespace. Select by the language of the prose you are writing, not by the document's source language. [Revayat Novel's writing guide](revayat-novel/references/writing-voice.md) explains how this works in a book translation.

| Language | Skill | Upstream revision | License |
| --- | --- | --- | --- |
| Persian | [persian](persian/SKILL.md) | [Mojtaba-Alehosseini/persian-skill](https://github.com/Mojtaba-Alehosseini/persian-skill/tree/4264f6ef03f7568bc15b14287a3db09bc815fc31) | [MIT](persian/LICENSE), [NOTICE](persian/NOTICE) |
| English | [humanizer](humanizer/SKILL.md) | [blader/humanizer v4.0.0](https://github.com/blader/humanizer/tree/b48c832f0f809c1b5658edbb838c2521b72f2413) | [MIT](humanizer/LICENSE) |
| Russian | [great-russian](great-russian/SKILL.md) | [frolin/great-russian-skill](https://github.com/frolin/great-russian-skill/tree/c9ba8661768927c21b7ba5608e63dce832c568b5) | [MIT](great-russian/LICENSE) |

The bundle includes the skill instructions, runtime references, and Persian helpers and seed data. It omits upstream installers, CI, plugin manifests and test suites. The bundled copies include local fixes for fidelity, safe file editing and Persian text normalization. The Persian instructions point to the upstream self-test rather than to an omitted local file. The standalone Revayat Novel installer only copies the book skill.

# Choose writing guidance by output language

Choose by the language of the editable prose. A Russian source translated into Persian uses Persian guidance. An English source translated into Persian uses Persian guidance. Keep source-language profiles for interpreting the source. Do not run a voice pass on quotations retained in their original language.

| Editable prose | Claude plugin skill | Use |
| --- | --- | --- |
| Persian | `revayat-novel:persian` | Use its craft guidance when drafting the translation and when reading it for fluency. |
| English | `revayat-novel:humanizer` | Use its embedded editing guidance for requested English prose. |
| Russian | `revayat-novel:great-russian` | Use its Russian editing guidance for requested Russian prose. |

If the host exposes these skills under another name, use the corresponding bundled skill offered by the host. A standalone copy of Revayat Novel does not require them: use [translation-policy.md](translation-policy.md), [glossary-and-voice.md](glossary-and-voice.md), and [persian-typography.md](persian-typography.md) for the Persian book. For a language without a bundled match, follow the user's brief. Revayat Novel publishes books in Persian only.

Apply the skill to prose, not to whole worksheets, review replies, `book.json`, or finished Word files. Preserve meaning, register, protected literals, names, numbers, emphasis and note markers. Keep user-approved voice and the project's review, merge and typography rules. Submit fluency repairs through the existing review workflow and rerun the source-meaning check after substantive edits. Use Revayat Novel's typography check on the book; do not pass its structured files to a general-purpose voice helper.

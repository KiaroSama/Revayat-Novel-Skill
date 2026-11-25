//! Worksheet terminology, drift checks, and first-mention placement.
use crate::falint;
pub use crate::glossentry::{
    AliasTargets, GLOBAL_TOP_N, Glossary, GlossaryEntry, ParentheticalPolicy, Policy, Voice,
    alias_accepted, alias_map, allocate_ids, canonical, count_frequencies, load, lookup,
    make_entry, new_glossary, save, scan, surface_forms,
};
use revayat_core::{BookDocument, InlineSpan, parse_markup, published_units, render_spans};
use serde::Serialize;
use std::{
    collections::{BTreeMap, BTreeSet, HashSet},
    ops::Range,
};
use unicode_normalization::char::is_combining_mark;

pub fn standalone_spans(text: &str, needle: &str) -> Vec<Range<usize>> {
    if needle.is_empty() {
        return Vec::new();
    }
    let joined = |c: char| {
        c.is_alphanumeric() || is_combining_mark(c) || matches!(c, '_' | '\u{200c}' | '\u{200d}')
    };
    text.match_indices(needle)
        .filter_map(|(start, value)| {
            let end = start + value.len();
            (!text[..start].chars().next_back().is_some_and(joined)
                && !text[end..].chars().next().is_some_and(joined))
            .then_some(start..end)
        })
        .collect()
}

pub fn parenthetical_policy(value: &str) -> Result<ParentheticalPolicy, String> {
    match value {
        "first_mention" => Ok(ParentheticalPolicy::FirstMention),
        "first_per_chapter" => Ok(ParentheticalPolicy::FirstPerChapter),
        "never" => Ok(ParentheticalPolicy::Never),
        _ => Err(format!(
            "unknown original_parenthetical policy {value:?}; expected one of first_mention, first_per_chapter, never"
        )),
    }
}

pub fn chapters_by_block(book: &BookDocument) -> BTreeMap<String, String> {
    published_units(book)
        .into_iter()
        .filter_map(|unit| unit.chapter_id.map(|chapter| (unit.id, chapter)))
        .collect()
}

pub fn mentioned_in_prose(target: &str, form: &str) -> Result<bool, fancy_regex::Error> {
    if form.is_empty() {
        return Ok(false);
    }
    for span in parse_markup(target) {
        if !span.verbatim
            && span.footnote.is_none()
            && !standalone_spans(&falint::mask_literals(&span.text)?.0, form).is_empty()
        {
            return Ok(true);
        }
    }
    Ok(false)
}

pub fn prose_of(text: &str) -> Result<String, fancy_regex::Error> {
    let mut prose = String::new();
    for span in parse_markup(text)
        .into_iter()
        .filter(|span| span.footnote.is_none())
    {
        if span.verbatim {
            prose.push_str(&" ".repeat(span.text.chars().count()));
        } else {
            prose.push_str(&falint::blank_protected(&span.text, false)?);
        }
    }
    Ok(prose)
}

pub fn entries_for_text<'a>(
    glossary: &'a Glossary,
    text: &str,
    top_n: usize,
) -> Vec<&'a GlossaryEntry> {
    let mut hit: Vec<_> = glossary
        .entries
        .iter()
        .filter(|entry| surface_forms(entry).any(|form| !standalone_spans(text, form).is_empty()))
        .collect();
    let mut ids: HashSet<_> = hit.iter().map(|entry| entry.id.as_str()).collect();
    let mut frequent: Vec<_> = glossary.entries.iter().collect();
    frequent.sort_by_key(|entry| std::cmp::Reverse(entry.frequency));
    hit.extend(
        frequent
            .into_iter()
            .take(top_n)
            .filter(|entry| !canonical(entry).is_empty() && ids.insert(&entry.id)),
    );
    hit
}

pub fn render_term_table(
    entries: &[&GlossaryEntry],
    policy: &Policy,
    block_ids: &[&str],
) -> String {
    let mut rows: Vec<_> = entries
        .iter()
        .copied()
        .filter(|entry| !canonical(entry).is_empty())
        .collect();
    if rows.is_empty() {
        return String::new();
    }
    rows.sort_by_key(|entry| std::cmp::Reverse(entry.frequency));
    let mut lines = vec![
        "| English | Aliases \u{2014} use exactly these | Use exactly this |".to_owned(),
        "| --- | --- | --- |".to_owned(),
    ];
    for entry in rows {
        let intro = policy.original_parenthetical != ParentheticalPolicy::Never
            && !entry.first_form.is_empty()
            && (block_ids.is_empty() || block_ids.contains(&entry.first_block_id.as_str()));
        let (form, note) = if intro {
            (
                entry.first_form.as_str(),
                "  ← first mention, introduce it here",
            )
        } else {
            (canonical(entry), "")
        };
        let aliases = if entry.aliases.is_empty() {
            "\u{2014}".into()
        } else {
            entry
                .aliases
                .iter()
                .map(|alias| {
                    alias_map(entry)
                        .find(|(name, _)| *name == alias)
                        .map_or_else(
                            || alias.clone(),
                            |(_, target)| format!("{alias} → {target}"),
                        )
                })
                .collect::<Vec<_>>()
                .join("، ")
        };
        lines.push(format!("| {} | {aliases} | {form}{note} |", entry.source));
    }
    lines.join("\n")
}

pub fn voice_entry<'a>(
    glossary: &'a Glossary,
    voice: &Voice,
) -> (Option<&'a GlossaryEntry>, String) {
    if !voice.entry.is_empty() {
        let found = glossary.entries.iter().find(|e| e.id == voice.entry);
        let name = if voice.character.is_empty() {
            "?"
        } else {
            &voice.character
        };
        let problem = if found.is_some() {
            String::new()
        } else {
            format!(
                "voice card for {name:?} names entry {:?}, which this glossary does not have",
                voice.entry
            )
        };
        return (found, problem);
    }
    let name = voice.character.trim();
    if name.is_empty() {
        return (None, "a voice card with no character name".into());
    }
    let found: Vec<_> = glossary
        .entries
        .iter()
        .filter(|entry| entry.source.trim() == name)
        .collect();
    match found.as_slice() {
        [entry] => (Some(entry), String::new()),
        [] => (None, String::new()),
        _ => (
            None,
            format!(
                "voice card for {name:?} matches {} glossary entries; add an `entry` id to say which character it is about",
                found.len()
            ),
        ),
    }
}

pub fn voice_problems(glossary: &Glossary) -> Vec<String> {
    glossary
        .voices
        .iter()
        .map(|voice| voice_entry(glossary, voice).1)
        .filter(|problem| !problem.is_empty())
        .collect()
}

pub fn render_voice_cards(glossary: &Glossary, text: &str) -> Result<String, fancy_regex::Error> {
    let prose = prose_of(text)?;
    let cards: Vec<_> = glossary
        .voices
        .iter()
        .filter_map(|voice| {
            let (entry, _) = voice_entry(glossary, voice);
            let seen = |s| !standalone_spans(&prose, s).is_empty();
            if entry.map_or_else(
                || seen(&voice.character),
                |entry| surface_forms(entry).any(seen),
            ) {
                let register = if voice.register.is_empty() {
                    "?"
                } else {
                    &voice.register
                };
                let detail = if voice.persian_policy.is_empty() {
                    &voice.speech_style
                } else {
                    &voice.persian_policy
                };
                Some(format!(
                    "- **{}** \u{2014} register: {register}; {detail}",
                    voice.character
                ))
            } else {
                None
            }
        })
        .collect();
    Ok(cards.join("\n"))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Obligation<'a> {
    pub source_form: &'a str,
    pub accepted: Vec<&'a str>,
}

pub fn obligations_for<'a>(
    entry: &'a GlossaryEntry,
    source: &str,
    distinct: bool,
) -> Result<Vec<Obligation<'a>>, fancy_regex::Error> {
    let prose = prose_of(source)?;
    let mut forms: Vec<_> = surface_forms(entry).collect();
    forms.sort_by_key(|form| std::cmp::Reverse(form.chars().count()));
    let mut consumed: Vec<Range<usize>> = Vec::new();
    let mut owed = Vec::new();
    for form in forms {
        let mut used = false;
        for span in standalone_spans(&prose, form) {
            if !consumed
                .iter()
                .any(|other| span.start < other.end && other.start < span.end)
            {
                used = true;
                consumed.push(span);
            }
        }
        if !used
            || owed
                .iter()
                .any(|item: &Obligation<'_>| item.source_form == form)
        {
            continue;
        }
        let name = canonical(entry);
        let alias = alias_map(entry).find_map(|(key, target)| (key == form).then_some(target));
        let mut accepted = if form == entry.source {
            let mut accepted = vec![name];
            if !distinct {
                accepted.extend(alias_accepted(entry));
            }
            accepted
        } else if let Some(alias) = alias {
            let mut accepted = vec![alias];
            if !distinct {
                accepted.push(name);
            }
            accepted
        } else {
            let mut accepted = vec![name];
            accepted.extend(alias_accepted(entry));
            accepted
        };
        let mut seen = HashSet::new();
        accepted.retain(|form| !form.is_empty() && seen.insert(*form));
        owed.push(Obligation {
            source_form: form,
            accepted,
        });
    }
    Ok(owed)
}

pub fn owed_forms<'a>(
    entry: &'a GlossaryEntry,
    source: &str,
    distinct: bool,
) -> Result<(Vec<&'a str>, Vec<&'a str>), fancy_regex::Error> {
    let owed = obligations_for(entry, source, distinct)?;
    Ok((
        owed.iter().map(|item| item.source_form).collect(),
        owed.into_iter().flat_map(|item| item.accepted).collect(),
    ))
}

#[derive(Debug, Clone, Copy)]
pub struct Translation<'a> {
    pub id: &'a str,
    pub source: &'a str,
    pub target: &'a str,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Violation {
    pub block: String,
    pub entry: String,
    pub source_forms: Vec<String>,
    pub expected: String,
    pub accepted: Vec<String>,
    pub used: Vec<String>,
    pub excerpt: String,
}

pub fn check<'a>(
    glossary: &Glossary,
    translations: impl IntoIterator<Item = Translation<'a>>,
) -> Result<Vec<Violation>, fancy_regex::Error> {
    let locked: Vec<_> = glossary
        .entries
        .iter()
        .filter(|entry| entry.locked && !canonical(entry).is_empty())
        .collect();
    let mut violations = Vec::new();
    if locked.is_empty() {
        return Ok(violations);
    }
    for block in translations
        .into_iter()
        .filter(|block| !block.target.trim().is_empty())
    {
        let target = prose_of(block.target.trim())?;
        for entry in &locked {
            let owed = obligations_for(entry, block.source, glossary.policy.keep_aliases_distinct)?;
            let unmet: Vec<_> = owed
                .iter()
                .filter(|item| {
                    !item
                        .accepted
                        .iter()
                        .any(|form| !standalone_spans(&target, form).is_empty())
                })
                .collect();
            if let Some(first) = unmet.first() {
                violations.push(Violation {
                    block: block.id.into(),
                    entry: entry.id.clone(),
                    expected: first
                        .accepted
                        .first()
                        .copied()
                        .unwrap_or_else(|| canonical(entry))
                        .into(),
                    source_forms: unmet.iter().map(|item| item.source_form.into()).collect(),
                    accepted: unmet
                        .iter()
                        .flat_map(|item| item.accepted.iter().map(|form| (*form).to_owned()))
                        .collect::<BTreeSet<_>>()
                        .into_iter()
                        .collect(),
                    used: owed.iter().map(|item| item.source_form.into()).collect(),
                    excerpt: target.chars().take(120).collect(),
                });
            }
        }
    }
    Ok(violations)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct MentionBlock {
    pub id: String,
    pub chapter: String,
    pub target: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct MentionReport {
    pub policy: ParentheticalPolicy,
    pub introduced: BTreeMap<String, String>,
    pub flattened: usize,
    pub unplaceable: Vec<String>,
    pub skipped: usize,
}

fn prose_view(target: &str) -> Result<(Vec<InlineSpan>, String), fancy_regex::Error> {
    let spans = parse_markup(target);
    let mut view = String::new();
    for span in &spans {
        if span.footnote.is_some() {
            view.push('\0');
        } else if span.verbatim {
            view.push_str(&"\0".repeat(span.text.len()));
        } else {
            let mut cursor = 0;
            for literal in falint::literal_ranges(&span.text)? {
                view.push_str(&span.text[cursor..literal.start]);
                let is_word = span.text[literal.clone()]
                    .starts_with(|c: char| c.is_ascii_alphabetic())
                    && !span.text[literal.clone()].contains(['/', '@']);
                if is_word {
                    view.push_str(&span.text[literal.clone()]);
                } else {
                    view.push_str(&"\0".repeat(literal.len()));
                }
                cursor = literal.end;
            }
            view.push_str(&span.text[cursor..]);
        }
    }
    Ok((spans, view))
}

fn flatten_in_prose(
    target: &str,
    first: &str,
    later: &str,
    keep: bool,
) -> Result<(String, usize), fancy_regex::Error> {
    let (mut spans, view) = prose_view(target)?;
    let matches: Vec<_> = standalone_spans(&view, first)
        .into_iter()
        .skip(usize::from(keep))
        .collect();
    if matches.is_empty() {
        return Ok((target.to_owned(), 0));
    }
    let mut offset = 0;
    for span in &mut spans {
        let len = if span.footnote.is_some() {
            1
        } else {
            span.text.len()
        };
        if !span.verbatim && span.footnote.is_none() {
            span.text = span
                .text
                .char_indices()
                .filter_map(|(i, ch)| {
                    (!matches.iter().any(|range| {
                        range.start + later.len() <= offset + i && offset + i < range.end
                    }))
                    .then_some(ch)
                })
                .collect();
        }
        offset += len;
    }
    Ok((render_spans(&spans), matches.len()))
}

fn placement(
    target: &str,
    name: &str,
) -> Result<Option<(usize, Range<usize>)>, fancy_regex::Error> {
    let (spans, view) = prose_view(target)?;
    let matches = standalone_spans(&view, name);
    let mut offset = 0;
    for (i, span) in spans.iter().enumerate() {
        let len = if span.footnote.is_some() {
            1
        } else {
            span.text.len()
        };
        if !span.verbatim
            && span.footnote.is_none()
            && let Some(range) = matches
                .iter()
                .find(|range| range.start >= offset && range.end <= offset + len)
        {
            return Ok(Some((i, range.start - offset..range.end - offset)));
        }
        offset += len;
    }
    Ok(None)
}

pub fn enforce_first_mentions(
    glossary: &Glossary,
    blocks: &mut [MentionBlock],
) -> Result<MentionReport, fancy_regex::Error> {
    let policy = glossary.policy.original_parenthetical;
    let mut report = MentionReport {
        policy,
        introduced: BTreeMap::new(),
        flattened: 0,
        unplaceable: Vec::new(),
        skipped: 0,
    };
    let mut targets = blocks.to_vec();
    let mut groups = indexmap::IndexMap::<&str, Vec<usize>>::new();
    for (i, block) in blocks.iter().enumerate() {
        groups
            .entry(if policy == ParentheticalPolicy::FirstPerChapter {
                &block.chapter
            } else {
                ""
            })
            .or_default()
            .push(i);
    }
    for entry in &glossary.entries {
        let (first, later) = (entry.first_form.trim(), canonical(entry));
        if !entry.locked
            || later.is_empty()
            || first == later
            || !first.starts_with(later)
            || !first
                .split_once('(')
                .and_then(|(_, rest)| rest.split_once(')'))
                .is_some_and(|(inside, _)| inside.chars().count() >= 2)
        {
            report.skipped += 1;
            continue;
        }
        let key = if entry.id.is_empty() {
            &entry.source
        } else {
            &entry.id
        };
        let mut placed = false;
        for group in groups.values() {
            let mut owner = None;
            for &i in group {
                let target = &targets[i].target;
                let old = !standalone_spans(&prose_view(target)?.1, first).is_empty();
                if !old && placement(target, later)?.is_none() {
                    continue;
                }
                if owner.is_none() || targets[i].id == entry.first_block_id {
                    owner = Some(i);
                }
                if targets[i].id == entry.first_block_id {
                    break;
                }
            }
            if policy == ParentheticalPolicy::Never {
                owner = None;
            }
            for &i in group {
                let (text, count) =
                    flatten_in_prose(&targets[i].target, first, later, owner == Some(i))?;
                targets[i].target = text;
                report.flattened += count;
            }
            if let Some(i) = owner {
                let target = &mut targets[i];
                if standalone_spans(&prose_view(&target.target)?.1, first).is_empty()
                    && let Some((span, range)) = placement(&target.target, later)?
                {
                    let mut spans = parse_markup(&target.target);
                    spans[span].text.replace_range(range, first);
                    target.target = render_spans(&spans);
                }
                placed = true;
                report
                    .introduced
                    .entry(key.clone())
                    .or_insert_with(|| target.id.clone());
            }
        }
        if !placed && policy != ParentheticalPolicy::Never {
            report.unplaceable.push(key.clone());
        }
    }
    blocks.clone_from_slice(&targets);
    Ok(report)
}

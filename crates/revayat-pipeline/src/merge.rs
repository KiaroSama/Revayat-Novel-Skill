use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

use revayat_core::{
    Block, BookDocument, Footnote, FootnoteOrigin, InlineSpan, PublishedKind, TextRun, base_of,
    footnote_refs_with_local, parse_markup, render_spans, rewrite_footnote_refs,
};
use serde::{Deserialize, Serialize};

use crate::{
    PipelineError,
    chunk::{self, Worksheet},
    storage,
    units::{check_preservation, replace_text, source_units},
};

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct MergeReport {
    pub applied: Vec<String>,
    pub translator_notes: usize,
}

pub(crate) fn validate_reply(sheet: &Worksheet, reply: &Worksheet) -> Result<(), PipelineError> {
    if sheet.id != reply.id || sheet.request != reply.request {
        return Err(PipelineError::Stale(reply.reply_filename().into()));
    }
    let mut seen = BTreeSet::new();
    for unit in &reply.units {
        if !seen.insert(unit.id.as_str()) {
            return Err(PipelineError::Duplicate(unit.id.clone()));
        }
    }
    if reply.units.len() < sheet.units.len() {
        return Err(PipelineError::Worksheet(format!(
            "missing units in {}",
            reply.id
        )));
    }
    let extras: BTreeSet<_> = reply.units[sheet.units.len()..]
        .iter()
        .map(|unit| unit.id.as_str())
        .collect();
    let mut anchored = BTreeSet::new();
    for (source, target) in sheet.units.iter().zip(&reply.units) {
        if (source.id.as_str(), source.kind) != (target.id.as_str(), target.kind) {
            return Err(PipelineError::Worksheet(format!(
                "expected {} in {}",
                source.id, sheet.id
            )));
        }
        check_preservation(&source.id, &source.text, &target.text)?;
        let source_notes = footnote_refs_with_local(&source.text, true);
        for note in footnote_refs_with_local(&target.text, true) {
            if source_notes.contains(&note) {
                continue;
            }
            if !local_note_id(&note)
                || !extras.contains(note.as_str())
                || !anchored.insert(note.clone())
                || matches!(
                    target.kind,
                    PublishedKind::Title | PublishedKind::ChapterTitle | PublishedKind::Footnote
                )
            {
                return Err(PipelineError::Worksheet(format!(
                    "unbound or duplicate footnote {note} in {}",
                    target.id
                )));
            }
        }
    }
    for offered in &reply.units[sheet.units.len()..] {
        if offered.kind != PublishedKind::Footnote
            || !anchored.contains(&offered.id)
            || !footnote_refs_with_local(&offered.text, true).is_empty()
        {
            return Err(PipelineError::Worksheet(format!(
                "invalid translator footnote {}",
                offered.id
            )));
        }
        check_preservation(&offered.id, &offered.text, &offered.text)?;
    }
    Ok(())
}

fn local_note_id(id: &str) -> bool {
    id.strip_prefix("tr-").is_some_and(|suffix| {
        !suffix.is_empty()
            && suffix.len() <= 64
            && suffix
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
    })
}

fn stable_id<'a>(id: &'a str, owners: &BTreeSet<&str>) -> &'a str {
    if owners.contains(id) { id } else { base_of(id) }
}

pub fn adopt_translator_notes(
    book: &BookDocument,
    sheet: &Worksheet,
    reply: &Worksheet,
) -> Result<(Vec<Footnote>, BTreeMap<String, String>), PipelineError> {
    validate_reply(sheet, reply)?;
    let source = source_units(book)?;
    let owners: BTreeSet<_> = source.iter().map(|unit| unit.id.as_str()).collect();
    let locations: BTreeMap<_, _> = source
        .iter()
        .map(|unit| (unit.id.as_str(), unit.chapter.as_deref()))
        .collect();
    let mut notes = Vec::new();
    let mut mapping = BTreeMap::new();
    let mut number = book
        .footnotes
        .iter()
        .map(|note| note.number)
        .max()
        .unwrap_or_default();
    let ids = book
        .footnotes
        .iter()
        .map(|note| note.id.as_str())
        .collect::<BTreeSet<_>>();
    for item in &reply.units[sheet.units.len()..] {
        let anchor = reply.units[..sheet.units.len()]
            .iter()
            .find(|unit| footnote_refs_with_local(&unit.text, true).contains(&item.id))
            .ok_or_else(|| PipelineError::Worksheet(format!("missing anchor for {}", item.id)))?;
        let owner = stable_id(&anchor.id, &owners);
        let chapter = locations.get(owner).copied().flatten().ok_or_else(|| {
            PipelineError::Worksheet(format!("footnote {} has no chapter", item.id))
        })?;
        let id = loop {
            number = number
                .checked_add(1)
                .ok_or_else(|| PipelineError::Worksheet("footnote numbering overflow".into()))?;
            let id = format!("fn{number:04}");
            if !ids.contains(id.as_str()) {
                break id;
            }
        };
        let runs = parse_markup(&item.text)
            .into_iter()
            .map(|span| TextRun {
                text: span.text,
                bold: span.bold,
                italic: span.italic,
                ..Default::default()
            })
            .collect();
        notes.push(Footnote {
            id: id.clone(),
            number,
            origin_chapter: Some(chapter.into()),
            anchor_block: Some(owner.into()),
            origin: FootnoteOrigin::Translator,
            blocks: vec![Block::Paragraph {
                id: format!("{id}-translation"),
                runs,
                style: None,
                align: None,
                rtl: revayat_core::has_persian(&item.text),
            }],
        });
        mapping.insert(item.id.clone(), id);
    }
    Ok((notes, mapping))
}

pub fn rejoin_units(
    book: &BookDocument,
    answers: &[(Worksheet, Worksheet)],
) -> Result<BTreeMap<String, String>, PipelineError> {
    for (sheet, reply) in answers {
        validate_reply(sheet, reply)?;
    }
    join_units(book, answers)
}

fn join_units(
    book: &BookDocument,
    answers: &[(Worksheet, Worksheet)],
) -> Result<BTreeMap<String, String>, PipelineError> {
    let source = source_units(book)?;
    let originals: BTreeMap<_, _> = source
        .iter()
        .map(|unit| (unit.id.as_str(), unit.text.as_str()))
        .collect();
    let owners = originals.keys().copied().collect::<BTreeSet<_>>();
    let mut grouped = BTreeMap::<&str, Vec<(&str, &str, &str)>>::new();
    let mut seen = BTreeSet::new();
    for (sheet, reply) in answers {
        for (source, target) in sheet.units.iter().zip(&reply.units) {
            let id = source.id.as_str();
            if !seen.insert(id) {
                return Err(PipelineError::Duplicate(id.into()));
            }
            let owner = stable_id(id, &owners);
            if !owners.contains(owner) {
                return Err(PipelineError::Worksheet(format!("unknown unit {id}")));
            }
            grouped
                .entry(owner)
                .or_default()
                .push((id, &source.text, &target.text));
        }
    }
    let mut result = BTreeMap::new();
    for (owner, mut parts) in grouped {
        if let [(id, source, target)] = parts.as_slice()
            && *id == owner
        {
            if originals.get(owner) != Some(source) {
                return Err(PipelineError::Worksheet(format!(
                    "stale source unit {owner}"
                )));
            }
            result.insert(owner.into(), (*target).into());
            continue;
        }
        parts.sort_by_key(|&(id, _, _)| revayat_core::index_of(id));
        let mut joined_source = Vec::new();
        let mut joined_target = Vec::<InlineSpan>::new();
        for (i, (id, source, target)) in parts.into_iter().enumerate() {
            if id != revayat_core::segment_id(owner, i + 1) {
                return Err(PipelineError::Worksheet(format!(
                    "incomplete segments for {owner}"
                )));
            }
            let source_spans = parse_markup(source);
            let target_spans = parse_markup(target);
            if let (Some(previous), Some(first)) = (joined_target.last_mut(), target_spans.first())
                && !previous.text.ends_with(char::is_whitespace)
                && !first.text.starts_with(char::is_whitespace)
            {
                let separator = joined_source
                    .last()
                    .and_then(|span: &InlineSpan| {
                        span.text
                            .rsplit_once(|ch: char| !ch.is_whitespace())
                            .map(|(_, end)| end)
                    })
                    .filter(|end| !end.is_empty())
                    .or_else(|| {
                        source_spans
                            .first()
                            .and_then(|span| {
                                span.text
                                    .split_once(|ch: char| !ch.is_whitespace())
                                    .map(|(start, _)| start)
                            })
                            .filter(|start| !start.is_empty())
                    });
                if let Some(separator) = separator {
                    previous.text.push_str(separator);
                }
            }
            joined_source.extend(source_spans);
            joined_target.extend(target_spans);
        }
        if originals.get(owner).is_none_or(|original| {
            render_spans(&joined_source) != render_spans(&parse_markup(original))
        }) {
            return Err(PipelineError::Worksheet(format!(
                "segments do not cover source unit {owner}"
            )));
        }
        result.insert(owner.into(), render_spans(&joined_target));
    }
    Ok(result)
}

pub fn apply_units(
    book: &mut BookDocument,
    texts: &BTreeMap<String, String>,
) -> Result<MergeReport, PipelineError> {
    let source = source_units(book)?;
    let source: BTreeMap<_, _> = source.iter().map(|unit| (unit.id.as_str(), unit)).collect();
    for (id, target) in texts {
        let Some(before) = source.get(id.as_str()) else {
            return Err(PipelineError::Worksheet(format!("unknown unit {id}")));
        };
        check_preservation(id, &before.text, target)?;
    }
    let mut candidate = book.clone();
    replace_text(&mut candidate, texts)?;
    candidate.validate()?;
    *book = candidate;
    Ok(MergeReport {
        applied: texts.keys().cloned().collect(),
        translator_notes: 0,
    })
}

pub(crate) fn apply_replies(
    book: &mut BookDocument,
    mut answers: Vec<(Worksheet, Worksheet)>,
) -> Result<MergeReport, PipelineError> {
    let mut candidate = book.clone();
    let mut count = 0;
    for (sheet, reply) in &mut answers {
        let (notes, mapping) = adopt_translator_notes(&candidate, sheet, reply)?;
        for target in reply.units.iter_mut().take(sheet.units.len()) {
            target.text = rewrite_footnote_refs(&target.text, &mapping);
        }
        count += notes.len();
        candidate.footnotes.extend(notes);
    }
    let texts = join_units(book, &answers)?;
    replace_text(&mut candidate, &texts)?;
    candidate.validate()?;
    *book = candidate;
    Ok(MergeReport {
        applied: texts.into_keys().collect(),
        translator_notes: count,
    })
}

pub fn merge_work_dir(work_dir: &Path) -> Result<MergeReport, PipelineError> {
    let dir = work_dir.join(chunk::CHUNKS_DIR);
    storage::edit_book(&work_dir.join(revayat_core::BOOK_FILENAME), |book| {
        let manifest = chunk::load_current(book, &dir)?;
        let replies = manifest
            .sheets
            .into_iter()
            .map(|sheet| {
                let reply = chunk::read_reply(&dir, &sheet)?;
                Ok((sheet, reply))
            })
            .collect::<Result<_, PipelineError>>()?;
        apply_replies(book, replies)
    })
}

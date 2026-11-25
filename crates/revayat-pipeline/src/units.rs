use std::collections::{BTreeMap, BTreeSet};

use revayat_core::{
    Block, BookDocument, InlineSpan, PublishedKind, TextRun, TranslationEligibility,
    emphasis_signature, footnote_refs, parse_markup, render_spans, translation_eligibility,
    verbatim_spans,
};
use serde::{Deserialize, Serialize};

use crate::PipelineError;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceUnit {
    pub id: String,
    pub kind: PublishedKind,
    pub chapter: Option<String>,
    pub text: String,
}

impl SourceUnit {
    pub(crate) fn eligible(&self) -> bool {
        translation_eligibility(&self.text, None, None) == TranslationEligibility::Eligible
    }
}

pub(crate) fn block_unit(
    block: &Block,
    chapter: Option<&str>,
    is_note: bool,
) -> Option<SourceUnit> {
    let kind = match block {
        Block::Paragraph { .. } => PublishedKind::Paragraph,
        Block::Heading { .. } => PublishedKind::Heading,
        Block::Image { .. } => PublishedKind::Caption,
        _ => return None,
    };
    Some(SourceUnit {
        id: block.id().into(),
        kind: if is_note {
            PublishedKind::Footnote
        } else {
            kind
        },
        chapter: chapter.map(str::to_owned),
        text: runs_markup(block.runs()),
    })
}

pub(crate) fn runs_markup(runs: &[TextRun]) -> String {
    render_spans(
        &runs
            .iter()
            .flat_map(|run| {
                let span = InlineSpan {
                    text: run.text.clone(),
                    bold: run.bold,
                    italic: run.italic,
                    ..Default::default()
                };
                let note = run.footnote_ref.as_ref().map(|id| InlineSpan {
                    text: String::new(),
                    footnote: Some(id.clone()),
                    ..span.clone()
                });
                std::iter::once(span).chain(note)
            })
            .collect::<Vec<_>>(),
    )
}

pub(crate) fn walk(blocks: &[Block], visit: &mut impl FnMut(&Block)) {
    for block in blocks {
        visit(block);
        if let Block::Table { rows, .. } = block {
            for cell in rows.iter().flat_map(|row| &row.cells) {
                walk(&cell.blocks, visit);
            }
        }
    }
}

pub(crate) fn source_units(book: &BookDocument) -> Result<Vec<SourceUnit>, PipelineError> {
    let mut units = vec![SourceUnit {
        id: "title".into(),
        kind: PublishedKind::Title,
        chapter: None,
        text: revayat_core::escape_markup(&book.title),
    }];
    for chapter in &book.chapters {
        units.push(SourceUnit {
            id: chapter.id.clone(),
            kind: PublishedKind::ChapterTitle,
            chapter: Some(chapter.id.clone()),
            text: revayat_core::escape_markup(&chapter.title),
        });
        walk(&chapter.blocks, &mut |block| {
            units.extend(block_unit(block, Some(&chapter.id), false))
        });
    }
    for note in &book.footnotes {
        walk(&note.blocks, &mut |block| {
            units.extend(block_unit(block, note.origin_chapter.as_deref(), true))
        });
    }
    let mut seen = BTreeSet::new();
    for unit in &units {
        if !safe_id(&unit.id) {
            return Err(PipelineError::Worksheet(format!(
                "invalid unit ID {}",
                unit.id
            )));
        }
        if !seen.insert(unit.id.as_str()) {
            return Err(PipelineError::Duplicate(unit.id.clone()));
        }
    }
    Ok(units)
}

pub(crate) fn safe_id(id: &str) -> bool {
    !id.is_empty()
        && id
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.' | ':' | '/' | '#'))
}

pub(crate) fn check_preservation(id: &str, before: &str, after: &str) -> Result<(), PipelineError> {
    if after.trim().is_empty()
        || parse_markup(after)
            .iter()
            .all(|span| span.text.trim().is_empty())
    {
        return Err(PipelineError::Worksheet(format!("blank answer for {id}")));
    }
    if emphasis_signature(before) != emphasis_signature(after) {
        return Err(PipelineError::Worksheet(format!(
            "bold or italic markup changed for {id}"
        )));
    }
    if verbatim_spans(before) != verbatim_spans(after) {
        return Err(PipelineError::Worksheet(format!(
            "verbatim text changed for {id}"
        )));
    }
    let original = footnote_refs(before);
    let translated = footnote_refs(after);
    if translated
        .iter()
        .filter(|note| original.contains(note))
        .ne(original.iter())
    {
        return Err(PipelineError::Worksheet(format!(
            "source footnote markers changed for {id}"
        )));
    }
    Ok(())
}

pub(crate) fn replace_text(
    book: &mut BookDocument,
    texts: &BTreeMap<String, String>,
) -> Result<(), PipelineError> {
    if let Some(title) = texts.get("title") {
        book.title = title_text("title", title)?;
    }
    for chapter in &mut book.chapters {
        if let Some(title) = texts.get(&chapter.id) {
            chapter.title = title_text(&chapter.id, title)?;
        }
        replace_blocks(&mut chapter.blocks, texts)?;
    }
    for note in &mut book.footnotes {
        replace_blocks(&mut note.blocks, texts)?;
    }
    Ok(())
}

fn title_text(id: &str, text: &str) -> Result<String, PipelineError> {
    let spans = parse_markup(text);
    if spans
        .iter()
        .any(|span| span.bold || span.italic || span.verbatim || span.footnote.is_some())
    {
        return Err(PipelineError::Worksheet(format!(
            "title {id} cannot carry inline markup"
        )));
    }
    Ok(spans.into_iter().map(|span| span.text).collect())
}

fn replace_blocks(
    blocks: &mut [Block],
    texts: &BTreeMap<String, String>,
) -> Result<(), PipelineError> {
    for block in blocks {
        match block {
            Block::Paragraph { id, runs, rtl, .. } => {
                if let Some(text) = texts.get(id) {
                    *runs = target_runs(id, runs, text)?;
                    *rtl = revayat_core::has_persian(text) || *rtl;
                }
            }
            Block::Heading { id, runs, .. }
            | Block::Image {
                id, caption: runs, ..
            } => {
                if let Some(text) = texts.get(id) {
                    *runs = target_runs(id, runs, text)?;
                }
            }
            Block::Table { rows, .. } => {
                for cell in rows.iter_mut().flat_map(|row| &mut row.cells) {
                    replace_blocks(&mut cell.blocks, texts)?;
                }
            }
            Block::PageBreak { .. } | Block::SceneBreak { .. } => {}
        }
    }
    Ok(())
}

pub(crate) fn target_runs(
    id: &str,
    source: &[TextRun],
    target: &str,
) -> Result<Vec<TextRun>, PipelineError> {
    let spans = parse_markup(target);
    let to_run = |span: InlineSpan, template: Option<&TextRun>| {
        let mut run = template.cloned().unwrap_or_default();
        run.text = span.text;
        run.bold = span.bold;
        run.italic = span.italic;
        run.footnote_ref = span.footnote;
        run
    };
    let metadata = |run: &TextRun| {
        (
            run.underline,
            run.strikethrough,
            run.superscript,
            run.subscript,
            run.hyperlink.is_some(),
        )
    };
    let protected = source
        .iter()
        .any(|run| metadata(run) != (false, false, false, false, false));
    if !protected {
        return Ok(spans.into_iter().map(|span| to_run(span, None)).collect());
    }
    let template: Vec<_> = source
        .iter()
        .flat_map(|run| {
            std::iter::once(run)
                .filter(|r| !r.text.is_empty())
                .chain(std::iter::once(run).filter(|r| r.footnote_ref.is_some()))
        })
        .collect();
    if spans.len() != template.len()
        || spans
            .iter()
            .zip(&template)
            .any(|(span, run)| (span.bold, span.italic) != (run.bold, run.italic))
    {
        return Err(PipelineError::Worksheet(format!(
            "cannot map protected run formatting in {id}"
        )));
    }
    Ok(spans
        .into_iter()
        .zip(template)
        .map(|(span, run)| to_run(span, Some(run)))
        .collect())
}

use std::{collections::BTreeSet, fmt::Write as _, path::Path};

use revayat_core::{
    BookDocument, InlineSpan, PublishedKind, published_digest, render_spans, sha256_bytes,
    split_text,
};
use serde::{Deserialize, Serialize};

use crate::{
    PipelineError, storage,
    units::{SourceUnit, safe_id, source_units},
};

pub const MANIFEST_FILENAME: &str = "manifest.json";
pub const CHUNKS_DIR: &str = "chunks";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChunkBudget {
    pub max_chars: usize,
    pub max_tokens: usize,
}

impl Default for ChunkBudget {
    fn default() -> Self {
        Self {
            max_chars: 6_000,
            max_tokens: 3_000,
        }
    }
}

impl ChunkBudget {
    fn check(self) -> Result<(), PipelineError> {
        if self.max_chars == 0 || self.max_tokens == 0 {
            return Err(PipelineError::Budget("limits must be positive".into()));
        }
        Ok(())
    }
    fn fits(self, chars: usize, tokens: usize) -> bool {
        chars <= self.max_chars && tokens <= self.max_tokens
    }
}

pub fn estimate_tokens(text: &str) -> usize {
    // shortcut: this is a byte/word estimate, use an exact tokenizer if a model requires one.
    text.len().div_ceil(4).max(text.split_whitespace().count())
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorksheetUnit {
    pub id: String,
    pub kind: PublishedKind,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Worksheet {
    pub id: String,
    pub request: String,
    pub units: Vec<WorksheetUnit>,
}

impl Worksheet {
    pub fn reply_filename(&self) -> String {
        format!("out_{}.md", self.id)
    }

    pub fn to_markdown(&self) -> String {
        let mut out = format!(
            "<!-- revayat-novel: request {} {} -->\n",
            self.id, self.request
        );
        for unit in &self.units {
            let _ = writeln!(out, "@@ {} {}", unit.id, kind_name(unit.kind));
            for line in unit.text.split('\n') {
                if control(line) {
                    out.push('\\');
                }
                out.push_str(line);
                out.push('\n');
            }
        }
        out
    }

    pub fn from_markdown(text: &str) -> Result<Self, PipelineError> {
        let mut lines = text
            .split_terminator('\n')
            .map(|line| line.strip_suffix('\r').unwrap_or(line));
        let Some(header) = lines
            .next()
            .and_then(|line| line.strip_prefix("<!-- revayat-novel: request "))
            .and_then(|line| line.strip_suffix(" -->"))
        else {
            return Err(PipelineError::Worksheet(
                "missing worksheet request header".into(),
            ));
        };
        let Some((id, request)) = header.split_once(' ') else {
            return Err(PipelineError::Worksheet(
                "invalid worksheet request header".into(),
            ));
        };
        if !revayat_core::is_safe_filename(id) || !request.starts_with("sheet1:") {
            return Err(PipelineError::Worksheet(
                "invalid worksheet identity".into(),
            ));
        }
        let mut units = Vec::<WorksheetUnit>::new();
        let mut seen = BTreeSet::new();
        let mut payload = Vec::new();
        for line in lines {
            if let Some(header) = line.strip_prefix("@@ ") {
                if let Some(unit) = units.last_mut() {
                    unit.text = payload.join("\n");
                    payload.clear();
                }
                let Some((id, kind)) = header.split_once(' ') else {
                    return Err(PipelineError::Worksheet("invalid unit header".into()));
                };
                if !safe_id(id) {
                    return Err(PipelineError::Worksheet(format!("invalid unit ID {id}")));
                }
                if !seen.insert(id) {
                    return Err(PipelineError::Duplicate(id.into()));
                }
                units.push(WorksheetUnit {
                    id: id.into(),
                    kind: parse_kind(kind)?,
                    text: String::new(),
                });
            } else if units.is_empty() || line.starts_with("<!-- revayat-novel:") {
                return Err(PipelineError::Worksheet(
                    "unexpected text outside a worksheet unit".into(),
                ));
            } else {
                payload.push(
                    line.strip_prefix('\\')
                        .filter(|line| control(line))
                        .unwrap_or(line),
                );
            }
        }
        if let Some(unit) = units.last_mut() {
            unit.text = payload.join("\n");
        }
        Ok(Self {
            id: id.into(),
            request: request.into(),
            units,
        })
    }
}

fn control(line: &str) -> bool {
    let line = line.trim_start_matches('\\');
    line.starts_with("@@ ") || line.starts_with("<!-- revayat-novel:")
}

fn kind_name(kind: PublishedKind) -> &'static str {
    match kind {
        PublishedKind::Title => "title",
        PublishedKind::ChapterTitle => "chapter_title",
        PublishedKind::Paragraph => "paragraph",
        PublishedKind::Heading => "heading",
        PublishedKind::Caption => "caption",
        PublishedKind::Footnote => "footnote",
    }
}

fn parse_kind(name: &str) -> Result<PublishedKind, PipelineError> {
    match name {
        "title" => Ok(PublishedKind::Title),
        "chapter_title" => Ok(PublishedKind::ChapterTitle),
        "paragraph" => Ok(PublishedKind::Paragraph),
        "heading" => Ok(PublishedKind::Heading),
        "caption" => Ok(PublishedKind::Caption),
        "footnote" => Ok(PublishedKind::Footnote),
        _ => Err(PipelineError::Worksheet(format!(
            "unknown worksheet unit kind {name}"
        ))),
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorksheetManifest {
    pub revision: String,
    pub budget: ChunkBudget,
    pub sheets: Vec<Worksheet>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReplyState {
    Pending,
    Invalid,
    Answered,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SheetStatus {
    pub id: String,
    pub state: ReplyState,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChunkStatus {
    pub pending: usize,
    pub answered: usize,
    pub invalid: usize,
    pub sheets: Vec<SheetStatus>,
}

pub(crate) fn create_sheet(
    id: String,
    binding: &str,
    units: Vec<WorksheetUnit>,
) -> Result<Worksheet, PipelineError> {
    let bytes = serde_json::to_vec(&(&id, binding, &units))?;
    Ok(Worksheet {
        id,
        request: format!("sheet1:{}", sha256_bytes(&bytes)),
        units,
    })
}

pub(crate) fn write_sheet(dir: &Path, sheet: &Worksheet) -> Result<(), PipelineError> {
    storage::write(
        &dir.join(format!("{}.md", sheet.id)),
        sheet.to_markdown().as_bytes(),
    )?;
    storage::write_json(&dir.join(format!("{}.json", sheet.id)), sheet)
}

pub(crate) fn read_reply(dir: &Path, sheet: &Worksheet) -> Result<Worksheet, PipelineError> {
    let markdown = dir.join(sheet.reply_filename());
    let json = markdown.with_extension("json");
    match (markdown.try_exists(), json.try_exists()) {
        (Ok(true), Ok(true)) => Err(PipelineError::Duplicate(sheet.id.clone())),
        (Ok(false), Ok(true)) => storage::read_json(&json),
        (Ok(_), Ok(false)) => {
            let bytes = storage::read(&markdown)?;
            let text = std::str::from_utf8(&bytes).map_err(|_| {
                PipelineError::Worksheet(format!("{} is not UTF-8", markdown.display()))
            })?;
            Worksheet::from_markdown(text)
        }
        (Err(error), _) => Err(crate::error::io_error("check", &markdown, error)),
        (_, Err(error)) => Err(crate::error::io_error("check", &json, error)),
    }
}

fn characters(text: &str) -> usize {
    text.chars().count()
}

pub(crate) fn budget_fits(budget: ChunkBudget, units: &[WorksheetUnit]) -> bool {
    units.iter().all(|unit| !unit.text.is_empty())
        && budget.fits(
            units.iter().map(|unit| characters(&unit.text)).sum(),
            units.iter().map(|unit| estimate_tokens(&unit.text)).sum(),
        )
}

pub(crate) fn plain_unit(unit: &SourceUnit) -> WorksheetUnit {
    WorksheetUnit {
        id: unit.id.clone(),
        kind: unit.kind,
        text: unit.text.clone(),
    }
}

fn parts(unit: &SourceUnit, budget: ChunkBudget) -> Result<Vec<WorksheetUnit>, PipelineError> {
    if budget.fits(characters(&unit.text), estimate_tokens(&unit.text)) {
        return Ok(vec![plain_unit(unit)]);
    }
    let mut parts = Vec::new();
    let mut buffer = Vec::<InlineSpan>::new();
    for span in revayat_core::parse_markup(&unit.text) {
        let mut fragments = vec![span.clone()];
        let rendered = render_spans(std::slice::from_ref(&span));
        if !budget.fits(characters(&rendered), estimate_tokens(&rendered)) {
            let overhead = rendered
                .chars()
                .count()
                .saturating_sub(span.text.chars().count());
            let limit = budget
                .max_chars
                .min(budget.max_tokens)
                .saturating_sub(overhead);
            if span.footnote.is_some() || span.verbatim || limit == 0 {
                return Err(PipelineError::Budget(format!(
                    "indivisible markup exceeds limits in {}",
                    unit.id
                )));
            }
            fragments = split_text(&span.text, limit)
                .into_iter()
                .map(|text| InlineSpan {
                    text: text.into(),
                    ..span.clone()
                })
                .collect();
        }
        for fragment in fragments {
            buffer.push(fragment);
            let candidate = render_spans(&buffer);
            if !budget.fits(characters(&candidate), estimate_tokens(&candidate)) {
                let Some(last) = buffer.pop() else {
                    return Err(PipelineError::Budget(unit.id.clone()));
                };
                if !buffer.is_empty() {
                    parts.push(render_spans(&buffer));
                }
                buffer = vec![last];
                let text = render_spans(&buffer);
                if !budget.fits(characters(&text), estimate_tokens(&text)) {
                    return Err(PipelineError::Budget(format!(
                        "indivisible text exceeds limits in {}",
                        unit.id
                    )));
                }
            }
        }
    }
    if !buffer.is_empty() {
        parts.push(render_spans(&buffer));
    }
    if parts
        .iter()
        .any(|text| revayat_core::prose(text).trim().is_empty())
    {
        return Err(PipelineError::Budget(format!(
            "limits isolate an untranslated marker in {}",
            unit.id
        )));
    }
    Ok(parts
        .into_iter()
        .enumerate()
        .map(|(i, text)| WorksheetUnit {
            id: revayat_core::segment_id(&unit.id, i + 1),
            kind: unit.kind,
            text,
        })
        .collect())
}

fn plan(book: &BookDocument, budget: ChunkBudget) -> Result<WorksheetManifest, PipelineError> {
    budget.check()?;
    let revision = published_digest(book)?;
    let units = source_units(book)?;
    let owners = units
        .iter()
        .map(|unit| unit.id.as_str())
        .collect::<BTreeSet<_>>();
    let mut groups = Vec::<Vec<WorksheetUnit>>::new();
    let mut active = Vec::new();
    let mut cost = (0, 0);
    let mut seen = BTreeSet::new();
    for unit in units.iter().filter(|unit| unit.eligible()) {
        if unit.kind == PublishedKind::ChapterTitle
            && !active.is_empty()
            && active
                .iter()
                .any(|unit: &WorksheetUnit| unit.kind != PublishedKind::Title)
        {
            groups.push(std::mem::take(&mut active));
            cost = (0, 0);
        }
        for part in parts(unit, budget)? {
            if !seen.insert(part.id.clone())
                || (part.id != unit.id && owners.contains(part.id.as_str()))
            {
                return Err(PipelineError::Duplicate(part.id));
            }
            let more = (characters(&part.text), estimate_tokens(&part.text));
            if !budget.fits(cost.0 + more.0, cost.1 + more.1) {
                groups.push(std::mem::take(&mut active));
                cost = (0, 0);
            }
            cost = (cost.0 + more.0, cost.1 + more.1);
            active.push(part);
        }
    }
    if !active.is_empty() {
        groups.push(active);
    }
    let binding = format!("{revision}:{}:{}", budget.max_chars, budget.max_tokens);
    let sheets = groups
        .into_iter()
        .enumerate()
        .map(|(i, units)| create_sheet(format!("chunk{:04}", i + 1), &binding, units))
        .collect::<Result<_, _>>()?;
    Ok(WorksheetManifest {
        revision,
        budget,
        sheets,
    })
}

pub fn build_worksheets(
    book: &BookDocument,
    out_dir: &Path,
    budget: ChunkBudget,
) -> Result<WorksheetManifest, PipelineError> {
    let new = plan(book, budget)?;
    storage::create_dir(out_dir)?;
    let path = out_dir.join(MANIFEST_FILENAME);
    let _guard = revayat_core::BookLock::acquire(&path)?;
    match storage::read_json::<WorksheetManifest>(&path) {
        Ok(old) if old != new => return Err(PipelineError::Stale(path)),
        Ok(_) | Err(PipelineError::Missing(_)) => {}
        Err(error) => return Err(error),
    }
    for sheet in &new.sheets {
        write_sheet(out_dir, sheet)?;
    }
    storage::write_json(&path, &new)?;
    Ok(new)
}

pub(crate) fn load_current(
    book: &BookDocument,
    dir: &Path,
) -> Result<WorksheetManifest, PipelineError> {
    let path = dir.join(MANIFEST_FILENAME);
    let manifest: WorksheetManifest = storage::read_json(&path)?;
    if manifest != plan(book, manifest.budget)? {
        return Err(PipelineError::Stale(path));
    }
    Ok(manifest)
}

pub fn chunk_status(book: &BookDocument, dir: &Path) -> Result<ChunkStatus, PipelineError> {
    let manifest = load_current(book, dir)?;
    let mut status = ChunkStatus {
        pending: 0,
        answered: 0,
        invalid: 0,
        sheets: Vec::new(),
    };
    for sheet in &manifest.sheets {
        let result =
            read_reply(dir, sheet).and_then(|reply| crate::merge::validate_reply(sheet, &reply));
        let (state, reason) = match result {
            Ok(()) => {
                status.answered += 1;
                (ReplyState::Answered, None)
            }
            Err(PipelineError::Missing(_)) => {
                status.pending += 1;
                (ReplyState::Pending, None)
            }
            Err(error) => {
                status.invalid += 1;
                (ReplyState::Invalid, Some(error.to_string()))
            }
        };
        status.sheets.push(SheetStatus {
            id: sheet.id.clone(),
            state,
            reason,
        });
    }
    Ok(status)
}

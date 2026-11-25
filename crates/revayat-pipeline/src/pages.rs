use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

use revayat_core::{
    Block, BookDocument, PageRecord, PageState, RunState, SignoffState, StageStatus,
    TranslationStage, load_book, published_digest, sha256_bytes, sha256_file,
};
use serde::{Deserialize, Serialize};

use crate::{
    PipelineError,
    chunk::{self, ChunkBudget, Worksheet},
    merge::{self, MergeReport},
    storage,
    units::{source_units, walk},
};

const PAGES_DIR: &str = "pages";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PageJob {
    pub page: u32,
    pub source: String,
    pub unit_ids: Vec<String>,
    pub sheet: Worksheet,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PagePlan {
    pub source_sha256: String,
    pub budget: ChunkBudget,
    pub jobs: Vec<PageJob>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PagesStatus {
    pub pages: BTreeMap<u32, PageState>,
    pub signoff: SignoffState,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VisualReview {
    pub preview_sha256: String,
    pub layout_matches: bool,
    pub text_readable: bool,
    pub direction_correct: bool,
    pub images_preserved: bool,
    pub notes_preserved: bool,
}

impl VisualReview {
    fn passes(&self) -> bool {
        self.layout_matches
            && self.text_readable
            && self.direction_correct
            && self.images_preserved
            && self.notes_preserved
    }
}

fn book_path(work: &Path) -> PathBuf {
    work.join(revayat_core::BOOK_FILENAME)
}
fn manifest_path(work: &Path) -> PathBuf {
    work.join(PAGES_DIR).join(chunk::MANIFEST_FILENAME)
}
fn preview_path(work: &Path, page: u32) -> PathBuf {
    work.join("previews").join(format!("page-{page:04}.docx"))
}
fn review_path(work: &Path, page: u32) -> PathBuf {
    work.join(PAGES_DIR).join(format!("review-{page:04}.json"))
}

fn page_books(book: &BookDocument) -> Result<BTreeMap<u32, BookDocument>, PipelineError> {
    let mut pages = BTreeMap::<u32, BookDocument>::new();
    for chapter in &book.chapters {
        let spans: Vec<_> = chapter
            .blocks
            .split(|block| matches!(block, Block::PageBreak { .. }))
            .collect();
        let explicit = chapter
            .provenance
            .as_ref()
            .map(|p| p.pages.as_slice())
            .unwrap_or_default();
        let start = chapter.index.max(1);
        if explicit.len() > 1 && explicit.len() != spans.len() {
            return Err(PipelineError::Workflow(format!(
                "no block-to-page boundaries for chapter {}",
                chapter.id
            )));
        }
        for (i, blocks) in spans.into_iter().enumerate() {
            let offset =
                u32::try_from(i).map_err(|_| PipelineError::Workflow("too many pages".into()))?;
            let number = explicit
                .get(i)
                .map(|entry| entry.page_number)
                .or_else(|| {
                    explicit
                        .first()
                        .map_or(Some(start), |entry| Some(entry.page_number))
                        .and_then(|first| first.checked_add(offset))
                })
                .ok_or_else(|| PipelineError::Workflow("page numbering overflow".into()))?;
            let page = pages.entry(number).or_insert_with(|| {
                let mut page = book.clone();
                page.title.clear();
                page.chapters.clear();
                page.footnotes.clear();
                page.assets.clear();
                page
            });
            let mut part = chapter.clone();
            if i > 0 {
                part.title.clear();
            }
            part.blocks = blocks.to_vec();
            if let Some(provenance) = &mut part.provenance {
                provenance.pages.retain(|entry| entry.page_number == number);
            }
            page.chapters.push(part);
        }
    }
    if let Some(mut entry) = pages.first_entry() {
        entry.get_mut().title.clone_from(&book.title);
    }
    for page in pages.values_mut() {
        let mut refs = BTreeSet::new();
        let mut assets = BTreeSet::new();
        let mut inspect = |block: &Block| {
            refs.extend(
                block
                    .runs()
                    .iter()
                    .filter_map(|run| run.footnote_ref.clone()),
            );
        };
        for chapter in &page.chapters {
            walk(&chapter.blocks, &mut inspect);
        }
        page.footnotes.extend(
            book.footnotes
                .iter()
                .filter(|note| refs.contains(&note.id))
                .cloned(),
        );
        let mut inspect_asset = |block: &Block| {
            if let Block::Image { asset_id, .. } = block {
                assets.insert(asset_id.clone());
            }
        };
        for chapter in &page.chapters {
            walk(&chapter.blocks, &mut inspect_asset);
        }
        for note in &page.footnotes {
            walk(&note.blocks, &mut inspect_asset);
        }
        page.assets.extend(
            book.assets
                .iter()
                .filter(|asset| assets.contains(&asset.id))
                .cloned(),
        );
    }
    Ok(pages)
}

fn source_id(book: &BookDocument) -> Result<String, PipelineError> {
    // Translations can replace runs and add notes, but cannot redefine page ownership.
    let chapters: Vec<_> = book
        .chapters
        .iter()
        .map(|chapter| {
            let mut blocks = Vec::new();
            walk(&chapter.blocks, &mut |block| {
                blocks.push((
                    block.id().to_owned(),
                    matches!(block, Block::PageBreak { .. }),
                ))
            });
            (&chapter.id, chapter.index, &chapter.provenance, blocks)
        })
        .collect();
    Ok(sha256_bytes(&serde_json::to_vec(&(
        &book.source_sha256,
        &book.page_geometry,
        &book.assets,
        chapters,
    ))?))
}

fn page_hash(page: &BookDocument) -> Result<String, PipelineError> {
    Ok(sha256_bytes(page.to_json()?.as_bytes()))
}

fn make_plan(book: &BookDocument, budget: ChunkBudget) -> Result<PagePlan, PipelineError> {
    if budget.max_chars == 0 || budget.max_tokens == 0 {
        return Err(PipelineError::Budget("limits must be positive".into()));
    }
    let source_sha256 = source_id(book)?;
    let mut jobs = Vec::new();
    for (page, subset) in page_books(book)? {
        let units: Vec<_> = source_units(&subset)?
            .into_iter()
            .filter(|unit| unit.eligible())
            .map(|unit| chunk::plain_unit(&unit))
            .collect();
        if !chunk::budget_fits(budget, &units) {
            return Err(PipelineError::Budget(format!(
                "page {page} exceeds the worksheet limits"
            )));
        }
        let source = page_hash(&subset)?;
        let unit_ids = units.iter().map(|unit| unit.id.clone()).collect();
        let sheet = chunk::create_sheet(
            format!("page-{page:04}"),
            &format!("{source}:{}:{}", budget.max_chars, budget.max_tokens),
            units,
        )?;
        jobs.push(PageJob {
            page,
            source,
            unit_ids,
            sheet,
        });
    }
    if jobs.is_empty() {
        return Err(PipelineError::Workflow("book has no pages".into()));
    }
    Ok(PagePlan {
        source_sha256,
        budget,
        jobs,
    })
}

fn load_plan(work: &Path, book: &BookDocument) -> Result<PagePlan, PipelineError> {
    let path = manifest_path(work);
    let plan: PagePlan = storage::read_json(&path)?;
    if plan.source_sha256 != source_id(book)? {
        return Err(PipelineError::Stale(path));
    }
    let live = page_books(book)?;
    if live.len() != plan.jobs.len()
        || live
            .keys()
            .copied()
            .ne(plan.jobs.iter().map(|job| job.page))
    {
        return Err(PipelineError::Stale(path));
    }
    for job in &plan.jobs {
        let binding = format!(
            "{}:{}:{}",
            job.source, plan.budget.max_chars, plan.budget.max_tokens
        );
        let expected = chunk::create_sheet(
            format!("page-{:04}", job.page),
            &binding,
            job.sheet.units.clone(),
        )?;
        if expected != job.sheet
            || job
                .unit_ids
                .iter()
                .ne(job.sheet.units.iter().map(|unit| &unit.id))
        {
            return Err(PipelineError::Stale(path));
        }
    }
    Ok(plan)
}

fn workflow_lock(work: &Path) -> Result<revayat_core::BookLock, PipelineError> {
    Ok(revayat_core::BookLock::acquire(
        work.join("pages-workflow"),
    )?)
}

pub fn build(work: &Path, budget: ChunkBudget) -> Result<PagePlan, PipelineError> {
    let _guard = workflow_lock(work)?;
    let book = load_book(book_path(work))?;
    let path = manifest_path(work);
    if path
        .try_exists()
        .map_err(|err| crate::error::io_error("check", &path, err))?
    {
        let plan = load_plan(work, &book)?;
        if plan.budget != budget {
            return Err(PipelineError::Stale(path));
        }
        return Ok(plan);
    }
    let plan = make_plan(&book, budget)?;
    let mut state = RunState::load(work)?;
    for job in &plan.jobs {
        state.note_page_source(job.page, &job.source)?;
        chunk::write_sheet(&work.join(PAGES_DIR), &job.sheet)?;
    }
    state.save(work)?;
    storage::write_json(&path, &plan)?;
    Ok(plan)
}

fn page_subset(book: &BookDocument, page: u32) -> Result<BookDocument, PipelineError> {
    page_books(book)?
        .remove(&page)
        .ok_or_else(|| PipelineError::Workflow(format!("unknown page {page}")))
}

fn job(plan: &PagePlan, page: u32) -> Result<&PageJob, PipelineError> {
    plan.jobs
        .iter()
        .find(|job| job.page == page)
        .ok_or_else(|| PipelineError::Workflow(format!("unknown page {page}")))
}

fn record(state: &RunState, page: u32) -> Result<&PageRecord, PipelineError> {
    state
        .pages
        .get(&page)
        .ok_or_else(|| PipelineError::Workflow(format!("untracked page {page}")))
}

fn record_mut(state: &mut RunState, page: u32) -> Result<&mut PageRecord, PipelineError> {
    state
        .pages
        .get_mut(&page)
        .ok_or_else(|| PipelineError::Workflow(format!("untracked page {page}")))
}

fn require_translation(
    work: &Path,
    page: u32,
    record: &PageRecord,
    subset: &BookDocument,
) -> Result<(), PipelineError> {
    if record.translation.as_deref() != Some(page_hash(subset)?.as_str()) {
        return Err(PipelineError::Stale(book_path(work)));
    }
    if !matches!(
        record.state,
        PageState::Merged
            | PageState::Rendered
            | PageState::QaPassed
            | PageState::Accepted
            | PageState::Failed
    ) {
        return Err(PipelineError::Workflow(format!(
            "page {page} has no merged translation"
        )));
    }
    Ok(())
}

fn require_preview(path: &Path, record: &PageRecord) -> Result<String, PipelineError> {
    let rendered = sha256_file(path)?;
    if record.render.as_deref() != Some(rendered.as_str()) {
        return Err(PipelineError::Stale(path.into()));
    }
    Ok(rendered)
}

fn observe(
    work: &Path,
    book: &BookDocument,
    plan: &PagePlan,
    state: &RunState,
) -> Result<PagesStatus, PipelineError> {
    let subsets = page_books(book)?;
    let mut pages = BTreeMap::new();
    for job in &plan.jobs {
        let subset = subsets
            .get(&job.page)
            .ok_or_else(|| PipelineError::Stale(manifest_path(work)))?;
        let record = record(state, job.page)?;
        if record.source != job.source {
            return Err(PipelineError::Stale(manifest_path(work)));
        }
        let observed = if record.translation.is_none() {
            if page_hash(subset)? != job.source {
                PageState::Failed
            } else if job.sheet.units.is_empty() {
                PageState::Extracted
            } else {
                match chunk::read_reply(&work.join(PAGES_DIR), &job.sheet)
                    .and_then(|reply| merge::validate_reply(&job.sheet, &reply))
                {
                    Ok(()) => PageState::Translated,
                    Err(PipelineError::Missing(_)) => PageState::Extracted,
                    Err(_) => PageState::Failed,
                }
            }
        } else if require_translation(work, job.page, record, subset).is_err() {
            PageState::Failed
        } else if record.render.is_some()
            && require_preview(&preview_path(work, job.page), record).is_err()
        {
            PageState::Merged
        } else if let Some(sha) = &record.qa {
            let path = review_path(work, job.page);
            if sha256_file(&path).is_ok_and(|current| &current == sha) {
                record.state
            } else {
                PageState::Rendered
            }
        } else {
            record.state
        };
        pages.insert(job.page, observed);
    }
    Ok(PagesStatus {
        pages,
        signoff: check_signoff(state, &published_digest(book)?),
    })
}

pub(crate) fn check_signoff(state: &RunState, revision: &str) -> SignoffState {
    for stage in TranslationStage::ALL {
        if let Some(record) = state
            .stages
            .get(&stage)
            .filter(|record| record.revision == revision && record.status == StageStatus::Verified)
        {
            let current = record.artifact_path.as_ref().is_some_and(|path| {
                storage::read_json::<(PathBuf, String)>(path).is_ok_and(|(artifact, digest)| {
                    sha256_file(&artifact).is_ok_and(|actual| actual == digest)
                })
            });
            if !current {
                return SignoffState::Rejected(stage);
            }
        }
    }
    state.signoff(revision)
}

pub fn status(work: &Path) -> Result<PagesStatus, PipelineError> {
    let _guard = workflow_lock(work)?;
    let book = load_book(book_path(work))?;
    let plan = load_plan(work, &book)?;
    observe(work, &book, &plan, &RunState::load(work)?)
}

pub fn next(work: &Path) -> Result<Option<PageJob>, PipelineError> {
    let _guard = workflow_lock(work)?;
    let book = load_book(book_path(work))?;
    let plan = load_plan(work, &book)?;
    let current = observe(work, &book, &plan, &RunState::load(work)?)?;
    Ok(plan
        .jobs
        .into_iter()
        .find(|job| current.pages.get(&job.page) != Some(&PageState::Accepted)))
}

pub fn merge(work: &Path, page: u32) -> Result<MergeReport, PipelineError> {
    let _guard = workflow_lock(work)?;
    let mut state = RunState::load(work)?;
    let (report, translation) = storage::edit_book(&book_path(work), |book| {
        let plan = load_plan(work, book)?;
        let job = job(&plan, page)?;
        let subset = page_subset(book, page)?;
        if record(&state, page)?.source != job.source || page_hash(&subset)? != job.source {
            return Err(PipelineError::Stale(manifest_path(work)));
        }
        let answers = if job.sheet.units.is_empty() {
            Vec::new()
        } else {
            let reply = chunk::read_reply(&work.join(PAGES_DIR), &job.sheet)?;
            vec![(job.sheet.clone(), reply)]
        };
        let report = merge::apply_replies(book, answers)?;
        Ok((report, page_hash(&page_subset(book, page)?)?))
    })?;
    let entry = record_mut(&mut state, page)?;
    entry.translation = Some(translation);
    entry.render = None;
    entry.qa = None;
    entry.state = PageState::Merged;
    state.save(work)?;
    Ok(report)
}

pub fn preview(work: &Path, page: u32) -> Result<revayat_ooxml::BuildReport, PipelineError> {
    let _guard = workflow_lock(work)?;
    let book = load_book(book_path(work))?;
    let plan = load_plan(work, &book)?;
    job(&plan, page)?;
    let subset = page_subset(&book, page)?;
    let mut state = RunState::load(work)?;
    let entry = record_mut(&mut state, page)?;
    require_translation(work, page, entry, &subset)?;
    let destination = preview_path(work, page);
    let report = revayat_ooxml::build_docx(
        &subset,
        &destination,
        &revayat_ooxml::BuildOptions {
            assets_dir: work.join("assets"),
            toc: false,
            page_numbers: false,
            ..Default::default()
        },
    )?;
    entry.render = Some(report.sha256.clone());
    entry.qa = None;
    entry.state = PageState::Rendered;
    state.save(work)?;
    Ok(report)
}

pub fn review(work: &Path, page: u32, review: &VisualReview) -> Result<PageState, PipelineError> {
    let _guard = workflow_lock(work)?;
    let book = load_book(book_path(work))?;
    job(&load_plan(work, &book)?, page)?;
    let mut state = RunState::load(work)?;
    let entry = record_mut(&mut state, page)?;
    require_translation(work, page, entry, &page_subset(&book, page)?)?;
    let path = preview_path(work, page);
    if review.preview_sha256 != require_preview(&path, entry)? {
        return Err(PipelineError::Stale(path));
    }
    let path = review_path(work, page);
    storage::write_json(&path, review)?;
    entry.qa = Some(sha256_file(path)?);
    let result = if review.passes() {
        PageState::QaPassed
    } else {
        PageState::Failed
    };
    entry.state = result;
    state.save(work)?;
    Ok(result)
}

pub fn accept(work: &Path, page: u32) -> Result<PageState, PipelineError> {
    let _guard = workflow_lock(work)?;
    let book = load_book(book_path(work))?;
    job(&load_plan(work, &book)?, page)?;
    let mut state = RunState::load(work)?;
    let entry = record_mut(&mut state, page)?;
    require_translation(work, page, entry, &page_subset(&book, page)?)?;
    require_preview(&preview_path(work, page), entry)?;
    let path = review_path(work, page);
    let review: VisualReview = storage::read_json(&path)?;
    if entry.qa.as_deref() != Some(sha256_file(&path)?.as_str())
        || entry.render.as_deref() != Some(review.preview_sha256.as_str())
    {
        return Err(PipelineError::Stale(path));
    }
    if !review.passes() || !matches!(entry.state, PageState::QaPassed | PageState::Accepted) {
        return Err(PipelineError::Workflow(format!(
            "page {page} has not passed review"
        )));
    }
    entry.state = PageState::Accepted;
    state.save(work)?;
    Ok(PageState::Accepted)
}

pub fn record_stage(
    work: &Path,
    stage: TranslationStage,
    artifact: &Path,
    verified_findings: usize,
) -> Result<(), PipelineError> {
    let _guard = workflow_lock(work)?;
    let book = load_book(book_path(work))?;
    let revision = published_digest(&book)?;
    let mut state = RunState::load(work)?;
    let path = artifact
        .canonicalize()
        .map_err(|error| crate::error::io_error("resolve stage evidence", artifact, error))?;
    let digest = sha256_file(&path)?;
    let stage_file = work.join(PAGES_DIR).join(format!(
        "stage-{}.json",
        serde_json::to_value(stage)?.as_str().unwrap_or("invalid")
    ));
    for previous in TranslationStage::ALL
        .into_iter()
        .take_while(|previous| *previous != stage)
    {
        if !state.stages.get(&previous).is_some_and(|record| {
            record.revision == revision && record.status == StageStatus::Verified
        }) {
            return Err(PipelineError::Workflow(format!(
                "stage {previous:?} must be verified first"
            )));
        }
    }
    storage::write_json(&stage_file, &(path, digest))?;
    state.stages.retain(|key, _| *key <= stage);
    state.record(
        stage,
        StageStatus::Verified,
        Some(stage_file),
        &revision,
        verified_findings,
    )?;
    state.save(work)?;
    Ok(())
}

use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Path, PathBuf},
};

use revayat_core::{BookDocument, Finding, Severity, load_book, published_digest, sha256_bytes};
use serde::{Deserialize, Serialize};

use crate::{
    PipelineError, chunk, merge, pages, storage,
    units::{check_preservation, source_units},
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EditorialStatus {
    Pending,
    Accepted,
    ChangesRequested,
    Applied,
    Stale,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditorialUnit {
    pub id: String,
    pub source: Option<String>,
    pub target: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditorialManifest {
    pub revision: String,
    pub request: String,
    pub units: Vec<EditorialUnit>,
    pub locked_terms: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FluencyEdit {
    pub id: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct FluencyRecord {
    request: String,
    edits: Vec<FluencyEdit>,
    applied_revision: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct MeaningRecord {
    request: String,
    findings: Vec<Finding>,
    reviewed: BTreeSet<String>,
}

fn book_path(work: &Path) -> PathBuf {
    work.join(revayat_core::BOOK_FILENAME)
}
fn manifest_path(work: &Path, stage: &str) -> PathBuf {
    work.join(stage).join("manifest.json")
}
fn reply_path(work: &Path, stage: &str) -> PathBuf {
    work.join(stage).join("review.json")
}

pub(crate) fn original_units(work: &Path) -> Result<BTreeMap<String, String>, PipelineError> {
    let from_chunks = storage::read_json::<chunk::WorksheetManifest>(
        &work.join(chunk::CHUNKS_DIR).join(chunk::MANIFEST_FILENAME),
    );
    let sheets = match from_chunks {
        Ok(manifest) => manifest.sheets,
        Err(PipelineError::Missing(_)) => match storage::read_json::<pages::PagePlan>(
            &work.join("pages").join(chunk::MANIFEST_FILENAME),
        ) {
            Ok(plan) => plan.jobs.into_iter().map(|job| job.sheet).collect(),
            Err(PipelineError::Missing(_)) => return Ok(BTreeMap::new()),
            Err(error) => return Err(error),
        },
        Err(error) => return Err(error),
    };
    let mut groups = BTreeMap::<String, Vec<(usize, String)>>::new();
    for unit in sheets.into_iter().flat_map(|sheet| sheet.units) {
        let owner = revayat_core::base_of(&unit.id);
        groups
            .entry(owner.into())
            .or_default()
            .push((revayat_core::index_of(&unit.id), unit.text));
    }
    Ok(groups
        .into_iter()
        .map(|(id, mut parts)| {
            parts.sort_by_key(|part| part.0);
            let mut spans = Vec::new();
            for (_, part) in parts {
                spans.extend(revayat_core::parse_markup(&part));
            }
            (id, revayat_core::render_spans(&spans))
        })
        .collect())
}

fn generate(
    work: &Path,
    book: &BookDocument,
    stage: &str,
    terms: &[String],
) -> Result<EditorialManifest, PipelineError> {
    let revision = published_digest(book)?;
    let originals = if stage == "meaning" {
        original_units(work)?
    } else {
        BTreeMap::new()
    };
    let units = source_units(book)?
        .into_iter()
        .filter(|unit| unit.eligible())
        .map(|unit| EditorialUnit {
            source: originals.get(&unit.id).cloned(),
            id: unit.id,
            target: unit.text,
        })
        .collect::<Vec<_>>();
    let locked_terms = terms
        .iter()
        .map(|term| term.trim())
        .filter(|term| !term.is_empty())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let request = format!(
        "editorial1:{}",
        sha256_bytes(&serde_json::to_vec(&(
            stage,
            &revision,
            &units,
            &locked_terms
        ))?)
    );
    Ok(EditorialManifest {
        revision,
        request,
        units,
        locked_terms,
    })
}

fn sheets_for(
    work: &Path,
    stage: &str,
    terms: &[String],
) -> Result<EditorialManifest, PipelineError> {
    let path = manifest_path(work, stage);
    storage::create_dir(&work.join(stage))?;
    let _lock = revayat_core::BookLock::acquire(&path)?;
    let manifest = generate(work, &load_book(book_path(work))?, stage, terms)?;
    // The complete request remains a single typed file; the numbered files are review-sized views.
    for (i, units) in manifest.units.chunks(20).enumerate() {
        storage::write_json(
            &work.join(stage).join(format!("sheet-{:04}.json", i + 1)),
            &(&manifest.request, units, &manifest.locked_terms),
        )?;
    }
    storage::write_json(&path, &manifest)?;
    Ok(manifest)
}

fn current(
    work: &Path,
    book: &BookDocument,
    stage: &str,
) -> Result<EditorialManifest, PipelineError> {
    let path = manifest_path(work, stage);
    let manifest: EditorialManifest = storage::read_json(&path)?;
    if manifest != generate(work, book, stage, &manifest.locked_terms)? {
        return Err(PipelineError::Stale(path));
    }
    Ok(manifest)
}

fn edits_for(
    manifest: &EditorialManifest,
    edits: &[FluencyEdit],
) -> Result<BTreeMap<String, String>, PipelineError> {
    let units: BTreeMap<_, _> = manifest
        .units
        .iter()
        .map(|unit| (unit.id.as_str(), &unit.target))
        .collect();
    let mut result = BTreeMap::new();
    for edit in edits {
        let Some(before) = units.get(edit.id.as_str()) else {
            return Err(PipelineError::Workflow(format!(
                "unknown editorial unit {}",
                edit.id
            )));
        };
        check_preservation(&edit.id, before, &edit.text)?;
        if revayat_core::footnote_refs_with_local(before, true)
            != revayat_core::footnote_refs_with_local(&edit.text, true)
        {
            return Err(PipelineError::Workflow(format!(
                "footnote markup changed in {}",
                edit.id
            )));
        }
        let old = revayat_core::plain_text(before);
        let new = revayat_core::plain_text(&edit.text);
        for term in &manifest.locked_terms {
            if old.matches(term).count() != new.matches(term).count() {
                return Err(PipelineError::Workflow(format!(
                    "locked term {term} changed in {}",
                    edit.id
                )));
            }
        }
        if result.insert(edit.id.clone(), edit.text.clone()).is_some() {
            return Err(PipelineError::Duplicate(edit.id.clone()));
        }
    }
    Ok(result)
}

pub mod fluency {
    use super::*;
    const STAGE: &str = "fluency";

    pub fn sheets(
        work: &Path,
        locked_terms: &[String],
    ) -> Result<EditorialManifest, PipelineError> {
        sheets_for(work, STAGE, locked_terms)
    }

    pub fn record(
        work: &Path,
        request: &str,
        edits: Vec<FluencyEdit>,
    ) -> Result<EditorialStatus, PipelineError> {
        let _lock = revayat_core::BookLock::acquire(manifest_path(work, STAGE))?;
        let manifest = current(work, &load_book(book_path(work))?, STAGE)?;
        if manifest.request != request {
            return Err(PipelineError::Stale(manifest_path(work, STAGE)));
        }
        edits_for(&manifest, &edits)?;
        let result = if edits.is_empty() {
            EditorialStatus::Accepted
        } else {
            EditorialStatus::ChangesRequested
        };
        storage::write_json(
            &reply_path(work, STAGE),
            &FluencyRecord {
                request: request.into(),
                edits,
                applied_revision: None,
            },
        )?;
        Ok(result)
    }

    pub fn apply(work: &Path) -> Result<merge::MergeReport, PipelineError> {
        let _lock = revayat_core::BookLock::acquire(manifest_path(work, STAGE))?;
        let path = reply_path(work, STAGE);
        let mut record: FluencyRecord = storage::read_json(&path)?;
        if record.applied_revision.is_some() || record.edits.is_empty() {
            return Err(PipelineError::Workflow("no unapplied fluency edits".into()));
        }
        let (report, revision) = storage::edit_book(&book_path(work), |book| {
            let manifest = current(work, book, STAGE)?;
            if manifest.request != record.request {
                return Err(PipelineError::Stale(path.clone()));
            }
            let report = merge::apply_units(book, &edits_for(&manifest, &record.edits)?)?;
            Ok((report, published_digest(book)?))
        })?;
        record.applied_revision = Some(revision);
        storage::write_json(&path, &record)?;
        Ok(report)
    }

    pub fn status(work: &Path) -> Result<EditorialStatus, PipelineError> {
        let path = reply_path(work, STAGE);
        let record: FluencyRecord = match storage::read_json(&path) {
            Ok(record) => record,
            Err(PipelineError::Missing(_)) => return Ok(EditorialStatus::Pending),
            Err(error) => return Err(error),
        };
        let book = load_book(book_path(work))?;
        if let Some(revision) = record.applied_revision {
            return Ok(if published_digest(&book)? == revision {
                EditorialStatus::Applied
            } else {
                EditorialStatus::Stale
            });
        }
        match current(work, &book, STAGE) {
            Ok(manifest) if manifest.request == record.request => {
                edits_for(&manifest, &record.edits)?;
                Ok(if record.edits.is_empty() {
                    EditorialStatus::Accepted
                } else {
                    EditorialStatus::ChangesRequested
                })
            }
            Ok(_) | Err(PipelineError::Stale(_)) => Ok(EditorialStatus::Stale),
            Err(error) => Err(error),
        }
    }
}

pub mod meaning {
    use super::*;
    const STAGE: &str = "meaning";

    pub fn sheets(work: &Path) -> Result<EditorialManifest, PipelineError> {
        sheets_for(work, STAGE, &[])
    }

    pub fn record(
        work: &Path,
        request: &str,
        findings: Vec<Finding>,
        reviewed: BTreeSet<String>,
    ) -> Result<EditorialStatus, PipelineError> {
        let _lock = revayat_core::BookLock::acquire(manifest_path(work, STAGE))?;
        let manifest = current(work, &load_book(book_path(work))?, STAGE)?;
        if manifest.request != request {
            return Err(PipelineError::Stale(manifest_path(work, STAGE)));
        }
        let record = MeaningRecord {
            request: request.into(),
            findings,
            reviewed,
        };
        let verdict = checked(&manifest, &record)?;
        storage::write_json(&reply_path(work, STAGE), &record)?;
        Ok(verdict)
    }

    fn checked(
        manifest: &EditorialManifest,
        record: &MeaningRecord,
    ) -> Result<EditorialStatus, PipelineError> {
        let expected: BTreeSet<_> = manifest.units.iter().map(|unit| &unit.id).collect();
        if manifest.units.iter().any(|unit| unit.source.is_none()) {
            return Err(PipelineError::Workflow(
                "meaning review requires the original source worksheets".into(),
            ));
        }
        if record.reviewed.iter().ne(expected)
            || record.findings.iter().any(|finding| {
                !record.reviewed.contains(&finding.unit) || finding.detail.trim().is_empty()
            })
        {
            return Err(PipelineError::Workflow(
                "meaning review must account for each current unit".into(),
            ));
        }
        Ok(
            if record
                .findings
                .iter()
                .any(|finding| finding.severity == Severity::Error)
            {
                EditorialStatus::ChangesRequested
            } else {
                EditorialStatus::Accepted
            },
        )
    }

    pub fn status(work: &Path) -> Result<EditorialStatus, PipelineError> {
        let record: MeaningRecord = match storage::read_json(&reply_path(work, STAGE)) {
            Ok(record) => record,
            Err(PipelineError::Missing(_)) => return Ok(EditorialStatus::Pending),
            Err(error) => return Err(error),
        };
        match current(work, &load_book(book_path(work))?, STAGE) {
            Ok(manifest) if manifest.request == record.request => checked(&manifest, &record),
            Ok(_) | Err(PipelineError::Stale(_)) => Ok(EditorialStatus::Stale),
            Err(error) => Err(error),
        }
    }
}

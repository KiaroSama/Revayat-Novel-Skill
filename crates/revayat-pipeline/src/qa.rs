use std::path::Path;

use revayat_core::{
    BookDocument, Report, RunState, Severity, SignoffState, has_persian, load_book,
    published_digest, sha256_file,
};
use serde::Serialize;

use crate::{
    PipelineError,
    editorial::original_units,
    pages::check_signoff,
    units::{check_preservation, source_units},
};

#[derive(Debug, Clone, Serialize)]
pub struct QaReport {
    pub report: Report,
    pub signoff: Option<SignoffState>,
}

impl QaReport {
    pub fn ok(&self) -> bool {
        self.report
            .findings
            .iter()
            .all(|finding| finding.severity != Severity::Error)
    }
}

pub fn qa_check(book: &BookDocument) -> QaReport {
    let mut report = Report::default();
    if let Err(error) = book.validate() {
        report.add(Severity::Error, "invalid-book", "book", error.to_string());
    }
    report.findings.extend(revayat_core::check_note_graph(book));
    report.count("chapters", book.chapters.len());
    report.count("assets", book.assets.len());
    if book.chapters.is_empty() {
        report.add(
            Severity::Error,
            "empty-book",
            "book",
            "No chapters to deliver",
        );
    }
    for asset in &book.assets {
        if !asset.original_bytes_preserved {
            report.add(
                Severity::Error,
                "asset-not-preserved",
                &asset.id,
                "The record does not attest to the original bytes",
            );
        }
    }
    match source_units(book) {
        Ok(units) => {
            report.count("units", units.len());
            if !units
                .iter()
                .any(|unit| unit.eligible() && has_persian(&unit.text))
            {
                report.add(
                    Severity::Error,
                    "untranslated-book",
                    "book",
                    "No Persian prose was found",
                );
            }
            for unit in units {
                let prose = revayat_core::prose(&unit.text);
                if unit.kind == revayat_core::PublishedKind::Title && prose.trim().is_empty() {
                    report.add(
                        Severity::Error,
                        "missing-title",
                        &unit.id,
                        "The book title is empty",
                    );
                } else if unit.eligible()
                    && !has_persian(&prose)
                    && prose.split_whitespace().count() >= 2
                {
                    report.add(
                        Severity::Error,
                        "untranslated-unit",
                        &unit.id,
                        "Text has no Persian letters",
                    );
                }
                for mut finding in revayat_core::check_bidi_isolation(&prose) {
                    finding.unit = unit.id.clone();
                    report.findings.push(finding);
                }
            }
        }
        Err(error) => report.add(Severity::Error, "invalid-units", "book", error.to_string()),
    }
    QaReport {
        report,
        signoff: None,
    }
}

pub fn qa_work_dir(work: &Path) -> Result<QaReport, PipelineError> {
    let book = load_book(work.join(revayat_core::BOOK_FILENAME))?;
    let mut qa = qa_check(&book);
    let originals = original_units(work)?;
    let live = source_units(&book)?;
    if originals.is_empty() {
        qa.report.add(
            Severity::Error,
            "unknown-source-coverage",
            "book",
            "No source worksheets are available to check coverage",
        );
    }
    for (id, before) in originals {
        let Some(after) = live.iter().find(|unit| unit.id == id) else {
            qa.report.add(
                Severity::Error,
                "missing-unit",
                id,
                "The source unit is no longer in the book",
            );
            continue;
        };
        if let Err(error) = check_preservation(&id, &before, &after.text) {
            qa.report.add(
                Severity::Error,
                "translation-structure",
                &id,
                error.to_string(),
            );
        }
        if before == after.text && !has_persian(&revayat_core::prose(&before)) {
            qa.report.add(
                Severity::Error,
                "copied-source",
                &id,
                "The translation exactly matches non-Persian source text",
            );
        }
    }
    for asset in &book.assets {
        let path = work.join("assets").join(&asset.rel_path);
        if !sha256_file(&path).is_ok_and(|hash| hash == asset.sha256) {
            qa.report.add(
                Severity::Error,
                "asset-byte-mismatch",
                &asset.id,
                "Original asset bytes are missing or differ",
            );
        }
    }
    let signoff = check_signoff(&RunState::load(work)?, &published_digest(&book)?);
    if signoff != SignoffState::Approved {
        qa.report.add(
            Severity::Error,
            "incomplete-signoff",
            "book",
            format!("{signoff:?}"),
        );
    }
    qa.signoff = Some(signoff);
    Ok(qa)
}

use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
};

use revayat_core::*;

#[test]
fn signoff_requires_all_stages_for_current_revision() -> Result<(), Box<dyn std::error::Error>> {
    let dir = tempfile::tempdir()?;
    let mut state = RunState::default();
    let revision = format!("pub3:{}", sha256_bytes(b"book"));
    for stage in TranslationStage::ALL {
        state.record(
            stage,
            StageStatus::Verified,
            Some("artifact.json".into()),
            &revision,
            1,
        )?;
    }
    assert_eq!(state.signoff(&revision), SignoffState::Approved);
    assert_eq!(
        state.signoff("changed"),
        SignoffState::Pending(TranslationStage::InitialTranslation)
    );
    state.save(dir.path())?;
    assert_eq!(RunState::load(dir.path())?, state);
    state.record(
        TranslationStage::IndependentReview,
        StageStatus::Failed,
        None,
        &revision,
        0,
    )?;
    assert_eq!(
        state.signoff(&revision),
        SignoffState::Rejected(TranslationStage::IndependentReview)
    );
    Ok(())
}

#[test]
fn repair_budget_reports_no_change_and_oscillation_before_exhaustion() {
    let mut repairs = RepairLedger::default();
    assert_eq!(
        repairs.record_attempt("p1/meaning", "src", "first", "argument 1", "review 1"),
        None
    );
    assert_eq!(
        repairs.record_attempt("p1/meaning", "src", "first", "argument 2", "review 2"),
        Some(RepairRefusal::NoNewEvidence)
    );
    repairs.close_absent(&BTreeSet::new());
    assert!(repairs.blocked().is_empty());
    assert_eq!(
        repairs.record_attempt("p1/meaning", "src", "second", "argument 3", "review 3"),
        None
    );
    assert_eq!(
        repairs.record_attempt("p1/meaning", "src", "third", "argument 4", "review 4"),
        None
    );
    assert_eq!(
        repairs.record_attempt("p1/meaning", "src", "second", "argument 5", "review 5"),
        Some(RepairRefusal::Oscillating)
    );
    assert_eq!(
        repairs.record_attempt("p1/meaning", "new src", "first", "argument 6", "review 6"),
        None
    );
}

#[test]
fn files_use_fresh_log_names_and_utc_format() -> Result<(), Box<dyn std::error::Error>> {
    let dir = tempfile::tempdir()?;
    let mut a = RunLog::create(dir.path(), "qa")?;
    let b = RunLog::create(dir.path(), "qa")?;
    assert_ne!(a.path(), b.path());
    a.stage("qa")?;
    let line = fs::read_to_string(a.path())?;
    assert!(line.ends_with(" UTC] [INFO] [qa] stage stage=qa\n"));
    assert_eq!(line.lines().count(), 1);
    assert_eq!(
        format_log_line(
            chrono::DateTime::from_timestamp(0, 0).ok_or("epoch unavailable")?,
            LogLevel::Error,
            "qa",
            "line\n2"
        ),
        "[1970-01-01 00:00:00 UTC] [ERROR] [qa] line 2\n"
    );
    Ok(())
}

#[test]
fn segment_partition_preserves_markup_and_source_newlines() -> Result<(), CoreError> {
    let text = "abc **عبارت فارسی**。中文！ End";
    let parts = split_text(text, 5);
    assert_eq!(parts.concat(), text);
    assert!(parts.iter().any(|part| part.contains("**عبارت فارسی**")));
    let records = [
        SegmentRecord {
            id: "p1#1".into(),
            offset: 0,
            length: 3,
        },
        SegmentRecord {
            id: "p1#2".into(),
            offset: 3,
            length: 2,
        },
    ];
    let separators = source_separators("اب\nپت", &records)?;
    let translation = BTreeMap::from([
        ("p1#2".into(), " Two ".into()),
        ("p1#1".into(), " One ".into()),
    ]);
    assert_eq!(rejoin(&translation, Some(&separators))["p1"], "One\nTwo");
    assert!(source_separators("short?", &records).is_err());
    Ok(())
}

#[test]
fn publication_and_eligibility_account_for_real_text() -> Result<(), CoreError> {
    let mut book = BookDocument::new(SourceFormat::Text, sha256_bytes(b"source"), "Test", "en");
    let old = published_digest(&book)?;
    book.title = "Updated".into();
    assert_ne!(published_digest(&book)?, old);
    assert_eq!(published_units(&book)[0].text, "Updated");
    let before_layout = published_digest(&book)?;
    book.page_geometry.margin_outer_twips += 20;
    assert_ne!(published_digest(&book)?, before_layout);
    assert_eq!(
        translation_eligibility("`literal`", None, None),
        TranslationEligibility::VerbatimOnly
    );
    assert_eq!(
        translation_eligibility("actual words", None, Some("wrong")),
        TranslationEligibility::StaleSource
    );
    assert_eq!(
        translation_eligibility("actual words", None, None),
        TranslationEligibility::Eligible
    );
    assert_eq!(
        translation_eligibility("actual words", Some("translated"), None),
        TranslationEligibility::AlreadyTranslated
    );
    Ok(())
}

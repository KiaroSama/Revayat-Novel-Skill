use revayat_eval::{Verdict, adjudicate, grade, grade_one, load_cases, normalise};
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};

#[test]
fn all_28_cases_pass_accepted_controls_and_fail_wrong_controls()
-> Result<(), Box<dyn std::error::Error>> {
    let benchmark = load_cases(None)?;
    assert_eq!(benchmark.cases.len(), 28);
    let mut ids = BTreeSet::new();
    for case in &benchmark.cases {
        assert!(ids.insert(&case.id), "duplicate ID: {}", case.id);
        assert!(!case.rationale.trim().is_empty(), "{}", case.id);
        let mut accepted = BTreeSet::new();
        for answer in &case.accepts {
            assert!(accepted.insert(normalise(answer)), "{}", case.id);
            assert_eq!(
                grade_one(case, answer).verdict,
                Verdict::Pass,
                "{}",
                case.id
            );
        }
        assert!(!accepted.is_empty(), "{}", case.id);
        assert!(!case.fails.is_empty(), "{}", case.id);
        for wrong in &case.fails {
            let result = grade_one(case, &wrong.text);
            assert_eq!(result.verdict, Verdict::Fail, "{}", case.id);
            assert_eq!(result.note, wrong.why, "{}", case.id);
        }
    }
    Ok(())
}

#[test]
fn missing_answers_are_unknown_without_an_overall_score() -> Result<(), Box<dyn std::error::Error>>
{
    let benchmark = load_cases(None)?;
    let report = grade(&benchmark, &BTreeMap::new());
    assert_eq!(report.cases.len(), 28);
    assert!(
        report
            .cases
            .iter()
            .all(|row| row.verdict == Verdict::Unknown)
    );
    assert_eq!(
        report.by_axis.values().map(|t| t.unknown).sum::<usize>(),
        28
    );
    assert!(!report.has_failures());
    assert_eq!(serde_json::to_value(report)?["overall"], json!(null));
    Ok(())
}

#[test]
fn prepared_adjudication_is_pending_and_bound_to_original_bytes()
-> Result<(), Box<dyn std::error::Error>> {
    let mut matrix = adjudicate::prepare(None)?;
    assert_eq!(matrix["cases"].as_array().map(Vec::len), Some(28));
    adjudicate::validate(&matrix, None)?;
    matrix["cases"][0]["status"] = json!("accept");
    assert!(adjudicate::validate(&matrix, None).is_err());
    Ok(())
}

#[test]
fn adjudication_rejects_duplicate_keys_at_any_depth() {
    let result = adjudicate::parse_object(br#"{"outer":{"same":true,"same":false}}"#);
    assert!(
        matches!(result, Err(adjudicate::Error::Invalid(message)) if message == "Duplicate review JSON key")
    );
}

use std::{collections::BTreeMap, error::Error, fs, path::Path};

use revayat_core::*;
use serde_json::{Value, json};

fn replay(case: &Value) -> Option<Value> {
    let args = case["args"].as_array()?;
    let arg = |index: usize| args.get(index);
    let string = |index: usize| arg(index)?.as_str();
    let flag = |name: &str| case["kwargs"][name].as_bool().unwrap_or(false);
    case.get("ok")?;
    Some(match case["fn"].as_str()? {
        "sha256_bytes" => json!(sha256_bytes(
            &serde_json::from_value::<Vec<u8>>(arg(0)?["$bytes"].clone()).ok()?
        )),
        "normalise_source" => json!(normalise_source(string(0)?)),
        "escape_markup" => json!(escape_markup(string(0)?)),
        "parse_markup" => json!(parse_markup(string(0)?)),
        "verbatim_spans" => json!(verbatim_spans(string(0)?)),
        "plain_text" => json!(plain_text(string(0)?)),
        "footnote_refs" => json!(footnote_refs_with_local(string(0)?, flag("include_local"))),
        "rewrite_footnote_refs" => json!(rewrite_footnote_refs(
            string(0)?,
            &serde_json::from_value(arg(1)?.clone()).ok()?
        )),
        "emphasis_signature" => json!({"$tuple": emphasis_signature(string(0)?)}),
        "segment_id" => json!(segment_id(string(0)?, arg(1)?.as_i64()?)),
        "base_of" => json!(base_of(string(0)?)),
        "index_of" => json!(index_of(string(0)?)),
        "cut_points" => json!(cut_points(string(0)?)),
        "split_text" => json!(split_text(string(0)?, arg(1)?.as_i64()?)),
        "owners" => json!(owners(
            arg(0)?
                .as_array()?
                .iter()
                .map(Value::as_str)
                .collect::<Option<Vec<_>>>()?
        )),
        "segments_by_owner" => json!(segments_by_owner(
            arg(0)?
                .as_array()?
                .iter()
                .map(Value::as_str)
                .collect::<Option<Vec<_>>>()?
        )),
        "source_separators" => json!(
            source_separators(
                string(0)?,
                &serde_json::from_value::<Vec<SegmentRecord>>(arg(1)?.clone()).ok()?
            )
            .ok()?
        ),
        "coverage_problems" => {
            let known: BTreeMap<String, std::collections::BTreeSet<usize>> =
                serde_json::from_value(arg(0)?.clone()).ok()?;
            let known = known
                .iter()
                .map(|(id, indices)| (id.as_str(), indices.clone()))
                .collect();
            json!(coverage_problems(
                &known,
                arg(1)?
                    .as_array()?
                    .iter()
                    .map(Value::as_str)
                    .collect::<Option<Vec<_>>>()?
            ))
        }
        "rejoin" => {
            let units: BTreeMap<String, String> = serde_json::from_value(arg(0)?.clone()).ok()?;
            let separators: Option<BTreeMap<String, String>> =
                serde_json::from_value(case["kwargs"]["separators"].clone()).ok()?;
            json!(rejoin(&units, separators.as_ref()))
        }
        "introduction_count" => json!(introduction_count(string(0)?, string(1)?)),
        "introduction_spans" | "placement_spans" => {
            let spans = if case["fn"] == "introduction_spans" {
                introduction_spans(string(0)?, string(1)?)
            } else {
                placement_spans(string(0)?, string(1)?)
            };
            json!(
                spans
                    .into_iter()
                    .map(|span| json!({"$tuple": span}))
                    .collect::<Vec<_>>()
            )
        }
        "flatten_in_prose" => {
            json!({"$tuple": flatten_in_prose(string(0)?, string(1)?, string(2)?, flag("keep_first"))})
        }
        "introduce_in_prose" => json!(introduce_in_prose(string(0)?, string(1)?, string(2)?)),
        _ => return None,
    })
}

fn check_cases(input: &str) -> Result<(usize, usize), Box<dyn Error>> {
    let mut failures = Vec::new();
    let mut matched = 0;
    let mut skipped = 0;
    for (index, line) in input.lines().enumerate() {
        let case: Value = serde_json::from_str(line)?;
        if let Some(actual) = replay(&case) {
            if actual == case["ok"] {
                matched += 1;
            } else {
                failures.push(format!(
                    "line {} {}: expected {}, got {}",
                    index + 1,
                    case["fn"],
                    case["ok"],
                    actual
                ));
            }
        } else {
            skipped += 1;
        }
    }
    assert!(
        failures.is_empty(),
        "{matched} matched, {} mismatches, {skipped} outside typed replay: {:?}",
        failures.len(),
        &failures[..failures.len().min(8)]
    );
    Ok((matched, skipped))
}

#[test]
fn recorded_examples_match_the_typed_api() -> Result<(), Box<dyn Error>> {
    let golden = include_str!("reference_cases.jsonl");
    assert_eq!(check_cases(golden)?, (golden.lines().count(), 0));
    Ok(())
}

#[test]
#[ignore = "Requires the original rust-anything recordings; set REVAYAT_RECORDINGS to the rust directory"]
fn replay_available_recordings() -> Result<(), Box<dyn Error>> {
    let root = std::env::var_os("REVAYAT_RECORDINGS").ok_or("REVAYAT_RECORDINGS is required")?;
    for unit in ["bookir", "markup", "segments", "naming"] {
        let path = Path::new(&root)
            .join(format!("core-{unit}-rs"))
            .join("cases.jsonl");
        let (matched, skipped) = check_cases(&fs::read_to_string(path)?)?;
        assert!(matched > 0, "{unit} did not exercise the typed API");
        println!("{unit}: {matched} matched, {skipped} outside typed replay");
    }
    Ok(())
}

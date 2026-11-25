use revayat_persian::{falint, famorph, glossary};
use serde_json::{Value, json};
use std::io;

const MORPH: &str = include_str!("fixtures/famorph.jsonl");
const TYPOGRAPHY: &str = include_str!("fixtures/falint.jsonl");
const GLOSSARY: &str = include_str!("fixtures/glossary.jsonl");

fn rows(source: &str) -> impl Iterator<Item = Result<Value, serde_json::Error>> {
    source
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(serde_json::from_str)
}

fn string(value: &Value) -> Result<&str, io::Error> {
    value.as_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("expected string, got {value}"),
        )
    })
}

#[test]
fn morphology_matches_all_468_recorded_cases() -> Result<(), Box<dyn std::error::Error>> {
    let mut checked = 0;
    for row in rows(MORPH) {
        let row = row?;
        let word = string(&row["args"][0])?;
        let actual = match string(&row["fn"])? {
            "is_verb_form" => famorph::is_verb_form(word),
            "takes_comparative" => {
                famorph::takes_comparative(word, row["args"][1].as_str().unwrap_or("تری"))
            }
            other => {
                return Err(
                    io::Error::other(format!("unrecognized morphology case: {other}")).into(),
                );
            }
        };
        assert_eq!(json!(actual), row["ok"], "{row}");
        checked += 1;
    }
    assert_eq!(checked, 468);
    Ok(())
}

#[test]
fn publication_and_glossary_match_recorded_text_contracts() -> Result<(), Box<dyn std::error::Error>>
{
    let mut checked = 0;
    let mut mismatches = Vec::new();
    for row in rows(TYPOGRAPHY).chain(rows(GLOSSARY)) {
        let row = row?;
        if row.get("ok").is_none() {
            continue;
        }
        let args = &row["args"];
        let actual = match string(&row["fn"])? {
            "mask_literals" => {
                let (masked, keep) = falint::mask_literals(string(&args[0])?)?;
                json!({"$tuple": [masked, keep]})
            }
            "blank_protected" => json!(falint::blank_protected(
                string(&args[0])?,
                row["kwargs"]["latin"].as_bool().unwrap_or(false)
            )?),
            "fix_text" if args[1].is_null() || args[1]["$undefined"] == true => json!(
                falint::fix_text(string(&args[0])?, &falint::Options::default())?
            ),
            "lint_text" => serde_json::to_value(falint::lint_text(string(&args[0])?)?)?,
            "standalone_spans" => {
                let text = string(&args[0])?;
                json!(glossary::standalone_spans(text, string(&args[1])?).into_iter().map(|range| json!({"$tuple":[text[..range.start].chars().count(), text[..range.end].chars().count()]})).collect::<Vec<_>>())
            }
            "mentioned_in_prose" => json!(glossary::mentioned_in_prose(
                string(&args[0])?,
                string(&args[1])?
            )?),
            "prose_of" => json!(glossary::prose_of(string(&args[0])?)?),
            _ => continue,
        };
        checked += 1;
        if actual != row["ok"] {
            mismatches.push(format!("{row}; actual={actual}"));
        }
    }
    assert!(checked > 1450, "only {checked} applicable oracle cases");
    assert!(
        mismatches.is_empty(),
        "{}/{} mismatched: {:#?}",
        mismatches.len(),
        checked,
        &mismatches[..mismatches.len().min(15)]
    );
    Ok(())
}

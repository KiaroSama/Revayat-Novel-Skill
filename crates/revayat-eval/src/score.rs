use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::{fs, io, path::Path};
use unicode_normalization::UnicodeNormalization;

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("could not read benchmark: {0}")]
    Io(#[from] io::Error),
    #[error("invalid benchmark JSON: {0}")]
    Json(#[from] serde_json::Error),
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq, Serialize)]
pub struct Benchmark {
    pub schema: String,
    pub cases: Vec<EvalCase>,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq, Serialize)]
pub struct EvalCase {
    pub id: String,
    pub axis: String,
    #[serde(default)]
    pub source: String,
    #[serde(default = "english")]
    pub source_language: String,
    #[serde(default)]
    pub rationale: String,
    pub accepts: Vec<String>,
    #[serde(default)]
    pub fails: Vec<WrongControl>,
}

fn english() -> String {
    "en".to_owned()
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq, Serialize)]
pub struct WrongControl {
    pub text: String,
    pub why: String,
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Verdict {
    Pass,
    Fail,
    Unknown,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq, Serialize)]
pub struct EvalScore {
    pub id: String,
    pub axis: String,
    pub verdict: Verdict,
    pub note: String,
}

#[derive(Debug, Clone, Default, Deserialize, PartialEq, Eq, Serialize)]
pub struct AxisTally {
    pub pass: usize,
    pub fail: usize,
    pub unknown: usize,
}

#[derive(Debug, Clone, Deserialize, PartialEq, Eq, Serialize)]
pub struct ScoreReport {
    pub schema: String,
    pub cases: Vec<EvalScore>,
    pub by_axis: BTreeMap<String, AxisTally>,
    pub overall: (),
    pub overall_note: String,
}

impl ScoreReport {
    pub fn has_failures(&self) -> bool {
        self.cases.iter().any(|row| row.verdict == Verdict::Fail)
    }
}

pub fn load_cases(path: Option<&Path>) -> Result<Benchmark, Error> {
    match path {
        Some(path) => Ok(serde_json::from_slice(&fs::read(path)?)?),
        None => Ok(serde_json::from_str(super::CASES)?),
    }
}

pub fn normalise(text: &str) -> String {
    let mut output = String::new();
    let mut gap = false;
    for c in text.nfkc() {
        if c.is_whitespace()
            || matches!(c, '\u{1c}'..='\u{1f}')
            || "‌،,;:.!?…«»\"'()[]\u{2014}\u{2013}-".contains(c)
        {
            gap = !output.is_empty();
        } else {
            if gap {
                output.push(' ');
                gap = false;
            }
            output.push(match c {
                'ي' => 'ی',
                'ك' => 'ک',
                _ => c,
            });
        }
    }
    output
}

pub fn grade_one(case: &EvalCase, answer: &str) -> EvalScore {
    let answer = normalise(answer);
    let (verdict, note) = if answer.is_empty() {
        (Verdict::Unknown, "no answer given")
    } else if case
        .accepts
        .iter()
        .any(|accepted| normalise(accepted) == answer)
    {
        (Verdict::Pass, "matches an accepted rendering")
    } else if let Some(wrong) = case
        .fails
        .iter()
        .find(|wrong| normalise(&wrong.text) == answer)
    {
        (Verdict::Fail, wrong.why.as_str())
    } else {
        (
            Verdict::Unknown,
            "a rendering this file has not seen. Read it against the source and the rationale; if it is right, add it to `accepts` so the next run knows.",
        )
    };
    EvalScore {
        id: case.id.clone(),
        axis: case.axis.clone(),
        verdict,
        note: note.to_owned(),
    }
}

pub fn grade(benchmark: &Benchmark, answers: &BTreeMap<String, String>) -> ScoreReport {
    let mut by_axis = BTreeMap::<String, AxisTally>::new();
    let cases = benchmark
        .cases
        .iter()
        .map(|case| {
            let result = grade_one(case, answers.get(&case.id).map_or("", String::as_str));
            let tally = by_axis.entry(case.axis.clone()).or_default();
            match result.verdict {
                Verdict::Pass => tally.pass += 1,
                Verdict::Fail => tally.fail += 1,
                Verdict::Unknown => tally.unknown += 1,
            }
            result
        })
        .collect();
    ScoreReport {
        schema: benchmark.schema.clone(), cases, by_axis, overall: (),
        overall_note: "deliberately absent: an average over these axes is not a fact about a translation, and a number invites tuning against the benchmark instead of the book".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::normalise;

    #[test]
    fn normalises_compatibility_spacing_and_letters() {
        assert_eq!(normalise(" Ａ، كجايی? "), "A کجایی");
        assert_eq!(normalise("کجایی؟"), "کجایی؟");
    }
}

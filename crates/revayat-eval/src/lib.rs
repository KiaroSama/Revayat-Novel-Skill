#![forbid(unsafe_code)]

pub mod adjudicate;
mod score;

pub use score::{
    AxisTally, Benchmark, Error, EvalCase, EvalScore, ScoreReport, Verdict, WrongControl, grade,
    grade_one, load_cases, normalise,
};

const CASES: &str = include_str!("../../../evaluation/cases.json");

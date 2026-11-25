#![forbid(unsafe_code)]

pub mod falint;
pub mod famorph;
pub mod glossary;
pub mod glossentry;
pub mod lookup;
pub mod persian_lint;

pub use revayat_core::{PDI, RLI, RLM, isolate_persian_line};

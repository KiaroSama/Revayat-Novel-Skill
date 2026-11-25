use std::collections::BTreeMap;

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Severity {
    Error,
    Warning,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Finding {
    pub severity: Severity,
    pub code: String,
    pub unit: String,
    pub detail: String,
}

impl Finding {
    pub fn new(
        severity: Severity,
        code: impl Into<String>,
        unit: impl Into<String>,
        detail: impl AsRef<str>,
    ) -> Self {
        Self {
            severity,
            code: code.into(),
            unit: unit.into(),
            detail: detail.as_ref().chars().take(200).collect(),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Report {
    pub findings: Vec<Finding>,
    pub counts: BTreeMap<String, usize>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReportSummary {
    pub ok: bool,
    pub errors: usize,
    pub warnings: usize,
    pub counts: BTreeMap<String, usize>,
    pub by_code: IndexMap<String, usize>,
    pub findings: Vec<Finding>,
    pub truncated: usize,
}

impl Report {
    pub fn add(
        &mut self,
        severity: Severity,
        code: impl Into<String>,
        unit: impl Into<String>,
        detail: impl AsRef<str>,
    ) {
        self.findings
            .push(Finding::new(severity, code, unit, detail));
    }

    pub fn count(&mut self, name: impl Into<String>, value: usize) {
        self.counts.insert(name.into(), value);
    }

    pub fn summary(&self, limit: Option<usize>) -> ReportSummary {
        let mut by_code = IndexMap::new();
        let (mut errors, mut warnings) = (Vec::new(), Vec::new());
        for finding in &self.findings {
            *by_code.entry(finding.code.clone()).or_insert(0) += 1;
            match finding.severity {
                Severity::Error => errors.push(finding),
                Severity::Warning => warnings.push(finding),
            }
        }
        by_code.sort_by(|_, a, _, b| b.cmp(a));
        let limit = limit.unwrap_or(self.findings.len());
        ReportSummary {
            ok: errors.is_empty(),
            errors: errors.len(),
            warnings: warnings.len(),
            counts: self.counts.clone(),
            by_code,
            findings: errors
                .into_iter()
                .chain(warnings)
                .take(limit)
                .cloned()
                .collect(),
            truncated: self.findings.len().saturating_sub(limit),
        }
    }
}

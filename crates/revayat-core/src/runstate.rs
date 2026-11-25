use std::{
    collections::{BTreeMap, BTreeSet},
    io::ErrorKind,
    path::{Path, PathBuf},
};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::{
    BookLock, CoreError, RUN_STATE_FILENAME, sha256_bytes,
    transaction::{atomic_write, read_bytes},
};

const SCHEMA_VERSION: u32 = 1;
pub const MAX_REPAIR_ATTEMPTS: usize = 3;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TranslationStage {
    InitialTranslation,
    TypesettingLayout,
    IndependentReview,
    ApplyEdits,
    RecheckAndRepair,
    FinalReviewDelivery,
}

impl TranslationStage {
    pub const ALL: [Self; 6] = [
        Self::InitialTranslation,
        Self::TypesettingLayout,
        Self::IndependentReview,
        Self::ApplyEdits,
        Self::RecheckAndRepair,
        Self::FinalReviewDelivery,
    ];
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StageStatus {
    Pending,
    InProgress,
    Verified,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StageRecord {
    pub status: StageStatus,
    pub artifact_path: Option<PathBuf>,
    pub timestamp_utc: DateTime<Utc>,
    pub verified_findings: usize,
    pub revision: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case", tag = "state", content = "stage")]
pub enum SignoffState {
    Pending(TranslationStage),
    Rejected(TranslationStage),
    Approved,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PageState {
    Pending,
    Extracted,
    Translated,
    Merged,
    Rendered,
    QaPassed,
    Accepted,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PageRecord {
    pub state: PageState,
    pub source: String,
    pub translation: Option<String>,
    pub render: Option<String>,
    pub qa: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunState {
    pub schema_version: u32,
    pub stages: BTreeMap<TranslationStage, StageRecord>,
    #[serde(default)]
    pub pages: BTreeMap<u32, PageRecord>,
    #[serde(default)]
    pub repairs: RepairLedger,
}

impl Default for RunState {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            stages: BTreeMap::new(),
            pages: BTreeMap::new(),
            repairs: RepairLedger::default(),
        }
    }
}

impl RunState {
    pub fn record(
        &mut self,
        stage: TranslationStage,
        status: StageStatus,
        artifact_path: Option<PathBuf>,
        revision: &str,
        verified_findings: usize,
    ) -> Result<(), CoreError> {
        let entry = StageRecord {
            status,
            artifact_path,
            timestamp_utc: Utc::now(),
            verified_findings,
            revision: revision.into(),
        };
        validate_stage(&entry)?;
        self.stages.retain(|&recorded, _| recorded <= stage);
        self.stages.insert(stage, entry);
        Ok(())
    }

    pub fn note_page_source(&mut self, page: u32, source: &str) -> Result<bool, CoreError> {
        if page == 0 || !crate::bookir::is_sha256(source) {
            return Err(CoreError::InvalidState(
                "page source requires SHA-256 and a one-based page number",
            ));
        }
        if self
            .pages
            .get(&page)
            .is_some_and(|entry| entry.source == source)
        {
            return Ok(false);
        }
        self.pages.insert(
            page,
            PageRecord {
                state: PageState::Extracted,
                source: source.into(),
                translation: None,
                render: None,
                qa: None,
            },
        );
        Ok(true)
    }

    pub fn signoff(&self, revision: &str) -> SignoffState {
        if self.validate().is_err() {
            return SignoffState::Rejected(TranslationStage::InitialTranslation);
        }
        for stage in TranslationStage::ALL {
            match self
                .stages
                .get(&stage)
                .filter(|record| record.revision == revision)
            {
                Some(record) if record.status == StageStatus::Failed => {
                    return SignoffState::Rejected(stage);
                }
                Some(record)
                    if record.status == StageStatus::Verified && validate_stage(record).is_ok() => {
                }
                _ => return SignoffState::Pending(stage),
            }
        }
        SignoffState::Approved
    }

    pub fn validate(&self) -> Result<(), CoreError> {
        if self.schema_version != SCHEMA_VERSION {
            return Err(CoreError::InvalidState("unsupported schema version"));
        }
        self.stages.values().try_for_each(validate_stage)?;
        if self.pages.iter().any(|(&page, record)| {
            page == 0
                || !crate::bookir::is_sha256(&record.source)
                || [&record.translation, &record.render, &record.qa]
                    .into_iter()
                    .flatten()
                    .any(|hash| !crate::bookir::is_sha256(hash))
        }) {
            return Err(CoreError::InvalidState("invalid page evidence"));
        }
        Ok(())
    }

    pub fn load(work_dir: impl AsRef<Path>) -> Result<Self, CoreError> {
        let path = work_dir.as_ref().join(RUN_STATE_FILENAME);
        let bytes = match read_bytes(&path) {
            Ok(bytes) => bytes,
            Err(CoreError::Io { source, .. }) if source.kind() == ErrorKind::NotFound => {
                return Ok(Self::default());
            }
            Err(error) => return Err(error),
        };
        let state: Self = serde_json::from_slice(&bytes)?;
        state.validate()?;
        Ok(state)
    }

    pub fn save(&self, work_dir: impl AsRef<Path>) -> Result<(), CoreError> {
        self.validate()?;
        let path = work_dir.as_ref().join(RUN_STATE_FILENAME);
        let _lock = BookLock::acquire(&path)?;
        atomic_write(
            &path,
            (serde_json::to_string_pretty(self)? + "\n").as_bytes(),
        )
    }
}

fn validate_stage(record: &StageRecord) -> Result<(), CoreError> {
    if record.revision.trim().is_empty()
        || (record.status == StageStatus::Verified
            && record
                .artifact_path
                .as_ref()
                .is_none_or(|path| path.as_os_str().is_empty()))
    {
        return Err(CoreError::InvalidState(
            "a stage needs a revision and a verified stage needs an artifact path",
        ));
    }
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RepairRefusal {
    NoNewEvidence,
    Oscillating,
    RoundsExhausted,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RepairAttempt {
    pub wording: String,
    pub argument: String,
    pub event: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RepairEpisode {
    pub source: String,
    pub attempts: Vec<RepairAttempt>,
    pub rejected: BTreeSet<String>,
    pub recurrences: usize,
    pub closed: bool,
}

impl RepairEpisode {
    pub fn decision(&self) -> Option<RepairRefusal> {
        let (last, earlier) = self.attempts.split_last()?;
        if earlier
            .last()
            .is_some_and(|attempt| attempt.wording == last.wording)
        {
            Some(RepairRefusal::NoNewEvidence)
        } else if self.rejected.contains(&last.wording)
            || earlier
                .iter()
                .any(|attempt| attempt.wording == last.wording)
        {
            Some(RepairRefusal::Oscillating)
        } else if self.attempts.len() >= MAX_REPAIR_ATTEMPTS {
            Some(RepairRefusal::RoundsExhausted)
        } else {
            None
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RepairLedger {
    pub issues: BTreeMap<String, RepairEpisode>,
    pub superseded: BTreeMap<String, Vec<RepairEpisode>>,
}

impl RepairLedger {
    pub fn record_attempt(
        &mut self,
        key: &str,
        source: &str,
        text: &str,
        argument: &str,
        event: &str,
    ) -> Option<RepairRefusal> {
        let source = sha256_bytes(source.as_bytes());
        let wording = sha256_bytes(text.as_bytes());
        let entry = self.issues.entry(key.into()).or_default();
        if entry.source != source || entry.closed {
            let mut replacement = RepairEpisode {
                source: source.clone(),
                ..RepairEpisode::default()
            };
            if entry.source == source {
                replacement.recurrences = entry.recurrences.saturating_add(1);
                replacement.rejected.extend(entry.rejected.iter().cloned());
                replacement
                    .rejected
                    .extend(entry.attempts.iter().map(|attempt| attempt.wording.clone()));
            }
            let previous = std::mem::replace(entry, replacement);
            if !previous.source.is_empty() {
                self.superseded
                    .entry(key.into())
                    .or_default()
                    .push(previous);
            }
        }
        if !event.is_empty()
            && let Some(last) = entry.attempts.last_mut()
            && last.event == event
        {
            last.argument.push_str("\n\n");
            last.argument.push_str(argument);
        } else {
            entry.attempts.push(RepairAttempt {
                wording,
                argument: argument.into(),
                event: event.into(),
            });
        }
        entry.decision()
    }

    pub fn close_absent(&mut self, seen: &BTreeSet<String>) {
        for (key, entry) in &mut self.issues {
            if !seen.contains(key) {
                entry.closed = true;
            }
        }
    }

    pub fn blocked(&self) -> BTreeMap<&str, RepairRefusal> {
        self.issues
            .iter()
            .filter(|(_, episode)| !episode.closed)
            .filter_map(|(key, episode)| episode.decision().map(|reason| (key.as_str(), reason)))
            .collect()
    }
}

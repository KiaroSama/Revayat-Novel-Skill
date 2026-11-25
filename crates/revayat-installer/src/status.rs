use crate::{Agent, InstallError, IoContext, Scope, files, lock, transaction};
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Absent,
    Unknown,
    Unstable,
    Prepared,
    CleanupPending,
    Committed,
    Conflict,
}

#[derive(Debug, PartialEq, Eq, Serialize)]
pub struct StatusReport {
    pub schema: &'static str,
    pub status: Status,
    pub installed: usize,
    pub receipts: usize,
    pub backups: usize,
    pub participants: usize,
    pub phase: Option<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cause: Option<&'static str>,
}

impl StatusReport {
    fn new(status: Status) -> Self {
        Self {
            schema: "revayat-novel/install-status@1",
            status,
            installed: 0,
            receipts: 0,
            backups: 0,
            participants: 0,
            phase: None,
            cause: None,
        }
    }
}

#[derive(PartialEq, Eq)]
struct Observation {
    report: StatusReport,
    selected: Option<(PathBuf, SystemTime)>,
}

fn observe(base: &Path, agent: Agent, scope: Scope) -> Result<Observation, InstallError> {
    let mut report = StatusReport::new(Status::Absent);
    let destinations = agent
        .destinations(scope)
        .map(|path| files::relative(&path))
        .collect::<Result<Vec<_>, _>>()?;
    for relative in &destinations {
        let path = base.join(relative);
        files::admit(&path)?;
        match files::kind(&path)? {
            Some(m) if m.is_dir() => report.installed += 1,
            Some(_) => return Err(InstallError::InvalidState(path)),
            None => {}
        }
    }
    let Some(state) = lock::state(base)? else {
        if report.installed != 0 {
            report.status = Status::Unknown;
        }
        return Ok(Observation {
            report,
            selected: None,
        });
    };
    let mut receipts = Vec::new();
    let journal = state.join("journal.json");
    let mut has_journal = None;
    for (count, entry) in fs::read_dir(&state)
        .at("Read installer status", &state)?
        .enumerate()
    {
        if count >= 1000 {
            return Err(InstallError::InvalidState(state));
        }
        let entry = entry.at("Read installer status member", &state)?;
        let path = entry.path();
        let Some(m) = files::kind(&path)? else {
            return Err(InstallError::Conflict(path));
        };
        if path == journal && m.is_file() {
            has_journal = Some((
                m.modified().at("Read journal timestamp", &path)?,
                path.clone(),
            ));
        }
        if entry
            .file_name()
            .to_str()
            .is_some_and(|s| s.starts_with("receipt-") && s.ends_with(".json"))
        {
            if !m.is_file() {
                return Err(InstallError::InvalidState(path));
            }
            receipts.push((m.modified().at("Read receipt timestamp", &path)?, path));
        }
    }
    report.receipts = receipts.len();
    report.backups = transaction::backup_count(&state)?;
    receipts.sort_unstable();
    let mut selected = None;
    for (time, path) in has_journal.into_iter().chain(receipts.into_iter().rev()) {
        let record = transaction::read_record(base, &path)?;
        if path == journal
            || record
                .participants
                .iter()
                .any(|p| destinations.contains(&p.destination))
        {
            selected = Some((path, time, record));
            break;
        }
    }
    if let Some((path, _, record)) = &selected {
        report.participants = record.participants.len();
        let phase = record.phase;
        use transaction::Phase;
        report.phase = Some(match phase {
            Phase::Prepared => "prepared",
            Phase::Committed => "committed",
            Phase::RolledBack => "rolled_back",
        });
        report.status = match transaction::consistent(base, record) {
            Ok(()) => match (path == &journal, phase) {
                (true, Phase::Prepared | Phase::RolledBack) => Status::Prepared,
                (true, Phase::Committed) => Status::CleanupPending,
                (false, Phase::Committed) => Status::Committed,
                (false, Phase::RolledBack) if report.installed == 0 => Status::Absent,
                (false, Phase::RolledBack) => Status::Unknown,
                (false, Phase::Prepared) => Status::Unknown,
            },
            Err(InstallError::Conflict(_)) => Status::Conflict,
            Err(e) => return Err(e),
        };
    } else if report.installed != 0 {
        report.status = Status::Unknown;
    }
    let selected = selected.map(|(path, time, _)| (path, time));
    Ok(Observation { report, selected })
}

pub fn inspect(base: &Path, agent: Agent, scope: Scope) -> StatusReport {
    let query = || files::root(base).and_then(|base| observe(&base, agent, scope));
    match (query(), query()) {
        (Ok(first), Ok(second)) if first == second => second.report,
        (Ok(_), _) | (_, Ok(_)) => {
            let mut report = StatusReport::new(Status::Unstable);
            report.cause = Some("state-changed");
            report
        }
        (Err(_), Err(_)) => {
            let mut report = StatusReport::new(Status::Unknown);
            report.cause = Some("unverifiable-state");
            report
        }
    }
}

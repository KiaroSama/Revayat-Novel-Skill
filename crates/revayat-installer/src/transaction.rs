use crate::{Agent, InstallError, IoContext, Options, STATE, Scope, files, lock::Lock};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::fs::{self, Metadata};
use std::io::Write;
use std::path::{Path, PathBuf};

const JOURNAL: &str = "journal.json";
const SCHEMA: &str = "revayat-novel/native-install@1";

#[derive(Debug, Serialize)]
pub struct InstallReport {
    pub committed: bool,
    pub cleanup_pending: bool,
    pub destinations: Vec<PathBuf>,
    pub backups: Vec<PathBuf>,
    pub receipt: Option<PathBuf>,
    pub journal: Option<PathBuf>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Phase {
    Prepared,
    Committed,
    RolledBack,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Record {
    schema: String,
    base: PathBuf,
    token: String,
    pub(crate) phase: Phase,
    pub(crate) participants: Vec<Participant>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Kind {
    File,
    Tree,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Participant {
    pub(crate) destination: String,
    stage: String,
    backup: String,
    kind: Kind,
    old: Option<String>,
    new: String,
}

#[derive(Debug, PartialEq, Eq)]
enum Layout {
    Restored,
    OldMoved,
    Promoted,
}

fn valid_token(value: &str) -> bool {
    (6..=32).contains(&value.len()) && value.bytes().all(|b| b.is_ascii_alphanumeric())
}

fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || matches!(b, b'a'..=b'f'))
}

fn state_path(token: &str, kind: &str, index: usize) -> String {
    format!("{STATE}/txn-{token}/{kind}-{index}")
}

pub(crate) fn read_record(base: &Path, path: &Path) -> Result<Record, InstallError> {
    let record: Record = serde_json::from_slice(&files::read(path, files::MAX_RECORD)?)
        .map_err(|_| InstallError::InvalidState(path.into()))?;
    validate(base, path, &record)?;
    Ok(record)
}

fn validate(base: &Path, path: &Path, record: &Record) -> Result<(), InstallError> {
    let invalid = || InstallError::InvalidState(path.into());
    if record.schema != SCHEMA
        || record.base != base
        || !valid_token(&record.token)
        || !(1..=9).contains(&record.participants.len())
    {
        return Err(invalid());
    }
    if path.file_name().and_then(|name| name.to_str()) != Some(JOURNAL)
        && path != receipt_path(&base.join(STATE), record)
    {
        return Err(invalid());
    }
    let allowed = [Scope::User, Scope::Project]
        .into_iter()
        .flat_map(|scope| Agent::All.destinations(scope))
        .map(|p| files::relative(&p))
        .collect::<Result<BTreeSet<_>, _>>()?;
    let mut seen = BTreeSet::new();
    for (i, p) in record.participants.iter().enumerate() {
        let kind = if p.destination == "AGENTS.md" {
            Kind::File
        } else if allowed.contains(&p.destination) {
            Kind::Tree
        } else {
            return Err(invalid());
        };
        if !seen.insert(&p.destination)
            || p.kind != kind
            || p.stage != state_path(&record.token, "stage", i)
            || p.backup != state_path(&record.token, "backup", i)
            || !valid_hash(&p.new)
            || p.old.as_deref().is_some_and(|old| !valid_hash(old))
        {
            return Err(invalid());
        }
        for name in [&p.destination, &p.stage, &p.backup] {
            let full = base.join(name);
            files::admit(&full)?;
            if files::kind(&full)?.is_some_and(|m| m.is_dir() != (kind == Kind::Tree)) {
                return Err(InstallError::Conflict(full));
            }
        }
    }
    Ok(())
}

fn classify(base: &Path, p: &Participant, phase: Phase) -> Result<Layout, InstallError> {
    let destination = base.join(&p.destination);
    let current = files::fingerprint(&destination)?;
    let staged = files::fingerprint(&base.join(&p.stage))?;
    let saved = files::fingerprint(&base.join(&p.backup))?;
    let old = p.old.as_deref();
    let new = Some(p.new.as_str());
    let (c, s, b) = (current.as_deref(), staged.as_deref(), saved.as_deref());
    if c == new && s.is_none() && b == old {
        return Ok(Layout::Promoted);
    }
    if phase != Phase::Committed {
        if c == old && s == new && b.is_none() {
            return Ok(Layout::Restored);
        }
        if c.is_none() && s == new && old.is_some() && b == old {
            return Ok(Layout::OldMoved);
        }
    }
    Err(InstallError::Conflict(destination))
}

pub(crate) fn consistent(base: &Path, record: &Record) -> Result<(), InstallError> {
    for p in &record.participants {
        let layout = classify(base, p, record.phase)?;
        if record.phase == Phase::RolledBack && layout != Layout::Restored {
            return Err(InstallError::Conflict(base.join(&p.destination)));
        }
    }
    Ok(())
}

fn save_record(path: &Path, record: &Record) -> Result<(), InstallError> {
    let parent = path
        .parent()
        .ok_or_else(|| InstallError::InvalidState(path.into()))?;
    let mut tmp = tempfile::Builder::new()
        .prefix("record-")
        .tempfile_in(parent)
        .at("Stage installer record", parent)?;
    let mut bytes = serde_json::to_vec(record)?;
    bytes.push(b'\n');
    tmp.write_all(&bytes).at("Write installer record", path)?;
    tmp.as_file().sync_all().at("Sync installer record", path)?;
    files::admit(path)?;
    tmp.persist(path)
        .map_err(|e| e.error)
        .at("Publish installer record", path)?;
    files::sync_dir(parent)
}

fn receipt_path(state: &Path, record: &Record) -> PathBuf {
    state.join(format!("receipt-{}.json", record.token))
}

fn finish(state: &Path, journal: &Path, record: &Record) -> Result<PathBuf, InstallError> {
    let receipt = receipt_path(state, record);
    let journal_bytes = files::read(journal, files::MAX_RECORD)?;
    if files::kind(&receipt)?.is_some() {
        if files::read(&receipt, files::MAX_RECORD)? != journal_bytes {
            return Err(InstallError::Conflict(receipt));
        }
    } else {
        save_record(&receipt, record)?;
    }
    fs::remove_file(journal).at("Remove completed journal", journal)?;
    files::sync_dir(state)?;
    Ok(receipt)
}

fn report(
    base: &Path,
    state: &Path,
    journal: &Path,
    record: &Record,
) -> Result<InstallReport, InstallError> {
    let committed = record.phase == Phase::Committed;
    let final_receipt = finish(state, journal, record);
    let (receipt, pending) = match final_receipt {
        Ok(path) => (Some(path), false),
        Err(_) if committed => (None, true),
        Err(e) => return Err(e),
    };
    Ok(InstallReport {
        committed,
        cleanup_pending: pending,
        receipt,
        journal: pending.then(|| journal.to_path_buf()),
        destinations: record
            .participants
            .iter()
            .filter(|p| p.kind == Kind::Tree)
            .map(|p| base.join(&p.destination))
            .collect(),
        backups: record
            .participants
            .iter()
            .filter(|p| committed && p.old.is_some())
            .map(|p| base.join(&p.backup))
            .collect(),
    })
}

fn recover_locked(base: &Path, state: &Path) -> Result<Option<InstallReport>, InstallError> {
    let journal = state.join(JOURNAL);
    if files::kind(&journal)?.is_none() {
        return Ok(None);
    }
    let mut record = read_record(base, &journal)?;
    let decisions = record
        .participants
        .iter()
        .map(|p| {
            files::tracked(&base.join(&p.destination))?;
            classify(base, p, record.phase)
        })
        .collect::<Result<Vec<_>, _>>()?;
    if record.phase != Phase::Committed {
        for (p, decision) in record.participants.iter().zip(decisions).rev() {
            let destination = base.join(&p.destination);
            if classify(base, p, record.phase)? != decision {
                return Err(InstallError::Conflict(destination));
            }
            if decision == Layout::Promoted {
                files::rename(&destination, &base.join(&p.stage))?;
            }
            if decision != Layout::Restored && p.old.is_some() {
                files::rename(&base.join(&p.backup), &destination)?;
            }
        }
        record.phase = Phase::RolledBack;
        consistent(base, &record)?;
        save_record(&journal, &record)?;
    }
    report(base, state, &journal, &record).map(Some)
}

pub fn recover(base: &Path) -> Result<Option<InstallReport>, InstallError> {
    let base = files::root(base)?;
    let lock = Lock::acquire(&base)?;
    recover_locked(&base, &lock.state)
}

struct Planned {
    destination: PathBuf,
    old: Option<String>,
    contents: Option<(Vec<u8>, Option<fs::Permissions>)>,
}

fn plan(base: &Path, source: &Path, options: Options<'_>) -> Result<Vec<Planned>, InstallError> {
    let mut plans = Vec::new();
    let mut pointer_selected = false;
    for agent in options.agent.selected() {
        let Some(home) = agent.home(options.scope) else {
            continue;
        };
        let home = base.join(home);
        files::admit(&home)?;
        if options.agent == Agent::All && files::kind(&home)?.is_none() {
            continue;
        }
        let destination = home.join("skills/revayat-novel");
        files::admit(&destination)?;
        if destination.starts_with(source) || source.starts_with(&destination) {
            return Err(InstallError::UnsafePath(destination));
        }
        if let Some(m) = files::kind(&destination)? {
            if !m.is_dir() {
                return Err(InstallError::UnsafePath(destination));
            }
            if !options.force {
                return Err(InstallError::OverwriteRequired(destination));
            }
        }
        files::tracked(&destination)?;
        let old = files::fingerprint(&destination)?;
        plans.push(Planned {
            destination,
            old,
            contents: None,
        });
        pointer_selected |= matches!(agent, Agent::Opencode | Agent::Antigravity);
    }
    if plans.is_empty() {
        return Err(InstallError::NoTargets);
    }
    if pointer_selected {
        let destination = base.join("AGENTS.md");
        files::tracked(&destination)?;
        let old = files::fingerprint(&destination)?;
        let (original, permissions) = match files::kind(&destination)? {
            None => (Vec::new(), None),
            Some(m) if m.is_file() => (
                files::read(&destination, files::MAX_RECORD)?,
                Some(m.permissions()),
            ),
            _ => return Err(InstallError::UnsafePath(destination)),
        };
        let mut pointers = Vec::new();
        for agent in [Agent::Opencode, Agent::Antigravity] {
            let Some(home) = agent.home(options.scope) else {
                continue;
            };
            let path = base.join(home).join("skills/revayat-novel");
            files::admit(&path)?;
            if plans.iter().any(|p| p.destination == path)
                || files::kind(&path)?.is_some_and(|m| m.is_dir())
            {
                pointers.push(path);
            }
        }
        pointers.sort();
        let updated = files::pointer(&original, &pointers)
            .ok_or_else(|| InstallError::Conflict(destination.clone()))?;
        plans.push(Planned {
            destination,
            old,
            contents: Some((updated, permissions)),
        });
    }
    Ok(plans)
}

pub fn install(options: Options<'_>) -> Result<InstallReport, InstallError> {
    install_with_bin(options, None)
}

pub fn install_with_bin(
    options: Options<'_>,
    bin: Option<&Path>,
) -> Result<InstallReport, InstallError> {
    let base = files::root(options.base)?;
    let source = files::root(options.payload)?;
    if source.starts_with(base.join(STATE)) || base.join(STATE).starts_with(&source) {
        return Err(InstallError::UnsafePath(source));
    }
    let lock = Lock::acquire(&base)?;
    let state = &lock.state;
    if let Some(recovered) = recover_locked(&base, state)?
        && recovered.cleanup_pending
    {
        return Ok(recovered);
    }
    let plans = plan(&base, &source, options)?;
    let payload = files::Payload::collect(&source, bin)?;
    let temp = tempfile::Builder::new()
        .prefix("txn-")
        .tempdir_in(state)
        .at("Create installer transaction", state)?;
    let token = temp
        .path()
        .file_name()
        .and_then(|n| n.to_str())
        .and_then(|n| n.strip_prefix("txn-"))
        .filter(|n| valid_token(n))
        .ok_or_else(|| InstallError::InvalidState(temp.path().into()))?
        .to_owned();
    let mut record = Record {
        schema: SCHEMA.into(),
        base: base.clone(),
        token,
        phase: Phase::Prepared,
        participants: Vec::new(),
    };
    for (i, plan) in plans.into_iter().enumerate() {
        let stage = state_path(&record.token, "stage", i);
        let stage_path = base.join(&stage);
        let kind = if let Some((body, perms)) = plan.contents {
            files::write(&stage_path, &body, perms)?;
            Kind::File
        } else {
            payload.stage(&stage_path)?;
            Kind::Tree
        };
        record.participants.push(Participant {
            destination: files::relative(
                plan.destination
                    .strip_prefix(&base)
                    .map_err(|_| InstallError::UnsafePath(plan.destination.clone()))?,
            )?,
            stage,
            backup: state_path(&record.token, "backup", i),
            kind,
            old: plan.old,
            new: files::fingerprint(&stage_path)?.ok_or(InstallError::Conflict(stage_path))?,
        });
    }
    files::sync_dir(temp.path())?;
    files::sync_dir(state)?;
    let journal = state.join(JOURNAL);
    validate(&base, &journal, &record)?;
    for p in &record.participants {
        let destination = base.join(&p.destination);
        let parent = destination
            .parent()
            .ok_or_else(|| InstallError::UnsafePath(destination.clone()))?;
        files::ensure_dir(parent)?;
        if classify(&base, p, Phase::Prepared)? != Layout::Restored {
            return Err(InstallError::Conflict(destination));
        }
    }
    // Once the journal may be durable, a drop must never remove its participants.
    let _retained = temp.keep();
    save_record(&journal, &record)?;
    let publish = || -> Result<(), InstallError> {
        for p in &record.participants {
            let destination = base.join(&p.destination);
            files::tracked(&destination)?;
            if classify(&base, p, Phase::Prepared)? != Layout::Restored {
                return Err(InstallError::Conflict(destination));
            }
            if p.old.is_some() {
                files::rename(&destination, &base.join(&p.backup))?;
            }
            files::rename(&base.join(&p.stage), &destination)?;
        }
        for p in &record.participants {
            classify(&base, p, Phase::Committed)?;
        }
        Ok(())
    };
    if let Err(e) = publish() {
        recover_locked(&base, state)?;
        return Err(e);
    }
    record.phase = Phase::Committed;
    if let Err(e) = save_record(&journal, &record) {
        if let Some(result) = recover_locked(&base, state)?
            && result.committed
        {
            return Ok(result);
        }
        return Err(e);
    }
    report(&base, state, &journal, &record)
}

pub(crate) fn backup_count(state: &Path) -> Result<usize, InstallError> {
    let mut count = 0;
    for entry in fs::read_dir(state).at("Read installer state", state)? {
        let entry = entry.at("Read installer state member", state)?;
        let path = entry.path();
        let m = files::kind(&path)?;
        if let Some(token) = entry
            .file_name()
            .to_str()
            .and_then(|n| n.strip_prefix("txn-"))
            && valid_token(token)
            && m.is_some_and(|m: Metadata| m.is_dir())
        {
            for item in fs::read_dir(&path).at("Read transaction", &path)? {
                let item = item.at("Read transaction member", &path)?;
                files::kind(&item.path())?;
                if item
                    .file_name()
                    .to_str()
                    .is_some_and(|n| n.starts_with("backup-"))
                {
                    count += 1;
                }
            }
        }
    }
    Ok(count)
}

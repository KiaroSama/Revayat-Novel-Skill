use std::{
    collections::BTreeMap,
    env, fs,
    path::{Path, PathBuf},
};

use anyhow::{Context, Result};
use clap::{Args, Subcommand};
use revayat_eval::{adjudicate, grade, load_cases};

#[derive(Args)]
pub struct InstallArgs {
    /// Target agent: claude, kiro, codex, cursor, cline, hermes, opencode, antigravity, or all
    #[arg(long)]
    pub agent: Option<String>,
    /// Install scope: user or project
    #[arg(long)]
    pub scope: Option<String>,
    /// Explicit destination base directory
    #[arg(long)]
    pub path: Option<PathBuf>,
    /// Overwrite an existing installation
    #[arg(long)]
    pub force: bool,
    /// Inspect installation status without modifying files
    #[arg(long)]
    pub status: bool,
    /// Skill payload root directory (defaults to auto-detected skills/ folder)
    #[arg(long)]
    pub payload: Option<PathBuf>,
}

#[derive(Args)]
pub struct EvalScoreArgs {
    /// Path to JSON object mapping case IDs to candidate answers
    #[arg(long)]
    pub answers: PathBuf,
    /// Optional path to benchmark cases JSON (defaults to embedded evaluation/cases.json)
    #[arg(long)]
    pub cases: Option<PathBuf>,
    /// Emit machine-readable JSON output
    #[arg(long)]
    pub json: bool,
}

#[derive(Subcommand)]
pub enum EvalAdjudicateCmd {
    /// Prepare an adjudication review template
    Prepare {
        #[arg(long)]
        out: PathBuf,
        #[arg(long)]
        cases: Option<PathBuf>,
    },
    /// Validate a completed adjudication review file
    Validate {
        #[arg(long)]
        review: PathBuf,
        #[arg(long)]
        cases: Option<PathBuf>,
    },
}

fn detect_home() -> PathBuf {
    env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn detect_payload(explicit: Option<&Path>) -> PathBuf {
    if let Some(dir) = explicit {
        return dir.to_path_buf();
    }
    let cwd = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let exe_dir = env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf));
    for start in exe_dir.into_iter().chain(std::iter::once(cwd.clone())) {
        for ancestor in start.ancestors() {
            let skill = ancestor.join("skills").join("revayat-novel");
            if skill.join("SKILL.md").is_file() {
                return skill;
            }
        }
    }
    cwd.join("skills").join("revayat-novel")
}

pub fn run_install(args: InstallArgs) -> Result<u8> {
    let mut raw = Vec::new();
    if let Some(agent) = args.agent {
        raw.push("--agent".into());
        raw.push(agent);
    }
    if let Some(scope) = args.scope {
        raw.push("--scope".into());
        raw.push(scope);
    }
    if let Some(path) = args.path {
        raw.push("--path".into());
        raw.push(path.to_string_lossy().into_owned());
    }
    if args.force {
        raw.push("--force".into());
    }
    if args.status {
        raw.push("--status".into());
    }

    let project = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let home = detect_home();
    let payload = detect_payload(args.payload.as_deref());
    let current_exe = env::current_exe().ok();

    let output =
        revayat_installer::run_with_bin(&raw, &project, &home, &payload, current_exe.as_deref())?;
    println!("{}", output.json);
    Ok(output.exit_code)
}

pub fn run_eval_score(args: EvalScoreArgs) -> Result<u8> {
    let benchmark = load_cases(args.cases.as_deref())?;
    let raw = fs::read_to_string(&args.answers)
        .with_context(|| format!("read answers file {}", args.answers.display()))?;
    let answers: BTreeMap<String, String> = serde_json::from_str(&raw)
        .with_context(|| format!("parse JSON answers map from {}", args.answers.display()))?;
    let report = grade(&benchmark, &answers);
    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        let passed = report
            .cases
            .iter()
            .filter(|c| c.verdict == revayat_eval::Verdict::Pass)
            .count();
        let failed = report
            .cases
            .iter()
            .filter(|c| c.verdict == revayat_eval::Verdict::Fail)
            .count();
        let unknown = report.cases.len().saturating_sub(passed + failed);
        println!(
            "Eval score: {passed}/{} passed, {failed} failed, {unknown} unknown",
            report.cases.len()
        );
    }
    Ok(if report.has_failures() { 1 } else { 0 })
}

pub fn run_eval_adjudicate(cmd: EvalAdjudicateCmd) -> Result<u8> {
    match cmd {
        EvalAdjudicateCmd::Prepare { out, cases } => {
            let prepared = adjudicate::prepare(cases.as_deref())?;
            if let Some(parent) = out.parent().filter(|p| !p.as_os_str().is_empty()) {
                fs::create_dir_all(parent)?;
            }
            fs::write(&out, serde_json::to_string_pretty(&prepared)?.as_bytes())
                .with_context(|| format!("write {}", out.display()))?;
            println!("Prepared adjudication template -> {}", out.display());
            Ok(0)
        }
        EvalAdjudicateCmd::Validate { review, cases } => {
            let value = adjudicate::object_file(&review)?;
            adjudicate::validate(&value, cases.as_deref())?;
            println!("OK: adjudication review {} is valid.", review.display());
            Ok(0)
        }
    }
}

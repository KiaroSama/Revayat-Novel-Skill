use crate::{Agent, InstallError, Options, Scope, Status, inspect, install_with_bin};
use std::path::Path;

#[derive(Debug, PartialEq, Eq)]
pub struct CliOutput {
    pub exit_code: u8,
    pub json: String,
}

pub fn run(
    args: &[String],
    project: &Path,
    home: &Path,
    payload: &Path,
) -> Result<CliOutput, InstallError> {
    run_with_bin(args, project, home, payload, None)
}

/// Accepts an optional directory containing the release binaries, or a single executable.
pub fn run_with_bin(
    args: &[String],
    project: &Path,
    home: &Path,
    payload: &Path,
    bin_dir: Option<&Path>,
) -> Result<CliOutput, InstallError> {
    let mut agent = Agent::All;
    let mut scope = Scope::User;
    let mut path = None;
    let mut status = false;
    let mut force = false;
    let mut args = args.iter().map(String::as_str);
    while let Some(arg) = args.next() {
        let mut value = || {
            args.next()
                .filter(|v| !v.starts_with("--"))
                .ok_or_else(|| InstallError::Arguments(format!("Missing value for {arg}")))
        };
        match arg {
            "--agent" => agent = value()?.parse()?,
            "--scope" => scope = value()?.parse()?,
            "--path" => path = Some(Path::new(value()?)),
            "--status" => status = true,
            "--force" => force = true,
            _ => {
                return Err(InstallError::Arguments(format!(
                    "Unknown installer argument: {arg}"
                )));
            }
        }
    }
    let resolved;
    let base = if scope == Scope::User {
        home
    } else if let Some(path) = path {
        resolved = project.join(path);
        &resolved
    } else {
        project
    };
    if status {
        let result = inspect(base, agent, scope);
        let exit_code = if matches!(result.status, Status::Absent | Status::Committed) {
            0
        } else {
            2
        };
        Ok(CliOutput {
            exit_code,
            json: serde_json::to_string(&result)?,
        })
    } else {
        let result = install_with_bin(
            Options {
                base,
                payload,
                agent,
                scope,
                force,
            },
            bin_dir,
        )?;
        Ok(CliOutput {
            exit_code: if result.cleanup_pending { 2 } else { 0 },
            json: serde_json::to_string(&result)?,
        })
    }
}

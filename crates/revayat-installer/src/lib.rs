#![forbid(unsafe_code)]

mod cli_runner;
mod files;
mod lock;
mod status;
mod transaction;

pub use cli_runner::{CliOutput, run, run_with_bin};
pub use status::{Status, StatusReport, inspect};
pub use transaction::{InstallReport, install, install_with_bin, recover};

use std::path::{Path, PathBuf};
use std::str::FromStr;
use thiserror::Error;

const STATE: &str = ".revayat-novel-installer";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Agent {
    Claude,
    Kiro,
    Codex,
    Cursor,
    Cline,
    Hermes,
    Opencode,
    Antigravity,
    All,
}

impl Agent {
    pub const TARGETS: [Self; 8] = [
        Self::Claude,
        Self::Kiro,
        Self::Codex,
        Self::Cursor,
        Self::Cline,
        Self::Hermes,
        Self::Opencode,
        Self::Antigravity,
    ];

    fn selected(self) -> impl Iterator<Item = Self> {
        Self::TARGETS
            .into_iter()
            .filter(move |agent| self == Self::All || *agent == self)
    }

    pub fn home(self, scope: Scope) -> Option<&'static str> {
        Some(match self {
            Self::Claude => ".claude",
            Self::Kiro => ".kiro",
            Self::Codex => ".codex",
            Self::Cursor => ".cursor",
            Self::Cline => ".cline",
            Self::Hermes => ".hermes",
            Self::Opencode if scope == Scope::User => ".config/opencode",
            Self::Opencode => ".opencode",
            Self::Antigravity => ".agents",
            Self::All => return None,
        })
    }

    fn destinations(self, scope: Scope) -> impl Iterator<Item = PathBuf> {
        self.selected().filter_map(move |agent| {
            agent
                .home(scope)
                .map(|home| Path::new(home).join("skills/revayat-novel"))
        })
    }
}

impl FromStr for Agent {
    type Err = InstallError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Ok(match value {
            "claude" => Self::Claude,
            "kiro" => Self::Kiro,
            "codex" => Self::Codex,
            "cursor" => Self::Cursor,
            "cline" => Self::Cline,
            "hermes" => Self::Hermes,
            "opencode" => Self::Opencode,
            "antigravity" => Self::Antigravity,
            "all" => Self::All,
            _ => return Err(InstallError::Arguments(format!("Unknown agent: {value}"))),
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scope {
    User,
    Project,
}

impl FromStr for Scope {
    type Err = InstallError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "user" => Ok(Self::User),
            "project" => Ok(Self::Project),
            _ => Err(InstallError::Arguments(format!("Unknown scope: {value}"))),
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Options<'a> {
    pub base: &'a Path,
    pub payload: &'a Path,
    pub agent: Agent,
    pub scope: Scope,
    pub force: bool,
}

#[derive(Debug, Error)]
pub enum InstallError {
    #[error("{0}")]
    Arguments(String),
    #[error("Unsafe installer path: {0}")]
    UnsafePath(PathBuf),
    #[error("{operation} {path}: {source}")]
    Io {
        operation: &'static str,
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("Another installer holds the OS lock")]
    Busy,
    #[error("Replacing an existing installation requires --force: {0}")]
    OverwriteRequired(PathBuf),
    #[error("Invalid installer payload: {0}")]
    Payload(PathBuf),
    #[error("Installer state conflicts with current content: {0}")]
    Conflict(PathBuf),
    #[error("Installer state is invalid: {0}")]
    InvalidState(PathBuf),
    #[error("No installed agent homes match --agent all; select an agent explicitly")]
    NoTargets,
    #[error("Cannot encode installer output: {0}")]
    Encoding(#[from] serde_json::Error),
}

pub(crate) trait IoContext<T> {
    fn at(self, operation: &'static str, path: &Path) -> Result<T, InstallError>;
}

impl<T> IoContext<T> for std::io::Result<T> {
    fn at(self, operation: &'static str, path: &Path) -> Result<T, InstallError> {
        self.map_err(|source| InstallError::Io {
            operation,
            path: path.into(),
            source,
        })
    }
}

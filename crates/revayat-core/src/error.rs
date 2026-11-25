use std::{io, path::PathBuf};

use thiserror::Error;

#[derive(Debug, Error)]
pub enum CoreError {
    #[error("{operation} {path}: {source}")]
    Io {
        operation: &'static str,
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("invalid JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("invalid book: {0}")]
    InvalidBook(String),
    #[error("book lock is held: {0}")]
    LockBusy(PathBuf),
    #[error("file changed since it was read: {0}")]
    StaleFile(PathBuf),
    #[error("invalid layout: {0}")]
    InvalidLayout(&'static str),
    #[error("invalid segment coverage: {0}")]
    InvalidSegments(&'static str),
    #[error("invalid run state: {0}")]
    InvalidState(&'static str),
    #[error("invalid log component: {0}")]
    InvalidLogComponent(String),
}

pub(crate) fn io_error(
    operation: &'static str,
    path: &std::path::Path,
    source: io::Error,
) -> CoreError {
    CoreError::Io {
        operation,
        path: path.to_owned(),
        source,
    }
}

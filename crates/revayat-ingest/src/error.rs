use std::{
    io,
    path::{Path, PathBuf},
};

use thiserror::Error;

#[derive(Debug, Error)]
pub enum IngestError {
    #[error("could not {action} {path}: {source}")]
    Io {
        action: &'static str,
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("input exceeds the {0} limit")]
    Limit(&'static str),
    #[error("invalid {format}: {detail}")]
    Invalid {
        format: &'static str,
        detail: String,
    },
    #[error("unsafe input path: {0:?}")]
    UnsafePath(String),
    #[error("unsafe source URL: {0}")]
    UnsafeUrl(&'static str),
    #[error("source request failed: {0}")]
    Http(#[source] reqwest::Error),
    #[error("could not read source response: {0}")]
    Response(#[source] io::Error),
    #[error("source returned HTTP {0}")]
    HttpStatus(reqwest::StatusCode),
    #[error("{0} timed out")]
    Timeout(&'static str),
    #[error("{0} was not found on PATH")]
    ToolMissing(&'static str),
    #[error("{tool} failed ({status})")]
    ToolFailed {
        tool: &'static str,
        status: std::process::ExitStatus,
    },
    #[error("ZIP error: {0}")]
    Zip(#[from] zip::result::ZipError),
    #[error("XML error: {0}")]
    Xml(#[from] roxmltree::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("image error: {0}")]
    Image(#[from] image::ImageError),
    #[error("PDF error: {0}")]
    Pdf(#[from] lopdf::Error),
    #[error(transparent)]
    Book(#[from] revayat_core::CoreError),
}

pub(crate) fn io(action: &'static str, path: &Path, source: io::Error) -> IngestError {
    IngestError::Io {
        action,
        path: path.into(),
        source,
    }
}

pub(crate) fn invalid(format: &'static str, detail: impl Into<String>) -> IngestError {
    IngestError::Invalid {
        format,
        detail: detail.into(),
    }
}

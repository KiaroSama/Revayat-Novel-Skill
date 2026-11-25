use std::{
    io,
    path::{Path, PathBuf},
};

use thiserror::Error;

#[derive(Debug, Error)]
pub enum PipelineError {
    #[error(transparent)]
    Core(#[from] revayat_core::CoreError),
    #[error(transparent)]
    Docx(#[from] revayat_ooxml::OoxmlError),
    #[error("{operation} {path}: {source}")]
    Io {
        operation: &'static str,
        path: PathBuf,
        #[source]
        source: io::Error,
    },
    #[error("invalid pipeline JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("invalid worksheet budget: {0}")]
    Budget(String),
    #[error("invalid worksheet: {0}")]
    Worksheet(String),
    #[error("missing artifact: {0}")]
    Missing(PathBuf),
    #[error("duplicate assignment: {0}")]
    Duplicate(String),
    #[error("stale artifact: {0}")]
    Stale(PathBuf),
    #[error("translation workflow refused: {0}")]
    Workflow(String),
    #[error("image {path}: {source}")]
    Image {
        path: PathBuf,
        #[source]
        source: image::ImageError,
    },
    #[error("no supported Word or LibreOffice renderer is available")]
    RendererUnavailable,
    #[error("renderer timed out: {0}")]
    RendererTimeout(PathBuf),
    #[error("renderer failed: {0}")]
    Renderer(String),
}

pub(crate) fn io_error(operation: &'static str, path: &Path, source: io::Error) -> PipelineError {
    PipelineError::Io {
        operation,
        path: path.into(),
        source,
    }
}

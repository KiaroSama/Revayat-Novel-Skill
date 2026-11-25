use thiserror::Error;

#[derive(Debug, Error)]
pub enum OoxmlError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("ZIP error: {0}")]
    Zip(#[from] zip::result::ZipError),
    #[error("invalid XML in {part}: {detail}")]
    Xml { part: String, detail: String },
    #[error("invalid OPC package: {0}")]
    Package(String),
    #[error("OPC limit exceeded: {0}")]
    Limit(String),
    #[error("unsupported DOCX content: {0}")]
    Unsupported(String),
    #[error("invalid document image: {0}")]
    Image(String),
    #[error(transparent)]
    Book(#[from] revayat_core::CoreError),
    #[error("could not publish DOCX: {0}")]
    Persist(#[from] tempfile::PersistError),
}

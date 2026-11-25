use std::{
    fs,
    io::{ErrorKind, Write},
    path::Path,
};

use revayat_core::{BookDocument, CoreError, update_book};
use serde::{Serialize, de::DeserializeOwned};

use crate::{PipelineError, error::io_error};

pub(crate) fn read(path: &Path) -> Result<Vec<u8>, PipelineError> {
    fs::read(path).map_err(|error| {
        if error.kind() == ErrorKind::NotFound {
            PipelineError::Missing(path.into())
        } else {
            io_error("read", path, error)
        }
    })
}

pub(crate) fn read_json<T: DeserializeOwned>(path: &Path) -> Result<T, PipelineError> {
    Ok(serde_json::from_slice(&read(path)?)?)
}

pub(crate) fn write_json(path: &Path, value: &impl Serialize) -> Result<(), PipelineError> {
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    write(path, &bytes)
}

pub(crate) fn write(path: &Path, bytes: &[u8]) -> Result<(), PipelineError> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    create_dir(parent)?;
    let mut tmp = tempfile::NamedTempFile::new_in(parent)
        .map_err(|err| io_error("create temporary file", parent, err))?;
    tmp.write_all(bytes)
        .and_then(|()| tmp.as_file().sync_all())
        .map_err(|err| io_error("write", path, err))?;
    tmp.persist(path)
        .map_err(|err| io_error("replace", path, err.error))?;
    Ok(())
}

pub(crate) fn create_dir(path: &Path) -> Result<(), PipelineError> {
    fs::create_dir_all(path).map_err(|err| io_error("create directory", path, err))
}

// Keep the typed pipeline refusal while using the shared book transaction.
pub(crate) fn edit_book<T>(
    path: &Path,
    edit: impl FnOnce(&mut BookDocument) -> Result<T, PipelineError>,
) -> Result<T, PipelineError> {
    let mut refusal = None;
    let result = update_book(path, |book| {
        edit(book).map_err(|error| {
            let detail = error.to_string();
            refusal = Some(error);
            CoreError::InvalidBook(detail)
        })
    });
    match refusal {
        Some(error) => Err(error),
        None => Ok(result?),
    }
}

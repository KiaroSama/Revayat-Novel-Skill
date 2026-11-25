use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

use crate::{BookDocument, CoreError, error::io_error, lock_path, sha256_bytes};

#[derive(Debug)]
pub struct BookLock {
    _file: File,
    path: PathBuf,
}

impl BookLock {
    pub fn acquire(path: impl AsRef<Path>) -> Result<Self, CoreError> {
        let path = lock_path(path.as_ref());
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&path)
            .map_err(|error| io_error("open lock", &path, error))?;
        fs2::FileExt::try_lock_exclusive(&file).map_err(|error| {
            if error.raw_os_error() == fs2::lock_contended_error().raw_os_error() {
                CoreError::LockBusy(path.clone())
            } else {
                io_error("acquire lock", &path, error)
            }
        })?;
        // Leave the lock file in place so all handles lock the same inode.
        Ok(Self { _file: file, path })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

pub(crate) fn read_bytes(path: &Path) -> Result<Vec<u8>, CoreError> {
    fs::read(path).map_err(|error| io_error("read", path, error))
}

fn parent_dir(path: &Path) -> &Path {
    path.parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
}

pub(crate) fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), CoreError> {
    let parent = parent_dir(path);
    let mut temporary = tempfile::NamedTempFile::new_in(parent)
        .map_err(|error| io_error("create temporary file in", parent, error))?;
    temporary
        .write_all(bytes)
        .and_then(|()| temporary.as_file().sync_all())
        .map_err(|error| io_error("write and sync temporary file for", path, error))?;
    temporary
        .persist(path)
        .map_err(|error| io_error("replace", path, error.error))?;
    Ok(())
}

pub fn load_book(path: impl AsRef<Path>) -> Result<BookDocument, CoreError> {
    BookDocument::from_json(read_bytes(path.as_ref())?)
}

pub fn save_book(path: impl AsRef<Path>, book: &BookDocument) -> Result<(), CoreError> {
    let path = path.as_ref();
    let _lock = BookLock::acquire(path)?;
    atomic_write(path, book.to_json()?.as_bytes())
}

pub fn replace_book(
    path: impl AsRef<Path>,
    book: &BookDocument,
    expected_sha256: &str,
) -> Result<(), CoreError> {
    let path = path.as_ref();
    let _lock = BookLock::acquire(path)?;
    if sha256_bytes(&read_bytes(path)?) != expected_sha256 {
        return Err(CoreError::StaleFile(path.to_owned()));
    }
    atomic_write(path, book.to_json()?.as_bytes())
}

pub fn update_book<T>(
    path: impl AsRef<Path>,
    edit: impl FnOnce(&mut BookDocument) -> Result<T, CoreError>,
) -> Result<T, CoreError> {
    let path = path.as_ref();
    let _lock = BookLock::acquire(path)?;
    let snapshot = read_bytes(path)?;
    let mut book = BookDocument::from_json(&snapshot)?;
    let output = edit(&mut book)?;
    let updated = book.to_json()?;
    if read_bytes(path)? != snapshot {
        return Err(CoreError::StaleFile(path.to_owned()));
    }
    if updated.as_bytes() != snapshot {
        atomic_write(path, updated.as_bytes())?;
    }
    Ok(output)
}

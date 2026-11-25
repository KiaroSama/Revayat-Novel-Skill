use crate::{InstallError, IoContext, STATE, files};
use fs2::FileExt;
use std::fs::{self, File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

pub(crate) struct Lock {
    pub(crate) state: PathBuf,
    _file: File,
}

pub(crate) fn state(base: &Path) -> Result<Option<PathBuf>, InstallError> {
    let state = base.join(STATE);
    let Some(m) = files::kind(&state)? else {
        return Ok(None);
    };
    if !m.is_dir() {
        return Err(InstallError::InvalidState(state));
    }
    #[cfg(unix)]
    if std::os::unix::fs::PermissionsExt::mode(&m.permissions()) & 0o077 != 0 {
        return Err(InstallError::InvalidState(state));
    }
    Ok(Some(state))
}

impl Lock {
    pub(crate) fn acquire(base: &Path) -> Result<Self, InstallError> {
        let path = base.join(STATE);
        if state(base)?.is_none() {
            #[cfg(unix)]
            let created = std::os::unix::fs::DirBuilderExt::mode(&mut fs::DirBuilder::new(), 0o700)
                .create(&path);
            #[cfg(not(unix))]
            let created = fs::create_dir(&path);
            match created {
                Ok(()) => files::sync_dir(base)?,
                Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {}
                Err(e) => return Err(e).at("Create installer state", &path),
            }
        }
        let state = state(base)?.ok_or(InstallError::InvalidState(path))?;
        let path = state.join("lock");
        files::admit(&path)?;
        if files::kind(&path)?.is_some_and(|m| !m.is_file()) {
            return Err(InstallError::UnsafePath(path));
        }
        let mut opts = OpenOptions::new();
        opts.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        std::os::unix::fs::OpenOptionsExt::mode(&mut opts, 0o600);
        let file = opts.open(&path).at("Open installer lock", &path)?;
        let started = Instant::now();
        let wait = Duration::from_millis(200);
        loop {
            match FileExt::try_lock_exclusive(&file) {
                Ok(()) => break,
                Err(e) if e.raw_os_error() == fs2::lock_contended_error().raw_os_error() => {
                    if started.elapsed() >= wait {
                        return Err(InstallError::Busy);
                    }
                    std::thread::sleep(
                        Duration::from_millis(15).min(wait.saturating_sub(started.elapsed())),
                    );
                }
                Err(e) => return Err(e).at("Acquire installer lock", &path),
            }
        }
        files::admit(&path)?;
        let open_meta = file.metadata().at("Inspect installer lock", &path)?;
        files::unchanged(&path, &open_meta)?;
        Ok(Self { state, _file: file })
    }
}

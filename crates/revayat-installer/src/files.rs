use crate::{InstallError, IoContext};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs::{self, File, Metadata, OpenOptions, Permissions};
use std::io::{self, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use walkdir::{DirEntry, WalkDir};

pub(crate) const MAX_RECORD: u64 = 1024 * 1024;
const MAX_FILES: usize = 20_000;
const MAX_BYTES: u64 = 256 * 1024 * 1024;

pub(crate) fn kind(path: &Path) -> Result<Option<Metadata>, InstallError> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(m) => m,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e).at("Inspect", path),
    };
    #[cfg(windows)]
    let reparse = std::os::windows::fs::MetadataExt::file_attributes(&metadata) & 0x400 != 0;
    #[cfg(not(windows))]
    let reparse = false;
    if reparse || metadata.file_type().is_symlink() || (!metadata.is_dir() && !metadata.is_file()) {
        return Err(InstallError::UnsafePath(path.into()));
    }
    Ok(Some(metadata))
}

pub(crate) fn admit(path: &Path) -> Result<(), InstallError> {
    if !path.is_absolute()
        || path
            .components()
            .any(|part| matches!(part, Component::ParentDir))
    {
        return Err(InstallError::UnsafePath(path.into()));
    }
    let ancestors: Vec<_> = path.ancestors().collect();
    for p in ancestors.into_iter().rev() {
        if kind(p)?.is_some_and(|m| !m.is_dir()) && p != path {
            return Err(InstallError::UnsafePath(p.into()));
        }
    }
    Ok(())
}

pub(crate) fn root(path: &Path) -> Result<PathBuf, InstallError> {
    let absolute = std::path::absolute(path).at("Resolve", path)?;
    admit(&absolute)?;
    if !kind(&absolute)?.is_some_and(|m| m.is_dir()) {
        return Err(InstallError::UnsafePath(absolute));
    }
    absolute.canonicalize().at("Resolve", &absolute)
}

pub(crate) fn relative(path: &Path) -> Result<String, InstallError> {
    let text = path
        .to_str()
        .ok_or_else(|| InstallError::UnsafePath(path.into()))?
        .replace('\\', "/");
    if text.is_empty()
        || text.split('/').any(|part| {
            let stem = part
                .split('.')
                .next()
                .unwrap_or_default()
                .to_ascii_uppercase();
            part.is_empty()
                || matches!(part, "." | "..")
                || part.ends_with(['.', ' '])
                || part
                    .chars()
                    .any(|c| c.is_control() || ":*?\"<>|".contains(c))
                || matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
                || ["COM", "LPT"].iter().any(|prefix| {
                    stem.strip_prefix(prefix).is_some_and(|digit| {
                        matches!(
                            digit,
                            "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
                        )
                    })
                })
        })
    {
        return Err(InstallError::UnsafePath(path.into()));
    }
    #[cfg(unix)]
    if path.as_os_str().as_encoded_bytes().contains(&b'\\') {
        return Err(InstallError::UnsafePath(path.into()));
    }
    Ok(text)
}

pub(crate) fn sync_dir(path: &Path) -> Result<(), InstallError> {
    #[cfg(unix)]
    {
        File::open(path)
            .and_then(|file| file.sync_all())
            .at("Sync directory", path)
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        Ok(())
    }
}

pub(crate) fn ensure_dir(path: &Path) -> Result<(), InstallError> {
    admit(path)?;
    if let Some(m) = kind(path)? {
        return if m.is_dir() {
            Ok(())
        } else {
            Err(InstallError::UnsafePath(path.into()))
        };
    }
    let parent = path
        .parent()
        .ok_or_else(|| InstallError::UnsafePath(path.into()))?;
    ensure_dir(parent)?;
    match fs::create_dir(path) {
        Ok(()) => sync_dir(parent),
        Err(e)
            if e.kind() == io::ErrorKind::AlreadyExists
                && kind(path)?.is_some_and(|m| m.is_dir()) =>
        {
            Ok(())
        }
        Err(e) => Err(e).at("Create directory", path),
    }
}

pub(crate) fn rename(source: &Path, target: &Path) -> Result<(), InstallError> {
    admit(source)?;
    admit(target)?;
    if kind(target)?.is_some() {
        return Err(InstallError::Conflict(target.into()));
    }
    fs::rename(source, target).at("Rename into", target)?;
    for p in [source, target] {
        if let Some(parent) = p.parent() {
            sync_dir(parent)?;
        }
    }
    Ok(())
}

pub(crate) fn tracked(path: &Path) -> Result<(), InstallError> {
    let mut enclosing = None;
    for ancestor in path.ancestors() {
        if kind(ancestor)?.is_some_and(|m| m.is_dir()) && kind(&ancestor.join(".git"))?.is_some() {
            enclosing = Some(ancestor);
            break;
        }
    }
    let Some(repo) = enclosing else {
        return Ok(());
    };
    let rel = path
        .strip_prefix(repo)
        .map_err(|_| InstallError::UnsafePath(path.into()))?;
    if rel.as_os_str().is_empty() {
        return Err(InstallError::Conflict(path.into()));
    }
    let expression = format!(":(top,literal){}", relative(rel)?);
    let mut git = Command::new("git");
    #[cfg(windows)]
    std::os::windows::process::CommandExt::creation_flags(&mut git, 0x0800_0000);
    let mut child = git
        .arg("-C")
        .arg(repo)
        .args(["ls-files", "--cached", "--error-unmatch", "--", &expression])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .at("Check tracked destination", path)?;
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) if status.code() == Some(1) => return Ok(()),
            Ok(Some(_)) => return Err(InstallError::Conflict(path.into())),
            Ok(None) if started.elapsed() < Duration::from_secs(10) => {
                std::thread::sleep(Duration::from_millis(15))
            }
            result => {
                let _ = child.kill();
                let _ = child.wait();
                return match result {
                    Err(e) => Err(e).at("Check tracked destination", path),
                    _ => Err(InstallError::Conflict(path.into())),
                };
            }
        }
    }
}

fn stable(left: &Metadata, right: &Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if (left.dev(), left.ino(), left.ctime(), left.ctime_nsec())
            != (right.dev(), right.ino(), right.ctime(), right.ctime_nsec())
        {
            return false;
        }
    }
    left.len() == right.len()
        && left.modified().ok() == right.modified().ok()
        && left.created().ok() == right.created().ok()
        && left.permissions() == right.permissions()
}

pub(crate) fn open_stable(path: &Path, before: &Metadata) -> Result<File, InstallError> {
    let opened = File::open(path).at("Open", path)?;
    if !stable(before, &opened.metadata().at("Inspect open file", path)?) {
        return Err(InstallError::Conflict(path.into()));
    }
    Ok(opened)
}

pub(crate) fn unchanged(path: &Path, before: &Metadata) -> Result<(), InstallError> {
    if kind(path)?.is_some_and(|after| stable(before, &after)) {
        Ok(())
    } else {
        Err(InstallError::Conflict(path.into()))
    }
}

pub(crate) fn read(path: &Path, limit: u64) -> Result<Vec<u8>, InstallError> {
    admit(path)?;
    let before = kind(path)?
        .filter(Metadata::is_file)
        .ok_or_else(|| InstallError::UnsafePath(path.into()))?;
    if before.len() > limit {
        return Err(InstallError::Payload(path.into()));
    }
    let mut body = Vec::new();
    open_stable(path, &before)?
        .take(limit + 1)
        .read_to_end(&mut body)
        .at("Read", path)?;
    unchanged(path, &before)?;
    if body.len() as u64 > limit {
        return Err(InstallError::Payload(path.into()));
    }
    Ok(body)
}

pub(crate) fn open_new(path: &Path, mode: u32) -> Result<File, InstallError> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut options, mode);
    #[cfg(not(unix))]
    let _ = mode;
    options.open(path).at("Create file", path)
}

pub(crate) fn write(
    path: &Path,
    body: &[u8],
    permissions: Option<Permissions>,
) -> Result<(), InstallError> {
    let mut file = open_new(path, 0o600)?;
    file.write_all(body).at("Write", path)?;
    if let Some(mode) = permissions {
        file.set_permissions(mode).at("Set permissions", path)?;
    }
    file.sync_all().at("Sync file", path)
}

fn allowed(path: &Path) -> bool {
    let extension = path.extension().and_then(|e| e.to_str());
    if path == Path::new("SKILL.md") {
        return true;
    }
    let first = path.iter().next();
    if first.is_some_and(|component| {
        matches!(
            component.to_str(),
            Some("references" | "scripts" | "examples" | "templates" | "assets")
        )
    }) && extension == Some("md")
    {
        return true;
    }
    path.parent() == Some(Path::new("assets/fonts/vazirmatn"))
        && (extension == Some("ttf") || path.file_name().is_some_and(|n| n == "OFL.txt"))
}

fn native_name(name: &str) -> bool {
    matches!(name, "revayat-novel" | "revayat-novel.exe")
}

fn traversable(entry: &DirEntry) -> bool {
    entry.depth() == 0 || !entry.file_name().to_string_lossy().starts_with('.')
}

struct PayloadFile {
    path: String,
    body: Vec<u8>,
    permissions: Permissions,
}

pub(crate) struct Payload(Vec<PayloadFile>);

impl Payload {
    pub(crate) fn collect(source: &Path, bin: Option<&Path>) -> Result<Self, InstallError> {
        let mut payload = Self(Vec::new());
        let mut budget = MAX_BYTES;
        for entry in WalkDir::new(source)
            .follow_links(false)
            .max_depth(32)
            .into_iter()
            .filter_entry(traversable)
        {
            let entry = entry.map_err(|_| InstallError::Payload(source.into()))?;
            if entry.depth() == 0 || entry.file_type().is_dir() {
                continue;
            }
            let path = entry
                .path()
                .strip_prefix(source)
                .map_err(|_| InstallError::UnsafePath(entry.path().into()))?;
            let binary = path.parent() == Some(Path::new("bin"))
                && path
                    .file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(native_name);
            if (binary && bin.is_none()) || allowed(path) {
                payload.push(entry.path(), relative(path)?, binary, &mut budget)?;
            }
        }
        if !payload.0.iter().any(|file| file.path == "SKILL.md") {
            return Err(InstallError::Payload(source.join("SKILL.md")));
        }
        if let Some(bin) = bin {
            let path = std::path::absolute(bin).at("Resolve", bin)?;
            admit(&path)?;
            let m = kind(&path)?.ok_or_else(|| InstallError::Payload(path.clone()))?;
            let mut added = false;
            if m.is_dir() {
                for name in ["revayat-novel", "revayat-novel.exe"] {
                    let file = path.join(name);
                    if kind(&file)?.is_some() {
                        payload.push(&file, format!("bin/{name}"), true, &mut budget)?;
                        added = true;
                    }
                }
            } else {
                let mut signature = [0; 4];
                let count = open_stable(&path, &m)?
                    .read(&mut signature)
                    .at("Read executable header", &path)?;
                if count < 2 {
                    return Err(InstallError::Payload(path));
                }
                let name = if signature.starts_with(b"MZ") {
                    "revayat-novel.exe"
                } else {
                    "revayat-novel"
                };
                payload.push(&path, format!("bin/{name}"), true, &mut budget)?;
                added = true;
            }
            if !added {
                return Err(InstallError::Payload(path));
            }
        }
        payload.0.sort_by(|a, b| a.path.cmp(&b.path));
        let mut seen = BTreeSet::new();
        if payload
            .0
            .iter()
            .any(|f| !seen.insert(f.path.to_lowercase()))
        {
            return Err(InstallError::Payload(source.into()));
        }
        Ok(payload)
    }

    fn push(
        &mut self,
        source: &Path,
        path: String,
        binary: bool,
        budget: &mut u64,
    ) -> Result<(), InstallError> {
        if self.0.len() >= MAX_FILES {
            return Err(InstallError::Payload(source.into()));
        }
        let perms = kind(source)?
            .filter(Metadata::is_file)
            .ok_or_else(|| InstallError::UnsafePath(source.into()))?
            .permissions();
        let body = read(source, *budget)?;
        *budget -= body.len() as u64;
        if binary
            && !(if path.ends_with(".exe") {
                body.starts_with(b"MZ")
            } else {
                body.starts_with(b"\x7fELF")
            })
        {
            return Err(InstallError::Payload(source.into()));
        }
        self.0.push(PayloadFile {
            path,
            body,
            permissions: perms,
        });
        Ok(())
    }

    pub(crate) fn stage(&self, path: &Path) -> Result<(), InstallError> {
        ensure_dir(path)?;
        let mut directories = BTreeSet::new();
        for file in &self.0 {
            let target = path.join(&file.path);
            let parent = target
                .parent()
                .ok_or_else(|| InstallError::UnsafePath(target.clone()))?;
            ensure_dir(parent)?;
            write(&target, &file.body, Some(file.permissions.clone()))?;
            if read(&target, file.body.len() as u64)? != file.body {
                return Err(InstallError::Conflict(target));
            }
            directories.insert(parent.to_path_buf());
        }
        for dir in directories {
            sync_dir(&dir)?;
        }
        sync_dir(path)
    }
}

pub(crate) fn fingerprint(path: &Path) -> Result<Option<String>, InstallError> {
    admit(path)?;
    if kind(path)?.is_none() {
        return Ok(None);
    }
    let mut sha = Sha256::new();
    let mut bytes = 0;
    let mut members = Vec::new();
    for (index, entry) in WalkDir::new(path)
        .follow_links(false)
        .sort_by_file_name()
        .into_iter()
        .enumerate()
    {
        if index >= MAX_FILES {
            return Err(InstallError::Conflict(path.into()));
        }
        let entry = entry.map_err(|_| InstallError::Conflict(path.into()))?;
        let p = entry.path();
        let m = kind(p)?.ok_or_else(|| InstallError::Conflict(p.into()))?;
        if entry.depth() != 0 && entry.file_name() == ".git" {
            return Err(InstallError::Conflict(p.into()));
        }
        let rel = if entry.depth() == 0 {
            String::new()
        } else {
            relative(
                p.strip_prefix(path)
                    .map_err(|_| InstallError::UnsafePath(p.into()))?,
            )?
        };
        sha.update((rel.len() as u64).to_le_bytes());
        sha.update(rel.as_bytes());
        sha.update([u8::from(m.is_file()), u8::from(m.permissions().readonly())]);
        #[cfg(unix)]
        sha.update(std::os::unix::fs::PermissionsExt::mode(&m.permissions()).to_le_bytes());
        if m.is_file() {
            if m.len() > MAX_BYTES - bytes {
                return Err(InstallError::Conflict(p.into()));
            }
            sha.update(m.len().to_le_bytes());
            let mut file = open_stable(p, &m)?.take(m.len() + 1);
            let mut buffer = [0; 64 * 1024];
            let mut count = 0;
            loop {
                let n = file.read(&mut buffer).at("Hash file", p)?;
                if n == 0 {
                    break;
                }
                sha.update(&buffer[..n]);
                count += n as u64;
            }
            if count != m.len() {
                return Err(InstallError::Conflict(p.into()));
            }
            bytes += count;
        }
        members.push((p.to_path_buf(), m));
    }
    for (p, before) in members {
        unchanged(&p, &before)?;
    }
    let fingerprint = sha.finalize().iter().map(|b| format!("{b:02x}")).collect();
    Ok(Some(fingerprint))
}

const BEGIN: &str = "<!-- BEGIN revayat-novel -->";
const END: &str = "<!-- END revayat-novel -->";

pub(crate) fn pointer(original: &[u8], paths: &[PathBuf]) -> Option<Vec<u8>> {
    let original = std::str::from_utf8(original).ok()?;
    let bom = original.strip_prefix('\u{feff}').map_or(0, |_| 3);
    let mut lines = original[bom..].split_inclusive(['\r', '\n']).peekable();
    let mut offset = bom;
    let mut marks = Vec::new();
    while let Some(line) = lines.next() {
        let text = line.trim_end_matches(['\r', '\n']);
        if text.contains(BEGIN) || text.contains(END) {
            marks.push((text, offset, offset + text.len()));
        }
        offset += line.len();
        if line.ends_with('\r') && lines.peek() == Some(&"\n") {
            lines.next();
            offset += 1;
        }
    }
    let (original_region, start, end) = match marks.as_slice() {
        [] => (original, original.len(), original.len()),
        [(BEGIN, start, _), (END, _, end)] => (&original[*start..*end], *start, *end),
        _ => return None,
    };
    let eol = if original_region.contains("\r\n") {
        "\r\n"
    } else {
        "\n"
    };
    let mut updated = original[..start].to_owned();
    if marks.is_empty() && !original.is_empty() && !original.ends_with(['\r', '\n']) {
        updated.push_str(eol);
    }
    updated.push_str(
        &[
            BEGIN,
            "## Revayat Novel: Persian book translation",
            "",
            "To translate a book into Persian and produce a Word file, follow:",
        ]
        .join(eol),
    );
    for path in paths {
        let path = path
            .to_str()?
            .trim_start_matches(r"\\?\")
            .replace('\\', "/");
        if path.chars().any(|c| c.is_control() || c == '`') {
            return None;
        }
        updated.push_str(eol);
        updated.push_str(&format!(
            "- `{path}/SKILL.md` (`{{SKILL_DIR}}` is `{path}`)."
        ));
    }
    updated.push_str(eol);
    updated.push_str(END);
    if marks.is_empty() {
        updated.push_str(eol);
    }
    updated.push_str(&original[end..]);
    Some(updated.into_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pointer_rejects_inline_and_unpaired_markers() {
        assert!(pointer(format!("inline {BEGIN}\n{END}").as_bytes(), &[]).is_none());
        assert!(pointer(BEGIN.as_bytes(), &[]).is_none());
    }

    #[test]
    fn relative_paths_refuse_windows_aliases() {
        for name in ["../other", "C:alternate", "a/NUL.txt", "a/trailing."] {
            assert!(relative(Path::new(name)).is_err(), "{name}");
        }
    }
}

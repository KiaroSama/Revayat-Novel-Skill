use std::{
    env, fs,
    io::{Cursor, Read},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use image::{ImageFormat, ImageReader, Limits, Rgb, RgbImage, imageops::FilterType};
use serde::Serialize;

use crate::{PipelineError, error::io_error, storage};

pub const DEFAULT_RENDER_TIMEOUT: Duration = Duration::from_secs(180);
const ASPECT_TOLERANCE: f64 = 0.08;
const COMPARISON_DIMENSION: u32 = 512;

#[derive(Debug, Clone, Serialize)]
pub struct RenderQaReport {
    pub ok: bool,
    pub source_dimensions: (u32, u32),
    pub target_dimensions: (u32, u32),
    pub aspect_matches: bool,
    pub source_blank: bool,
    pub target_blank: bool,
    pub mean_difference: f64,
    pub changed_pixel_fraction: f64,
    pub diff_path: Option<PathBuf>,
    pub findings: Vec<&'static str>,
}

fn open_png(path: &Path) -> Result<RgbImage, PipelineError> {
    let file = fs::File::open(path).map_err(|error| io_error("open PNG", path, error))?;
    let mut reader = ImageReader::with_format(std::io::BufReader::new(file), ImageFormat::Png);
    let mut limits = Limits::default();
    limits.max_image_width = Some(16_384);
    limits.max_image_height = Some(16_384);
    limits.max_alloc = Some(128 * 1024 * 1024);
    reader.limits(limits);
    let image = reader.decode().map_err(|source| PipelineError::Image {
        path: path.into(),
        source,
    })?;
    let rgba = image.into_rgba8();
    Ok(RgbImage::from_fn(rgba.width(), rgba.height(), |x, y| {
        let [r, g, b, a] = rgba.get_pixel(x, y).0;
        let blend = |channel| {
            ((u16::from(channel) * u16::from(a) + 255 * (255 - u16::from(a))) / 255) as u8
        };
        Rgb([blend(r), blend(g), blend(b)])
    }))
}

pub fn render_qa(
    source_png: &Path,
    target_png: &Path,
    diff_png: Option<&Path>,
) -> Result<RenderQaReport, PipelineError> {
    let source = open_png(source_png)?;
    let target = open_png(target_png)?;
    let source_dimensions = source.dimensions();
    let target_dimensions = target.dimensions();
    let ratio = |image: &RgbImage| f64::from(image.width()) / f64::from(image.height());
    let aspect_matches = (ratio(&source) / ratio(&target) - 1.0).abs() <= ASPECT_TOLERANCE;
    let blank = |image: &RgbImage| {
        !image
            .pixels()
            .any(|pixel| pixel.0.iter().any(|color| *color < 245))
    };
    let (source_blank, target_blank) = (blank(&source), blank(&target));
    let mut findings = Vec::new();
    if !aspect_matches {
        findings.push("page-aspect-mismatch");
    }
    if !source_blank && target_blank {
        findings.push("unexpected-blank-page");
    }
    if source_blank && !target_blank {
        findings.push("unexpected-content-on-blank-page");
    }
    let width = source.width().min(COMPARISON_DIMENSION);
    let height = source.height().min(COMPARISON_DIMENSION);
    let normalize =
        |image: &RgbImage| image::imageops::resize(image, width, height, FilterType::Triangle);
    let (source, target) = (normalize(&source), normalize(&target));
    let mut delta = RgbImage::new(width, height);
    let mut changed = 0u32;
    let mut difference = 0u64;
    for ((left, right), result) in source.pixels().zip(target.pixels()).zip(delta.pixels_mut()) {
        let channels = std::array::from_fn(|i| left.0[i].abs_diff(right.0[i]));
        if channels.iter().any(|value| *value > 8) {
            changed += 1;
        }
        difference += channels.iter().copied().map(u64::from).sum::<u64>();
        *result = Rgb(channels);
    }
    if let Some(path) = diff_png {
        if [source_png, target_png].iter().any(|input| {
            *input == path
                || path
                    .canonicalize()
                    .ok()
                    .zip(input.canonicalize().ok())
                    .is_some_and(|(a, b)| a == b)
        }) {
            return Err(PipelineError::Renderer(
                "diff output would overwrite an input PNG".into(),
            ));
        }
        let mut bytes = Cursor::new(Vec::new());
        image::DynamicImage::ImageRgb8(delta)
            .write_to(&mut bytes, ImageFormat::Png)
            .map_err(|source| PipelineError::Image {
                path: path.into(),
                source,
            })?;
        storage::write(path, bytes.get_ref())?;
    }
    let pixels = f64::from(width * height);
    Ok(RenderQaReport {
        ok: findings.is_empty(),
        source_dimensions,
        target_dimensions,
        aspect_matches,
        source_blank,
        target_blank,
        mean_difference: difference as f64 / (pixels * 3.0 * 255.0),
        changed_pixel_fraction: f64::from(changed) / pixels,
        diff_path: diff_png.map(Path::to_owned),
        findings,
    })
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WordBackend {
    LibreOffice(PathBuf),
    Word(PathBuf),
}

pub fn word_backend() -> Option<WordBackend> {
    libreoffice()
        .map(WordBackend::LibreOffice)
        .or_else(|| word().map(WordBackend::Word))
}

pub(crate) fn locate(names: &[&str]) -> Option<PathBuf> {
    let path = env::var_os("PATH")?;
    env::split_paths(&path)
        .filter(|path| path.is_absolute())
        .flat_map(|path| names.iter().map(move |name| path.join(name)))
        .find(|path| executable(path))
        .and_then(|path| path.canonicalize().ok())
}

fn executable(path: &Path) -> bool {
    let Ok(meta) = path.metadata() else {
        return false;
    };
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        meta.is_file() && meta.permissions().mode() & 0o111 != 0
    }
    #[cfg(not(unix))]
    meta.is_file()
}

pub(crate) fn tesseract() -> Option<PathBuf> {
    let names: &[&str] = if cfg!(windows) {
        &["tesseract.exe"]
    } else {
        &["tesseract"]
    };
    locate(names).or_else(|| {
        if !cfg!(windows) {
            return None;
        }
        ["ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"]
            .into_iter()
            .filter_map(env::var_os)
            .map(PathBuf::from)
            .filter(|base| base.is_absolute())
            .map(|base| base.join("Tesseract-OCR/tesseract.exe"))
            .find(|path| executable(path))
    })
}

pub(crate) fn libreoffice() -> Option<PathBuf> {
    let names: &[&str] = if cfg!(windows) {
        &["soffice.com", "soffice.exe", "libreoffice.exe"]
    } else {
        &["soffice", "libreoffice"]
    };
    locate(names).or_else(|| {
        ["ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"]
            .into_iter()
            .filter_map(env::var_os)
            .map(PathBuf::from)
            .filter(|base| base.is_absolute())
            .flat_map(|base| {
                [
                    base.join("LibreOffice/program/soffice.com"),
                    base.join("LibreOffice/program/soffice.exe"),
                ]
            })
            .find(|path| executable(path))
    })
}

pub(crate) fn word() -> Option<PathBuf> {
    if !cfg!(windows) {
        return None;
    }
    locate(&["WINWORD.EXE"]).or_else(|| {
        ["ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"]
            .into_iter()
            .filter_map(env::var_os)
            .map(PathBuf::from)
            .filter(|base| base.is_absolute())
            .flat_map(|base| {
                [
                    base.join("Microsoft Office/root/Office16/WINWORD.EXE"),
                    base.join("Microsoft Office/Office16/WINWORD.EXE"),
                ]
            })
            .find(|path| executable(path))
    })
}

fn strip_verbatim_prefix(path: &Path) -> PathBuf {
    #[cfg(windows)]
    if let Some(raw) = path.to_str() {
        if let Some(unc) = raw.strip_prefix(r"\\?\UNC\") {
            return PathBuf::from(format!(r"\\{unc}"));
        }
        if let Some(local) = raw.strip_prefix(r"\\?\") {
            return PathBuf::from(local);
        }
    }
    path.to_path_buf()
}

fn profile_uri(profile: &Path) -> Result<String, PipelineError> {
    let path = profile
        .to_str()
        .ok_or_else(|| PipelineError::Renderer("LibreOffice profile path is not Unicode".into()))?;
    let path = path
        .strip_prefix(r"\\?\")
        .unwrap_or(path)
        .replace('\\', "/");
    let mut url = if path.starts_with('/') {
        String::from("file://")
    } else {
        String::from("file:///")
    };
    use std::fmt::Write as _;
    for byte in path.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b':' | b'-' | b'_' | b'.' | b'~') {
            url.push(char::from(byte));
        } else {
            let _ = write!(url, "%{byte:02X}");
        }
    }
    Ok(url)
}

const WORD_EXPORT: &str = r#"
$ErrorActionPreference = 'Stop'
$word = $null
$doc = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $word.Options.UpdateLinksAtOpen = $false
    $doc = $word.Documents.Open($env:REVAYAT_RENDER_INPUT, $false, $true, $false)
    $doc.ExportAsFixedFormat($env:REVAYAT_RENDER_OUTPUT, 17)
} finally {
    if ($null -ne $doc) { $doc.Close($false) }
    if ($null -ne $word) { $word.Quit($false) }
}
"#;

fn terminate(child: &mut Child) {
    let pid = child.id().to_string();
    #[cfg(windows)]
    let mut command = {
        let mut command = Command::new("taskkill.exe");
        command.args(["/F", "/T", "/PID", &pid]);
        command
    };
    #[cfg(unix)]
    let mut command = {
        let mut command = Command::new("/bin/kill");
        command.args(["-KILL", "--", &format!("-{pid}")]);
        command
    };
    #[cfg(any(windows, unix))]
    let _ = command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    let _ = child.kill();
    let _ = child.wait();
}

fn run_bounded(
    mut command: Command,
    launcher: &Path,
    timeout: Duration,
) -> Result<(), PipelineError> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let mut child = command
        .spawn()
        .map_err(|error| io_error("launch renderer", launcher, error))?;
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) if status.success() => return Ok(()),
            Ok(Some(status)) => {
                return Err(PipelineError::Renderer(format!(
                    "{} exited with {status}",
                    launcher.display()
                )));
            }
            Ok(None) if started.elapsed() < timeout => thread::sleep(
                Duration::from_millis(25).min(timeout.saturating_sub(started.elapsed())),
            ),
            Ok(None) => {
                terminate(&mut child);
                return Err(PipelineError::RendererTimeout(launcher.into()));
            }
            Err(error) => {
                terminate(&mut child);
                return Err(io_error("wait for renderer", launcher, error));
            }
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct WordRenderReport {
    pub backend: WordBackend,
    pub pdf: PathBuf,
}

pub fn wordrender(
    docx: &Path,
    out_dir: &Path,
    timeout: Duration,
) -> Result<WordRenderReport, PipelineError> {
    if timeout.is_zero() {
        return Err(PipelineError::Renderer("timeout must be positive".into()));
    }
    let backend = word_backend().ok_or(PipelineError::RendererUnavailable)?;
    let original = docx
        .canonicalize()
        .map_err(|error| io_error("resolve DOCX", docx, error))?;
    if !original.is_file() {
        return Err(PipelineError::Renderer("input is not a DOCX file".into()));
    }
    let stem = original
        .file_stem()
        .ok_or_else(|| PipelineError::Renderer("DOCX filename has no stem".into()))?;
    storage::create_dir(out_dir)?;
    let output_dir = out_dir
        .canonicalize()
        .map_err(|error| io_error("resolve output", out_dir, error))?;
    let temp = tempfile::tempdir_in(&output_dir)
        .map_err(|error| io_error("create renderer scratch", &output_dir, error))?;
    let mut rendered = temp.path().join(stem);
    rendered.set_extension("pdf");
    match &backend {
        WordBackend::LibreOffice(program) => {
            let profile = temp.path().join("profile");
            let mut command = Command::new(program);
            command
                .arg(format!("-env:UserInstallation={}", profile_uri(&profile)?))
                .args([
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                ])
                .arg(temp.path())
                .arg(&original);
            run_bounded(command, program, timeout)?;
        }
        WordBackend::Word(_) => {
            let shell = locate(&["pwsh.exe"]).ok_or(PipelineError::RendererUnavailable)?;
            let word_input = strip_verbatim_prefix(&original);
            let word_output = strip_verbatim_prefix(&rendered);
            let mut command = Command::new(&shell);
            command
                .args([
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    WORD_EXPORT,
                ])
                .env("REVAYAT_RENDER_INPUT", word_input)
                .env("REVAYAT_RENDER_OUTPUT", word_output);
            run_bounded(command, &shell, timeout)?;
        }
    }
    let mut pdf = fs::File::open(&rendered)
        .map_err(|error| io_error("read renderer output", &rendered, error))?;
    let mut header = [0; 5];
    pdf.read_exact(&mut header)
        .map_err(|error| io_error("read PDF header", &rendered, error))?;
    if &header != b"%PDF-" {
        return Err(PipelineError::Renderer(
            "renderer did not produce a PDF".into(),
        ));
    }
    drop(pdf);
    let filename = rendered
        .file_name()
        .ok_or_else(|| PipelineError::Renderer("PDF filename is missing".into()))?;
    let pdf = output_dir.join(filename);
    fs::rename(&rendered, &pdf).map_err(|error| io_error("publish PDF", &pdf, error))?;
    Ok(WordRenderReport { backend, pdf })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_private_profile_path() -> Result<(), PipelineError> {
        let path = if cfg!(windows) {
            Path::new(r"C:\a space\کتاب")
        } else {
            Path::new("/a space/کتاب")
        };
        let uri = profile_uri(path)?;
        assert!(uri.starts_with("file:///"));
        assert!(uri.ends_with("a%20space/%DA%A9%D8%AA%D8%A7%D8%A8"));
        Ok(())
    }
}

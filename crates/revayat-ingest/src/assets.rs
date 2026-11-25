use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::{Cursor, Read, Write},
    path::Path,
};

use image::{ImageFormat, ImageReader};
use revayat_core::{AssetRecord, Block, TextRun, sha256_bytes};

use crate::{
    IngestError,
    error::{invalid, io},
};

pub(crate) const MAX_IMAGE_BYTES: u64 = 64 * 1024 * 1024;
pub(crate) const MAX_PIXELS: u64 = 40_000_000;
pub(crate) const MAX_TEXT_BYTES: u64 = 16 * 1024 * 1024;

pub(crate) fn read_file(path: &Path, limit: u64) -> Result<Vec<u8>, IngestError> {
    let file = File::open(path).map_err(|e| io("open", path, e))?;
    if !file
        .metadata()
        .map_err(|e| io("inspect", path, e))?
        .is_file()
    {
        return Err(IngestError::UnsafePath(path.display().to_string()));
    }
    let mut bytes = Vec::new();
    file.take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| io("read", path, e))?;
    if bytes.len() as u64 > limit {
        return Err(IngestError::Limit("file size"));
    }
    Ok(bytes)
}

pub(crate) fn write_new(path: &Path, bytes: &[u8]) -> Result<(), IngestError> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent).map_err(|e| io("create", parent, e))?;
    let mut temp = tempfile::NamedTempFile::new_in(parent).map_err(|e| io("stage", parent, e))?;
    temp.write_all(bytes).map_err(|e| io("write", path, e))?;
    match temp.persist_noclobber(path) {
        Ok(_) => Ok(()),
        Err(e)
            if e.error.kind() == std::io::ErrorKind::AlreadyExists
                && fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_file())
                && read_file(path, bytes.len() as u64).is_ok_and(|existing| existing == bytes) =>
        {
            Ok(())
        }
        Err(e) => Err(io("publish without overwriting", path, e.error)),
    }
}

pub(crate) fn check_pixels(width: u32, height: u32) -> Result<(), IngestError> {
    if width == 0 || height == 0 || u64::from(width) * u64::from(height) > MAX_PIXELS {
        return Err(IngestError::Limit("raster pixel area"));
    }
    Ok(())
}

pub(crate) fn dimensions(bytes: &[u8]) -> Result<(ImageFormat, u32, u32), IngestError> {
    let format = image::guess_format(bytes)?;
    let (w, h) = ImageReader::with_format(Cursor::new(bytes), format).into_dimensions()?;
    check_pixels(w, h)?;
    Ok((format, w, h))
}

pub(crate) fn store_image(
    dir: &Path,
    records: &mut BTreeMap<String, AssetRecord>,
    bytes: &[u8],
    original: bool,
) -> Result<AssetRecord, IngestError> {
    let (format, width_px, height_px) = dimensions(bytes)?;
    let ext = match format {
        ImageFormat::Jpeg => "jpg",
        ImageFormat::Png => "png",
        ImageFormat::Gif => "gif",
        ImageFormat::WebP => "webp",
        ImageFormat::Tiff => "tiff",
        ImageFormat::Bmp => "bmp",
        _ => return Err(invalid("image", "unsupported asset format")),
    };
    let hash = sha256_bytes(bytes);
    let id = format!("image-{hash}");
    if let Some(record) = records.get_mut(&id) {
        record.original_bytes_preserved &= original;
        return Ok(record.clone());
    }
    let rel_path = format!("{id}.{ext}");
    write_new(&dir.join(&rel_path), bytes)?;
    let record = AssetRecord {
        id: id.clone(),
        media_type: format.to_mime_type().into(),
        sha256: hash,
        width_px,
        height_px,
        rel_path,
        original_bytes_preserved: original,
    };
    records.insert(id, record.clone());
    Ok(record)
}

pub(crate) fn image_block(id: String, asset: AssetRecord, alt: &str) -> Block {
    Block::Image {
        id,
        asset_id: asset.id,
        width_emu: u64::from(asset.width_px) * 9_525,
        height_emu: u64::from(asset.height_px) * 9_525,
        caption: (!alt.trim().is_empty())
            .then(|| plain_run(alt))
            .into_iter()
            .collect(),
    }
}

pub(crate) fn plain_run(text: impl Into<String>) -> TextRun {
    TextRun {
        text: text.into(),
        ..TextRun::default()
    }
}

pub(crate) fn paragraph(id: String, runs: Vec<TextRun>) -> Block {
    let rtl = runs.iter().any(|r| revayat_core::has_persian(&r.text));
    Block::Paragraph {
        id,
        runs,
        style: None,
        align: None,
        rtl,
    }
}

pub(crate) fn source_title(path: &Path) -> String {
    path.file_stem()
        .and_then(|p| p.to_str())
        .unwrap_or("Untitled")
        .to_owned()
}

pub(crate) fn canonical_local(
    root: &Path,
    candidate: &Path,
) -> Result<std::path::PathBuf, IngestError> {
    let candidate = candidate
        .canonicalize()
        .map_err(|e| io("resolve", candidate, e))?;
    if !candidate.starts_with(root) {
        return Err(IngestError::UnsafePath(candidate.display().to_string()));
    }
    Ok(candidate)
}

pub(crate) fn uri_part(value: &str) -> Result<String, IngestError> {
    fn hex(b: u8) -> Option<u8> {
        (b as char).to_digit(16).map(|n| n as u8)
    }
    let mut input = value.bytes();
    let mut result = Vec::with_capacity(value.len());
    while let Some(b) = input.next() {
        let b = if b == b'%' {
            let a = input.next().and_then(hex);
            let b = input.next().and_then(hex);
            let (Some(a), Some(b)) = (a, b) else {
                return Err(IngestError::UnsafePath(value.into()));
            };
            a * 16 + b
        } else {
            b
        };
        if matches!(b, b'/' | b'\\' | b':') || b.is_ascii_control() {
            return Err(IngestError::UnsafePath(value.into()));
        }
        result.push(b);
    }
    String::from_utf8(result).map_err(|_| IngestError::UnsafePath(value.into()))
}

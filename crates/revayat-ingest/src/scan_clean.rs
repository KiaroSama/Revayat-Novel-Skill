use std::{
    io::Cursor,
    path::{Path, PathBuf},
};

use image::{DynamicImage, ImageFormat, ImageReader, RgbaImage};
use serde::{Deserialize, Serialize};

use crate::{
    IngestError,
    assets::{MAX_IMAGE_BYTES, dimensions, read_file, write_new},
    error::invalid,
};

const SATURATION: u32 = 16;
const MAX_COLORED_FRACTION: f64 = 0.06;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScanSurvey {
    pub width_px: u32,
    pub height_px: u32,
    pub dpi: Option<[f64; 2]>,
    pub colored_fraction: f64,
    pub cleaned: bool,
}

fn colored(pixel: &[u8; 4]) -> bool {
    let min = pixel[0].min(pixel[1]).min(pixel[2]);
    let max = pixel[0].max(pixel[1]).max(pixel[2]);
    pixel[3] != 0 && u32::from(max - min) * 255 > SATURATION * u32::from(max)
}

fn density(bytes: &[u8], format: ImageFormat) -> Option<[f64; 2]> {
    match format {
        ImageFormat::Png => {
            let mut chunks = bytes.get(8..)?;
            while chunks.len() >= 12 {
                let len = u32::from_be_bytes(chunks.get(..4)?.try_into().ok()?) as usize;
                let kind = chunks.get(4..8)?;
                if kind == b"IDAT" {
                    break;
                }
                if kind == b"pHYs" && len == 9 && chunks.get(16) == Some(&1) {
                    let x = u32::from_be_bytes(chunks.get(8..12)?.try_into().ok()?);
                    let y = u32::from_be_bytes(chunks.get(12..16)?.try_into().ok()?);
                    return (x > 0 && y > 0)
                        .then_some([f64::from(x) * 0.0254, f64::from(y) * 0.0254]);
                }
                chunks = chunks.get(len.checked_add(12)?..)?;
            }
            None
        }
        ImageFormat::Jpeg => {
            let mut rest = bytes.get(2..)?;
            while let [0xff, marker, ..] = rest {
                if *marker == 0xff {
                    rest = rest.get(1..)?;
                    continue;
                }
                if matches!(marker, 0xda | 0xd9) {
                    break;
                }
                let len = usize::from(u16::from_be_bytes(rest.get(2..4)?.try_into().ok()?));
                if len < 2 {
                    break;
                }
                let data = rest.get(4..len + 2)?;
                if *marker == 0xe0 && data.starts_with(b"JFIF\0") {
                    let scale = match data.get(7)? {
                        1 => 1.0,
                        2 => 2.54,
                        _ => return None,
                    };
                    let x = u16::from_be_bytes(data.get(8..10)?.try_into().ok()?);
                    let y = u16::from_be_bytes(data.get(10..12)?.try_into().ok()?);
                    return (x > 0 && y > 0)
                        .then_some([f64::from(x) * scale, f64::from(y) * scale]);
                }
                rest = rest.get(len + 2..)?;
            }
            None
        }
        ImageFormat::Bmp => {
            let size = u32::from_le_bytes(bytes.get(14..18)?.try_into().ok()?);
            if size < 40 {
                return None;
            }
            let x = i32::from_le_bytes(bytes.get(38..42)?.try_into().ok()?);
            let y = i32::from_le_bytes(bytes.get(42..46)?.try_into().ok()?);
            (x > 0 && y > 0).then_some([f64::from(x) * 0.0254, f64::from(y) * 0.0254])
        }
        _ => None,
    }
}

fn load(path: &Path) -> Result<(ScanSurvey, RgbaImage), IngestError> {
    let bytes = read_file(path, MAX_IMAGE_BYTES)?;
    let (format, width_px, height_px) = dimensions(&bytes)?;
    let mut reader = ImageReader::with_format(Cursor::new(&bytes), format);
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(width_px);
    limits.max_image_height = Some(height_px);
    limits.max_alloc = Some(384 * 1024 * 1024);
    reader.limits(limits);
    let pixels = reader.decode()?.into_rgba8();
    let count = pixels.pixels().filter(|p| colored(&p.0)).count();
    let colored_fraction = count as f64 / f64::from(width_px) / f64::from(height_px);
    let dpi = density(&bytes, format);
    Ok((
        ScanSurvey {
            width_px,
            height_px,
            dpi,
            colored_fraction,
            cleaned: false,
        },
        pixels,
    ))
}

pub fn survey(input: &Path) -> Result<ScanSurvey, IngestError> {
    Ok(load(input)?.0)
}

fn paint(info: &mut ScanSurvey, pixels: &mut RgbaImage, force: bool, ghost_threshold: Option<u8>) {
    if (info.colored_fraction == 0.0 && ghost_threshold.is_none())
        || (!force && info.colored_fraction > MAX_COLORED_FRACTION)
    {
        return;
    }
    let cut = u32::from(ghost_threshold.unwrap_or(244).max(16));
    for pixel in pixels.pixels_mut().filter(|p| p.0[3] != 0) {
        let [r, g, b, a] = pixel.0;
        let brightness =
            (299 * u32::from(r) + 587 * u32::from(g) + 114 * u32::from(b) + 500) / 1_000;
        if (colored(&pixel.0) || brightness >= cut) && [r, g, b] != [255; 3] {
            pixel.0 = [255, 255, 255, a];
            info.cleaned = true;
        }
    }
}

fn output_png(path: &Path, pixels: RgbaImage, dpi: Option<[f64; 2]>) -> Result<(), IngestError> {
    let mut png = Cursor::new(Vec::new());
    DynamicImage::ImageRgba8(pixels).write_to(&mut png, ImageFormat::Png)?;
    write_new(path, &with_png_dpi(png.into_inner(), dpi))
}

pub(crate) fn with_png_dpi(mut png: Vec<u8>, dpi: Option<[f64; 2]>) -> Vec<u8> {
    if let Some([x, y]) = dpi {
        let mut chunk = b"pHYs".to_vec();
        chunk.extend_from_slice(&((x / 0.0254).round() as u32).to_be_bytes());
        chunk.extend_from_slice(&((y / 0.0254).round() as u32).to_be_bytes());
        chunk.push(1);
        let mut crc = u32::MAX;
        for b in &chunk {
            crc ^= u32::from(*b);
            for _ in 0..8 {
                crc = (crc >> 1) ^ (0xedb8_8320 & 0u32.wrapping_sub(crc & 1));
            }
        }
        chunk.extend_from_slice(&(!crc).to_be_bytes());
        chunk.splice(0..0, 9u32.to_be_bytes());
        png.splice(33..33, chunk);
    }
    png
}

pub fn clean(
    input: &Path,
    output: &Path,
    force: bool,
    ghost_threshold: Option<u8>,
) -> Result<ScanSurvey, IngestError> {
    if input == output {
        return Err(IngestError::UnsafePath(output.display().to_string()));
    }
    if !output
        .extension()
        .and_then(std::ffi::OsStr::to_str)
        .is_some_and(|e| e.eq_ignore_ascii_case("png"))
    {
        return Err(invalid("scan output", "output must use the .png extension"));
    }
    let (mut info, mut pixels) = load(input)?;
    paint(&mut info, &mut pixels, force, ghost_threshold);
    output_png(output, pixels, info.dpi)?;
    Ok(info)
}

pub fn preview(
    input: &Path,
    out_dir: &Path,
    force: bool,
    ghost_threshold: Option<u8>,
) -> Result<Vec<PathBuf>, IngestError> {
    let (mut info, mut pixels) = load(input)?;
    let original = out_dir.join("original.png");
    output_png(&original, pixels.clone(), info.dpi)?;
    paint(&mut info, &mut pixels, force, ghost_threshold);
    let cleaned = out_dir.join("cleaned.png");
    output_png(&cleaned, pixels, info.dpi)?;
    Ok(vec![original, cleaned])
}

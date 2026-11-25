use std::{collections::BTreeMap, io::Cursor};

use image::{ImageFormat, ImageReader};
use revayat_core::sha256_bytes;
use roxmltree::Node;

use crate::{
    OoxmlError,
    opc::{Package, R, Relationship, drawing_extents},
};

pub(crate) fn image_info(
    bytes: &[u8],
) -> Result<(&'static str, &'static str, u32, u32), OoxmlError> {
    let reader = ImageReader::new(Cursor::new(bytes)).with_guessed_format()?;
    let (kind, ext) = match reader.format() {
        Some(ImageFormat::Png) => ("image/png", "png"),
        Some(ImageFormat::Jpeg) => ("image/jpeg", "jpg"),
        Some(ImageFormat::Gif) => ("image/gif", "gif"),
        Some(ImageFormat::Bmp) => ("image/bmp", "bmp"),
        Some(ImageFormat::Tiff) => ("image/tiff", "tiff"),
        Some(ImageFormat::WebP) => ("image/webp", "webp"),
        _ => {
            return Err(OoxmlError::Image(
                "unknown or unsupported raster encoding".into(),
            ));
        }
    };
    let (w, h) = reader
        .into_dimensions()
        .map_err(|e| OoxmlError::Image(e.to_string()))?;
    if w == 0 || h == 0 || u64::from(w) * u64::from(h) > 100_000_000 {
        return Err(OoxmlError::Limit("image dimensions".into()));
    }
    Ok((kind, ext, w, h))
}

pub(crate) struct Media {
    pub target: String,
    pub bytes: Vec<u8>,
    pub sha256: String,
    pub media_type: &'static str,
    pub extension: &'static str,
    pub width_px: u32,
    pub height_px: u32,
    pub width_emu: u64,
    pub height_emu: u64,
}

pub(crate) fn resolve_drawing(
    pkg: &mut Package,
    rels: &BTreeMap<String, Relationship>,
    types: &BTreeMap<String, String>,
    n: Node<'_, '_>,
) -> Result<Vec<Media>, OoxmlError> {
    drawing_extents(n)?
        .into_iter()
        .map(|d| {
            let rel = rels
                .get(d.id)
                .filter(|r| !r.external && r.kind == format!("{R}/image"))
                .ok_or_else(|| {
                    OoxmlError::Package(format!("missing embedded image relationship {}", d.id))
                })?;
            let bytes = pkg.read(&rel.target)?;
            let (media_type, extension, width_px, height_px) = image_info(&bytes)?;
            if types.get(&rel.target).map(String::as_str) != Some(media_type) {
                return Err(OoxmlError::Image(format!(
                    "wrong content type for {}",
                    rel.target
                )));
            }
            Ok(Media {
                target: rel.target.clone(),
                sha256: sha256_bytes(&bytes),
                bytes,
                media_type,
                extension,
                width_px,
                height_px,
                width_emu: d.width_emu,
                height_emu: d.height_emu,
            })
        })
        .collect()
}

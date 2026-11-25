use std::{
    collections::{BTreeMap, HashMap},
    io::Cursor,
    path::{Path, PathBuf},
};

use hayro::{PixmapSettings, RenderCache, RenderSettings};
use hayro_interpret::{
    BlendMode, CacheKey, ClipPath, Context, Device, DrawMode, DrawProps, Image, ImageData,
    ImageDrawProps, InterpreterCache, InterpreterSettings, LumaData, SoftMask, TransformExt,
    font::{Glyph, GlyphRun},
    hayro_cmap::BfString,
    interpret_page,
    kurbo::{BezPath, Point, Rect, Shape},
};
use hayro_syntax::{Filter, Pdf, page::Page};
use image::{DynamicImage, ImageBuffer, ImageFormat};
use revayat_core::{
    AssetRecord, Block, BookDocument, Chapter, PageGeometry, SourceFormat, TextRun, sha256_bytes,
};
use serde::{Deserialize, Serialize};

use crate::{
    IngestError,
    assets::{check_pixels, paragraph, read_file, source_title, store_image, write_new},
    error::invalid,
};

const MAX_PDF_BYTES: u64 = 512 * 1024 * 1024;
const MAX_PAGES: usize = 20_000;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PdfPageSummary {
    pub page_number: u32,
    pub width_pt: f32,
    pub height_pt: f32,
    pub text_coverage_ratio: f64,
    pub scanned: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PdfSummary {
    pub page_count: usize,
    pub pages: Vec<PdfPageSummary>,
    pub text_coverage_ratio: f64,
    pub scanned_pages: Vec<u32>,
}

fn open(bytes: Vec<u8>) -> Result<Pdf, IngestError> {
    let pdf = Pdf::new(bytes).map_err(|e| invalid("PDF", format!("{e:?}")))?;
    if pdf.pages().is_empty() {
        return Err(invalid("PDF", "no pages"));
    }
    if pdf.pages().len() > MAX_PAGES {
        return Err(IngestError::Limit("PDF page count"));
    }
    Ok(pdf)
}

#[derive(Clone, Copy)]
struct Position {
    first: Point,
    last: Point,
    end: Point,
    size: f64,
    horizontal: bool,
}

impl Position {
    fn line_gap(self, other: Self) -> f64 {
        if self.horizontal {
            (self.first.y - other.first.y).abs()
        } else {
            (self.first.x - other.first.x).abs()
        }
    }

    fn text_gap(self, other: Self) -> f64 {
        if self.horizontal {
            (other.first.x - self.end.x) * (self.last.x - self.first.x).signum()
        } else {
            (other.first.y - self.end.y) * (self.last.y - self.first.y).signum()
        }
    }
}

struct TextLine {
    position: Position,
    runs: Vec<TextRun>,
}

enum Content {
    Text(TextLine),
    Image(AssetRecord, Rect),
}

struct PageContent<'a, 'dir> {
    area: Rect,
    chars: usize,
    text_area: f64,
    image_area: f64,
    content: Vec<Content>,
    image_output: Option<&'a mut ImageOutput<'dir>>,
    error: Option<IngestError>,
}

struct ImageOutput<'a> {
    dir: &'a Path,
    records: BTreeMap<String, AssetRecord>,
    seen: HashMap<u128, AssetRecord>,
}

impl Device<'_> for PageContent<'_, '_> {
    fn draw_path(&mut self, _: &BezPath, _: DrawProps<'_>, _: &DrawMode) {}
    fn push_clip_path(&mut self, _: &ClipPath) {}
    fn push_transparency_group(&mut self, _: f32, _: Option<SoftMask<'_>>, _: BlendMode) {}
    fn pop_clip(&mut self) {}
    fn pop_transparency_group(&mut self) {}

    fn draw_glyph_run(&mut self, run: &GlyphRun<'_, '_>, props: DrawProps<'_>, mode: &DrawMode) {
        let mut text = String::new();
        let mut first: Option<Point> = None;
        let mut last = Point::ZERO;
        let mut end = Point::ZERO;
        let mut size = 0.0_f64;
        let mut glyph_area = 0.0;
        let mut italic = false;
        let mut bold = matches!(mode, DrawMode::Stroke(_) | DrawMode::FillAndStroke(..));
        if let Some(glyph) = run.glyphs().first()
            && let Glyph::Outline(outline) = &**glyph
            && let Some(font) = outline.font_data()
        {
            bold |= font.weight.is_some_and(|weight| weight >= 600);
            italic = font.is_italic;
        }
        for glyph in run.glyphs() {
            let transform = props.transform * glyph.transform();
            let [a, b, c, d, _, _] = transform.as_coeffs();
            let pt = transform * Point::ZERO;
            let font_size = c.hypot(d) * 1000.0;
            if !font_size.is_finite() || !pt.x.is_finite() || !pt.y.is_finite() {
                continue;
            }
            let incline = (a * c + b * d).abs() / (a.hypot(b) * c.hypot(d));
            italic |= incline > 0.12;
            let advance = match &**glyph {
                Glyph::Outline(outline) => {
                    let outline_box = outline.outline().bounding_box();
                    glyph_area += transform
                        .transform_rect_bbox(outline_box)
                        .intersect(self.area)
                        .area();
                    f64::from(outline.advance_width().unwrap_or(500.0))
                }
                Glyph::Type3(_) => 500.0,
            };
            let current_end = transform * Point::new(advance, 0.0);
            if first.is_some()
                && !text.ends_with(char::is_whitespace)
                && pt.distance(end) > font_size * 0.28
                && pt.distance(last) > font_size * 0.5
            {
                text.push(' ');
            }
            match glyph.as_unicode() {
                Some(BfString::Char(ch)) => text.push(ch),
                Some(BfString::String(value)) => text.push_str(&value),
                None => text.push(char::REPLACEMENT_CHARACTER),
            }
            first.get_or_insert(pt);
            last = pt;
            end = current_end;
            size = size.max(font_size);
        }
        let Some(first) = first else {
            return;
        };
        if text.trim().is_empty() {
            return;
        }
        let horizontal = (last.x - first.x).abs() >= (last.y - first.y).abs();
        let position = Position {
            first,
            last,
            end,
            size,
            horizontal,
        };
        if let Some(Content::Text(line)) = self.content.last_mut()
            && line.position.first == first
            && line.position.last == last
            && let Some(prev) = line.runs.last_mut()
            && prev.text == text
        {
            prev.bold |= bold;
            return;
        }
        self.chars += text
            .chars()
            .filter(|c| !c.is_whitespace() && *c != char::REPLACEMENT_CHARACTER)
            .count();
        self.text_area += glyph_area;
        let mut style = TextRun {
            text,
            bold,
            italic,
            ..TextRun::default()
        };
        if let Some(Content::Text(line)) = self.content.last_mut()
            && line.position.horizontal == horizontal
            && line.position.line_gap(position) < line.position.size.min(size) * 0.35
            && (line.position.text_gap(position) > -size * 0.7)
            && line.position.text_gap(position) < size * 2.5
        {
            if line.position.text_gap(position) > size * 0.18
                && !style.text.starts_with(char::is_whitespace)
                && line
                    .runs
                    .last()
                    .is_some_and(|p| !p.text.ends_with(char::is_whitespace))
            {
                style.text.insert(0, ' ');
            }
            line.position.last = last;
            line.position.end = end;
            line.runs.push(style);
        } else {
            self.content.push(Content::Text(TextLine {
                position,
                runs: vec![style],
            }));
        }
    }

    fn draw_image(&mut self, img: Image<'_, '_>, props: ImageDrawProps<'_>) {
        let Image::Raster(image) = img else {
            return;
        };
        let rect = props
            .transform
            .transform_rect_bbox(Rect::new(0.0, 0.0, 1.0, 1.0))
            .intersect(self.area);
        self.image_area = self.image_area.max(rect.area());
        let Some(output) = self.image_output.as_deref_mut() else {
            return;
        };
        if self.error.is_some() {
            return;
        }
        let key = image.cache_key();
        let result = (|| {
            if let Some(asset) = output.seen.get(&key) {
                return Ok(asset.clone());
            }
            check_pixels(image.width(), image.height())?;
            let stream = image.stream();
            let dict = stream.dict();
            use hayro_syntax::object::dict::keys::{DECODE, MASK, SMASK};
            let original = stream.filters().as_slice() == [Filter::DctDecode]
                && !dict.contains_key(DECODE)
                && !dict.contains_key(MASK)
                && !dict.contains_key(SMASK);
            let asset = if original {
                store_image(output.dir, &mut output.records, &stream.raw_data(), true)?
            } else {
                let mut pixels = None;
                image.with_rgba(|data, alpha| pixels = Some((data, alpha)), None);
                let (data, alpha) = pixels
                    .ok_or_else(|| invalid("PDF image", "image decoder returned no pixels"))?;
                store_image(
                    output.dir,
                    &mut output.records,
                    &lossless_png(data, alpha)?,
                    false,
                )?
            };
            output.seen.insert(key, asset.clone());
            Ok(asset)
        })();
        match result {
            Ok(asset) => self.content.push(Content::Image(asset, rect)),
            Err(error) => self.error = Some(error),
        }
    }
}

fn lossless_png(data: ImageData, alpha: Option<LumaData>) -> Result<Vec<u8>, IngestError> {
    let (width, height) = (data.width(), data.height());
    check_pixels(width, height)?;
    let alpha = alpha
        .map(|a| {
            if (a.width, a.height) != (width, height) {
                return Err(invalid("PDF image", "alpha and image dimensions differ"));
            }
            Ok(a.data)
        })
        .transpose()?;
    let img = match (data, alpha) {
        (ImageData::Rgb(rgb), None) => {
            ImageBuffer::from_raw(width, height, rgb.data).map(DynamicImage::ImageRgb8)
        }
        (ImageData::Luma(gray), None) => {
            ImageBuffer::from_raw(width, height, gray.data).map(DynamicImage::ImageLuma8)
        }
        (ImageData::Rgb(rgb), Some(alpha)) => {
            let values = rgb
                .data
                .as_chunks::<3>()
                .0
                .iter()
                .zip(alpha)
                .flat_map(|(p, a)| [p[0], p[1], p[2], a])
                .collect();
            ImageBuffer::from_raw(width, height, values).map(DynamicImage::ImageRgba8)
        }
        (ImageData::Luma(gray), Some(alpha)) => {
            let values = gray
                .data
                .into_iter()
                .zip(alpha)
                .flat_map(|(l, a)| [l, a])
                .collect();
            ImageBuffer::from_raw(width, height, values).map(DynamicImage::ImageLumaA8)
        }
    }
    .ok_or_else(|| invalid("PDF image", "pixel buffer does not match dimensions"))?;
    let mut png = Cursor::new(Vec::new());
    img.write_to(&mut png, ImageFormat::Png)?;
    Ok(png.into_inner())
}

fn page_dimensions(page: &Page<'_>) -> Result<(f32, f32), IngestError> {
    let (w, h) = page.render_dimensions();
    if !(w > 0.0 && h > 0.0 && w.is_finite() && h.is_finite()) {
        return Err(invalid(
            "PDF geometry",
            "page dimensions must be finite and positive",
        ));
    }
    Ok((w, h))
}

fn interpret<'a>(
    page: &Page<'a>,
    cache: &InterpreterCache<'a>,
    output: Option<&mut ImageOutput<'_>>,
) -> Result<(PageContent<'static, 'static>, f32, f32), IngestError> {
    let (w, h) = page_dimensions(page)?;
    let area = Rect::new(0.0, 0.0, f64::from(w), f64::from(h));
    let mut context = Context::new(
        page.initial_transform(true).to_kurbo(),
        area,
        cache,
        page.xref(),
        InterpreterSettings::default(),
    );
    let mut device = PageContent {
        area,
        chars: 0,
        text_area: 0.0,
        image_area: 0.0,
        content: Vec::new(),
        image_output: output,
        error: None,
    };
    interpret_page(page, &mut context, &mut device);
    if let Some(error) = device.error {
        return Err(error);
    }
    Ok((
        PageContent {
            area,
            chars: device.chars,
            text_area: device.text_area,
            image_area: device.image_area,
            content: device.content,
            image_output: None,
            error: None,
        },
        w,
        h,
    ))
}

pub fn inspect_pdf(path: &Path) -> Result<PdfSummary, IngestError> {
    let pdf = open(read_file(path, MAX_PDF_BYTES)?)?;
    let cache = InterpreterCache::new();
    let mut pages = Vec::new();
    let mut scanned_pages = Vec::new();
    let (mut text_area, mut document_area) = (0.0, 0.0);
    for (index, page) in pdf.pages().iter().enumerate() {
        let (content, width_pt, height_pt) = interpret(page, &cache, None)?;
        let area = content.area.area();
        let page_number = (index + 1) as u32;
        let scanned = content.chars < 60 && content.image_area >= area * 0.5;
        if scanned {
            scanned_pages.push(page_number);
        }
        let visible_text_area = content.text_area.min(area);
        text_area += visible_text_area;
        document_area += area;
        pages.push(PdfPageSummary {
            page_number,
            width_pt,
            height_pt,
            text_coverage_ratio: visible_text_area / area,
            scanned,
        });
    }
    Ok(PdfSummary {
        page_count: pages.len(),
        pages,
        text_coverage_ratio: text_area / document_area,
        scanned_pages,
    })
}

fn append_blocks(chapter: &mut Chapter, content: Vec<Content>, page: usize) {
    let mut sizes: Vec<_> = content
        .iter()
        .filter_map(|c| match c {
            Content::Text(line) => Some(line.position.size),
            _ => None,
        })
        .collect();
    sizes.sort_by(f64::total_cmp);
    let body_size = sizes.get(sizes.len() / 2).copied().unwrap_or(0.0);
    let mut prior: Option<Position> = None;
    for (n, item) in content.into_iter().enumerate() {
        let id = format!("p{page:04}-b{n:05}");
        match item {
            Content::Text(line) => {
                let is_heading = body_size > 0.0 && line.position.size > body_size * 1.25;
                if !is_heading
                    && prior.is_some_and(|p| {
                        p.horizontal == line.position.horizontal
                            && p.line_gap(line.position) <= body_size * 1.65
                            && (p.first.x - line.position.first.x).abs() <= body_size
                    })
                    && let Some(Block::Paragraph { runs, .. }) = chapter.blocks.last_mut()
                {
                    let mut line_runs = line.runs;
                    if let Some(first) = line_runs.first_mut()
                        && !first.text.starts_with(char::is_whitespace)
                    {
                        first.text.insert(0, ' ');
                    }
                    runs.extend(line_runs);
                } else {
                    chapter.blocks.push(if is_heading {
                        Block::Heading {
                            id,
                            level: 2,
                            runs: line.runs,
                        }
                    } else {
                        paragraph(id, line.runs)
                    });
                }
                prior = (!is_heading).then_some(line.position);
            }
            Content::Image(asset, rect) => {
                let width_emu = if rect.width() > 0.0 {
                    (rect.width() * 12_700.0).round().max(1.0) as u64
                } else {
                    u64::from(asset.width_px) * 9_525
                };
                let height_emu = if rect.height() > 0.0 {
                    (rect.height() * 12_700.0).round().max(1.0) as u64
                } else {
                    u64::from(asset.height_px) * 9_525
                };
                chapter.blocks.push(Block::Image {
                    id,
                    asset_id: asset.id,
                    width_emu,
                    height_emu,
                    caption: Vec::new(),
                });
                prior = None;
            }
        }
    }
}

pub fn read_pdf(path: &Path, assets_dir: &Path) -> Result<BookDocument, IngestError> {
    let bytes = read_file(path, MAX_PDF_BYTES)?;
    let digest = sha256_bytes(&bytes);
    let structure = lopdf::Document::load_mem(&bytes)?;
    let pdf = open(bytes)?;
    let title = pdf
        .metadata()
        .title
        .as_ref()
        .and_then(|bytes| {
            lopdf::decode_text_string(&lopdf::Object::string_literal(bytes.to_vec())).ok()
        })
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| source_title(path));
    let mut book = BookDocument::new(SourceFormat::Pdf, digest, &title, "und");
    let mut outline = BTreeMap::new();
    if let Ok(toc) = structure.get_toc() {
        for heading in toc.toc {
            outline.entry(heading.page).or_insert(heading.title);
        }
    }
    let cache = InterpreterCache::new();
    let mut images = ImageOutput {
        dir: assets_dir,
        records: BTreeMap::new(),
        seen: HashMap::new(),
    };
    let mut chapter = Chapter {
        id: "ch0001".into(),
        index: 1,
        title,
        ..Chapter::default()
    };
    for (index, page) in pdf.pages().iter().enumerate() {
        let page_number = index + 1;
        if let Some(title) = outline.remove(&page_number) {
            if !chapter.blocks.is_empty() {
                book.chapters.push(chapter);
                let index = (book.chapters.len() + 1) as u32;
                chapter = Chapter {
                    id: format!("ch{index:04}"),
                    index,
                    title,
                    ..Chapter::default()
                };
            } else {
                chapter.title = title;
            }
        }
        let (page_content, width, height) = interpret(page, &cache, Some(&mut images))?;
        if index == 0 {
            book.page_geometry =
                PageGeometry::from_points(f64::from(width), f64::from(height), [0.0; 4])?;
        } else if !chapter.blocks.is_empty() {
            chapter.blocks.push(Block::PageBreak {
                id: format!("p{page_number:04}-break"),
            });
        }
        append_blocks(&mut chapter, page_content.content, page_number);
    }
    if !chapter.blocks.is_empty() {
        book.chapters.push(chapter);
    }
    if book.chapters.is_empty() {
        return Err(invalid("PDF", "no extractable text or raster images"));
    }
    book.assets = images.records.into_values().collect();
    book.validate()?;
    Ok(book)
}

pub fn render_pdf_pages(
    path: &Path,
    out_dir: &Path,
    dpi: f32,
) -> Result<Vec<PathBuf>, IngestError> {
    if !dpi.is_finite() || dpi <= 0.0 {
        return Err(invalid("PDF render", "DPI must be finite and positive"));
    }
    let pdf = open(read_file(path, MAX_PDF_BYTES)?)?;
    let scale = dpi / 72.0;
    for page in pdf.pages().iter() {
        let (w, h) = page_dimensions(page)?;
        let (w, h) = (w * scale, h * scale);
        if w > f32::from(u16::MAX) || h > f32::from(u16::MAX) {
            return Err(IngestError::Limit("PDF render dimension"));
        }
        check_pixels(w as u32, h as u32)?;
    }
    let cache = RenderCache::new();
    let settings = PixmapSettings {
        x_scale: scale,
        y_scale: scale,
        bg_color: hayro::vello_cpu::color::palette::css::WHITE,
    };
    let interpret = InterpreterSettings::default();
    let render = RenderSettings::default();
    pdf.pages()
        .iter()
        .enumerate()
        .map(|(i, page)| {
            let output = out_dir.join(format!("page-{:04}.png", i + 1));
            let png = hayro::render(page, &cache, &interpret, &render, &settings)
                .into_png()
                .map_err(|e| invalid("PDF render", e.to_string()))?;
            write_new(
                &output,
                &crate::scan_clean::with_png_dpi(png, Some([f64::from(dpi); 2])),
            )?;
            Ok(output)
        })
        .collect()
}

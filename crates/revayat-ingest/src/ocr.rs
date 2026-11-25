use std::{
    collections::BTreeMap,
    env, fs,
    io::{Read, Seek},
    path::{Component, Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use revayat_core::{Block, BookDocument, Chapter, SourceFormat, sha256_bytes};
use serde::{Deserialize, Serialize};

use crate::{
    IngestError,
    assets::{
        MAX_IMAGE_BYTES, MAX_TEXT_BYTES, canonical_local, image_block, paragraph, plain_run,
        read_file, source_title, store_image,
    },
    error::{invalid, io},
};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OcrToken {
    pub text: String,
    pub confidence: Option<f32>,
    pub uncertain: bool,
    pub low_confidence: bool,
    pub block_number: u32,
    pub paragraph_number: u32,
    pub line_number: u32,
    pub word_number: u32,
    pub bbox_px: [u32; 4],
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct OcrPage {
    pub page_number: u32,
    pub width_px: u32,
    pub height_px: u32,
    pub tokens: Vec<OcrToken>,
}

fn executable(path: &Path) -> bool {
    let Ok(metadata) = path.metadata() else {
        return false;
    };
    if !metadata.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        metadata.permissions().mode() & 0o111 != 0
    }
    #[cfg(windows)]
    {
        path.extension()
            .and_then(std::ffi::OsStr::to_str)
            .is_some_and(|ext| ext.eq_ignore_ascii_case("exe") || ext.eq_ignore_ascii_case("com"))
    }
    #[cfg(not(any(unix, windows)))]
    true
}

pub fn locate_tool(names: &[&str]) -> Option<PathBuf> {
    let path = env::var_os("PATH")?;
    for name in names {
        if !matches!(
            Path::new(name).components().collect::<Vec<_>>().as_slice(),
            [Component::Normal(_)]
        ) {
            continue;
        }
        for dir in env::split_paths(&path).filter(|dir| dir.is_absolute()) {
            let candidate = dir.join(name);
            if executable(&candidate) {
                return candidate.canonicalize().ok();
            }
            #[cfg(windows)]
            if candidate.extension().is_none() {
                for ext in ["exe", "com"] {
                    let with_ext = candidate.with_extension(ext);
                    if executable(&with_ext) {
                        return with_ext.canonicalize().ok();
                    }
                }
            }
        }
    }
    None
}

const TSV_COLUMNS: &str =
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext";

pub fn parse_tesseract_tsv(tsv: &str) -> Result<OcrPage, IngestError> {
    if tsv.len() as u64 > MAX_TEXT_BYTES {
        return Err(IngestError::Limit("OCR text size"));
    }
    let mut lines = tsv.trim_start_matches('\u{feff}').lines();
    if lines.next() != Some(TSV_COLUMNS) {
        return Err(invalid("Tesseract TSV", "missing or invalid header"));
    }
    let mut page = OcrPage::default();
    let mut page_dimensions = false;
    for (index, line) in lines.enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let fields: Vec<_> = line.splitn(12, '\t').collect();
        let err = || invalid("Tesseract TSV", format!("invalid row {}", index + 2));
        let [
            level,
            number,
            block,
            par,
            line,
            word,
            left,
            top,
            width,
            height,
            conf,
            text,
        ] = fields.as_slice()
        else {
            return Err(err());
        };
        let nat = |value: &str| value.parse::<u32>().map_err(|_| err());
        let (level, number) = (nat(level)?, nat(number)?);
        if !(1..=5).contains(&level) || number == 0 {
            return Err(err());
        }
        if page.page_number != 0 && number != page.page_number {
            return Err(invalid("Tesseract TSV", "expected a single page"));
        }
        page.page_number = number;
        let (left, top, width, height) = (nat(left)?, nat(top)?, nat(width)?, nat(height)?);
        let (right, bottom) = (
            left.checked_add(width).ok_or_else(err)?,
            top.checked_add(height).ok_or_else(err)?,
        );
        if level == 1 {
            if page_dimensions
                || left != 0
                || top != 0
                || width == 0
                || height == 0
                || right < page.width_px
                || bottom < page.height_px
            {
                return Err(err());
            }
            page.width_px = width;
            page.height_px = height;
            page_dimensions = true;
        } else if page_dimensions {
            if right > page.width_px || bottom > page.height_px {
                return Err(err());
            }
        } else {
            page.width_px = page.width_px.max(right);
            page.height_px = page.height_px.max(bottom);
        }
        if level != 5 || text.trim().is_empty() {
            continue;
        }
        let confidence = match conf.trim() {
            "" | "-1" | "-1.0" => None,
            value => {
                let n = value.parse::<f32>().map_err(|_| err())?;
                if n == -1.0 {
                    None
                } else if (0.0..=100.0).contains(&n) {
                    Some(n)
                } else {
                    return Err(err());
                }
            }
        };
        page.tokens.push(OcrToken {
            text: text.trim().into(),
            confidence,
            uncertain: confidence.is_none_or(|n| n < 85.0),
            low_confidence: confidence.is_none_or(|n| n < 60.0),
            block_number: nat(block)?,
            paragraph_number: nat(par)?,
            line_number: nat(line)?,
            word_number: nat(word)?,
            bbox_px: [left, top, right, bottom],
        });
    }
    if page.page_number == 0 {
        return Err(invalid("Tesseract TSV", "no page rows"));
    }
    Ok(page)
}

pub fn run_tesseract_tsv(
    image: &Path,
    lang: &str,
    timeout_secs: u64,
) -> Result<OcrPage, IngestError> {
    if lang.is_empty()
        || !lang.split('+').all(|part| {
            !part.is_empty() && part.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
        })
    {
        return Err(invalid(
            "OCR language",
            "use installed language codes separated by '+'",
        ));
    }
    if timeout_secs == 0 {
        return Err(IngestError::Timeout("tesseract"));
    }
    let tool = locate_tool(&["tesseract"]).ok_or(IngestError::ToolMissing("tesseract"))?;
    let absolute = image.canonicalize().map_err(|e| io("resolve", image, e))?;
    crate::assets::dimensions(&read_file(&absolute, MAX_IMAGE_BYTES)?)?;
    let mut output = tempfile::tempfile().map_err(|e| io("stage OCR output for", image, e))?;
    let write_end = output
        .try_clone()
        .map_err(|e| io("duplicate OCR output for", image, e))?;
    let deadline = Instant::now() + Duration::from_secs(timeout_secs.min(3600));
    let mut child = Command::new(tool)
        .arg(absolute)
        .args(["stdout", "-l", lang, "tsv"])
        .stdin(Stdio::null())
        .stdout(Stdio::from(write_end))
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| io("start tesseract for", image, e))?;
    let status = loop {
        let poll = (|| {
            if Instant::now() >= deadline {
                return Err(IngestError::Timeout("tesseract"));
            }
            if output
                .metadata()
                .map_err(|e| io("inspect OCR output for", image, e))?
                .len()
                > MAX_TEXT_BYTES
            {
                return Err(IngestError::Limit("OCR output size"));
            }
            child
                .try_wait()
                .map_err(|e| io("wait for tesseract on", image, e))
        })();
        match poll {
            Ok(Some(status)) => break status,
            Ok(None) => thread::sleep(
                Duration::from_millis(20).min(deadline.saturating_duration_since(Instant::now())),
            ),
            Err(error) => {
                let _ = child.kill();
                child
                    .wait()
                    .map_err(|e| io("reap tesseract for", image, e))?;
                return Err(error);
            }
        }
    };
    if !status.success() {
        return Err(IngestError::ToolFailed {
            tool: "tesseract",
            status,
        });
    }
    output
        .rewind()
        .map_err(|e| io("rewind OCR output for", image, e))?;
    let mut tsv = String::new();
    output
        .take(MAX_TEXT_BYTES + 1)
        .read_to_string(&mut tsv)
        .map_err(|e| io("read OCR output for", image, e))?;
    parse_tesseract_tsv(&tsv)
}

#[derive(Deserialize)]
struct MinerItem {
    #[serde(rename = "type")]
    kind: String,
    #[serde(default)]
    text: String,
    #[serde(default)]
    text_level: Option<u8>,
    page_idx: u32,
    #[serde(default)]
    img_path: Option<String>,
    #[serde(default)]
    img_caption: Vec<String>,
    #[serde(default)]
    table_caption: Vec<String>,
    #[serde(default)]
    img_footnote: Vec<String>,
    #[serde(default)]
    table_footnote: Vec<String>,
    #[serde(default)]
    table_body: Option<String>,
}

fn miner_file(path: &Path) -> Result<PathBuf, IngestError> {
    if !path.is_dir() {
        return Ok(path.into());
    }
    let root = path.canonicalize().map_err(|e| io("resolve", path, e))?;
    let mut pending = vec![(root, 0)];
    let mut candidate = None;
    let mut count = 0;
    while let Some((dir, level)) = pending.pop() {
        for entry in fs::read_dir(&dir).map_err(|e| io("list", &dir, e))? {
            let entry = entry.map_err(|e| io("read directory entry in", &dir, e))?;
            let kind = entry
                .file_type()
                .map_err(|e| io("inspect", &entry.path(), e))?;
            count += 1;
            if count > 10_000 {
                return Err(IngestError::Limit("MinerU directory entries"));
            }
            if kind.is_dir() && level < 8 {
                pending.push((entry.path(), level + 1));
            }
            if kind.is_file()
                && entry
                    .file_name()
                    .to_string_lossy()
                    .ends_with("_content_list.json")
                && candidate.replace(entry.path()).is_some()
            {
                return Err(invalid(
                    "MinerU",
                    "multiple content lists; pass the desired JSON file",
                ));
            }
        }
    }
    candidate.ok_or_else(|| invalid("MinerU", "no *_content_list.json found"))
}

pub fn import_mineru(path: &Path) -> Result<BookDocument, IngestError> {
    read_mineru(path, None)
}

pub fn import_mineru_with_assets(
    path: &Path,
    assets_dir: &Path,
) -> Result<BookDocument, IngestError> {
    read_mineru(path, Some(assets_dir))
}

fn read_mineru(path: &Path, assets_dir: Option<&Path>) -> Result<BookDocument, IngestError> {
    let path = miner_file(path)?;
    let raw = read_file(&path, MAX_TEXT_BYTES)?;
    let mut items: Vec<MinerItem> = serde_json::from_slice(&raw)?;
    items.sort_by_key(|item| item.page_idx);
    if items.last().is_some_and(|item| item.page_idx >= 20_000) {
        return Err(IngestError::Limit("MinerU page count"));
    }
    let root = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let root = root.canonicalize().map_err(|e| io("resolve", root, e))?;
    let default_assets = root.join("revayat-assets");
    let assets_dir = assets_dir.unwrap_or(&default_assets);
    let mut book = BookDocument::new(
        SourceFormat::Pdf,
        sha256_bytes(&raw),
        source_title(&path),
        "und",
    );
    let mut assets = BTreeMap::new();
    let mut page = None;
    let mut blocks = Vec::new();
    for (i, item) in items.into_iter().enumerate() {
        if page.is_some_and(|p| p != item.page_idx) {
            blocks.push(Block::PageBreak {
                id: format!("p{:04}-break", item.page_idx + 1),
            });
        }
        page = Some(item.page_idx);
        let id = format!("b{:06}", i + 1);
        if !item.text.trim().is_empty() {
            let runs = vec![plain_run(item.text.trim())];
            blocks.push(
                if let Some(level) = item.text_level.filter(|&n| (1..=9).contains(&n)) {
                    Block::Heading {
                        id: id.clone(),
                        level,
                        runs,
                    }
                } else {
                    paragraph(id.clone(), runs)
                },
            );
        }
        if item.kind == "table"
            && let Some(table) = &item.table_body
            && !table.trim().is_empty()
        {
            let html = scraper::Html::parse_document(table);
            blocks.extend(crate::dom::blocks(
                html.root_element(),
                &format!("{id}-table"),
                false,
                |_, _, _| Ok(None),
            )?);
        }
        if let Some(relative) = item.img_path.filter(|s| !s.is_empty()) {
            let rel = Path::new(&relative);
            if relative.contains(['\\', ':'])
                || !rel
                    .components()
                    .all(|part| matches!(part, Component::Normal(_)))
            {
                return Err(IngestError::UnsafePath(relative));
            }
            let file = canonical_local(&root, &root.join(rel))?;
            let pixels = read_file(&file, MAX_IMAGE_BYTES)?;
            let asset = store_image(assets_dir, &mut assets, &pixels, true)?;
            blocks.push(image_block(format!("{id}-image"), asset, ""));
        }
        for (j, caption) in item
            .img_caption
            .into_iter()
            .chain(item.table_caption)
            .chain(item.img_footnote)
            .chain(item.table_footnote)
            .enumerate()
        {
            if !caption.trim().is_empty() {
                blocks.push(paragraph(
                    format!("{id}-caption{j}"),
                    vec![plain_run(caption.trim())],
                ));
            }
        }
    }
    if !blocks.iter().any(|b| !matches!(b, Block::PageBreak { .. })) {
        return Err(invalid("MinerU", "no readable content"));
    }
    let title = crate::dom::first_heading(&blocks).unwrap_or_else(|| book.title.clone());
    book.chapters.push(Chapter {
        id: "ch0001".into(),
        index: 1,
        title,
        blocks,
        provenance: None,
    });
    book.assets = assets.into_values().collect();
    book.validate()?;
    Ok(book)
}

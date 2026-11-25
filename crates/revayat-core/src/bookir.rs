use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::{ChapterProvenance, CoreError, PageGeometry, check_note_graph, is_safe_filename};

pub const BOOK_SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SourceFormat {
    Docx,
    Epub,
    Pdf,
    Html,
    Text,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct TextRun {
    pub text: String,
    pub bold: bool,
    pub italic: bool,
    pub underline: bool,
    pub strikethrough: bool,
    pub superscript: bool,
    pub subscript: bool,
    pub footnote_ref: Option<String>,
    pub hyperlink: Option<String>,
    pub lang: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct TableCell {
    pub blocks: Vec<Block>,
    pub grid_span: Option<u32>,
    pub row_span: Option<u32>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct TableRow {
    pub cells: Vec<TableCell>,
    pub header: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Block {
    Paragraph {
        id: String,
        runs: Vec<TextRun>,
        #[serde(default)]
        style: Option<String>,
        #[serde(default)]
        align: Option<String>,
        #[serde(default)]
        rtl: bool,
    },
    Heading {
        id: String,
        level: u8,
        runs: Vec<TextRun>,
    },
    Table {
        id: String,
        rows: Vec<TableRow>,
    },
    Image {
        id: String,
        asset_id: String,
        width_emu: u64,
        height_emu: u64,
        #[serde(default)]
        caption: Vec<TextRun>,
    },
    PageBreak {
        id: String,
    },
    SceneBreak {
        id: String,
    },
}

impl Block {
    pub fn id(&self) -> &str {
        match self {
            Self::Paragraph { id, .. }
            | Self::Heading { id, .. }
            | Self::Table { id, .. }
            | Self::Image { id, .. }
            | Self::PageBreak { id }
            | Self::SceneBreak { id } => id,
        }
    }

    pub fn runs(&self) -> &[TextRun] {
        match self {
            Self::Paragraph { runs, .. } | Self::Heading { runs, .. } => runs,
            Self::Image { caption, .. } => caption,
            Self::Table { .. } | Self::PageBreak { .. } | Self::SceneBreak { .. } => &[],
        }
    }
}

pub(crate) fn walk_blocks(blocks: &[Block], visit: &mut impl FnMut(&Block)) {
    for block in blocks {
        visit(block);
        if let Block::Table { rows, .. } = block {
            for cell in rows.iter().flat_map(|row| &row.cells) {
                walk_blocks(&cell.blocks, visit);
            }
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Chapter {
    pub id: String,
    #[serde(default)]
    pub index: u32,
    pub title: String,
    pub blocks: Vec<Block>,
    #[serde(default)]
    pub provenance: Option<ChapterProvenance>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Footnote {
    pub id: String,
    pub number: u32,
    pub blocks: Vec<Block>,
    #[serde(default)]
    pub origin_chapter: Option<String>,
    #[serde(default)]
    pub anchor_block: Option<String>,
    #[serde(default)]
    pub origin: FootnoteOrigin,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FootnoteOrigin {
    #[default]
    Source,
    Translator,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AssetRecord {
    pub id: String,
    pub media_type: String,
    pub sha256: String,
    pub width_px: u32,
    pub height_px: u32,
    pub rel_path: String,
    pub original_bytes_preserved: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BookDocument {
    pub schema_version: u32,
    pub source_format: SourceFormat,
    pub source_sha256: String,
    pub title: String,
    pub language: String,
    pub page_geometry: PageGeometry,
    pub chapters: Vec<Chapter>,
    pub footnotes: Vec<Footnote>,
    pub assets: Vec<AssetRecord>,
}

pub(crate) fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|b| b.is_ascii_hexdigit())
}

fn unique<'a>(ids: &mut BTreeSet<&'a str>, id: &'a str, what: &str) -> Result<(), CoreError> {
    if id.is_empty() || id.trim() != id || id.chars().any(char::is_control) || !ids.insert(id) {
        return Err(CoreError::InvalidBook(format!(
            "invalid or duplicate {what} ID: {id:?}"
        )));
    }
    Ok(())
}

fn validate_blocks<'a>(
    blocks: &'a [Block],
    ids: &mut BTreeSet<&'a str>,
    assets: &BTreeSet<&str>,
    depth: usize,
) -> Result<(), CoreError> {
    if depth > 32 {
        return Err(CoreError::InvalidBook(
            "table nesting exceeds 32 levels".into(),
        ));
    }
    for block in blocks {
        unique(ids, block.id(), "block")?;
        let valid = match block {
            Block::Heading { level, .. } => (1..=9).contains(level),
            Block::Image {
                asset_id,
                width_emu,
                height_emu,
                ..
            } => assets.contains(asset_id.as_str()) && *width_emu > 0 && *height_emu > 0,
            Block::Table { rows, .. } => {
                for cell in rows.iter().flat_map(|row| &row.cells) {
                    if cell.grid_span == Some(0) || cell.row_span == Some(0) {
                        return Err(CoreError::InvalidBook(format!(
                            "zero span in table {}",
                            block.id()
                        )));
                    }
                    validate_blocks(&cell.blocks, ids, assets, depth + 1)?;
                }
                !rows.is_empty() && rows.iter().all(|row| !row.cells.is_empty())
            }
            Block::Paragraph { .. } | Block::PageBreak { .. } | Block::SceneBreak { .. } => true,
        };
        if !valid
            || block
                .runs()
                .iter()
                .any(|run| run.superscript && run.subscript)
        {
            return Err(CoreError::InvalidBook(format!(
                "invalid block: {}",
                block.id()
            )));
        }
    }
    Ok(())
}

impl BookDocument {
    pub fn new(
        source_format: SourceFormat,
        source_sha256: impl Into<String>,
        title: impl Into<String>,
        language: impl Into<String>,
    ) -> Self {
        Self {
            schema_version: BOOK_SCHEMA_VERSION,
            source_format,
            source_sha256: source_sha256.into(),
            title: title.into(),
            language: language.into(),
            page_geometry: PageGeometry::default(),
            chapters: Vec::new(),
            footnotes: Vec::new(),
            assets: Vec::new(),
        }
    }

    pub fn validate(&self) -> Result<(), CoreError> {
        if self.schema_version != BOOK_SCHEMA_VERSION {
            return Err(CoreError::InvalidBook(format!(
                "unsupported schema version {}",
                self.schema_version
            )));
        }
        if !is_sha256(&self.source_sha256) || self.language.trim().is_empty() {
            return Err(CoreError::InvalidBook(
                "source SHA-256 and language are required".into(),
            ));
        }
        self.page_geometry.validate()?;
        let mut asset_ids = BTreeSet::new();
        let mut asset_paths = BTreeSet::new();
        for asset in &self.assets {
            unique(&mut asset_ids, &asset.id, "asset")?;
            if !is_sha256(&asset.sha256)
                || asset.width_px == 0
                || asset.height_px == 0
                || !asset.media_type.starts_with("image/")
                || asset.media_type.len() == 6
                || !asset.rel_path.split('/').all(is_safe_filename)
                || !asset_paths.insert(asset.rel_path.to_lowercase())
            {
                return Err(CoreError::InvalidBook(format!(
                    "invalid image asset: {}",
                    asset.id
                )));
            }
        }
        let mut chapter_ids = BTreeSet::new();
        let mut block_ids = BTreeSet::new();
        for chapter in &self.chapters {
            unique(&mut chapter_ids, &chapter.id, "chapter")?;
            if let Some(provenance) = &chapter.provenance {
                provenance.validate()?;
            }
            validate_blocks(&chapter.blocks, &mut block_ids, &asset_ids, 0)?;
        }
        let mut note_ids = BTreeSet::new();
        let mut note_numbers = BTreeSet::new();
        for note in &self.footnotes {
            unique(&mut note_ids, &note.id, "footnote")?;
            if note.number == 0
                || !note_numbers.insert(note.number)
                || note
                    .origin_chapter
                    .as_deref()
                    .is_some_and(|chapter| !chapter_ids.contains(chapter))
            {
                return Err(CoreError::InvalidBook(format!(
                    "invalid footnote number or chapter: {}",
                    note.id
                )));
            }
            validate_blocks(&note.blocks, &mut block_ids, &asset_ids, 0)?;
        }
        if let Some(problem) = check_note_graph(self).first() {
            return Err(CoreError::InvalidBook(format!(
                "{}: {}",
                problem.unit, problem.detail
            )));
        }
        Ok(())
    }

    pub fn from_json(bytes: impl AsRef<[u8]>) -> Result<Self, CoreError> {
        let book: Self = serde_json::from_slice(bytes.as_ref())?;
        book.validate()?;
        Ok(book)
    }

    pub fn to_json(&self) -> Result<String, CoreError> {
        self.validate()?;
        Ok(serde_json::to_string_pretty(self)? + "\n")
    }
}

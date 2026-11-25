use std::{
    fs::File,
    io::{self, Read},
    path::Path,
};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::{
    Block, BookDocument, CoreError, FootnoteOrigin,
    bookir::{is_sha256, walk_blocks},
    error::io_error,
};

pub fn sha256_bytes(bytes: &[u8]) -> String {
    hex_digest(&Sha256::digest(bytes))
}

fn hex_digest(digest: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(digest.len() * 2);
    for &byte in digest {
        out.push(char::from(DIGITS[usize::from(byte >> 4)]));
        out.push(char::from(DIGITS[usize::from(byte & 15)]));
    }
    out
}

pub fn sha256_file(path: impl AsRef<Path>) -> Result<String, CoreError> {
    let path = path.as_ref();
    let mut file = File::open(path).map_err(|error| io_error("open for hashing", path, error))?;
    hash_reader(&mut file).map_err(|error| io_error("hash", path, error))
}

fn hash_reader(reader: &mut impl Read) -> io::Result<String> {
    let mut hash = Sha256::new();
    let mut buffer = [0; 8192];
    loop {
        match reader.read(&mut buffer) {
            Ok(0) => return Ok(hex_digest(&hash.finalize())),
            Ok(count) => hash.update(&buffer[..count]),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PageProvenance {
    pub page_number: u32,
    pub source_sha256: String,
}

impl PageProvenance {
    pub fn validate(&self) -> Result<(), CoreError> {
        if self.page_number == 0 || !is_sha256(&self.source_sha256) {
            return Err(CoreError::InvalidBook(
                "page provenance requires a one-based page number and SHA-256".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ChapterProvenance {
    pub source_sha256: String,
    #[serde(default)]
    pub source_locator: Option<String>,
    #[serde(default)]
    pub pages: Vec<PageProvenance>,
}

impl ChapterProvenance {
    pub fn validate(&self) -> Result<(), CoreError> {
        if !is_sha256(&self.source_sha256)
            || self
                .pages
                .windows(2)
                .any(|pages| pages[0].page_number >= pages[1].page_number)
        {
            return Err(CoreError::InvalidBook(
                "chapter provenance requires SHA-256 and increasing page numbers".into(),
            ));
        }
        self.pages.iter().try_for_each(PageProvenance::validate)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PublishedKind {
    Title,
    ChapterTitle,
    Paragraph,
    Heading,
    Caption,
    Footnote,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PublishedUnit {
    pub id: String,
    pub kind: PublishedKind,
    pub text: String,
    pub chapter_id: Option<String>,
    pub footnote_origin: Option<FootnoteOrigin>,
}

pub fn published_units(book: &BookDocument) -> Vec<PublishedUnit> {
    let mut units = Vec::new();
    let mut add = |id: &str, kind, text: String, chapter_id: Option<&str>, footnote_origin| {
        if !text.trim().is_empty() {
            units.push(PublishedUnit {
                id: id.into(),
                kind,
                text,
                chapter_id: chapter_id.map(str::to_owned),
                footnote_origin,
            });
        }
    };
    add(
        "title",
        PublishedKind::Title,
        book.title.clone(),
        None,
        None,
    );
    for chapter in &book.chapters {
        let owner = Some(chapter.id.as_str());
        add(
            &chapter.id,
            PublishedKind::ChapterTitle,
            chapter.title.clone(),
            owner,
            None,
        );
        walk_blocks(&chapter.blocks, &mut |block| {
            let kind = match block {
                Block::Paragraph { .. } => PublishedKind::Paragraph,
                Block::Heading { .. } => PublishedKind::Heading,
                Block::Image { .. } => PublishedKind::Caption,
                Block::Table { .. } | Block::PageBreak { .. } | Block::SceneBreak { .. } => return,
            };
            add(
                block.id(),
                kind,
                block.runs().iter().map(|run| run.text.as_str()).collect(),
                owner,
                None,
            );
        });
    }
    for note in &book.footnotes {
        walk_blocks(&note.blocks, &mut |block| {
            add(
                block.id(),
                PublishedKind::Footnote,
                block.runs().iter().map(|run| run.text.as_str()).collect(),
                note.origin_chapter.as_deref(),
                Some(note.origin),
            );
        });
    }
    units
}

pub fn published_digest(book: &BookDocument) -> Result<String, CoreError> {
    // A layout, note anchor, or source asset change can alter the delivered book without changing its prose.
    let bytes = book.to_json()?;
    Ok(format!("pub3:{}", sha256_bytes(bytes.as_bytes())))
}

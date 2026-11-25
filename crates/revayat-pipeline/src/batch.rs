use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};

use revayat_core::{
    BookDocument, FootnoteOrigin, TranslationStage, load_book, published_digest, save_book,
};
use serde::{Deserialize, Serialize};

use crate::{
    PipelineError, chunk, merge, storage,
    units::{source_units, walk},
};

const BATCH: &str = "batch.json";
const SHARD: &str = "shard.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BatchMode {
    SingleBookShards,
    MultiBook,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ShardStatus {
    Planned,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BatchShard {
    pub shard_id: String,
    pub chapter_ids: Vec<String>,
    pub word_count: usize,
    pub status: ShardStatus,
    pub assigned_stage: TranslationStage,
    pub work_dir: PathBuf,
    pub source_revision: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BatchManifest {
    pub mode: BatchMode,
    pub book_id: String,
    pub revision: String,
    pub max_chars_per_shard: Option<usize>,
    pub shards: Vec<BatchShard>,
}

fn partition(book: &BookDocument, chapter_ids: &[String], carries_title: bool) -> BookDocument {
    let mut shard = book.clone();
    if !carries_title {
        shard.title.clear();
    }
    shard
        .chapters
        .retain(|chapter| chapter_ids.contains(&chapter.id));
    let mut refs = BTreeSet::new();
    for chapter in &shard.chapters {
        walk(&chapter.blocks, &mut |block| {
            refs.extend(
                block
                    .runs()
                    .iter()
                    .filter_map(|run| run.footnote_ref.clone()),
            )
        });
    }
    shard.footnotes.retain(|note| refs.contains(&note.id));
    shard
}

fn descriptor(id: String, book: &BookDocument, dir: PathBuf) -> Result<BatchShard, PipelineError> {
    Ok(BatchShard {
        shard_id: id,
        chapter_ids: book
            .chapters
            .iter()
            .map(|chapter| chapter.id.clone())
            .collect(),
        word_count: source_units(book)?
            .iter()
            .map(|unit| revayat_core::prose(&unit.text).split_whitespace().count())
            .sum(),
        status: ShardStatus::Planned,
        assigned_stage: TranslationStage::InitialTranslation,
        work_dir: dir,
        source_revision: published_digest(book)?,
    })
}

fn shard_plan(
    book: &BookDocument,
    max_chars_per_shard: usize,
) -> Result<BatchManifest, PipelineError> {
    if max_chars_per_shard == 0 || book.chapters.is_empty() {
        return Err(PipelineError::Budget(
            "shards need a positive budget and at least one chapter".into(),
        ));
    }
    let revision = published_digest(book)?;
    let mut groups = Vec::new();
    let mut group = Vec::new();
    let mut size = 0usize;
    for chapter in &book.chapters {
        let single = partition(
            book,
            std::slice::from_ref(&chapter.id),
            groups.is_empty() && group.is_empty(),
        );
        let cost: usize = source_units(&single)?
            .iter()
            .map(|unit| unit.text.chars().count())
            .sum();
        if cost > max_chars_per_shard {
            return Err(PipelineError::Budget(format!(
                "chapter {} exceeds the shard limit",
                chapter.id
            )));
        }
        if size.saturating_add(cost) > max_chars_per_shard {
            groups.push(std::mem::take(&mut group));
            size = 0;
        }
        size += cost;
        group.push(chapter.id.clone());
    }
    groups.push(group);
    let shards = groups
        .iter()
        .enumerate()
        .map(|(i, group)| {
            let id = format!("shard{:04}", i + 1);
            descriptor(
                id.clone(),
                &partition(book, group, i == 0),
                Path::new("shards").join(id),
            )
        })
        .collect::<Result<_, _>>()?;
    Ok(BatchManifest {
        mode: BatchMode::SingleBookShards,
        book_id: book.source_sha256.clone(),
        revision,
        max_chars_per_shard: Some(max_chars_per_shard),
        shards,
    })
}

pub fn plan_shards(
    book: &BookDocument,
    work_dir: &Path,
    max_chars_per_shard: usize,
) -> Result<BatchManifest, PipelineError> {
    let plan = shard_plan(book, max_chars_per_shard)?;
    storage::create_dir(work_dir)?;
    let path = work_dir.join(BATCH);
    let _guard = revayat_core::BookLock::acquire(&path)?;
    match storage::read_json::<BatchManifest>(&path) {
        Ok(old) if old == plan => return Ok(old),
        Ok(_) => return Err(PipelineError::Stale(path)),
        Err(PipelineError::Missing(_)) => {}
        Err(error) => return Err(error),
    }
    for asset in &book.assets {
        let path = work_dir.join("assets").join(&asset.rel_path);
        if revayat_core::sha256_file(&path)? != asset.sha256 {
            return Err(PipelineError::Stale(path));
        }
    }
    for (i, shard) in plan.shards.iter().enumerate() {
        let dir = work_dir.join(&shard.work_dir);
        storage::create_dir(&dir)?;
        let partition = partition(book, &shard.chapter_ids, i == 0);
        let book_path = dir.join(revayat_core::BOOK_FILENAME);
        match load_book(&book_path) {
            Ok(current) if current == partition => {}
            Ok(_) => return Err(PipelineError::Stale(book_path)),
            Err(revayat_core::CoreError::Io { source, .. })
                if source.kind() == std::io::ErrorKind::NotFound =>
            {
                save_book(&book_path, &partition)?
            }
            Err(error) => return Err(error.into()),
        }
        for asset in &partition.assets {
            let file = work_dir.join("assets").join(&asset.rel_path);
            let bytes = storage::read(&file)?;
            if revayat_core::sha256_bytes(&bytes) != asset.sha256 {
                return Err(PipelineError::Stale(file));
            }
            storage::write(&dir.join("assets").join(&asset.rel_path), &bytes)?;
        }
        storage::write_json(&dir.join(SHARD), shard)?;
    }
    storage::write_json(&path, &plan)?;
    Ok(plan)
}

pub fn merge_shards(
    work_dir: &Path,
    shard_dirs: &[PathBuf],
) -> Result<merge::MergeReport, PipelineError> {
    let path = work_dir.join(BATCH);
    let plan: BatchManifest = storage::read_json(&path)?;
    let budget = plan
        .max_chars_per_shard
        .ok_or_else(|| PipelineError::Stale(path.clone()))?;
    storage::edit_book(&work_dir.join(revayat_core::BOOK_FILENAME), |book| {
        if shard_plan(book, budget)? != plan {
            return Err(PipelineError::Stale(path));
        }
        let mut submitted = BTreeMap::new();
        for dir in shard_dirs {
            let actual: BatchShard = storage::read_json(&dir.join(SHARD))?;
            if submitted
                .insert(actual.shard_id.clone(), (dir, actual.clone()))
                .is_some()
            {
                return Err(PipelineError::Duplicate(actual.shard_id));
            }
        }
        let mut result = book.clone();
        let mut report = merge::MergeReport::default();
        for (i, planned) in plan.shards.iter().enumerate() {
            let Some((dir, actual)) = submitted.remove(&planned.shard_id) else {
                return Err(PipelineError::Missing(work_dir.join(&planned.work_dir)));
            };
            if &actual != planned {
                return Err(PipelineError::Stale(dir.join(SHARD)));
            }
            let mut translated = partition(book, &planned.chapter_ids, i == 0);
            let replies = dir.join(chunk::CHUNKS_DIR);
            let expected = chunk::load_current(&translated, &replies)?;
            let answers = expected
                .sheets
                .into_iter()
                .map(|sheet| {
                    let answer = chunk::read_reply(&replies, &sheet)?;
                    Ok((sheet, answer))
                })
                .collect::<Result<_, PipelineError>>()?;
            let recorded = merge::apply_replies(&mut translated, answers)?;
            let file = dir.join(revayat_core::BOOK_FILENAME);
            if translated != load_book(&file)? {
                return Err(PipelineError::Stale(file));
            }
            if i == 0 {
                result.title.clone_from(&translated.title);
            }
            import_partition(&mut result, translated)?;
            report.applied.extend(recorded.applied);
            report.translator_notes += recorded.translator_notes;
        }
        if let Some(extra) = submitted.into_keys().next() {
            return Err(PipelineError::Workflow(format!("unexpected shard {extra}")));
        }
        result.validate()?;
        *book = result;
        Ok(report)
    })
}

fn import_partition(book: &mut BookDocument, mut shard: BookDocument) -> Result<(), PipelineError> {
    let mut next = book
        .footnotes
        .iter()
        .map(|note| note.number)
        .max()
        .unwrap_or_default();
    let mut rewrite = BTreeMap::new();
    for note in &mut shard.footnotes {
        if let Some(index) = book
            .footnotes
            .iter()
            .position(|base| base.id == note.id && base.anchor_block == note.anchor_block)
        {
            book.footnotes[index].clone_from(note);
        } else if note.origin == FootnoteOrigin::Translator {
            let id = loop {
                next = next
                    .checked_add(1)
                    .ok_or_else(|| PipelineError::Workflow("footnote numbering overflow".into()))?;
                let id = format!("fn{next:04}");
                if !book.footnotes.iter().any(|old| old.id == id) {
                    break id;
                }
            };
            rewrite.insert(note.id.clone(), id.clone());
            note.id = id.clone();
            note.number = next;
            if let [revayat_core::Block::Paragraph { id: block_id, .. }] =
                note.blocks.as_mut_slice()
            {
                *block_id = format!("{id}-translation");
            }
            book.footnotes.push(note.clone());
        } else {
            return Err(PipelineError::Workflow(format!(
                "unknown source note {}",
                note.id
            )));
        }
    }
    for mut chapter in shard.chapters {
        rename_refs(&mut chapter.blocks, &rewrite);
        let saved = book
            .chapters
            .iter_mut()
            .find(|original| original.id == chapter.id)
            .ok_or_else(|| PipelineError::Workflow(format!("unknown chapter {}", chapter.id)))?;
        *saved = chapter;
    }
    Ok(())
}

fn rename_refs(blocks: &mut [revayat_core::Block], mapping: &BTreeMap<String, String>) {
    use revayat_core::Block;
    for block in blocks {
        let runs = match block {
            Block::Paragraph { runs, .. }
            | Block::Heading { runs, .. }
            | Block::Image { caption: runs, .. } => runs,
            Block::Table { rows, .. } => {
                for cell in rows.iter_mut().flat_map(|row| &mut row.cells) {
                    rename_refs(&mut cell.blocks, mapping);
                }
                continue;
            }
            Block::PageBreak { .. } | Block::SceneBreak { .. } => continue,
        };
        for run in runs {
            if let Some(id) = run.footnote_ref.as_mut()
                && let Some(replacement) = mapping.get(id)
            {
                id.clone_from(replacement);
            }
        }
    }
}

pub fn plan_books(input_dirs: &[PathBuf], out_dir: &Path) -> Result<BatchManifest, PipelineError> {
    if input_dirs.is_empty() {
        return Err(PipelineError::Workflow("no input books".into()));
    }
    let mut seen = BTreeSet::new();
    let mut shards = Vec::new();
    for (i, dir) in input_dirs.iter().enumerate() {
        let canonical =
            fs::canonicalize(dir).map_err(|error| crate::error::io_error("resolve", dir, error))?;
        if !seen.insert(canonical.clone()) {
            return Err(PipelineError::Duplicate(dir.display().to_string()));
        }
        let book = load_book(canonical.join(revayat_core::BOOK_FILENAME))?;
        shards.push(descriptor(format!("book{:04}", i + 1), &book, canonical)?);
    }
    let revision = revayat_core::sha256_bytes(&serde_json::to_vec(&shards)?);
    let plan = BatchManifest {
        mode: BatchMode::MultiBook,
        book_id: revision.clone(),
        revision,
        max_chars_per_shard: None,
        shards,
    };
    let path = out_dir.join(BATCH);
    storage::create_dir(out_dir)?;
    let _guard = revayat_core::BookLock::acquire(&path)?;
    match storage::read_json::<BatchManifest>(&path) {
        Ok(old) if old == plan => return Ok(old),
        Ok(_) => return Err(PipelineError::Stale(path)),
        Err(PipelineError::Missing(_)) => {}
        Err(error) => return Err(error),
    }
    storage::write_json(&path, &plan)?;
    Ok(plan)
}

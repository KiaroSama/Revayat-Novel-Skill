use std::{error::Error, fs, path::Path};

use revayat_core::{
    Block, BookDocument, Chapter, ChapterProvenance, PageProvenance, SignoffState, SourceFormat,
    TextRun, TranslationStage, save_book, sha256_bytes,
};
use revayat_pipeline::{batch, chunk, doctor, editorial, merge, pages, qa, render};

fn source() -> BookDocument {
    let hash = sha256_bytes(b"synthetic source");
    let mut book = BookDocument::new(SourceFormat::Text, &hash, "Book", "en");
    for index in 1..=2 {
        book.chapters.push(Chapter {
            id: format!("ch{index}"),
            index,
            title: format!("Chapter {index}"),
            provenance: Some(ChapterProvenance {
                source_sha256: hash.clone(),
                source_locator: None,
                pages: vec![PageProvenance {
                    page_number: index,
                    source_sha256: hash.clone(),
                }],
            }),
            blocks: vec![Block::Paragraph {
                id: format!("b{index}"),
                style: None,
                align: None,
                rtl: false,
                runs: vec![TextRun {
                    text: "The night was very quiet! ".repeat(3),
                    bold: true,
                    ..Default::default()
                }],
            }],
        });
    }
    book
}

fn answer(dir: &Path, sheet: &chunk::Worksheet) -> Result<(), Box<dyn Error>> {
    let mut reply = sheet.clone();
    for unit in &mut reply.units {
        unit.text = if unit.text.starts_with("**") {
            "**\u{200f}\u{2067}شب آرام بود.\u{2069}**"
        } else {
            "\u{200f}\u{2067}فصل\u{2069}"
        }
        .into();
    }
    fs::write(dir.join(sheet.reply_filename()), reply.to_markdown())?;
    Ok(())
}

#[test]
fn worksheets_are_bounded_and_bound_to_the_source() -> Result<(), Box<dyn Error>> {
    let temp = tempfile::tempdir()?;
    let mut book = source();
    let limit = chunk::ChunkBudget {
        max_chars: 32,
        max_tokens: 20,
    };
    let manifest = chunk::build_worksheets(&book, temp.path(), limit)?;
    assert!(manifest.sheets.len() > 2);
    for sheet in &manifest.sheets {
        assert!(
            sheet
                .units
                .iter()
                .map(|unit| unit.text.chars().count())
                .sum::<usize>()
                <= 32
        );
        assert_eq!(
            chunk::Worksheet::from_markdown(&sheet.to_markdown())?,
            *sheet
        );
        assert_eq!(
            serde_json::from_str::<chunk::Worksheet>(&serde_json::to_string(sheet)?)?,
            *sheet
        );
    }
    assert_eq!(
        chunk::chunk_status(&book, temp.path())?.pending,
        manifest.sheets.len()
    );
    book.chapters[0].title.push('!');
    assert!(chunk::chunk_status(&book, temp.path()).is_err());
    Ok(())
}

#[test]
fn strict_merge_keeps_prior_bytes_when_a_reply_is_missing() -> Result<(), Box<dyn Error>> {
    let work = tempfile::tempdir()?;
    let path = work.path().join("book.json");
    let book = source();
    save_book(&path, &book)?;
    let chunks = work.path().join("chunks");
    let sheets = chunk::build_worksheets(&book, &chunks, chunk::ChunkBudget::default())?;
    answer(&chunks, &sheets.sheets[0])?;
    let prior = fs::read(&path)?;
    assert!(merge::merge_work_dir(work.path()).is_err());
    assert_eq!(fs::read(&path)?, prior);
    for sheet in &sheets.sheets[1..] {
        answer(&chunks, sheet)?;
    }
    merge::merge_work_dir(work.path())?;
    assert_ne!(fs::read(path)?, prior);
    Ok(())
}

#[test]
fn strict_merge_preserves_source_notes_and_rejects_duplicate_local_anchors()
-> Result<(), Box<dyn Error>> {
    let work = tempfile::tempdir()?;
    let mut book = source();
    let Block::Paragraph { runs, .. } = &mut book.chapters[0].blocks[0] else {
        return Err("invalid test block".into());
    };
    runs.push(TextRun {
        footnote_ref: Some("fn0001".into()),
        ..Default::default()
    });
    book.footnotes.push(revayat_core::Footnote {
        id: "fn0001".into(),
        number: 1,
        origin_chapter: Some("ch1".into()),
        anchor_block: Some("b1".into()),
        blocks: vec![Block::Paragraph {
            id: "source-note".into(),
            runs: vec![TextRun {
                text: "Source note".into(),
                ..Default::default()
            }],
            style: None,
            align: None,
            rtl: false,
        }],
        ..Default::default()
    });
    let path = work.path().join("book.json");
    save_book(&path, &book)?;
    let chunks = work.path().join("chunks");
    let manifest = chunk::build_worksheets(&book, &chunks, chunk::ChunkBudget::default())?;
    let mut replies = Vec::new();
    for sheet in manifest.sheets {
        let mut reply = sheet.clone();
        let mut local = false;
        for unit in &mut reply.units {
            unit.text = match unit.id.as_str() {
                "b1" => {
                    local = true;
                    "**شب آرام بود.**[[fn:fn0001]][[fn:tr-1]]"
                }
                "b2" => "**باد آرام بود.**",
                _ => "فصل",
            }
            .into();
        }
        if local {
            reply.units.push(chunk::WorksheetUnit {
                id: "tr-1".into(),
                kind: revayat_core::PublishedKind::Footnote,
                text: "یادداشت مترجم".into(),
            });
        }
        if let Some(unit) = reply.units.iter_mut().find(|unit| unit.id == "b1") {
            unit.text.push_str("[[fn:tr-1]]");
            fs::write(chunks.join(sheet.reply_filename()), reply.to_markdown())?;
            let Some(unit) = reply.units.iter_mut().find(|unit| unit.id == "b1") else {
                return Err("missing test unit".into());
            };
            unit.text.truncate(unit.text.len() - "[[fn:tr-1]]".len());
        } else {
            fs::write(chunks.join(sheet.reply_filename()), reply.to_markdown())?;
        }
        replies.push(reply);
    }
    let before = fs::read(&path)?;
    assert_eq!(chunk::chunk_status(&book, &chunks)?.invalid, 1);
    assert!(merge::merge_work_dir(work.path()).is_err());
    assert_eq!(fs::read(&path)?, before);
    for reply in replies {
        fs::write(chunks.join(reply.reply_filename()), reply.to_markdown())?;
    }
    assert_eq!(merge::merge_work_dir(work.path())?.translator_notes, 1);
    let merged = revayat_core::load_book(path)?;
    assert!(revayat_core::check_note_graph(&merged).is_empty());
    assert_eq!(merged.footnotes.len(), 2);
    assert_eq!(
        merged.footnotes[1].origin,
        revayat_core::FootnoteOrigin::Translator
    );
    Ok(())
}

#[test]
fn batch_refuses_missing_duplicate_and_stale_shards() -> Result<(), Box<dyn Error>> {
    let work = tempfile::tempdir()?;
    let book = source();
    save_book(work.path().join("book.json"), &book)?;
    let plan = batch::plan_shards(&book, work.path(), 100)?;
    assert_eq!(plan.shards.len(), 2);
    let dirs: Vec<_> = plan
        .shards
        .iter()
        .map(|shard| work.path().join(&shard.work_dir))
        .collect();
    let original = fs::read(work.path().join("book.json"))?;
    assert!(batch::merge_shards(work.path(), &dirs[..1]).is_err());
    assert!(batch::merge_shards(work.path(), &[dirs[0].clone(), dirs[0].clone()]).is_err());
    assert_eq!(fs::read(work.path().join("book.json"))?, original);
    for dir in &dirs {
        let shard = revayat_core::load_book(dir.join("book.json"))?;
        let chunks = dir.join("chunks");
        let sheets = chunk::build_worksheets(&shard, &chunks, chunk::ChunkBudget::default())?;
        for sheet in &sheets.sheets {
            answer(&chunks, sheet)?;
        }
        merge::merge_work_dir(dir)?;
    }
    batch::merge_shards(work.path(), &dirs)?;
    let merged = fs::read(work.path().join("book.json"))?;
    assert_ne!(merged, original);
    assert!(batch::merge_shards(work.path(), &dirs).is_err());
    assert_eq!(fs::read(work.path().join("book.json"))?, merged);
    let books = batch::plan_books(&dirs, &work.path().join("books"))?;
    assert_eq!(books.shards.len(), 2);
    assert!(batch::plan_books(&[dirs[0].clone(), dirs[0].clone()], work.path()).is_err());
    Ok(())
}

#[test]
fn pages_require_current_evidence_and_all_six_stages() -> Result<(), Box<dyn Error>> {
    let work = tempfile::tempdir()?;
    let book = source();
    save_book(work.path().join("book.json"), &book)?;
    let plan = pages::build(work.path(), chunk::ChunkBudget::default())?;
    assert_eq!(pages::next(work.path())?.map(|job| job.page), Some(1));
    assert!(pages::accept(work.path(), 1).is_err());
    let evidence = work.path().join("evidence.txt");
    fs::write(&evidence, "review evidence")?;
    assert!(
        pages::record_stage(
            work.path(),
            TranslationStage::IndependentReview,
            &evidence,
            0
        )
        .is_err()
    );
    for entry in &plan.jobs {
        answer(&work.path().join("pages"), &entry.sheet)?;
    }
    pages::merge(work.path(), 1)?;
    pages::merge(work.path(), 2)?;
    for page in [1, 2] {
        let preview = pages::preview(work.path(), page)?;
        let mut review = pages::VisualReview {
            preview_sha256: preview.sha256,
            layout_matches: true,
            text_readable: true,
            direction_correct: false,
            images_preserved: true,
            notes_preserved: true,
        };
        assert_eq!(
            pages::review(work.path(), page, &review)?,
            revayat_core::PageState::Failed
        );
        assert!(pages::accept(work.path(), page).is_err());
        review.direction_correct = true;
        assert_eq!(
            pages::review(work.path(), page, &review)?,
            revayat_core::PageState::QaPassed
        );
        assert_eq!(
            pages::accept(work.path(), page)?,
            revayat_core::PageState::Accepted
        );
    }
    assert!(pages::next(work.path())?.is_none());
    for stage in TranslationStage::ALL {
        pages::record_stage(work.path(), stage, &evidence, 0)?;
    }
    assert_eq!(pages::status(work.path())?.signoff, SignoffState::Approved);
    assert!(qa::qa_work_dir(work.path())?.ok());
    fs::write(&evidence, "changed evidence")?;
    assert_eq!(
        pages::status(work.path())?.signoff,
        SignoffState::Rejected(TranslationStage::InitialTranslation)
    );
    Ok(())
}

#[test]
fn editorial_is_bound_to_revision_and_retains_emphasis_and_locked_terms()
-> Result<(), Box<dyn Error>> {
    let work = tempfile::tempdir()?;
    let book = source();
    save_book(work.path().join("book.json"), &book)?;
    chunk::build_worksheets(
        &book,
        &work.path().join("chunks"),
        chunk::ChunkBudget::default(),
    )?;
    let locks = vec!["night".to_string()];
    let manifest = editorial::fluency::sheets(work.path(), &locks)?;
    let id = "b1".to_string();
    let bad = editorial::FluencyEdit {
        id: id.clone(),
        text: "changed night".into(),
    };
    assert!(editorial::fluency::record(work.path(), &manifest.request, vec![bad]).is_err());
    let good = editorial::FluencyEdit {
        id,
        text: "**Quiet night, still night, long night.**".into(),
    };
    editorial::fluency::record(work.path(), &manifest.request, vec![good])?;
    editorial::fluency::apply(work.path())?;
    assert!(editorial::fluency::apply(work.path()).is_err());
    let meaning = editorial::meaning::sheets(work.path())?;
    assert!(
        editorial::meaning::record(
            work.path(),
            "foreign",
            vec![],
            meaning.units.iter().map(|u| u.id.clone()).collect()
        )
        .is_err()
    );
    editorial::meaning::record(
        work.path(),
        &meaning.request,
        vec![],
        meaning.units.iter().map(|u| u.id.clone()).collect(),
    )?;
    assert_eq!(
        editorial::meaning::status(work.path())?,
        editorial::EditorialStatus::Accepted
    );
    Ok(())
}

#[test]
fn qa_reports_untranslated_book_and_accepts_structurally_sound_target() -> Result<(), Box<dyn Error>>
{
    let work = tempfile::tempdir()?;
    let book = source();
    assert!(!qa::qa_check(&book).ok());
    save_book(work.path().join("book.json"), &book)?;
    let dir = work.path().join("chunks");
    let manifest = chunk::build_worksheets(&book, &dir, chunk::ChunkBudget::default())?;
    for sheet in &manifest.sheets {
        answer(&dir, sheet)?;
    }
    merge::merge_work_dir(work.path())?;
    let target = revayat_core::load_book(work.path().join("book.json"))?;
    assert!(qa::qa_check(&target).ok());
    assert!(!qa::qa_work_dir(work.path())?.ok());
    Ok(())
}

#[test]
fn compares_png_pixels_and_rejects_blank_or_wrong_aspect_pages() -> Result<(), Box<dyn Error>> {
    let work = tempfile::tempdir()?;
    let a = work.path().join("a.png");
    let b = work.path().join("b.png");
    let delta = work.path().join("diff.png");
    let mut ink = image::RgbImage::from_pixel(20, 30, image::Rgb([255, 255, 255]));
    ink.put_pixel(5, 5, image::Rgb([0, 0, 0]));
    ink.save(&a)?;
    ink.save(&b)?;
    let identical = render::render_qa(&a, &b, Some(&delta))?;
    assert!(identical.ok && identical.mean_difference == 0.0 && delta.is_file());
    image::RgbImage::from_pixel(30, 20, image::Rgb([255, 255, 255])).save(&b)?;
    let bad = render::render_qa(&a, &b, None)?;
    assert!(!bad.ok && bad.target_blank && !bad.aspect_matches);
    Ok(())
}

#[test]
fn doctor_json_reports_binary_target_and_available_five_formats() -> Result<(), Box<dyn Error>> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let report = serde_json::to_value(doctor::run_doctor(Some(&root)))?;
    assert_eq!(report["version"], "3.0.0");
    assert_eq!(report["requires_python"], false);
    assert_eq!(
        report["manifests"].as_object().map(|value| value.len()),
        Some(5)
    );
    assert!(
        report["build_target"]
            .as_str()
            .is_some_and(|value| value.contains(std::env::consts::ARCH))
    );
    Ok(())
}

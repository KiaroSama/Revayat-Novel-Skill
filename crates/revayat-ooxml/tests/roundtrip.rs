use std::{fs, path::Path};

use image::{ImageFormat, Rgb, RgbImage};
use revayat_core::{
    AssetRecord, Block, BookDocument, Chapter, Footnote, FootnoteOrigin, SourceFormat, TableCell,
    TableRow, TextRun, sha256_bytes,
};
use revayat_ooxml::{BuildOptions, Package, build_docx, check_docx, read_docx};

fn run(text: &str) -> TextRun {
    TextRun {
        text: text.into(),
        ..TextRun::default()
    }
}

fn paragraph(id: &str, runs: Vec<TextRun>) -> Block {
    Block::Paragraph {
        id: id.into(),
        runs,
        style: None,
        align: None,
        rtl: true,
    }
}

fn fixture(assets: &Path) -> Result<(BookDocument, Vec<u8>), Box<dyn std::error::Error>> {
    fs::create_dir_all(assets)?;
    let image = assets.join("original.png");
    RgbImage::from_pixel(13, 7, Rgb([10, 20, 30])).save_with_format(&image, ImageFormat::Png)?;
    let bytes = fs::read(image)?;
    let mut book = BookDocument::new(SourceFormat::Docx, sha256_bytes(b"source"), "کتاب", "fa");
    book.assets.push(AssetRecord {
        id: "asset1".into(),
        media_type: "image/png".into(),
        sha256: sha256_bytes(&bytes),
        width_px: 13,
        height_px: 7,
        rel_path: "original.png".into(),
        original_bytes_preserved: true,
    });
    book.chapters.push(Chapter {
        id: "ch1".into(),
        index: 1,
        title: "فصل".into(),
        provenance: None,
        blocks: vec![
            Block::Heading {
                id: "head".into(),
                level: 1,
                runs: vec![run("فصل")],
            },
            paragraph(
                "p1",
                vec![
                    TextRun {
                        bold: true,
                        italic: true,
                        underline: true,
                        strikethrough: true,
                        superscript: true,
                        hyperlink: Some("https://example.org/name?a=b&c=d".into()),
                        ..run("Alice")
                    },
                    run(" به خانه برگشت."),
                    TextRun {
                        footnote_ref: Some("fn0001".into()),
                        ..TextRun::default()
                    },
                ],
            ),
            Block::Table {
                id: "table".into(),
                rows: vec![TableRow {
                    header: true,
                    cells: vec![TableCell {
                        blocks: vec![paragraph("cell", vec![run("سلول")])],
                        ..TableCell::default()
                    }],
                }],
            },
            Block::Image {
                id: "image".into(),
                asset_id: "asset1".into(),
                width_emu: 130_000,
                height_emu: 70_000,
                caption: Vec::new(),
            },
        ],
    });
    book.footnotes.push(Footnote {
        id: "fn0001".into(),
        number: 1,
        blocks: vec![paragraph("note", vec![run("یادداشت")])],
        origin_chapter: Some("ch1".into()),
        anchor_block: Some("p1".into()),
        origin: FootnoteOrigin::Source,
    });
    Ok((book, bytes))
}

#[test]
fn book_roundtrip_preserves_media_styles_notes_navigation_and_isolation()
-> Result<(), Box<dyn std::error::Error>> {
    let temp = tempfile::tempdir()?;
    let assets = temp.path().join("assets");
    let (book, original) = fixture(&assets)?;
    let output = temp.path().join("book.docx");
    build_docx(
        &book,
        &output,
        &BuildOptions {
            assets_dir: assets,
            header: Some("سربرگ".into()),
            ..BuildOptions::default()
        },
    )?;
    let mut pkg = Package::open(&output)?;
    let main = String::from_utf8(pkg.read("word/document.xml")?)?;
    for bit in [
        "w:bidi",
        "w:val=\"start\"",
        "w:bidiVisual",
        "w:footnoteReference",
        "w:bookmarkStart",
        "TOC \\o",
        "\u{2067}",
        "\u{2069}",
    ] {
        assert!(main.contains(bit), "missing {bit}");
    }
    assert!(String::from_utf8(pkg.read("word/styles.xml")?)?.contains("Vazirmatn"));
    let fonts = String::from_utf8(pkg.read("word/fontTable.xml")?)?;
    assert!(fonts.contains("w:embedRegular") && fonts.contains("w:embedBold"));
    for variant in ["Regular", "Bold", "Medium", "Light"] {
        assert!(
            !pkg.read(&format!("word/fonts/Vazirmatn-{variant}.ttf"))?
                .is_empty()
        );
    }
    assert_eq!(pkg.read("word/media/original.png")?, original);
    let report = check_docx(&output, Some(&book))?;
    assert!(report.summary(None).ok, "{:?}", report.findings);
    let extracted_dir = temp.path().join("extracted");
    let extracted = read_docx(&output, &extracted_dir)?;
    assert_eq!(extracted.page_geometry, book.page_geometry);
    assert_eq!(extracted.footnotes.len(), 1);
    assert_eq!(extracted.assets[0].sha256, sha256_bytes(&original));
    assert_eq!(
        fs::read(extracted_dir.join(&extracted.assets[0].rel_path))?,
        original
    );
    assert!(
        extracted
            .chapters
            .iter()
            .flat_map(|c| &c.blocks)
            .any(|b| matches!(b, Block::Table { .. }))
    );
    assert!(
        extracted
            .chapters
            .iter()
            .flat_map(|c| &c.blocks)
            .flat_map(Block::runs)
            .any(|r| r.bold
                && r.italic
                && r.underline
                && r.strikethrough
                && r.superscript
                && r.hyperlink.is_some())
    );
    Ok(())
}

#[test]
fn refuses_non_zip_and_invalid_xml() -> Result<(), Box<dyn std::error::Error>> {
    let temp = tempfile::tempdir()?;
    let path = temp.path().join("broken.docx");
    fs::write(&path, b"not a ZIP")?;
    assert!(check_docx(&path, None).is_err());
    let mut zip = zip::ZipWriter::new(fs::File::create(&path)?);
    zip.start_file(
        "[Content_Types].xml",
        zip::write::SimpleFileOptions::default(),
    )?;
    std::io::Write::write_all(&mut zip, b"<Types")?;
    zip.finish()?;
    assert!(read_docx(&path, &temp.path().join("assets")).is_err());
    assert!(check_docx(&path, None).is_err());
    Ok(())
}

#[test]
fn rejects_modified_image_and_keeps_previous_output() -> Result<(), Box<dyn std::error::Error>> {
    let temp = tempfile::tempdir()?;
    let assets = temp.path().join("assets");
    let (book, _) = fixture(&assets)?;
    let output = temp.path().join("book.docx");
    let options = BuildOptions {
        assets_dir: assets.clone(),
        ..BuildOptions::default()
    };
    build_docx(&book, &output, &options)?;
    let previous = fs::read(&output)?;
    RgbImage::from_pixel(13, 7, Rgb([12, 24, 36])).save(assets.join("original.png"))?;
    assert!(build_docx(&book, &output, &options).is_err());
    assert_eq!(fs::read(&output)?, previous);
    let mut wrong = book.clone();
    wrong.assets[0].sha256 = sha256_bytes(b"not the original image");
    let report = check_docx(&output, Some(&wrong))?;
    assert!(report.findings.iter().any(|f| f.code == "image-integrity"));
    Ok(())
}

#[test]
fn namespaced_helpers_resolve_real_parts() -> Result<(), Box<dyn std::error::Error>> {
    use revayat_ooxml::{drawing_extents, footnote_bodies, footnote_references, qname, text_of};
    let w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
    assert_eq!(qname("w", "p")?, format!("{{{w}}}p"));
    assert!(qname("other", "p").is_err());
    let source = format!(
        r#"<x:footnotes xmlns:x="{w}"><x:footnote x:id="7"><x:p><x:r><x:t>Note</x:t><x:tab/><x:t>body</x:t></x:r></x:p></x:footnote><x:footnoteReference x:id="7"/></x:footnotes>"#
    );
    let doc = roxmltree::Document::parse(&source)?;
    assert_eq!(footnote_references(doc.root_element()), ["7"]);
    let notes = footnote_bodies(doc.root_element())?;
    assert_eq!(
        notes.get("7").copied().map(text_of).as_deref(),
        Some("Note\tbody")
    );
    assert!(drawing_extents(doc.root_element())?.is_empty());
    Ok(())
}

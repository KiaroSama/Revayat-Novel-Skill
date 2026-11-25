use std::{
    fs,
    io::{Cursor, Write},
};

use image::{DynamicImage, ImageFormat, Rgb, RgbImage};
use lopdf::{
    Document, Object, Stream,
    content::{Content, Operation},
    dictionary,
};
use revayat_core::{Block, SourceFormat, sha256_bytes};
use revayat_ingest::{
    IngestError, clean, fetch_url, import_html_files, import_mineru, import_mineru_with_assets,
    import_url, inspect_pdf, parse_html_chapter, parse_tesseract_tsv, preview, read_epub, read_pdf,
    render_pdf_pages, survey,
};
use tempfile::tempdir;
use zip::{ZipWriter, write::SimpleFileOptions};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn png() -> Result<Vec<u8>, image::ImageError> {
    let mut bytes = Cursor::new(Vec::new());
    DynamicImage::ImageRgb8(RgbImage::from_pixel(3, 2, Rgb([50, 100, 150])))
        .write_to(&mut bytes, ImageFormat::Png)?;
    Ok(bytes.into_inner())
}

#[test]
fn imports_epub_in_spine_order_with_original_assets_and_notes() -> TestResult {
    let dir = tempdir()?;
    let path = dir.path().join("test.epub");
    let assets = dir.path().join("assets");
    let pixels = png()?;
    let mut zip = ZipWriter::new(fs::File::create(&path)?);
    for (name, content) in [
        ("mimetype", "application/epub+zip"),
        (
            "META-INF/container.xml",
            r#"<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>"#,
        ),
        (
            "OPS/book.opf",
            r#"<package xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>Sample</dc:title><dc:language>en</dc:language></metadata><manifest><item id="one" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/><item id="picture" href="pic.png" media-type="image/png"/></manifest><spine><itemref idref="one"/><itemref idref="notes" linear="no"/></spine></package>"#,
        ),
        (
            "OPS/nav.xhtml",
            r#"<html xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol><li><a href="chapter.xhtml#first">From the TOC</a></li></ol></nav></body></html>"#,
        ),
        (
            "OPS/chapter.xhtml",
            r#"<html xmlns:epub="http://www.idpf.org/2007/ops"><body><h1 id="first">Heading</h1><p>A <strong>bold <em>both</em></strong> <u>underlined</u> <a href="https://example.com/">link</a><a epub:type="noteref" href="notes.xhtml#n1">1</a>.</p><img src="pic.png" alt="Illustration"/><table><tr><th>Column</th></tr><tr><td>Value</td></tr></table></body></html>"#,
        ),
        (
            "OPS/notes.xhtml",
            r#"<html xmlns:epub="http://www.idpf.org/2007/ops"><body><aside epub:type="endnote" id="n1"><p>Original note.</p></aside></body></html>"#,
        ),
    ] {
        zip.start_file(name, SimpleFileOptions::default())?;
        zip.write_all(content.as_bytes())?;
    }
    zip.start_file("OPS/pic.png", SimpleFileOptions::default())?;
    zip.write_all(&pixels)?;
    zip.finish()?;
    let book = read_epub(&path, &assets)?;
    book.validate()?;
    assert_eq!(book.title, "Sample");
    assert_eq!(book.chapters.len(), 1);
    assert_eq!(book.chapters[0].title, "From the TOC");
    assert!(matches!(
        book.chapters[0].blocks[0],
        Block::Heading { level: 1, .. }
    ));
    let runs = book.chapters[0].blocks[1].runs();
    assert!(
        runs.iter()
            .any(|r| r.text.trim() == "both" && r.bold && r.italic)
    );
    assert!(
        runs.iter()
            .any(|r| r.text.trim() == "underlined" && r.underline)
    );
    assert!(
        runs.iter()
            .any(|r| r.hyperlink.as_deref() == Some("https://example.com/"))
    );
    assert!(
        runs.iter()
            .any(|r| r.footnote_ref.as_deref() == Some(book.footnotes[0].id.as_str()))
    );
    assert_eq!(book.footnotes[0].blocks[0].runs()[0].text, "Original note.");
    assert!(
        book.chapters[0]
            .blocks
            .iter()
            .any(|b| matches!(b, Block::Table { rows, .. } if rows.len() == 2 && rows[0].header))
    );
    let asset = &book.assets[0];
    assert_eq!((asset.width_px, asset.height_px), (3, 2));
    assert!(asset.original_bytes_preserved);
    assert_eq!(asset.sha256, sha256_bytes(&pixels));
    assert_eq!(fs::read(assets.join(&asset.rel_path))?, pixels);
    Ok(())
}

#[test]
fn resolves_epub2_ncx_and_plain_note_anchors() -> TestResult {
    let dir = tempdir()?;
    let path = dir.path().join("epub2.epub");
    let mut zip = ZipWriter::new(fs::File::create(&path)?);
    for (name, contents) in [
        (
            "META-INF/container.xml",
            r#"<container><rootfile full-path="book.opf"/></container>"#,
        ),
        (
            "book.opf",
            r#"<package><manifest><item id="text" href="main.xhtml" media-type="application/xhtml+xml"/><item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest><spine toc="ncx"><itemref idref="text"/></spine></package>"#,
        ),
        (
            "toc.ncx",
            r#"<ncx><navMap><navPoint><navLabel><text>A chapter</text></navLabel><content src="main.xhtml"/></navPoint></navMap></ncx>"#,
        ),
        (
            "main.xhtml",
            r#"<html><body><p>Main text<sup><a href="notes.xhtml#note">1</a></sup></p><p>Another reference<sup><a href="notes.xhtml#note">1</a></sup>.</p></body></html>"#,
        ),
        (
            "notes.xhtml",
            "<html><body><p id='note'>The note.</p></body></html>",
        ),
    ] {
        zip.start_file(name, SimpleFileOptions::default())?;
        zip.write_all(contents.as_bytes())?;
    }
    zip.finish()?;
    let book = read_epub(&path, dir.path())?;
    assert_eq!(book.chapters[0].title, "A chapter");
    assert_eq!(book.footnotes.len(), 2);
    for note in &book.footnotes {
        assert_eq!(note.blocks[0].runs()[0].text, "The note.");
    }
    book.validate()?;
    Ok(())
}

#[test]
fn refuses_epub_archive_traversal() -> TestResult {
    let dir = tempdir()?;
    let path = dir.path().join("unsafe.epub");
    let mut zip = ZipWriter::new(fs::File::create(&path)?);
    zip.start_file("../outside", SimpleFileOptions::default())?;
    zip.write_all(b"unexpected")?;
    zip.finish()?;
    assert!(matches!(
        read_epub(&path, dir.path()),
        Err(IngestError::UnsafePath(_))
    ));
    assert!(!dir.path().join("outside").exists());
    Ok(())
}

#[test]
fn parses_web_chapter_without_boilerplate() -> TestResult {
    let chapter = parse_html_chapter(
        r#"<html><title>Page chrome</title><nav><p>Navigation</p></nav><article><h1>Chapter one</h1><p>A <b>bold</b> <i>word</i> <a href="next">link</a><br>next line.</p><div class="advertisement"><p>Buy this</p></div><script>bad code</script></article></html>"#,
        Some("https://example.com/book/"),
        None,
    )?;
    assert_eq!(chapter.title, "Chapter one");
    assert_eq!(chapter.blocks.len(), 2);
    let runs = chapter.blocks[1].runs();
    assert!(runs.iter().any(|r| r.bold && r.text.trim() == "bold"));
    assert!(runs.iter().any(|r| r.italic && r.text.trim() == "word"));
    assert!(
        runs.iter()
            .any(|r| r.hyperlink.as_deref() == Some("https://example.com/book/next"))
    );
    assert_eq!(
        runs.iter().map(|r| r.text.as_str()).collect::<String>(),
        "A bold word link\nnext line."
    );
    Ok(())
}

#[test]
fn imports_local_images_and_legacy_html_encoding() -> TestResult {
    let dir = tempdir()?;
    let path = dir.path().join("café.html");
    fs::write(dir.path().join("pic.png"), png()?)?;
    fs::write(
        &path,
        b"<meta charset=windows-1252><h1>Caf\xe9</h1><p>Text<img src=pic.png> after</p>",
    )?;
    let book = import_html_files(&[path], &dir.path().join("assets"))?;
    assert_eq!(book.source_format, SourceFormat::Html);
    assert_eq!(book.chapters[0].title, "Café");
    assert_eq!(book.assets.len(), 1);
    assert!(matches!(book.chapters[0].blocks[2], Block::Image { .. }));
    book.validate()?;
    Ok(())
}

#[test]
fn rejects_nonpublic_web_destinations_before_sending() -> TestResult {
    let dir = tempdir()?;
    for url in [
        "file:///text",
        "http://127.0.0.1/",
        "http://10.2.3.4/",
        "http://[::1]/",
        "http://[::ffff:192.168.1.1]/",
        "http://169.254.169.254/",
        "http://localhost/",
        "https://user@example.com/",
    ] {
        assert!(
            matches!(fetch_url(url, 1), Err(IngestError::UnsafeUrl(_))),
            "{url}"
        );
        assert!(matches!(
            import_url(url, dir.path(), 1),
            Err(IngestError::UnsafeUrl(_))
        ));
    }
    assert_eq!(fs::read_dir(dir.path())?.count(), 0);
    Ok(())
}

const TSV: &str = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n";

#[test]
fn ocr_keeps_low_and_unknown_confidence_visible() -> TestResult {
    let tsv = format!(
        "{TSV}1\t1\t0\t0\t0\t0\t0\t0\t120\t60\t-1\t\n5\t1\t1\t1\t1\t1\t2\t2\t12\t10\t95.5\tHigh\n5\t1\t1\t1\t1\t2\t16\t2\t20\t10\t73\tmaybe\n5\t1\t1\t1\t1\t3\t40\t2\t13\t10\t23\tlow\n5\t1\t1\t1\t1\t4\t55\t2\t23\t10\t-1\tناخوانا\n"
    );
    let page = parse_tesseract_tsv(&tsv)?;
    assert_eq!((page.width_px, page.height_px), (120, 60));
    assert_eq!(page.tokens[0].confidence, Some(95.5));
    assert!(!page.tokens[0].uncertain);
    assert!(page.tokens[1].uncertain && !page.tokens[1].low_confidence);
    assert!(page.tokens[2].uncertain && page.tokens[2].low_confidence);
    assert_eq!(page.tokens[3].confidence, None);
    assert!(page.tokens[3].uncertain && page.tokens[3].low_confidence);
    assert_eq!(page.tokens[3].text, "ناخوانا");
    assert!(parse_tesseract_tsv("broken").is_err());
    Ok(())
}

#[test]
fn imports_mineru_in_page_order_and_preserves_source() -> TestResult {
    let dir = tempdir()?;
    let path = dir.path().join("result_content_list.json");
    let data = br#"[{"type":"text","text_level":1,"text":"Chapter one","page_idx":0},{"type":"text","text":"First paragraph.","page_idx":0},{"type":"text","text":"Page two.","page_idx":1}]"#;
    fs::write(&path, data)?;
    let book = import_mineru(&path)?;
    assert_eq!(book.source_sha256, sha256_bytes(data));
    assert_eq!(book.chapters[0].title, "Chapter one");
    assert!(matches!(book.chapters[0].blocks[0], Block::Heading { .. }));
    assert!(
        book.chapters[0]
            .blocks
            .iter()
            .any(|b| matches!(b, Block::PageBreak { .. }))
    );
    assert_eq!(fs::read(&path)?, data);
    book.validate()?;
    Ok(())
}

#[test]
fn mineru_assets_use_the_explicit_destination() -> TestResult {
    let dir = tempdir()?;
    let path = dir.path().join("result_content_list.json");
    let raw = png()?;
    fs::write(dir.path().join("pic.png"), &raw)?;
    fs::write(
        &path,
        br#"[{"type":"image","img_path":"pic.png","page_idx":0}]"#,
    )?;
    let assets = dir.path().join("work/assets");
    let book = import_mineru_with_assets(&path, &assets)?;
    assert!(matches!(book.chapters[0].blocks[0], Block::Image { .. }));
    assert_eq!(book.assets.len(), 1);
    assert_eq!(fs::read(assets.join(&book.assets[0].rel_path))?, raw);
    assert!(!dir.path().join("revayat-assets").exists());
    book.validate()?;
    Ok(())
}

#[test]
fn scan_clean_preserves_dimensions_ink_and_source() -> TestResult {
    let dir = tempdir()?;
    let input = dir.path().join("scan.png");
    let output = dir.path().join("clean.png");
    let mut source = RgbImage::from_pixel(10, 10, Rgb([255, 255, 255]));
    source.put_pixel(1, 1, Rgb([220, 40, 40]));
    source.put_pixel(2, 2, Rgb([20, 20, 20]));
    source.save(&input)?;
    let before = fs::read(&input)?;
    let info = survey(&input)?;
    assert_eq!((info.width_px, info.height_px), (10, 10));
    assert!((info.colored_fraction - 0.01).abs() < f64::EPSILON);
    assert_eq!(info.dpi, None);
    let compared = preview(&input, &dir.path().join("preview"), false, None)?;
    assert_eq!(compared.len(), 2);
    clean(&input, &output, false, None)?;
    let cleaned = image::open(&output)?.to_rgb8();
    assert_eq!(cleaned.dimensions(), (10, 10));
    assert_eq!(*cleaned.get_pixel(1, 1), Rgb([255, 255, 255]));
    assert_eq!(*cleaned.get_pixel(2, 2), Rgb([20, 20, 20]));
    assert_eq!(fs::read(&input)?, before);
    assert!(clean(&input, &input, false, None).is_err());
    Ok(())
}

#[test]
fn pdf_preserves_page_geometry_text_pixels_and_renders() -> TestResult {
    let dir = tempdir()?;
    let path = dir.path().join("book.pdf");
    let assets = dir.path().join("assets");
    let mut pdf = Document::with_version("1.5");
    let pages = pdf.new_object_id();
    let font = pdf.add_object(
        dictionary! {"Type" => "Font", "Subtype" => "Type1", "BaseFont" => "Helvetica"},
    );
    let img = pdf.add_object(Stream::new(
        dictionary! {
            "Type" => "XObject", "Subtype" => "Image", "Width" => 2, "Height" => 1,
            "ColorSpace" => "DeviceRGB", "BitsPerComponent" => 8,
        },
        vec![255, 0, 0, 0, 0, 255],
    ));
    let resources = pdf.add_object(
        dictionary! {"Font" => dictionary! {"F1" => font}, "XObject" => dictionary! {"Im1" => img}},
    );
    let page_ops = [
        vec![
            Operation::new("BT", vec![]),
            Operation::new("Tf", vec!["F1".into(), 12.into()]),
            Operation::new("Td", vec![15.into(), 60.into()]),
            Operation::new("Tj", vec![Object::string_literal("A readable paragraph.")]),
            Operation::new("ET", vec![]),
        ],
        vec![
            Operation::new("q", vec![]),
            Operation::new(
                "cm",
                vec![
                    180.into(),
                    0.into(),
                    0.into(),
                    90.into(),
                    0.into(),
                    0.into(),
                ],
            ),
            Operation::new("Do", vec!["Im1".into()]),
            Operation::new("Q", vec![]),
        ],
    ];
    let mut kids = Vec::<Object>::new();
    for operations in page_ops {
        let contents = pdf.add_object(Stream::new(
            dictionary! {},
            Content { operations }.encode()?,
        ));
        let id = pdf
            .add_object(dictionary! {"Type" => "Page", "Parent" => pages, "Contents" => contents});
        kids.push(id.into());
    }
    pdf.objects.insert(pages, Object::Dictionary(dictionary! {"Type" => "Pages", "Kids" => kids, "Count" => 2, "Resources" => resources, "MediaBox" => vec![0.into(),0.into(),180.into(),90.into()]}));
    let catalog = pdf.add_object(dictionary! {"Type" => "Catalog", "Pages" => pages});
    pdf.trailer.set("Root", catalog);
    pdf.save(&path)?;
    let summary = inspect_pdf(&path)?;
    assert_eq!(summary.page_count, 2);
    assert_eq!(
        (summary.pages[0].width_pt, summary.pages[0].height_pt),
        (180.0, 90.0)
    );
    assert!(summary.pages[0].text_coverage_ratio > 0.0 && !summary.pages[0].scanned);
    assert!(summary.pages[1].scanned);
    let book = read_pdf(&path, &assets)?;
    assert_eq!(book.page_geometry.width_twips, 3600);
    assert!(book.chapters.iter().flat_map(|c| &c.blocks).any(|b| {
        b.runs()
            .iter()
            .any(|r| r.text.contains("A readable paragraph."))
    }));
    assert_eq!(book.assets.len(), 1);
    let asset = &book.assets[0];
    assert_eq!((asset.width_px, asset.height_px), (2, 1));
    let stored = fs::read(assets.join(&asset.rel_path))?;
    assert_eq!(asset.sha256, sha256_bytes(&stored));
    assert_eq!(
        image::load_from_memory(&stored)?.to_rgb8().into_raw(),
        [255, 0, 0, 0, 0, 255]
    );
    let paths = render_pdf_pages(&path, &dir.path().join("rendered"), 144.0)?;
    assert_eq!(paths.len(), 2);
    assert_eq!(image::image_dimensions(&paths[1])?, (360, 180));
    assert!(
        survey(&paths[1])?
            .dpi
            .is_some_and(|dpi| dpi.into_iter().all(|d| (d - 144.0).abs() < 0.025))
    );
    assert!(render_pdf_pages(&path, dir.path(), f32::MAX).is_err());
    Ok(())
}

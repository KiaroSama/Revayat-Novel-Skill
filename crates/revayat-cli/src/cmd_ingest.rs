use std::{fs, path::PathBuf};

use anyhow::{Context, Result, bail};
use clap::{Args, Subcommand};
use revayat_core::{
    BOOK_FILENAME, BOOK_SCHEMA_VERSION, Block, BookDocument, Chapter, PageGeometry, SourceFormat,
    TextRun, save_book, sha256_bytes,
};
use revayat_ingest::{
    clean, import_html_files, import_mineru_with_assets, import_url, inspect_pdf, preview,
    read_epub, read_pdf, render_pdf_pages, run_tesseract_tsv, survey,
};
use revayat_ooxml::read_docx;
use serde::Serialize;

#[derive(Args)]
pub struct ExtractArgs {
    /// Source file (.docx, .epub, .pdf, .html) or directory of HTML chapters
    pub input: PathBuf,
    /// Output working directory (writes book.json and lossless assets/)
    #[arg(long)]
    pub out: PathBuf,
    /// Emit machine-readable JSON summary
    #[arg(long)]
    pub json: bool,
}

#[derive(Args)]
pub struct WebImportArgs {
    /// Chapter or book URL to fetch via rustls
    pub url: String,
    /// Output working directory (writes book.json and raw HTML)
    #[arg(long)]
    pub out: PathBuf,
    /// HTTP timeout in seconds
    #[arg(long, default_value_t = 30)]
    pub timeout: u64,
    /// Emit machine-readable JSON summary
    #[arg(long)]
    pub json: bool,
}

#[derive(Subcommand)]
pub enum CleanScanCmd {
    /// Inspect scanned raster image dimensions and contrast
    Survey {
        /// Directory or raster image path
        input: PathBuf,
        #[arg(long)]
        json: bool,
    },
    /// Clean scanned pages into an output directory
    Run {
        input: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long)]
        force: bool,
        #[arg(long)]
        threshold: Option<u8>,
        #[arg(long)]
        json: bool,
    },
    /// Generate cleaned preview PNGs
    Preview {
        input: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long)]
        force: bool,
        #[arg(long)]
        threshold: Option<u8>,
        #[arg(long)]
        json: bool,
    },
}

#[derive(Args)]
pub struct OcrSidecarArgs {
    /// Source PDF or raster image to OCR via optional external tesseract
    pub input: PathBuf,
    /// Output working directory
    #[arg(long)]
    pub out: PathBuf,
    /// Tesseract language string (e.g. eng, fas+eng)
    #[arg(long, default_value = "eng")]
    pub lang: String,
    /// Render DPI when rasterizing PDF pages
    #[arg(long, default_value_t = 200.0)]
    pub dpi: f32,
    /// Import an existing MinerU JSON export instead of invoking tesseract
    #[arg(long)]
    pub mineru: Option<PathBuf>,
    #[arg(long)]
    pub json: bool,
}

#[derive(Serialize)]
struct ExtractSummary {
    source_format: SourceFormat,
    book_path: PathBuf,
    chapters: usize,
    footnotes: usize,
    assets: usize,
}

pub fn run_extract(args: ExtractArgs) -> Result<u8> {
    fs::create_dir_all(&args.out)
        .with_context(|| format!("create work dir {}", args.out.display()))?;
    let assets_dir = args.out.join("assets");
    fs::create_dir_all(&assets_dir)
        .with_context(|| format!("create assets dir {}", assets_dir.display()))?;

    let book: BookDocument = if args.input.is_dir() {
        let mut files: Vec<PathBuf> = fs::read_dir(&args.input)
            .with_context(|| format!("read directory {}", args.input.display()))?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| {
                p.extension()
                    .and_then(|ext| ext.to_str())
                    .is_some_and(|ext| matches!(ext.to_ascii_lowercase().as_str(), "html" | "htm"))
            })
            .collect();
        files.sort();
        if files.is_empty() {
            bail!("no .html/.htm files found in {}", args.input.display());
        }
        import_html_files(&files, &assets_dir)?
    } else {
        let ext = args
            .input
            .extension()
            .and_then(|e| e.to_str())
            .map(str::to_ascii_lowercase)
            .unwrap_or_default();
        match ext.as_str() {
            "docx" => read_docx(&args.input, &assets_dir)?,
            "epub" => read_epub(&args.input, &assets_dir)?,
            "pdf" => read_pdf(&args.input, &assets_dir)?,
            "html" | "htm" => import_html_files(std::slice::from_ref(&args.input), &assets_dir)?,
            "txt" | "md" => read_plain_text(&args.input)?,
            other => bail!(
                "unsupported input format '.{other}'; expected docx, epub, pdf, html, txt, or md"
            ),
        }
    };

    let book_path = args.out.join(BOOK_FILENAME);
    save_book(&book_path, &book)?;
    let summary = ExtractSummary {
        source_format: book.source_format,
        book_path,
        chapters: book.chapters.len(),
        footnotes: book.footnotes.len(),
        assets: book.assets.len(),
    };
    if args.json {
        println!("{}", serde_json::to_string_pretty(&summary)?);
    } else {
        println!(
            "Extracted {:?} -> {} ({} chapters, {} footnotes, {} assets)",
            summary.source_format,
            summary.book_path.display(),
            summary.chapters,
            summary.footnotes,
            summary.assets
        );
    }
    Ok(0)
}

pub fn run_web_import(args: WebImportArgs) -> Result<u8> {
    fs::create_dir_all(&args.out)
        .with_context(|| format!("create work dir {}", args.out.display()))?;
    let assets_dir = args.out.join("assets");
    fs::create_dir_all(&assets_dir)
        .with_context(|| format!("create assets dir {}", assets_dir.display()))?;
    let book = import_url(&args.url, &assets_dir, args.timeout)?;
    let book_path = args.out.join(BOOK_FILENAME);
    save_book(&book_path, &book)?;

    if args.json {
        let payload = serde_json::json!({
            "url": args.url,
            "book_path": book_path,
            "chapters": book.chapters.len(),
            "assets": book.assets.len(),
        });
        println!("{}", serde_json::to_string_pretty(&payload)?);
    } else {
        println!(
            "Imported {} -> {} ({} assets)",
            args.url,
            book_path.display(),
            book.assets.len()
        );
    }
    Ok(0)
}

pub fn run_clean_scan(cmd: CleanScanCmd) -> Result<u8> {
    match cmd {
        CleanScanCmd::Survey { input, json } => {
            let report = survey(&input)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&report)?);
            } else {
                println!("{}", serde_json::to_string(&report)?);
            }
        }
        CleanScanCmd::Run {
            input,
            out,
            force,
            threshold,
            json,
        } => {
            let report = clean(&input, &out, force, threshold)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&report)?);
            } else {
                println!("Cleaned scan -> {}", out.display());
            }
        }
        CleanScanCmd::Preview {
            input,
            out,
            force,
            threshold,
            json,
        } => {
            let files = preview(&input, &out, force, threshold)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&files)?);
            } else {
                println!(
                    "Generated {} preview images in {}",
                    files.len(),
                    out.display()
                );
            }
        }
    }
    Ok(0)
}

pub fn run_ocr_sidecar(args: OcrSidecarArgs) -> Result<u8> {
    fs::create_dir_all(&args.out)
        .with_context(|| format!("create work dir {}", args.out.display()))?;

    if let Some(mineru_path) = &args.mineru {
        let assets_dir = args.out.join("assets");
        fs::create_dir_all(&assets_dir)
            .with_context(|| format!("create assets dir {}", assets_dir.display()))?;
        let book = import_mineru_with_assets(mineru_path, &assets_dir)?;
        let book_path = args.out.join(BOOK_FILENAME);
        save_book(&book_path, &book)?;
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "mode": "mineru",
                "book_path": book_path,
                "chapters": book.chapters.len(),
                "assets": book.assets.len(),
            }))?
        );
        return Ok(0);
    }

    let is_pdf = args
        .input
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case("pdf"));

    let image_paths: Vec<PathBuf> = if is_pdf {
        let _summary = inspect_pdf(&args.input)?;
        let pages_dir = args.out.join("ocr_pages");
        render_pdf_pages(&args.input, &pages_dir, args.dpi)?
    } else {
        vec![args.input.clone()]
    };

    let mut pages = Vec::with_capacity(image_paths.len());
    for image_path in &image_paths {
        let page = run_tesseract_tsv(image_path, &args.lang, 120)?;
        pages.push(page);
    }

    let sidecar_path = args.out.join("ocr_sidecar.json");
    let encoded = serde_json::to_string_pretty(&pages)?;
    fs::write(&sidecar_path, encoded.as_bytes())
        .with_context(|| format!("write {}", sidecar_path.display()))?;

    if args.json {
        println!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "sidecar": sidecar_path,
                "pages": pages.len(),
            }))?
        );
    } else {
        println!(
            "Wrote OCR sidecar ({} pages) -> {}",
            pages.len(),
            sidecar_path.display()
        );
    }
    Ok(0)
}

fn read_plain_text(path: &std::path::Path) -> Result<BookDocument> {
    let raw = fs::read_to_string(path)
        .with_context(|| format!("read UTF-8 text file {}", path.display()))?;
    let title = path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("Book")
        .to_string();
    let normalized = raw.replace("\r\n", "\n").replace('\r', "\n");
    let mut blocks = Vec::new();
    for (idx, para) in normalized.split("\n\n").enumerate() {
        let trimmed = para.trim();
        if trimmed.is_empty() {
            continue;
        }
        blocks.push(Block::Paragraph {
            id: format!("p{:04}", idx + 1),
            runs: vec![TextRun {
                text: trimmed.to_string(),
                ..TextRun::default()
            }],
            style: None,
            align: None,
            rtl: revayat_core::has_persian(trimmed),
        });
    }
    Ok(BookDocument {
        schema_version: BOOK_SCHEMA_VERSION,
        source_format: SourceFormat::Text,
        source_sha256: sha256_bytes(raw.as_bytes()),
        title: title.clone(),
        language: "en".into(),
        page_geometry: PageGeometry::default(),
        chapters: vec![Chapter {
            id: "ch0001".into(),
            index: 1,
            title,
            blocks,
            provenance: None,
        }],
        footnotes: Vec::new(),
        assets: Vec::new(),
    })
}

use std::{collections::BTreeSet, fs, path::PathBuf, time::Duration};

use anyhow::{Context, Result, bail};
use clap::{Args, Subcommand};
use revayat_core::{BOOK_FILENAME, Finding, Severity, load_book};
use revayat_ooxml::{BuildOptions, build_docx, check_docx};
use revayat_pipeline::{
    batch,
    chunk::{ChunkBudget, build_worksheets, chunk_status},
    editorial::{FluencyEdit, fluency, meaning},
    merge::merge_work_dir,
    pages::{self, VisualReview},
    qa::qa_work_dir,
    render::{render_qa, wordrender},
    run_doctor,
};

#[derive(Subcommand)]
pub enum ChunkCmd {
    /// Partition book.json into bounded translation worksheets
    Build {
        work: PathBuf,
        #[arg(long)]
        out: Option<PathBuf>,
        #[arg(long, default_value_t = 3500)]
        max_tokens: usize,
        #[arg(long)]
        json: bool,
    },
    /// Report translation worksheet progress
    Status {
        work: PathBuf,
        #[arg(long)]
        dir: Option<PathBuf>,
        #[arg(long)]
        json: bool,
    },
}

#[derive(Subcommand)]
pub enum BatchCmd {
    /// Partition a large book into parallel agent shards
    PlanShards {
        work: PathBuf,
        #[arg(long, default_value_t = 24000)]
        max_chars: usize,
        #[arg(long)]
        json: bool,
    },
    /// Merge completed parallel shards back into the parent Book IR
    MergeShards {
        work: PathBuf,
        #[arg(long = "shard", required = true)]
        shards: Vec<PathBuf>,
        #[arg(long)]
        json: bool,
    },
    /// Create a multi-book batch plan across independent book directories
    PlanBooks {
        #[arg(long = "book", required = true)]
        books: Vec<PathBuf>,
        #[arg(long)]
        out: PathBuf,
        #[arg(long)]
        json: bool,
    },
}

#[derive(Subcommand)]
pub enum PagesCmd {
    /// Build page-by-page translation jobs
    Build {
        work: PathBuf,
        #[arg(long, default_value_t = 3500)]
        max_tokens: usize,
    },
    /// Show page-by-page status
    Status { work: PathBuf },
    /// Show next actionable page job
    Next { work: PathBuf },
    /// Merge a translated page into Book IR
    Merge { work: PathBuf, page: u32 },
    /// Build a single-page preview DOCX
    Preview { work: PathBuf, page: u32 },
    /// Record visual review findings for a page
    Review {
        work: PathBuf,
        page: u32,
        #[arg(long)]
        verdict: PathBuf,
    },
    /// Accept a reviewed page
    Accept { work: PathBuf, page: u32 },
}

#[derive(Args)]
pub struct MergeArgs {
    pub work: PathBuf,
    #[arg(long)]
    pub json: bool,
}

#[derive(Args)]
pub struct BuildArgs {
    pub work: PathBuf,
    #[arg(long)]
    pub out: PathBuf,
    #[arg(long)]
    pub no_toc: bool,
    #[arg(long)]
    pub no_page_numbers: bool,
    #[arg(long)]
    pub header: Option<String>,
    #[arg(long)]
    pub footer: Option<String>,
    #[arg(long)]
    pub json: bool,
}

#[derive(Subcommand)]
pub enum FluencyCmd {
    Sheets {
        work: PathBuf,
        #[arg(long = "lock")]
        locked_terms: Vec<String>,
    },
    Record {
        work: PathBuf,
        #[arg(long)]
        request: String,
        #[arg(long)]
        edits: PathBuf,
    },
    Apply {
        work: PathBuf,
    },
    Status {
        work: PathBuf,
    },
}

#[derive(Subcommand)]
pub enum MeaningCmd {
    Sheets {
        work: PathBuf,
    },
    Record {
        work: PathBuf,
        #[arg(long)]
        request: String,
        #[arg(long)]
        findings: PathBuf,
        #[arg(long = "reviewed")]
        reviewed: Vec<String>,
    },
    Status {
        work: PathBuf,
    },
}

#[derive(Subcommand)]
pub enum QaCmd {
    /// Run Book IR structural and textual quality checks
    Check {
        work: PathBuf,
        #[arg(long)]
        json: bool,
    },
    /// Run OOXML/RTL/font/image checks on a built DOCX
    Docx {
        docx: PathBuf,
        #[arg(long)]
        work: Option<PathBuf>,
        #[arg(long)]
        json: bool,
    },
}

#[derive(Subcommand)]
pub enum DocQaCmd {
    Check {
        docx: PathBuf,
        #[arg(long)]
        work: Option<PathBuf>,
        #[arg(long)]
        json: bool,
    },
    Review {
        docx: PathBuf,
        #[arg(long)]
        work: Option<PathBuf>,
        #[arg(long)]
        json: bool,
    },
}

#[derive(Args)]
pub struct RenderQaArgs {
    /// Optional built DOCX to render via LibreOffice/Word and rasterize with pure-Rust hayro
    pub docx: Option<PathBuf>,
    /// Output directory for rendered PNGs and visual diff artifacts (required in DOCX mode)
    #[arg(long)]
    pub out: Option<PathBuf>,
    /// Source reference PDF (optional in DOCX mode) or reference PNG (in PNG pair mode)
    #[arg(long)]
    pub source: Option<PathBuf>,
    /// Target rendered PNG (in PNG pair mode)
    #[arg(long)]
    pub target: Option<PathBuf>,
    /// Optional diff PNG output path (in PNG pair mode)
    #[arg(long)]
    pub diff: Option<PathBuf>,
    /// Render DPI for pure-Rust hayro PDF rasterization
    #[arg(long, default_value_t = 150.0)]
    pub dpi: f32,
    /// Timeout in seconds for optional Word/LibreOffice rendering
    #[arg(long, default_value_t = 120)]
    pub timeout: u64,
    #[arg(long)]
    pub json: bool,
}

#[derive(Args)]
pub struct WordRenderArgs {
    pub docx: PathBuf,
    #[arg(long)]
    pub out: PathBuf,
    #[arg(long, default_value_t = 120)]
    pub timeout: u64,
    #[arg(long)]
    pub json: bool,
}

#[derive(Args)]
pub struct DoctorArgs {
    /// Emit machine-readable JSON output
    #[arg(long)]
    pub json: bool,
    /// Optional repo or skill root directory to inspect
    #[arg(long)]
    pub root: Option<PathBuf>,
}

pub fn run_chunk(cmd: ChunkCmd) -> Result<u8> {
    match cmd {
        ChunkCmd::Build {
            work,
            out,
            max_tokens,
            json,
        } => {
            let book = load_book(work.join(BOOK_FILENAME))?;
            let out_dir = out.unwrap_or_else(|| work.join("worksheets"));
            let budget = ChunkBudget {
                max_tokens,
                ..ChunkBudget::default()
            };
            let manifest = build_worksheets(&book, &out_dir, budget)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&manifest)?);
            } else {
                println!(
                    "Built {} worksheets in {}",
                    manifest.sheets.len(),
                    out_dir.display()
                );
            }
        }
        ChunkCmd::Status { work, dir, json } => {
            let book = load_book(work.join(BOOK_FILENAME))?;
            let target = dir.unwrap_or_else(|| work.join("worksheets"));
            let status = chunk_status(&book, &target)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&status)?);
            } else {
                println!(
                    "Worksheets: {} answered, {} pending, {} invalid",
                    status.answered, status.pending, status.invalid
                );
            }
        }
    }
    Ok(0)
}

pub fn run_batch(cmd: BatchCmd) -> Result<u8> {
    match cmd {
        BatchCmd::PlanShards {
            work,
            max_chars,
            json,
        } => {
            let book = load_book(work.join(BOOK_FILENAME))?;
            let manifest = batch::plan_shards(&book, &work, max_chars)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&manifest)?);
            } else {
                println!(
                    "Planned {} parallel shards in {}",
                    manifest.shards.len(),
                    work.display()
                );
            }
        }
        BatchCmd::MergeShards { work, shards, json } => {
            let report = batch::merge_shards(&work, &shards)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&report)?);
            } else {
                println!("Merged {} shards into {}", shards.len(), work.display());
            }
        }
        BatchCmd::PlanBooks { books, out, json } => {
            let manifest = batch::plan_books(&books, &out)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&manifest)?);
            } else {
                println!(
                    "Planned {} books in {}",
                    manifest.shards.len(),
                    out.display()
                );
            }
        }
    }
    Ok(0)
}

pub fn run_pages(cmd: PagesCmd) -> Result<u8> {
    match cmd {
        PagesCmd::Build { work, max_tokens } => {
            let budget = ChunkBudget {
                max_tokens,
                ..ChunkBudget::default()
            };
            let plan = pages::build(&work, budget)?;
            println!("{}", serde_json::to_string_pretty(&plan)?);
        }
        PagesCmd::Status { work } => {
            println!("{}", serde_json::to_string_pretty(&pages::status(&work)?)?);
        }
        PagesCmd::Next { work } => {
            println!("{}", serde_json::to_string_pretty(&pages::next(&work)?)?);
        }
        PagesCmd::Merge { work, page } => {
            println!(
                "{}",
                serde_json::to_string_pretty(&pages::merge(&work, page)?)?
            );
        }
        PagesCmd::Preview { work, page } => {
            println!(
                "{}",
                serde_json::to_string_pretty(&pages::preview(&work, page)?)?
            );
        }
        PagesCmd::Review {
            work,
            page,
            verdict,
        } => {
            let raw = fs::read_to_string(&verdict)
                .with_context(|| format!("read visual review {}", verdict.display()))?;
            let review: VisualReview = serde_json::from_str(&raw)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&pages::review(&work, page, &review)?)?
            );
        }
        PagesCmd::Accept { work, page } => {
            println!(
                "{}",
                serde_json::to_string_pretty(&pages::accept(&work, page)?)?
            );
        }
    }
    Ok(0)
}

pub fn run_merge(args: MergeArgs) -> Result<u8> {
    let report = merge_work_dir(&args.work)?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        println!("Merged worksheets into {}", args.work.display());
    }
    Ok(0)
}

pub fn run_build(args: BuildArgs) -> Result<u8> {
    let book = load_book(args.work.join(BOOK_FILENAME))?;
    let options = BuildOptions {
        assets_dir: args.work.join("assets"),
        toc: !args.no_toc,
        page_numbers: !args.no_page_numbers,
        header: args.header,
        footer: args.footer,
    };
    let report = build_docx(&book, &args.out, &options)?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        println!(
            "Built {} (sha256: {}, {} images, {} footnotes)",
            report.path.display(),
            report.sha256,
            report.images,
            report.footnotes
        );
    }
    Ok(0)
}

pub fn run_fluency(cmd: FluencyCmd) -> Result<u8> {
    match cmd {
        FluencyCmd::Sheets { work, locked_terms } => {
            println!(
                "{}",
                serde_json::to_string_pretty(&fluency::sheets(&work, &locked_terms)?)?
            );
        }
        FluencyCmd::Record {
            work,
            request,
            edits,
        } => {
            let raw = fs::read_to_string(&edits)
                .with_context(|| format!("read fluency edits {}", edits.display()))?;
            let parsed: Vec<FluencyEdit> = serde_json::from_str(&raw)?;
            println!(
                "{}",
                serde_json::to_string_pretty(&fluency::record(&work, &request, parsed)?)?
            );
        }
        FluencyCmd::Apply { work } => {
            println!("{}", serde_json::to_string_pretty(&fluency::apply(&work)?)?);
        }
        FluencyCmd::Status { work } => {
            println!(
                "{}",
                serde_json::to_string_pretty(&fluency::status(&work)?)?
            );
        }
    }
    Ok(0)
}

pub fn run_meaning(cmd: MeaningCmd) -> Result<u8> {
    match cmd {
        MeaningCmd::Sheets { work } => {
            println!(
                "{}",
                serde_json::to_string_pretty(&meaning::sheets(&work)?)?
            );
        }
        MeaningCmd::Record {
            work,
            request,
            findings,
            reviewed,
        } => {
            let raw = fs::read_to_string(&findings)
                .with_context(|| format!("read meaning findings {}", findings.display()))?;
            let parsed: Vec<Finding> = serde_json::from_str(&raw)?;
            let set: BTreeSet<String> = reviewed.into_iter().collect();
            println!(
                "{}",
                serde_json::to_string_pretty(&meaning::record(&work, &request, parsed, set)?)?
            );
        }
        MeaningCmd::Status { work } => {
            println!(
                "{}",
                serde_json::to_string_pretty(&meaning::status(&work)?)?
            );
        }
    }
    Ok(0)
}

pub fn run_qa(cmd: QaCmd) -> Result<u8> {
    match cmd {
        QaCmd::Check { work, json } => {
            let report = qa_work_dir(&work)?;
            let has_errors = report
                .report
                .findings
                .iter()
                .any(|f| f.severity == Severity::Error);
            if json {
                println!("{}", serde_json::to_string_pretty(&report)?);
            } else {
                println!("QA findings: {}", report.report.findings.len());
            }
            Ok(if has_errors { 1 } else { 0 })
        }
        QaCmd::Docx { docx, work, json } => run_docx_check(docx, work, json),
    }
}

pub fn run_doc_qa(cmd: DocQaCmd) -> Result<u8> {
    match cmd {
        DocQaCmd::Check { docx, work, json } | DocQaCmd::Review { docx, work, json } => {
            run_docx_check(docx, work, json)
        }
    }
}

fn run_docx_check(docx: PathBuf, work: Option<PathBuf>, json: bool) -> Result<u8> {
    let book = match work {
        Some(dir) => Some(load_book(dir.join(BOOK_FILENAME))?),
        None => None,
    };
    let report = check_docx(&docx, book.as_ref())?;
    let has_errors = report
        .findings
        .iter()
        .any(|f| f.severity == Severity::Error);
    if json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else if report.findings.is_empty() {
        println!("OK: DOCX checks passed for {}", docx.display());
    } else {
        for f in &report.findings {
            println!("[{:?}] {}: {}", f.severity, f.code, f.detail);
        }
    }
    Ok(if has_errors { 1 } else { 0 })
}

pub fn run_render_qa(args: RenderQaArgs) -> Result<u8> {
    if let Some(docx) = args.docx {
        let out_dir = args
            .out
            .context("--out <dir> is required when running render-qa on a DOCX")?;
        if args.timeout == 0 {
            bail!("timeout must be greater than zero");
        }
        let rendered = wordrender(&docx, &out_dir, Duration::from_secs(args.timeout))?;
        let target_dir = out_dir.join("target_pages");
        let target_pngs = revayat_ingest::render_pdf_pages(&rendered.pdf, &target_dir, args.dpi)?;
        if let Some(source_pdf) = args.source {
            let source_dir = out_dir.join("source_pages");
            let source_pngs = revayat_ingest::render_pdf_pages(&source_pdf, &source_dir, args.dpi)?;
            let diff_dir = out_dir.join("diff_pages");
            fs::create_dir_all(&diff_dir)?;
            let mut reports = Vec::new();
            let mut any_finding = source_pngs.len() != target_pngs.len();
            for (idx, (src, tgt)) in source_pngs.iter().zip(target_pngs.iter()).enumerate() {
                let diff_path = diff_dir.join(format!("page_{:04}_diff.png", idx + 1));
                let rep = render_qa(src, tgt, Some(&diff_path))?;
                if !rep.findings.is_empty() {
                    any_finding = true;
                }
                reports.push(rep);
            }
            let summary = serde_json::json!({
                "source_pages": source_pngs.len(),
                "target_pages": target_pngs.len(),
                "comparisons": reports,
            });
            println!("{}", serde_json::to_string_pretty(&summary)?);
            return Ok(if any_finding { 1 } else { 0 });
        }
        let summary = serde_json::json!({
            "pdf": rendered.pdf,
            "target_pages": target_pngs,
        });
        println!("{}", serde_json::to_string_pretty(&summary)?);
        return Ok(0);
    }

    let source = args
        .source
        .context("provide either <docx> --out <dir> or --source <png> --target <png>")?;
    let target = args
        .target
        .context("--target <png> is required in PNG comparison mode")?;
    let report = render_qa(&source, &target, args.diff.as_deref())?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else if report.findings.is_empty() {
        println!("OK: rendered pages match within tolerance.");
    } else {
        println!("render-qa findings: {}", report.findings.join(", "));
    }
    Ok(if report.findings.is_empty() { 0 } else { 1 })
}

pub fn run_wordrender(args: WordRenderArgs) -> Result<u8> {
    if args.timeout == 0 {
        bail!("timeout must be greater than zero");
    }
    let report = wordrender(&args.docx, &args.out, Duration::from_secs(args.timeout))?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        println!("Rendered PDF -> {}", report.pdf.display());
    }
    Ok(0)
}

pub fn run_doctor_cmd(args: DoctorArgs) -> Result<u8> {
    let report = run_doctor(args.root.as_deref());
    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        println!("revayat-novel {} ({})", report.version, report.build_target);
        println!("requires_python: {}", report.requires_python);
        if let Some(root) = &report.skill_root {
            println!("skill_root: {}", root.display());
        }
        println!(
            "fonts: {}/{} present",
            report.fonts.values().filter(|&&ok| ok).count(),
            report.fonts.len()
        );
        println!(
            "manifests: {}/{} present",
            report.manifests.values().filter(|&&ok| ok).count(),
            report.manifests.len()
        );
    }
    Ok(0)
}

#![forbid(unsafe_code)]

mod cmd_ingest;
mod cmd_meta;
mod cmd_persian;
mod cmd_pipeline;

use std::{
    path::{Path, PathBuf},
    process::ExitCode,
};

use anyhow::Result;
use clap::{Parser, Subcommand};
use revayat_core::{LogLevel, RunLog};

#[derive(Parser)]
#[command(
    name = "revayat-novel",
    version,
    about = "Pure-Rust book translation, Persian typography, OOXML/DOCX builder, and multi-agent pipeline CLI"
)]
struct Cli {
    /// Optional directory for structured UTC operational logs (creates logs/<component>_*_UTC.log)
    #[arg(long, global = true)]
    log_dir: Option<PathBuf>,

    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Extract PDF/EPUB/DOCX/HTML into Book IR + lossless assets
    Extract(cmd_ingest::ExtractArgs),
    /// Fetch and sanitize a web novel chapter via rustls + scraper
    WebImport(cmd_ingest::WebImportArgs),
    /// Survey, clean, or preview scanned book rasters
    CleanScan {
        #[command(subcommand)]
        cmd: cmd_ingest::CleanScanCmd,
    },
    /// Run optional external tesseract OCR or MinerU JSON import into a sidecar
    OcrSidecar(cmd_ingest::OcrSidecarArgs),
    /// Manage locked names and terms glossary
    Glossary {
        #[command(subcommand)]
        cmd: cmd_persian::GlossaryCmd,
    },
    /// Partition Book IR into bounded translation worksheets
    Chunk {
        #[command(subcommand)]
        cmd: cmd_pipeline::ChunkCmd,
    },
    /// Parallel shard and multi-book batch orchestration
    Batch {
        #[command(subcommand)]
        cmd: cmd_pipeline::BatchCmd,
    },
    /// Page-by-page translation and review lifecycle
    Pages {
        #[command(subcommand)]
        cmd: cmd_pipeline::PagesCmd,
    },
    /// Atomically merge translated worksheets into Book IR
    Merge(cmd_pipeline::MergeArgs),
    /// Build RTL Persian DOCX with bundled/embedded Vazirmatn and lossless images
    Build(cmd_pipeline::BuildArgs),
    /// Persian book typography and bidi isolation linter/fixer
    Falint {
        #[command(subcommand)]
        cmd: cmd_persian::FalintCmd,
    },
    /// Natural Persian fluency editorial pass
    Fluency {
        #[command(subcommand)]
        cmd: cmd_pipeline::FluencyCmd,
    },
    /// Source-to-target meaning fidelity verification pass
    Meaning {
        #[command(subcommand)]
        cmd: cmd_pipeline::MeaningCmd,
    },
    /// End-to-end book and DOCX quality gates
    Qa {
        #[command(subcommand)]
        cmd: cmd_pipeline::QaCmd,
    },
    /// OOXML/RTL/font/image/table/footnote verification
    DocQa {
        #[command(subcommand)]
        cmd: cmd_pipeline::DocQaCmd,
    },
    /// Compare rendered source and target PNGs for visual regressions
    RenderQa(cmd_pipeline::RenderQaArgs),
    /// Render DOCX to PDF using optional LibreOffice or Microsoft Word
    Wordrender(cmd_pipeline::WordRenderArgs),
    /// Inspect binary health, bundled Vazirmatn fonts, manifests, and optional tools
    Doctor(cmd_pipeline::DoctorArgs),
    /// Standalone Persian mechanics and style linter
    PersianLint(cmd_persian::PersianLintArgs),
    /// Persian idiom and glossary lookup
    PersianLookup(cmd_persian::PersianLookupArgs),
    /// Recoverable atomic multi-agent skill and binary installer
    Install(cmd_meta::InstallArgs),
    /// Score the 28-case evaluation benchmark
    EvalScore(cmd_meta::EvalScoreArgs),
    /// Evaluation adjudication workflow
    EvalAdjudicate {
        #[command(subcommand)]
        cmd: cmd_meta::EvalAdjudicateCmd,
    },
}

fn command_name(cmd: &Command) -> &'static str {
    match cmd {
        Command::Extract(_) => "extract",
        Command::WebImport(_) => "web-import",
        Command::CleanScan { .. } => "clean-scan",
        Command::OcrSidecar(_) => "ocr-sidecar",
        Command::Glossary { .. } => "glossary",
        Command::Chunk { .. } => "chunk",
        Command::Batch { .. } => "batch",
        Command::Pages { .. } => "pages",
        Command::Merge(_) => "merge",
        Command::Build(_) => "build",
        Command::Falint { .. } => "falint",
        Command::Fluency { .. } => "fluency",
        Command::Meaning { .. } => "meaning",
        Command::Qa { .. } => "qa",
        Command::DocQa { .. } => "doc-qa",
        Command::RenderQa(_) => "render-qa",
        Command::Wordrender(_) => "wordrender",
        Command::Doctor(_) => "doctor",
        Command::PersianLint(_) => "persian-lint",
        Command::PersianLookup(_) => "persian-lookup",
        Command::Install(_) => "install",
        Command::EvalScore(_) => "eval-score",
        Command::EvalAdjudicate { .. } => "eval-adjudicate",
    }
}

fn open_log(explicit: Option<&Path>) -> Option<RunLog> {
    let base = explicit.map(Path::to_path_buf)?;
    RunLog::create(base, "revayat-novel").ok()
}

fn dispatch(command: Command) -> Result<u8> {
    match command {
        Command::Extract(args) => cmd_ingest::run_extract(args),
        Command::WebImport(args) => cmd_ingest::run_web_import(args),
        Command::CleanScan { cmd } => cmd_ingest::run_clean_scan(cmd),
        Command::OcrSidecar(args) => cmd_ingest::run_ocr_sidecar(args),
        Command::Glossary { cmd } => cmd_persian::run_glossary(cmd),
        Command::Chunk { cmd } => cmd_pipeline::run_chunk(cmd),
        Command::Batch { cmd } => cmd_pipeline::run_batch(cmd),
        Command::Pages { cmd } => cmd_pipeline::run_pages(cmd),
        Command::Merge(args) => cmd_pipeline::run_merge(args),
        Command::Build(args) => cmd_pipeline::run_build(args),
        Command::Falint { cmd } => cmd_persian::run_falint(cmd),
        Command::Fluency { cmd } => cmd_pipeline::run_fluency(cmd),
        Command::Meaning { cmd } => cmd_pipeline::run_meaning(cmd),
        Command::Qa { cmd } => cmd_pipeline::run_qa(cmd),
        Command::DocQa { cmd } => cmd_pipeline::run_doc_qa(cmd),
        Command::RenderQa(args) => cmd_pipeline::run_render_qa(args),
        Command::Wordrender(args) => cmd_pipeline::run_wordrender(args),
        Command::Doctor(args) => cmd_pipeline::run_doctor_cmd(args),
        Command::PersianLint(args) => cmd_persian::run_persian_lint(args),
        Command::PersianLookup(args) => cmd_persian::run_persian_lookup(args),
        Command::Install(args) => cmd_meta::run_install(args),
        Command::EvalScore(args) => cmd_meta::run_eval_score(args),
        Command::EvalAdjudicate { cmd } => cmd_meta::run_eval_adjudicate(cmd),
    }
}

fn redact_for_log(input: &str) -> String {
    let mut out = String::with_capacity(input.len().min(256));
    for token in input.split_whitespace() {
        if !out.is_empty() {
            out.push(' ');
        }
        let lower = token.to_ascii_lowercase();
        if let Some(scheme_end) = token.find("://") {
            let after = &token[scheme_end + 3..];
            if let Some(at) = after.find('@') {
                let host = &after[at + 1..];
                out.push_str(&token[..scheme_end + 3]);
                out.push_str("[REDACTED]@");
                out.push_str(host);
                continue;
            }
        }
        if let Some((k, _)) = token.split_once('=') {
            let kl = k.to_ascii_lowercase();
            if kl.contains("token")
                || kl.contains("secret")
                || kl.contains("password")
                || kl.contains("passwd")
                || kl.contains("apikey")
                || kl.contains("api_key")
                || kl.contains("auth")
                || kl.contains("cookie")
            {
                out.push_str(k);
                out.push_str("=[REDACTED]");
                continue;
            }
        }
        if lower == "bearer" {
            out.push_str("Bearer");
            continue;
        }
        out.push_str(token);
        if out.len() >= 256 {
            let cut = out.floor_char_boundary(256);
            out.truncate(cut);
            out.push_str("...");
            break;
        }
    }
    out.replace(['\r', '\n'], " ")
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let sub = command_name(&cli.command);
    let mut log = open_log(cli.log_dir.as_deref());
    if let Some(logger) = log.as_mut() {
        let _ = logger.log(LogLevel::Info, &format!("command_start command={sub}"));
    }

    match dispatch(cli.command) {
        Ok(code) => {
            if let Some(logger) = log.as_mut() {
                let _ = logger.log(
                    LogLevel::Info,
                    &format!("command_exit command={sub} exit_code={code}"),
                );
            }
            ExitCode::from(code)
        }
        Err(err) => {
            let msg = format!("{err:#}");
            if let Some(logger) = log.as_mut() {
                let safe = redact_for_log(&msg);
                let _ = logger.log(
                    LogLevel::Error,
                    &format!("command_failed command={sub} error={safe}"),
                );
            }
            eprintln!("error: {msg}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::redact_for_log;

    #[test]
    fn redacts_credentialed_urls_and_secret_kv_pairs() {
        let raw = "fetch https://user:topsecret@example.com/ch1 failed api_key=abc123 token=xyz";
        let safe = redact_for_log(raw);
        assert!(!safe.contains("topsecret"));
        assert!(!safe.contains("abc123"));
        assert!(!safe.contains("xyz"));
        assert!(safe.contains("https://[REDACTED]@example.com/ch1"));
        assert!(safe.contains("api_key=[REDACTED]"));
        assert!(safe.contains("token=[REDACTED]"));
    }
}

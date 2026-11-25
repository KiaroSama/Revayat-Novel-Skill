use std::{
    fs,
    io::{self, Read},
    path::PathBuf,
};

use anyhow::{Context, Result, bail};
use clap::{Args, Subcommand, ValueEnum};
use revayat_core::{BOOK_FILENAME, load_book, save_book};
use revayat_persian::{
    falint, glossary, glossentry, lookup,
    persian_lint::{self, Digits},
};

#[derive(Copy, Clone, ValueEnum)]
pub enum DigitsArg {
    Persian,
    Latin,
    Keep,
}

impl From<DigitsArg> for Digits {
    fn from(value: DigitsArg) -> Self {
        match value {
            DigitsArg::Persian => Digits::Persian,
            DigitsArg::Latin => Digits::Latin,
            DigitsArg::Keep => Digits::Keep,
        }
    }
}

#[derive(Args)]
pub struct PersianLintArgs {
    /// Check without modifying; exit non-zero if issues are found
    #[arg(long)]
    pub check: bool,
    /// Normalize and print fixed Persian text
    #[arg(long)]
    pub fix: bool,
    /// Write fixed output back to the input file
    #[arg(short = 'i', long = "in-place")]
    pub in_place: bool,
    /// Emit machine-readable JSON output
    #[arg(long)]
    pub json: bool,
    /// Include style / officialese / AI-tell suggestions
    #[arg(long)]
    pub style: bool,
    /// Digit conversion mode
    #[arg(long, value_enum, default_value_t = DigitsArg::Persian)]
    pub digits: DigitsArg,
    /// Input file path (reads UTF-8 stdin if omitted)
    pub file: Option<PathBuf>,
}

#[derive(Args)]
pub struct PersianLookupArgs {
    /// Reverse lookup (Persian -> English)
    #[arg(long)]
    pub reverse: bool,
    /// Emit JSON output
    #[arg(long)]
    pub json: bool,
    /// Input file path (reads UTF-8 stdin if omitted)
    pub file: Option<PathBuf>,
}

#[derive(Subcommand)]
pub enum FalintCmd {
    /// Check a Book IR working directory for Persian typography and bidi issues
    Lint {
        work: PathBuf,
        #[arg(long)]
        json: bool,
    },
    /// Apply deterministic Persian typography and RLI/PDI fixes to a Book IR
    Fix {
        work: PathBuf,
        #[arg(long)]
        keep_digits: bool,
        #[arg(long)]
        json: bool,
    },
}

#[derive(Subcommand)]
pub enum GlossaryCmd {
    /// Scan a Book IR for recurring proper nouns and term candidates
    Scan {
        work: PathBuf,
        #[arg(long, default_value_t = 2)]
        min_count: usize,
        #[arg(long)]
        json: bool,
    },
    /// Print glossary entries relevant to a text file or stdin
    TermsFor {
        #[arg(long)]
        glossary: PathBuf,
        file: Option<PathBuf>,
        #[arg(long)]
        json: bool,
    },
    /// Count frequencies of glossary terms in a Book IR
    Count {
        work: PathBuf,
        #[arg(long)]
        glossary: PathBuf,
    },
    /// Check glossary voice/policy consistency and first-mention rules
    Check {
        work: PathBuf,
        #[arg(long)]
        glossary: PathBuf,
        #[arg(long)]
        json: bool,
    },
}

fn read_text(file: Option<&PathBuf>) -> Result<String> {
    if let Some(path) = file {
        fs::read_to_string(path).with_context(|| format!("read UTF-8 file {}", path.display()))
    } else {
        let mut buf = String::new();
        io::stdin()
            .read_to_string(&mut buf)
            .context("read UTF-8 stdin")?;
        Ok(buf)
    }
}

pub fn run_persian_lint(args: PersianLintArgs) -> Result<u8> {
    if args.in_place && args.file.is_none() {
        bail!("--in-place requires a file path");
    }
    let text = read_text(args.file.as_ref())?;
    let digits: Digits = args.digits.into();
    let (fixed, stats) = persian_lint::normalize(&text, digits)?;

    if args.fix || args.in_place {
        if args.in_place {
            if let Some(path) = &args.file {
                fs::write(path, fixed.as_bytes())
                    .with_context(|| format!("write {}", path.display()))?;
            }
        } else {
            print!("{fixed}");
        }
        return Ok(0);
    }

    let changes = persian_lint::line_changes(&text, &fixed);
    let suggestions = persian_lint::suggestions(&text, args.style)?;
    let ok = changes.is_empty() && suggestions.is_empty();

    if args.json {
        let payload = serde_json::json!({
            "ok": ok,
            "stats": stats,
            "changes": changes,
            "suggestions": suggestions,
        });
        println!("{}", serde_json::to_string_pretty(&payload)?);
    } else if ok {
        println!("OK: no Persian mechanics issues found.");
    } else {
        for change in &changes {
            println!(
                "line {}: {} -> {}",
                change.line, change.before, change.after
            );
        }
        for s in &suggestions {
            println!("[{}] {}: {}", s.kind, s.hint, s.context);
        }
    }
    Ok(if ok { 0 } else { 1 })
}

pub fn run_persian_lookup(args: PersianLookupArgs) -> Result<u8> {
    let text = read_text(args.file.as_ref())?;
    let dir = if args.reverse {
        lookup::Direction::PersianToEnglish
    } else {
        lookup::Direction::EnglishToPersian
    };
    let brief = lookup::brief(&text, dir)?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&brief)?);
    } else {
        for entry in &brief.idioms {
            println!("idiom: {} -> {}", entry.en, entry.fa);
        }
        for entry in &brief.glossary {
            println!("term: {} -> {}", entry.en, entry.fa);
        }
        if brief.idioms.is_empty() && brief.glossary.is_empty() {
            println!("No matching idioms or glossary terms found.");
        }
    }
    Ok(0)
}

pub fn run_falint(cmd: FalintCmd) -> Result<u8> {
    match cmd {
        FalintCmd::Lint { work, json } => {
            let book = load_book(work.join(BOOK_FILENAME))?;
            let report = falint::lint_book(&book)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&report)?);
            } else {
                println!("falint findings: {}", report.findings.len());
            }
            Ok(if report.findings.is_empty() { 0 } else { 1 })
        }
        FalintCmd::Fix {
            work,
            keep_digits,
            json,
        } => {
            let path = work.join(BOOK_FILENAME);
            let mut book = load_book(&path)?;
            let mut options = falint::Options::default();
            if keep_digits {
                options.persian_digits = false;
            }
            let changes = falint::fix_book(&mut book, &options)?;
            save_book(&path, &book)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&changes)?);
            } else {
                println!("Applied falint fixes to {}", path.display());
            }
            Ok(0)
        }
    }
}

fn collect_block_texts(book: &revayat_core::BookDocument) -> Vec<(String, String)> {
    let mut out = Vec::new();
    for ch in &book.chapters {
        for block in &ch.blocks {
            match block {
                revayat_core::Block::Paragraph { id, runs, .. }
                | revayat_core::Block::Heading { id, runs, .. } => {
                    let text: String = runs.iter().map(|r| r.text.as_str()).collect();
                    out.push((id.clone(), text));
                }
                _ => {}
            }
        }
    }
    out
}

pub fn run_glossary(cmd: GlossaryCmd) -> Result<u8> {
    match cmd {
        GlossaryCmd::Scan {
            work,
            min_count,
            json,
        } => {
            let book = load_book(work.join(BOOK_FILENAME))?;
            let pairs = collect_block_texts(&book);
            let refs: Vec<(&str, &str)> = pairs
                .iter()
                .map(|(id, text)| (id.as_str(), text.as_str()))
                .collect();
            let candidates = glossentry::scan(refs, min_count, glossentry::GLOBAL_TOP_N)?;
            if json {
                println!("{}", serde_json::to_string_pretty(&candidates)?);
            } else {
                println!("Found {} glossary candidates", candidates.len());
            }
            Ok(0)
        }
        GlossaryCmd::TermsFor {
            glossary: glossary_path,
            file,
            json,
        } => {
            let g = glossentry::load(&glossary_path)?;
            let text = read_text(file.as_ref())?;
            let entries = glossary::entries_for_text(&g, &text, glossentry::GLOBAL_TOP_N);
            if json {
                println!("{}", serde_json::to_string_pretty(&entries)?);
            } else {
                print!("{}", glossary::render_term_table(&entries, &g.policy, &[]));
            }
            Ok(0)
        }
        GlossaryCmd::Count {
            work,
            glossary: glossary_path,
        } => {
            let book = load_book(work.join(BOOK_FILENAME))?;
            let mut g = glossentry::load(&glossary_path)?;
            let pairs = collect_block_texts(&book);
            let refs: Vec<&str> = pairs.iter().map(|(_, text)| text.as_str()).collect();
            glossentry::count_frequencies(&mut g, refs);
            glossentry::save(&g, &glossary_path)?;
            println!("Updated frequencies in {}", glossary_path.display());
            Ok(0)
        }
        GlossaryCmd::Check {
            work: _,
            glossary: glossary_path,
            json,
        } => {
            let g = glossentry::load(&glossary_path)?;
            let problems = glossary::voice_problems(&g);
            if json {
                println!("{}", serde_json::to_string_pretty(&problems)?);
            } else if problems.is_empty() {
                println!("OK: glossary checks passed.");
            } else {
                for p in &problems {
                    println!("problem: {p}");
                }
            }
            Ok(if problems.is_empty() { 0 } else { 1 })
        }
    }
}

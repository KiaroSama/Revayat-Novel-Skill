//! Publication typography. Apply fixes to prose without changing its markup.
use crate::{famorph, isolate_persian_line};
use fancy_regex::{Captures, Regex, Replacer};
use revayat_core::{
    Block, BookDocument, InlineSpan, TextRun, parse_markup, prose, published_units, render_spans,
};
use serde::Serialize;
use std::{collections::BTreeMap, sync::LazyLock};

const LETTERS: &str = "ء-غف-يٮ-ۓۺ-ۿ";
const WORD: &str = r"\pL\pN_";
const LITERALS: &str = r"(?xi)(?:https?://|www\.)\S+|[\pL\pN_.+-]+@[\pL\pN_-]+\.[\pL\pN_.-]+|(?<![\pL\pN_])[0-9]{1,4}(?:[.:/-][0-9]{1,4})+(?![\pL\pN_])|(?<![\pL\pN_])[A-Za-zİı][\pL\pN_.'’\-]*(?<=[\pL\pN_])(?![\pL\pN_])";

macro_rules! rx {
    ($pattern:expr) => {{
        static RX: LazyLock<Result<Regex, fancy_regex::Error>> =
            LazyLock::new(|| Regex::new(&$pattern));
        RX.as_ref().map_err(Clone::clone)?
    }};
}

type Cap<'a> = Captures<'a, str>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Options {
    pub persian_digits: bool,
    pub quotes: bool,
    pub ellipsis: bool,
    pub zwnj: bool,
    pub punctuation: bool,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            persian_digits: true,
            quotes: true,
            ellipsis: true,
            zwnj: true,
            punctuation: true,
        }
    }
}

pub fn mask_literals(text: &str) -> Result<(String, Vec<&str>), fancy_regex::Error> {
    let mut keep = Vec::new();
    let mut masked = String::new();
    let mut start = 0;
    for found in rx!(LITERALS).find_iter(text) {
        let found = found?;
        masked.push_str(&text[start..found.start()]);
        masked.push_str(&format!("\0{}\0", keep.len()));
        keep.push(found.as_str());
        start = found.end();
    }
    masked.push_str(&text[start..]);
    Ok((masked, keep))
}

pub fn unmask_literals(text: &str, keep: &[&str]) -> Result<String, fancy_regex::Error> {
    replace(text, rx!("\0([0-9]+)\0"), |c: &Cap<'_>| {
        c[1].parse::<usize>()
            .ok()
            .and_then(|i| keep.get(i))
            .map_or(&c[0], |s| s)
            .to_owned()
    })
}

pub fn blank_protected(text: &str, latin: bool) -> Result<String, fancy_regex::Error> {
    let word = rx!(r"^[A-Za-z][\pL\pN_.'’\-]*$");
    replace(text, rx!(LITERALS), |c: &Cap<'_>| {
        if !latin && word.is_match(&c[0]).unwrap_or(false) {
            c[0].to_owned()
        } else {
            " ".repeat(c[0].chars().count())
        }
    })
}

pub(crate) fn literal_ranges(
    text: &str,
) -> Result<Vec<std::ops::Range<usize>>, fancy_regex::Error> {
    rx!(LITERALS)
        .find_iter(text)
        .map(|m| m.map(|m| m.range()))
        .collect()
}

fn replace(text: &str, pattern: &Regex, rep: impl Replacer) -> Result<String, fancy_regex::Error> {
    Ok(pattern.try_replacen(text, 0, rep)?.into_owned())
}

fn prefix() -> Result<&'static Regex, fancy_regex::Error> {
    Ok(rx!(format!(
        r"(?<![{WORD}])(می|نمی) +([{LETTERS}]+)(?![{WORD}])"
    )))
}
fn suffix() -> Result<&'static Regex, fancy_regex::Error> {
    Ok(rx!(format!(
        r"([{LETTERS}]{{2,}}) +(ها|های|هایی|هایم|هایت|هایش|هایمان|هایتان|هایشان|تری|ترین)(?![{WORD}])"
    )))
}

fn digit(c: char, from: char) -> char {
    char::from_u32(u32::from(c) - u32::from(from) + 0x06f0).unwrap_or(c)
}

pub fn fix_prose(text: &str, options: &Options) -> Result<String, fancy_regex::Error> {
    let (masked, keep) = mask_literals(text)?;
    let mut text: String = masked
        .chars()
        .filter_map(|c| {
            Some(match c {
                'ي' | 'ى' => 'ی',
                'ك' => 'ک',
                '\u{a0}' => ' ',
                '٠'..='٩' => digit(c, '٠'),
                'ـ' | '\u{200b}' | '\u{feff}' => return None,
                _ => c,
            })
        })
        .collect();
    if options.quotes {
        text = replace(&text, rx!(r#"(?s)["“](.+?)["”]"#), "«${1}»")?;
    }
    if options.ellipsis {
        text = replace(&text, rx!(r"\.{3,}"), "…")?;
    }
    if options.punctuation {
        text = replace(
            &text,
            rx!(format!(r"(?<=[{LETTERS}۰-۹]) *([,;?])")),
            |c: &Cap<'_>| match &c[1] {
                "," => "،",
                ";" => "؛",
                _ => "؟",
            },
        )?;
        text = replace(&text, rx!(r"[ \t]+([،؛؟!:.»…])"), "$1")?;
        text = replace(&text, rx!(format!(r"([،؛؟!:])(?=[{LETTERS}])")), "$1 ")?;
        text = replace(&text, rx!(r"«\s+|\s+»"), |c: &Cap<'_>| {
            c[0].trim().to_owned()
        })?;
    }
    if options.zwnj {
        for _ in 0..3 {
            let picked = replace(&text, suffix()?, |c: &Cap<'_>| {
                let (word, ending) = (&c[1], &c[2]);
                if !matches!(ending, "تری" | "ترین") || famorph::takes_comparative(word, ending)
                {
                    format!("{word}‌{ending}")
                } else {
                    c[0].to_owned()
                }
            })?;
            let picked = replace(&picked, prefix()?, |c: &Cap<'_>| {
                if famorph::is_verb_form(&c[2]) {
                    format!("{}‌{}", &c[1], &c[2])
                } else {
                    c[0].to_owned()
                }
            })?;
            if picked == text {
                break;
            }
            text = picked;
        }
    }
    if options.persian_digits {
        text = replace(
            &text,
            rx!(format!(r"(?<![{WORD}.:/\x00-])[0-9]+(?![{WORD}.:/\x00-])")),
            |c: &Cap<'_>| c[0].chars().map(|c| digit(c, '0')).collect::<String>(),
        )?;
    }
    text = replace(&text, rx!(r"[ \t]{2,}"), " ")?;
    unmask_literals(&text, &keep)
}

fn quote_positions(text: &str) -> Result<Vec<usize>, fancy_regex::Error> {
    let ranges = literal_ranges(text)?;
    Ok(text
        .char_indices()
        .filter(|&(i, c)| {
            matches!(c, '"' | '“' | '”') && !ranges.iter().any(|range| range.contains(&i))
        })
        .map(|(i, _)| i)
        .collect())
}

fn fix_spans(spans: &mut [InlineSpan], options: &Options) -> Result<(), fancy_regex::Error> {
    if options.quotes {
        let mut marks = Vec::new();
        for (i, span) in spans
            .iter()
            .enumerate()
            .filter(|(_, s)| !s.verbatim && s.footnote.is_none())
        {
            marks.extend(quote_positions(&span.text)?.into_iter().map(|p| (i, p)));
        }
        let mut replacements = Vec::new();
        for &[(left, start), (right, end)] in marks.as_chunks::<2>().0 {
            if left != right {
                replacements.extend([(left, start, "«"), (right, end, "»")]);
            }
        }
        for (index, at, mark) in replacements.into_iter().rev() {
            let text = &mut spans[index].text;
            if let Some(original) = text[at..].chars().next() {
                text.replace_range(at..at + original.len_utf8(), mark);
            }
        }
    }
    for span in spans
        .iter_mut()
        .filter(|s| !s.verbatim && s.footnote.is_none())
    {
        span.text = fix_prose(&span.text, options)?;
    }
    Ok(())
}

pub fn fix_text(text: &str, options: &Options) -> Result<String, fancy_regex::Error> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Ok(text.to_owned());
    }
    let start = text.len() - text.trim_start().len();
    let end = start + trimmed.len();
    let mut spans = parse_markup(trimmed);
    fix_spans(&mut spans, options)?;
    Ok(format!(
        "{}{}{}",
        &text[..start],
        render_spans(&spans),
        &text[end..]
    ))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Issue {
    pub code: &'static str,
    pub detail: String,
}

pub fn lint_text(text: &str) -> Result<Vec<Issue>, fancy_regex::Error> {
    let plain = prose(text);
    let mut issues = Vec::new();
    if plain.trim().is_empty() {
        return Ok(issues);
    }
    let mut note = |code, detail: &str| {
        issues.push(Issue {
            code,
            detail: detail.chars().take(160).collect(),
        })
    };
    if rx!("[يكىـ٠-٩]").is_match(&plain)? {
        note(
            "arabic-forms",
            "Arabic yeh/kaf/tatweel or Arabic-Indic digits remain",
        );
    }
    let (opens, closes) = (plain.matches('«').count(), plain.matches('»').count());
    if opens != closes {
        note(
            "guillemets",
            &format!("unbalanced Persian quotes: {opens} « vs {closes} »"),
        );
    }
    if plain.contains(['"', '“', '”']) {
        note("latin-quotes", "Latin quotation marks in Persian prose");
    }
    if rx!(r"([،؛؟!])\1+").is_match(&plain)? {
        note("double-punctuation", "repeated punctuation mark");
    }
    let mut prefix_notes = 0;
    for m in prefix()?.captures_iter(plain.as_str()) {
        let m = m?;
        if !famorph::is_verb_form(&m[2]) {
            note(
                "zwnj-prefix-undecided",
                &format!(
                    "«{}»: left as written. «می» is the verb prefix and the noun *wine*, and the following word is not a verb form this pass recognises \u{2014} joining it would change the sentence",
                    &m[0]
                ),
            );
            prefix_notes += 1;
            if prefix_notes == 3 {
                break;
            }
        }
    }
    for m in suffix()?.captures_iter(plain.as_str()) {
        let m = m?;
        if matches!(&m[2], "تری" | "ترین") && !famorph::takes_comparative(&m[1], &m[2]) {
            note(
                "zwnj-comparative-undecided",
                &format!(
                    "«{}»: not joined. «تری» is also the noun *wetness*; «{}» is not in famorph._COMPARABLE",
                    &m[0], &m[1]
                ),
            );
        }
    }
    if let Some(m) = rx!(format!(r"[{LETTERS}]{{2,}} +تر(?![{WORD}])")).find(&plain)? {
        note(
            "zwnj-comparative",
            &format!(
                "«{}»: a comparative needs the ZWNJ, «تر» meaning wet must not have it \u{2014} only a reader can tell which this is",
                m.as_str()
            ),
        );
    }
    let latin = plain.bytes().filter(u8::is_ascii_alphabetic).count();
    let persian = rx!(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]").find_iter(&plain).count();
    if latin * 10 > (latin + persian) * 3
        && let Some(m) = rx!(r"[A-Za-z][A-Za-z ,'’\-]{25,}").find(&plain)?
    {
        let sample: String = m.as_str().chars().take(80).collect();
        note("untranslated", &format!("long Latin passage: {sample}"));
    } else if persian == 0 && latin > 0 {
        note("untranslated", "no Persian characters at all");
    }
    if rx!(format!(r"[{LETTERS}][A-Za-z]|[A-Za-z][{LETTERS}]")).is_match(&plain)? {
        note(
            "script-collision",
            "Latin and Persian letters with no separating space",
        );
    }
    Ok(issues)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Finding {
    pub unit: String,
    #[serde(flatten)]
    pub issue: Issue,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct Report {
    pub findings: Vec<Finding>,
    pub count: usize,
    pub by_code: BTreeMap<&'static str, usize>,
}

pub fn lint_book(book: &BookDocument) -> Result<Report, fancy_regex::Error> {
    let mut report = Report::default();
    for unit in published_units(book) {
        for issue in lint_text(&unit.text)? {
            *report.by_code.entry(issue.code).or_default() += 1;
            report.findings.push(Finding {
                unit: unit.id.clone(),
                issue,
            });
        }
    }
    report.count = report.findings.len();
    Ok(report)
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct Changes {
    pub changed: Vec<String>,
    pub changed_count: usize,
}

pub fn fix_book(book: &mut BookDocument, options: &Options) -> Result<Changes, fancy_regex::Error> {
    fn fix_runs(runs: &mut [TextRun], options: &Options) -> Result<bool, fancy_regex::Error> {
        let mut spans: Vec<_> = runs
            .iter()
            .map(|run| InlineSpan {
                text: run.text.clone(),
                bold: run.bold,
                italic: run.italic,
                verbatim: run.hyperlink.is_some(),
                footnote: run.footnote_ref.clone(),
            })
            .collect();
        fix_spans(&mut spans, options)?;
        if let Some(first) = spans.first_mut() {
            let mut whole = String::from(&first.text);
            for run in runs.iter().skip(1) {
                whole.push_str(&run.text);
            }
            if revayat_core::starts_with_latin_in_persian_line(&whole)
                && !whole.starts_with(revayat_core::RLM)
            {
                first.text.insert_str(0, "\u{200f}\u{2067}");
                if let Some(last) = spans.last_mut() {
                    last.text.push(revayat_core::PDI);
                }
            }
        }
        let mut changed = false;
        for (run, span) in runs.iter_mut().zip(spans) {
            changed |= run.text != span.text;
            run.text = span.text;
        }
        Ok(changed)
    }
    fn fix_blocks(
        blocks: &mut [Block],
        options: &Options,
        changed: &mut Vec<String>,
    ) -> Result<(), fancy_regex::Error> {
        for block in blocks {
            match block {
                Block::Paragraph { id, runs, .. }
                | Block::Heading { id, runs, .. }
                | Block::Image {
                    id, caption: runs, ..
                } => {
                    if fix_runs(runs, options)? {
                        changed.push(id.clone());
                    }
                }
                Block::Table { rows, .. } => {
                    for cell in rows.iter_mut().flat_map(|row| &mut row.cells) {
                        fix_blocks(&mut cell.blocks, options, changed)?;
                    }
                }
                Block::PageBreak { .. } | Block::SceneBreak { .. } => {}
            }
        }
        Ok(())
    }
    let mut next = book.clone();
    let mut changed = Vec::new();
    let mut fix_title = |id: &str, title: &mut String| -> Result<(), fancy_regex::Error> {
        let fixed = isolate_persian_line(&fix_text(title, options)?);
        if fixed != *title {
            *title = fixed;
            changed.push(id.to_owned());
        }
        Ok(())
    };
    fix_title("title", &mut next.title)?;
    for chapter in &mut next.chapters {
        fix_title(&chapter.id, &mut chapter.title)?;
    }
    for chapter in &mut next.chapters {
        fix_blocks(&mut chapter.blocks, options, &mut changed)?;
    }
    for note in &mut next.footnotes {
        fix_blocks(&mut note.blocks, options, &mut changed)?;
    }
    *book = next;
    Ok(Changes {
        changed_count: changed.len(),
        changed,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keeps_literal_urls_and_pairs_quotes_across_emphasis() -> Result<(), fancy_regex::Error> {
        let text = "\"این **خوب** است\" https://example.com/كتاب";
        assert_eq!(
            fix_text(text, &Options::default())?,
            "«این **خوب** است» https://example.com/كتاب"
        );
        Ok(())
    }
}

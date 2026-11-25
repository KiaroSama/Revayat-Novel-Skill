use std::{
    ffi::OsString,
    path::{Path, PathBuf},
    sync::LazyLock,
};

use regex::Regex;

use crate::{InlineSpan, parse_markup, render_spans};

pub const BOOK_FILENAME: &str = "book.json";
pub const RUN_STATE_FILENAME: &str = "run-state.json";
pub const QA_REPORT_FILENAME: &str = "qa-report.json";

fn reserved(name: &str) -> bool {
    let stem = name
        .split('.')
        .next()
        .unwrap_or_default()
        .trim_end()
        .to_ascii_uppercase();
    matches!(
        stem.as_str(),
        "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
    ) || stem
        .strip_prefix("COM")
        .or_else(|| stem.strip_prefix("LPT"))
        .is_some_and(|suffix| {
            matches!(
                suffix,
                "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
            )
        })
}

fn invalid_char(ch: char) -> bool {
    ch.is_control()
        || matches!(ch, '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*')
        || crate::bidi::is_bidi_control(ch)
}

pub fn is_safe_filename(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 255
        && !name.ends_with(['.', ' '])
        && !reserved(name)
        && !name.chars().any(invalid_char)
}

pub fn safe_filename(name: &str) -> String {
    let mut result = String::new();
    for ch in name
        .trim()
        .chars()
        .map(|ch| if invalid_char(ch) { '_' } else { ch })
    {
        if result.len() + ch.len_utf8() > 230 {
            break;
        }
        result.push(ch);
    }
    result.truncate(result.trim_end_matches(['.', ' ']).len());
    if result.is_empty() {
        return "book".into();
    }
    if reserved(&result) {
        result.insert(0, '_');
    }
    result
}

pub fn work_dir_name(title: &str) -> String {
    format!("{}_work", safe_filename(title))
}
pub fn output_filename(title: &str) -> String {
    format!("{}.docx", safe_filename(title))
}
pub fn report_filename(label: &str) -> String {
    format!("{}-report.json", safe_filename(label))
}
pub fn checkpoint_filename(label: &str) -> String {
    format!("{}-checkpoint.json", safe_filename(label))
}

pub fn lock_path(path: &Path) -> PathBuf {
    let mut name = OsString::from(path.as_os_str());
    name.push(".lock");
    name.into()
}

static URL: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?i)(?:https?://|www\.)[^\s\u{fffc}]+").ok());

fn prose_view(target: &str) -> (Vec<InlineSpan>, Vec<char>) {
    let spans = parse_markup(target);
    let unmasked: String = spans
        .iter()
        .map(|span| {
            if span.footnote.is_some() {
                "\u{fffc}".into()
            } else if span.verbatim {
                "\u{fffc}".repeat(span.text.chars().count())
            } else {
                span.text.clone()
            }
        })
        .collect();
    let mut view = Vec::new();
    let mut offset = 0;
    for found in URL.iter().flat_map(|re| re.find_iter(&unmasked)) {
        view.extend(unmasked[offset..found.start()].chars());
        view.extend(std::iter::repeat_n(
            '\u{fffc}',
            found.as_str().chars().count(),
        ));
        offset = found.end();
    }
    view.extend(unmasked[offset..].chars());
    (spans, view)
}

fn joined(ch: char) -> bool {
    ch.is_alphanumeric()
        || matches!(ch, '_' | '\u{200c}' | '\u{200d}')
        || unicode_normalization::char::is_combining_mark(ch)
}

fn spans_of(view: &[char], form: &str) -> Vec<(usize, usize)> {
    let needle: Vec<_> = form.chars().collect();
    if needle.is_empty() {
        return Vec::new();
    }
    view.windows(needle.len())
        .enumerate()
        .filter_map(|(start, window)| {
            let end = start + needle.len();
            (window == needle
                && !start
                    .checked_sub(1)
                    .and_then(|i| view.get(i))
                    .copied()
                    .is_some_and(joined)
                && !view.get(end).copied().is_some_and(joined))
            .then_some((start, end))
        })
        .collect()
}

fn width(span: &InlineSpan) -> usize {
    if span.footnote.is_some() {
        1
    } else {
        span.text.chars().count()
    }
}

pub fn introduction_spans(target: &str, form: &str) -> Vec<(usize, usize)> {
    spans_of(&prose_view(target).1, form)
}

pub fn introduction_count(target: &str, form: &str) -> usize {
    introduction_spans(target, form).len()
}

pub fn placement_spans(target: &str, form: &str) -> Vec<(usize, usize)> {
    let (spans, view) = prose_view(target);
    let matches = spans_of(&view, form);
    let mut found = Vec::new();
    let mut offset = 0;
    for span in spans {
        let end = offset + width(&span);
        if span.footnote.is_none() && !span.verbatim {
            found.extend(
                matches
                    .iter()
                    .copied()
                    .filter(|&(start, stop)| offset <= start && stop <= end),
            );
        }
        offset = end;
    }
    found
}

pub fn flatten_in_prose(
    target: &str,
    first_form: &str,
    later_form: &str,
    keep_first: bool,
) -> (String, usize) {
    let (mut spans, view) = prose_view(target);
    let matches = spans_of(&view, first_form);
    let matches = &matches[usize::from(keep_first && !matches.is_empty())..];
    if matches.is_empty() {
        return (target.into(), 0);
    }
    let later_len = later_form.chars().count();
    let mut offset = 0;
    for span in &mut spans {
        let end = offset + width(span);
        if span.footnote.is_none() && !span.verbatim {
            span.text = span
                .text
                .chars()
                .enumerate()
                .filter_map(|(index, ch)| {
                    let index = index + offset;
                    (!matches
                        .iter()
                        .any(|&(start, end)| (start + later_len..end).contains(&index)))
                    .then_some(ch)
                })
                .collect();
        }
        offset = end;
    }
    (render_spans(&spans), matches.len())
}

pub fn introduce_in_prose(target: &str, later_form: &str, first_form: &str) -> Option<String> {
    if introduction_count(target, first_form) > 0 {
        return Some(target.into());
    }
    let (start, end) = *placement_spans(target, later_form).first()?;
    let mut spans = parse_markup(target);
    let mut offset = 0;
    for span in &mut spans {
        let stop = offset + width(span);
        if offset <= start && end <= stop && span.footnote.is_none() && !span.verbatim {
            let points: Vec<_> = span
                .text
                .char_indices()
                .map(|(index, _)| index)
                .chain([span.text.len()])
                .collect();
            span.text
                .replace_range(points[start - offset]..points[end - offset], first_form);
            return Some(render_spans(&spans));
        }
        offset = stop;
    }
    None
}

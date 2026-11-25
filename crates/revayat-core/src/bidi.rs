use unicode_bidi::{BidiClass, bidi_class};

use crate::{Finding, Severity};

pub const RLI: char = '\u{2067}';
pub const PDI: char = '\u{2069}';
pub const RLM: char = '\u{200f}';

pub(crate) fn is_bidi_control(ch: char) -> bool {
    matches!(ch, '\u{061c}' | '\u{200e}'..='\u{200f}' | '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}')
}

pub fn has_persian(text: &str) -> bool {
    text.chars().any(|ch| {
        ch.is_alphabetic()
            && matches!(ch,
        '\u{0620}'..='\u{063f}' | '\u{0641}'..='\u{064a}' | '\u{066e}'..='\u{06d3}'
        | '\u{06ee}'..='\u{06ff}' | '\u{0750}'..='\u{077f}' | '\u{08a0}'..='\u{08c9}'
        | '\u{fb50}'..='\u{fdff}' | '\u{fe70}'..='\u{fefc}')
    })
}

fn is_code(line: &str) -> bool {
    let line = line.trim();
    line.starts_with("```")
        || line.starts_with("~~~")
        || (line.starts_with('`')
            && line.ends_with('`')
            && line
                .get(1..line.len().saturating_sub(1))
                .is_some_and(|body| !body.contains('`')))
}

pub fn starts_with_latin_in_persian_line(line: &str) -> bool {
    !is_code(line)
        && has_persian(line)
        && line
            .chars()
            .filter(|&ch| !is_bidi_control(ch))
            .find_map(|ch| match bidi_class(ch) {
                BidiClass::L => Some(true),
                BidiClass::R | BidiClass::AL => Some(false),
                _ => None,
            })
            == Some(true)
}

pub fn strip_bidi_controls(text: &str) -> String {
    text.chars().filter(|&ch| !is_bidi_control(ch)).collect()
}

fn balanced(text: &str) -> bool {
    let mut depth = 0usize;
    for ch in text.chars() {
        match ch {
            '\u{2066}'..='\u{2068}' => depth += 1,
            PDI if depth == 0 => return false,
            PDI => depth -= 1,
            _ => {}
        }
    }
    depth == 0
}

fn isolated(line: &str) -> bool {
    let line = line.trim().trim_start_matches(RLM);
    line.strip_prefix(RLI)
        .and_then(|inner| inner.strip_suffix(PDI))
        .is_some_and(balanced)
}

pub fn isolate_persian_line(line: &str) -> String {
    if !starts_with_latin_in_persian_line(line) || isolated(line) {
        return line.to_owned();
    }
    let (line, ending) = if let Some(text) = line.strip_suffix("\r\n") {
        (text, "\r\n")
    } else if let Some(text) = line.strip_suffix('\n') {
        (text, "\n")
    } else {
        (line, "")
    };
    let line = line.trim_start_matches(RLM);
    let text = if balanced(line) {
        line.into()
    } else {
        strip_bidi_controls(line)
    };
    format!("{RLM}{RLI}{text}{PDI}{ending}")
}

pub fn check_bidi_isolation(text: &str) -> Vec<Finding> {
    let mut fence = None;
    let mut findings = Vec::new();
    for (index, line) in text.lines().enumerate() {
        let marker = ["```", "~~~"]
            .into_iter()
            .find(|marker| line.trim_start().starts_with(marker));
        if let Some(marker) = marker {
            if fence == Some(marker) {
                fence = None;
            } else if fence.is_none() {
                fence = Some(marker);
            }
            continue;
        }
        if fence.is_some() || is_code(line) {
            continue;
        }
        let (code, detail) = if !balanced(line) {
            ("unbalanced-bidi", "Unmatched Unicode bidi isolate")
        } else if starts_with_latin_in_persian_line(line) && !isolated(line) {
            (
                "missing-bidi-isolation",
                "Persian line starts with left-to-right text and needs an RLI/PDI pair",
            )
        } else {
            continue;
        };
        findings.push(Finding::new(
            Severity::Error,
            code,
            (index + 1).to_string(),
            detail,
        ));
    }
    findings
}

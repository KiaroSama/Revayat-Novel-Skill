use std::{collections::BTreeMap, sync::LazyLock};

use regex::Regex;
use serde::{Deserialize, Serialize};

static INLINE: LazyLock<Option<Regex>> = LazyLock::new(|| {
    Regex::new(concat!(
        r"(?P<escaped>\\(?:[\\*`]|\[\[fn:))",
        r"|\[\[fn:(?P<note>[A-Za-z0-9_-]+)\]\]",
        r"|`(?P<verbatim>[^`]*)`",
        r"|\*\*\*(?P<both>(?:[^*\\]|\\.)+?)\*\*\*",
        r"|\*\*(?P<bold>(?:[^*\\]|\\.)+?)\*\*",
        r"|\*(?P<italic>(?:[^*\\]|\\.)+?)\*",
    ))
    .ok()
});

pub(crate) fn inline_regex() -> Option<&'static Regex> {
    INLINE.as_ref()
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct InlineSpan {
    pub text: String,
    pub bold: bool,
    pub italic: bool,
    pub verbatim: bool,
    pub footnote: Option<String>,
}

fn marker(bold: bool, italic: bool) -> &'static str {
    match (bold, italic) {
        (true, true) => "***",
        (true, false) => "**",
        (false, true) => "*",
        (false, false) => "",
    }
}

pub fn escape_markup(text: &str) -> String {
    let mut result = String::with_capacity(text.len());
    for (index, ch) in text.char_indices() {
        if matches!(ch, '\\' | '*' | '`') || (ch == '[' && text[index..].starts_with("[[fn:")) {
            result.push('\\');
        }
        result.push(ch);
    }
    result
}

fn push(spans: &mut Vec<InlineSpan>, span: InlineSpan) {
    if span.footnote.is_none() && span.text.is_empty() && !span.verbatim {
        return;
    }
    if span.footnote.is_none()
        && !span.verbatim
        && let Some(previous) = spans.last_mut()
        && previous.footnote.is_none()
        && !previous.verbatim
        && (previous.bold, previous.italic) == (span.bold, span.italic)
    {
        previous.text.push_str(&span.text);
    } else {
        spans.push(span);
    }
}

fn unescape(text: &str) -> String {
    let mut result = String::with_capacity(text.len());
    let mut input = text.chars().peekable();
    while let Some(ch) = input.next() {
        if ch == '\\' && input.peek().is_some_and(|next| *next != '\n') {
            continue;
        }
        result.push(ch);
    }
    result
}

fn parse_with_style(text: &str, style: (bool, bool), spans: &mut Vec<InlineSpan>, depth: usize) {
    let ordinary = |text: String| InlineSpan {
        text,
        bold: style.0,
        italic: style.1,
        ..Default::default()
    };
    let mut end = 0;
    if let Some(re) = inline_regex().filter(|_| depth < 16) {
        for cap in re.captures_iter(text) {
            let Some(token) = cap.get(0) else { continue };
            push(spans, ordinary(unescape(&text[end..token.start()])));
            if let Some(found) = cap.name("escaped") {
                push(spans, ordinary(found.as_str()[1..].into()));
            } else if let Some(note) = cap.name("note") {
                push(
                    spans,
                    InlineSpan {
                        footnote: Some(note.as_str().into()),
                        ..ordinary(String::new())
                    },
                );
            } else if let Some(code) = cap.name("verbatim") {
                push(
                    spans,
                    InlineSpan {
                        verbatim: true,
                        ..ordinary(code.as_str().into())
                    },
                );
            } else {
                for (field, bold, italic) in [
                    ("both", true, true),
                    ("bold", true, false),
                    ("italic", false, true),
                ] {
                    if let Some(body) = cap.name(field) {
                        parse_with_style(
                            body.as_str(),
                            (style.0 || bold, style.1 || italic),
                            spans,
                            depth + 1,
                        );
                        break;
                    }
                }
            }
            end = token.end();
        }
    }
    push(spans, ordinary(unescape(&text[end..])));
}

pub fn parse_markup(text: &str) -> Vec<InlineSpan> {
    let mut spans = Vec::new();
    parse_with_style(text, (false, false), &mut spans, 0);
    spans
}

pub fn render_spans(spans: &[InlineSpan]) -> String {
    let mut out = String::new();
    for group in
        spans.chunk_by(|left, right| (left.bold, left.italic) == (right.bold, right.italic))
    {
        let Some(first) = group.first() else { continue };
        let marker = marker(first.bold, first.italic);
        let mut body = String::new();
        for span in group {
            if let Some(id) = &span.footnote {
                body.push_str(&format!("[[fn:{id}]]"));
            } else if span.verbatim {
                body.push_str(&format!("`{}`", span.text));
            } else {
                body.push_str(&escape_markup(&span.text));
            }
        }
        if !body.is_empty() {
            out.push_str(marker);
            out.push_str(&body);
            out.push_str(marker);
        }
    }
    out
}

pub fn render_markup<'a>(spans: impl IntoIterator<Item = (&'a str, bool, bool)>) -> String {
    let mut merged = Vec::new();
    for (text, bold, italic) in spans {
        push(
            &mut merged,
            InlineSpan {
                text: text.into(),
                bold,
                italic,
                ..Default::default()
            },
        );
    }
    let mut output = String::new();
    for span in merged {
        let content = span.text.trim();
        let m = marker(span.bold, span.italic);
        if !content.is_empty() && !m.is_empty() {
            let lead = span.text.len() - span.text.trim_start().len();
            output.push_str(&span.text[..lead]);
            output.push_str(m);
            output.push_str(&escape_markup(content));
            output.push_str(m);
            output.push_str(&span.text[lead + content.len()..]);
        } else {
            output.push_str(&escape_markup(&span.text));
        }
    }
    output
}

pub fn plain_text(text: &str) -> String {
    parse_markup(text)
        .into_iter()
        .map(|span| span.text)
        .collect()
}

pub fn prose(text: &str) -> String {
    parse_markup(text)
        .into_iter()
        .filter(|span| !span.verbatim)
        .map(|span| span.text)
        .collect()
}

pub fn verbatim_spans(text: &str) -> Vec<String> {
    parse_markup(text)
        .into_iter()
        .filter(|span| span.verbatim)
        .map(|span| span.text)
        .collect()
}

pub(crate) fn canonical_note_id(id: &str) -> bool {
    id.strip_prefix("fn")
        .is_some_and(|digits| digits.len() >= 4 && digits.bytes().all(|b| b.is_ascii_digit()))
}

pub fn footnote_refs(text: &str) -> Vec<String> {
    footnote_refs_with_local(text, false)
}

pub fn footnote_refs_with_local(text: &str, include_local: bool) -> Vec<String> {
    parse_markup(text)
        .into_iter()
        .filter_map(|span| span.footnote)
        .filter(|id| include_local || canonical_note_id(id))
        .collect()
}

pub fn rewrite_footnote_refs(text: &str, mapping: &BTreeMap<String, String>) -> String {
    if mapping.is_empty() {
        return text.into();
    }
    let mut spans = parse_markup(text);
    for span in &mut spans {
        if let Some(replacement) = span.footnote.as_ref().and_then(|id| mapping.get(id)) {
            span.footnote = Some(replacement.clone());
        }
    }
    render_spans(&spans)
}

pub fn emphasis_signature(text: &str) -> (usize, usize, usize) {
    parse_markup(text)
        .iter()
        .filter(|span| span.footnote.is_none())
        .fold((0, 0, 0), |(bold, italic, verbatim), span| {
            if span.verbatim {
                (bold, italic, verbatim + 1)
            } else {
                (
                    bold + usize::from(span.bold),
                    italic + usize::from(span.italic),
                    verbatim,
                )
            }
        })
}

pub fn normalise_source(text: &str) -> String {
    fn clean(text: &str) -> String {
        let prose = |text: &str| {
            let mut output = String::new();
            let mut spacing = None;
            for ch in text.chars() {
                let ch = match ch {
                    '\u{feff}' | '\u{200b}' | '\u{200d}' | '\u{00ad}' | '\u{0640}' => continue,
                    '\u{00a0}' => ' ',
                    _ => ch,
                };
                if matches!(ch, ' ' | '\t') {
                    spacing = Some(if spacing.is_some() { ' ' } else { ch });
                } else {
                    output.extend(spacing.take());
                    output.push(ch);
                }
            }
            output.extend(spacing);
            output
        };
        let Some(re) = inline_regex() else {
            return prose(text);
        };
        let mut output = String::new();
        let mut end = 0;
        for cap in re.captures_iter(text) {
            let Some(token) = cap.get(0) else { continue };
            output.push_str(&prose(&text[end..token.start()]));
            if cap.name("verbatim").is_some() || cap.name("note").is_some() {
                output.push_str(token.as_str());
            } else if let Some((style, body)) = [("both", "***"), ("bold", "**"), ("italic", "*")]
                .into_iter()
                .find_map(|(name, style)| cap.name(name).map(|body| (style, body)))
            {
                output.push_str(style);
                output.push_str(&clean(body.as_str()));
                output.push_str(style);
            } else {
                output.push_str(&prose(token.as_str()));
            }
            end = token.end();
        }
        output.push_str(&prose(&text[end..]));
        output
    }
    clean(text).trim_matches([' ', '\t']).into()
}

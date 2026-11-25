use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;

const IDIOMS: &str = include_str!("../../../skills/persian/scripts/data/idioms.tsv");
const GLOSSARY: &str = include_str!("../../../skills/persian/scripts/data/glossary.tsv");
const PYTHON_WORD: &str = r"\pL\pN_";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction {
    EnglishToPersian,
    PersianToEnglish,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct Entry {
    pub en: String,
    pub fa: String,
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Brief {
    pub idioms: Vec<Entry>,
    pub glossary: Vec<Entry>,
}

fn space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')
}

pub fn parse_tsv(raw: &str) -> Vec<Entry> {
    raw.split(['\r', '\n'])
        .filter_map(|line| {
            if line.trim_start_matches(space).starts_with('#') {
                return None;
            }
            let mut fields = line.split('\t').map(|part| part.trim_matches(space));
            let en = fields.next().filter(|s| !s.is_empty())?;
            let fa = fields.next().filter(|s| !s.is_empty())?;
            Some(Entry {
                en: en.into(),
                fa: fa.into(),
                note: fields.next().unwrap_or_default().into(),
            })
        })
        .collect()
}

pub fn en_pattern(phrase: &str) -> Result<Regex, regex::Error> {
    let words: Vec<_> = phrase.split(space).filter(|s| !s.is_empty()).collect();
    let last = words.last().copied().unwrap_or_default();
    let uppercase = last.chars().any(char::is_uppercase) && !last.chars().any(char::is_lowercase);
    let phrase = words
        .iter()
        .map(|word| regex::escape(word))
        .collect::<Vec<_>>()
        .join(r"[\s\x1c-\x1f]+");
    let inflection = if last.chars().count() > 2 && !uppercase {
        "(?:es|s|ed|ing|d)?"
    } else {
        ""
    };
    let flags = if uppercase { "" } else { "(?i)" };
    Regex::new(&format!(
        "{flags}(?:^|[^{PYTHON_WORD}])(?:{phrase}{inflection})(?:$|[^{PYTHON_WORD}])"
    ))
}

pub fn dedup<'a>(entries: impl IntoIterator<Item = &'a Entry>) -> Vec<&'a Entry> {
    let mut seen = HashSet::new();
    entries
        .into_iter()
        .filter(|row| seen.insert(row.en.as_str()))
        .collect()
}

pub fn find_en<'a>(rows: &'a [Entry], text: &str) -> Result<Vec<&'a Entry>, regex::Error> {
    let mut matches = Vec::new();
    for row in rows {
        if en_pattern(&row.en)?.is_match(text) {
            matches.push(row);
        }
    }
    Ok(dedup(matches))
}

pub fn find_fa<'a>(rows: &'a [Entry], text: &str) -> Vec<&'a Entry> {
    dedup(rows.iter().filter(|row| {
        let fa = row
            .fa
            .split_once('/')
            .map_or(row.fa.as_str(), |(first, _)| first);
        !row.fa.is_empty() && text.contains(fa.trim_matches(space))
    }))
}

pub fn brief(text: &str, direction: Direction) -> Result<Brief, regex::Error> {
    let hits = |tsv| -> Result<Vec<Entry>, regex::Error> {
        let rows = parse_tsv(tsv);
        let found = match direction {
            Direction::EnglishToPersian => find_en(&rows, text)?,
            Direction::PersianToEnglish => find_fa(&rows, text),
        };
        Ok(found.into_iter().cloned().collect())
    };
    Ok(Brief {
        idioms: hits(IDIOMS)?,
        glossary: hits(GLOSSARY)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_bundled_banks_in_utf8() {
        assert!(!parse_tsv(IDIOMS).is_empty());
        assert!(
            parse_tsv(GLOSSARY)
                .iter()
                .any(|entry| entry.fa.contains('ی'))
        );
    }
}

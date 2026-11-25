use std::{
    collections::{BTreeMap, BTreeSet},
    sync::LazyLock,
};

use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::{CoreError, markup::inline_regex};

pub fn segment_id(unit_id: &str, index: impl std::fmt::Display) -> String {
    format!("{unit_id}#{index}")
}

fn segment(unit_id: &str) -> Option<(&str, usize)> {
    let (base, index) = unit_id.rsplit_once('#')?;
    if base.is_empty()
        || base.contains('\n')
        || index.is_empty()
        || !index.bytes().all(|c| c.is_ascii_digit())
    {
        return None;
    }
    Some((base, index.parse().ok()?))
}

pub fn base_of(unit_id: &str) -> &str {
    segment(unit_id).map_or(unit_id, |(base, _)| base)
}
pub fn index_of(unit_id: &str) -> usize {
    segment(unit_id).map_or(0, |(_, index)| index)
}

fn byte_cut_points(text: &str) -> Vec<usize> {
    let mut points = Vec::new();
    let mut start = 0;
    if let Some(regex) = inline_regex() {
        for token in regex.find_iter(text) {
            points.extend(
                text[start..token.start()]
                    .char_indices()
                    .map(|(i, _)| start + i),
            );
            points.push(token.start());
            start = token.end();
        }
    }
    points.extend(text[start..].char_indices().map(|(i, _)| start + i));
    points.push(text.len());
    points
}

pub fn cut_points(text: &str) -> Vec<usize> {
    let mut bytes = byte_cut_points(text).into_iter().peekable();
    let mut points = Vec::new();
    for (index, (offset, _)) in text.char_indices().chain([(text.len(), '\0')]).enumerate() {
        if bytes.next_if_eq(&offset).is_some() {
            points.push(index);
        }
    }
    points
}

static SENTENCE_END: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"[。！？]+[」』）】〉》”’»]*").ok());
static URL: LazyLock<Option<Regex>> =
    LazyLock::new(|| Regex::new(r"(?i)(?:https?://|www\.)\S+").ok());
static MARK: LazyLock<Option<Regex>> = LazyLock::new(|| Regex::new(r"^\p{M}").ok());

pub fn split_text(text: &str, budget: impl TryInto<usize>) -> Vec<&str> {
    let Ok(budget) = budget.try_into() else {
        return vec![text];
    };
    if budget == 0 || text.chars().count() <= budget {
        return vec![text];
    }
    let safe = byte_cut_points(text);
    let urls: Vec<_> = URL.iter().flat_map(|re| re.find_iter(text)).collect();
    let mut url_index = 0;
    let mut sentence_ends = BTreeSet::new();
    for stop in SENTENCE_END.iter().flat_map(|re| re.find_iter(text)) {
        while urls
            .get(url_index)
            .is_some_and(|url| url.end() <= stop.start())
        {
            url_index += 1;
        }
        let in_url = urls
            .get(url_index)
            .is_some_and(|url| url.start() < stop.end());
        let tail = &text[stop.end()..];
        if !in_url
            && !tail.starts_with('\u{200d}')
            && !MARK.as_ref().is_some_and(|re| re.is_match(tail))
        {
            sentence_ends.insert(stop.end());
        }
    }
    let ends: Vec<_> = text
        .char_indices()
        .skip(1)
        .chain([(text.len(), '\0')])
        .enumerate()
        .filter_map(|(index, (offset, _))| {
            (safe.binary_search(&offset).is_ok()
                && (offset == text.len()
                    || text[..offset].ends_with(char::is_whitespace)
                    || sentence_ends.contains(&offset)))
            .then_some((index + 1, offset))
        })
        .collect();
    let mut parts = Vec::new();
    let (mut start, mut used) = (0, 0usize);
    while start < text.len() {
        let index = ends.partition_point(|&(count, _)| count <= used.saturating_add(budget));
        let next = index
            .checked_sub(1)
            .and_then(|i| ends.get(i))
            .filter(|&&(count, _)| count > used)
            .or_else(|| ends.get(index));
        let Some(&(count, offset)) = next else {
            parts.push(&text[start..]);
            break;
        };
        parts.push(&text[start..offset]);
        (start, used) = (offset, count);
    }
    parts
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SegmentRecord {
    pub id: String,
    pub offset: usize,
    pub length: usize,
}

pub fn source_separators(
    source: &str,
    records: &[SegmentRecord],
) -> Result<BTreeMap<String, String>, CoreError> {
    let mut records: Vec<_> = records.iter().collect();
    records.sort_by_key(|record| record.offset);
    let offsets: Vec<_> = source
        .char_indices()
        .map(|(i, _)| i)
        .chain([source.len()])
        .collect();
    let mut end = 0usize;
    let mut result = BTreeMap::new();
    for (position, record) in records.into_iter().enumerate() {
        if record.offset != end {
            return Err(CoreError::InvalidSegments(
                "segments must form an ordered partition",
            ));
        }
        let Some(&byte) = offsets.get(end) else {
            return Err(CoreError::InvalidSegments("offset exceeds source"));
        };
        if position > 0 {
            let left = &source[..byte];
            let right = &source[byte..];
            let gap = &source[left.trim_end().len()..source.len() - right.trim_start().len()];
            if gap.contains(['\r', '\n']) {
                result.insert(
                    record.id.clone(),
                    gap.replace("\r\n", "\n").replace('\r', "\n"),
                );
            }
        }
        end = end
            .checked_add(record.length)
            .ok_or(CoreError::InvalidSegments("length exceeds source"))?;
    }
    if end != offsets.len() - 1 {
        return Err(CoreError::InvalidSegments("source coverage is incomplete"));
    }
    Ok(result)
}

pub fn rejoin(
    units: &BTreeMap<String, String>,
    separators: Option<&BTreeMap<String, String>>,
) -> BTreeMap<String, String> {
    let mut whole = BTreeMap::new();
    let mut parts = BTreeMap::<&str, Vec<(usize, &str)>>::new();
    for (id, value) in units {
        if let Some((base, index)) = segment(id) {
            parts.entry(base).or_default().push((index, value.trim()));
        } else {
            whole.insert(id.clone(), value.clone());
        }
    }
    for (base, mut parts) in parts {
        parts.sort_unstable();
        let mut text = String::new();
        for (index, part) in parts.into_iter().filter(|(_, text)| !text.is_empty()) {
            if !text.is_empty() {
                text.push_str(
                    separators
                        .and_then(|separators| separators.get(&segment_id(base, index)))
                        .map_or(" ", String::as_str),
                );
            }
            text.push_str(part);
        }
        whole.insert(base.into(), text);
    }
    whole
}

pub fn segments_by_owner<'a>(
    ids: impl IntoIterator<Item = &'a str>,
) -> BTreeMap<&'a str, BTreeSet<usize>> {
    let mut owners = BTreeMap::<_, BTreeSet<_>>::new();
    for id in ids {
        if let Some((base, index)) = segment(id) {
            owners.entry(base).or_default().insert(index);
        }
    }
    owners
}

pub fn owners<'a>(ids: impl IntoIterator<Item = &'a str>) -> Vec<&'a str> {
    let mut seen = BTreeSet::new();
    ids.into_iter()
        .map(base_of)
        .filter(|base| seen.insert(*base))
        .collect()
}

pub fn coverage_problems<'a>(
    known: &BTreeMap<&str, BTreeSet<usize>>,
    answered: impl IntoIterator<Item = &'a str>,
) -> Vec<String> {
    let answered: Vec<_> = answered.into_iter().collect();
    let whole: BTreeSet<_> = answered
        .iter()
        .copied()
        .filter(|id| segment(id).is_none())
        .collect();
    let mut problems = Vec::new();
    for (owner, indices) in segments_by_owner(answered) {
        let expected = known.get(owner).unwrap_or(&indices);
        let answered: Vec<_> = indices.iter().collect();
        let missing: Vec<_> = expected.difference(&indices).collect();
        if !missing.is_empty() {
            let expected: Vec<_> = expected.iter().collect();
            problems.push(format!("{owner}: segments {answered:?} answered but the manifest splits it into {expected:?}; writing this would replace the block with part of itself (missing {missing:?})"));
        }
        if whole.contains(owner) {
            problems.push(format!("{owner}: answered both as a whole unit and as segments {answered:?}; those are two different claims about what the unit is"));
        }
    }
    problems
}

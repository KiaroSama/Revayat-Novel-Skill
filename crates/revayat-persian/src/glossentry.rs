//! Glossary storage and candidate extraction.
use indexmap::IndexMap;
use regex::Regex;
use revayat_core::plain_text;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeSet, HashSet},
    fs,
    io::{self, Write},
    path::Path,
    sync::LazyLock,
};
use unicode_normalization::{UnicodeNormalization, char::is_combining_mark};

pub const SCHEMA: &str = "revayat-novel/glossary@1";
pub const GLOBAL_TOP_N: usize = 25;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ParentheticalPolicy {
    #[default]
    FirstMention,
    FirstPerChapter,
    Never,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Policy {
    #[serde(default)]
    pub original_parenthetical: ParentheticalPolicy,
    pub lock_canonical: bool,
    pub keep_aliases_distinct: bool,
    #[serde(default)]
    pub book_voice: String,
}
impl Default for Policy {
    fn default() -> Self {
        Self {
            original_parenthetical: ParentheticalPolicy::FirstMention,
            lock_canonical: true,
            keep_aliases_distinct: true,
            book_voice: String::new(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum AliasTargets {
    Mapped(IndexMap<String, String>),
    Legacy(Vec<String>),
}
impl Default for AliasTargets {
    fn default() -> Self {
        Self::Legacy(Vec::new())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GlossaryEntry {
    pub id: String,
    #[serde(alias = "term")]
    pub source: String,
    #[serde(default, alias = "translation")]
    pub target: String,
    #[serde(default)]
    pub first_form: String,
    #[serde(default)]
    pub later_form: String,
    #[serde(default = "unknown")]
    pub category: String,
    #[serde(default, alias = "variants")]
    pub aliases: Vec<String>,
    #[serde(default)]
    pub alias_targets: AliasTargets,
    #[serde(default = "unknown")]
    pub gender: String,
    #[serde(default)]
    pub locked: bool,
    #[serde(default)]
    pub frequency: usize,
    #[serde(default)]
    pub first_block_id: String,
    #[serde(default)]
    pub notes: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pronunciation_note: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_url: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Voice {
    pub character: String,
    pub entry: String,
    pub register: String,
    pub persian_policy: String,
    pub speech_style: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Glossary {
    pub schema: String,
    pub policy: Policy,
    pub entries: Vec<GlossaryEntry>,
    pub voices: Vec<Voice>,
}
impl Default for Glossary {
    fn default() -> Self {
        Self {
            schema: SCHEMA.into(),
            policy: Policy::default(),
            entries: Vec::new(),
            voices: Vec::new(),
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("glossary I/O failed: {0}")]
    Io(#[from] io::Error),
    #[error("invalid glossary JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("unsupported glossary schema: {0}")]
    Schema(String),
    #[error("duplicate glossary ID: {0}")]
    DuplicateId(String),
    #[error("glossary ID space exhausted")]
    IdExhausted,
    #[error("glossary pattern failed: {0}")]
    Regex(#[from] regex::Error),
}

pub fn new_glossary() -> Glossary {
    Glossary::default()
}

pub fn load(path: &Path) -> Result<Glossary, Error> {
    let data = match fs::read(path) {
        Ok(data) => data,
        Err(e) if e.kind() == io::ErrorKind::NotFound && !path.exists() => {
            return Ok(new_glossary());
        }
        Err(e) => return Err(e.into()),
    };
    let glossary: Glossary = serde_json::from_slice(&data)?;
    if glossary.schema != SCHEMA {
        return Err(Error::Schema(glossary.schema));
    }
    Ok(glossary)
}

pub fn save(glossary: &Glossary, path: &Path) -> Result<(), Error> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let mut tmp = tempfile::NamedTempFile::new_in(parent)?;
    serde_json::to_writer_pretty(tmp.as_file_mut(), glossary)?;
    tmp.write_all(b"\n")?;
    tmp.as_file().sync_all()?;
    tmp.persist(path).map_err(|e| e.error)?;
    Ok(())
}

fn unknown() -> String {
    "unknown".into()
}

pub fn make_entry(
    index: usize,
    source: &str,
    category: &str,
    frequency: usize,
    aliases: impl IntoIterator<Item = String>,
) -> GlossaryEntry {
    GlossaryEntry {
        id: format!("g{index:04}"),
        source: source.into(),
        target: String::new(),
        first_form: String::new(),
        later_form: String::new(),
        category: category.into(),
        aliases: aliases
            .into_iter()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect(),
        alias_targets: AliasTargets::default(),
        gender: unknown(),
        locked: false,
        frequency,
        first_block_id: String::new(),
        notes: String::new(),
        pronunciation_note: None,
        source_url: None,
    }
}

pub fn allocate_ids(
    entries: &[GlossaryEntry],
    proposals: &mut [GlossaryEntry],
) -> Result<(), Error> {
    let mut used = HashSet::new();
    let mut highest = 0usize;
    for entry in entries {
        if !used.insert(entry.id.as_str()) {
            return Err(Error::DuplicateId(entry.id.clone()));
        }
        if let Some(num) = entry
            .id
            .strip_prefix('g')
            .and_then(|n| n.parse::<usize>().ok())
        {
            highest = highest.max(num);
        }
    }
    highest
        .checked_add(proposals.len())
        .ok_or(Error::IdExhausted)?;
    for (i, entry) in proposals.iter_mut().enumerate() {
        entry.id = format!("g{:04}", highest + i + 1);
    }
    Ok(())
}

pub fn surface_forms(entry: &GlossaryEntry) -> impl Iterator<Item = &str> {
    std::iter::once(entry.source.as_str())
        .chain(entry.aliases.iter().map(String::as_str))
        .filter(|s| !s.is_empty())
}
pub fn alias_map(entry: &GlossaryEntry) -> impl Iterator<Item = (&str, &str)> {
    let map = match &entry.alias_targets {
        AliasTargets::Mapped(map) => Some(map),
        AliasTargets::Legacy(_) => None,
    };
    map.into_iter()
        .flat_map(|map| map.iter())
        .filter(|(k, v)| !k.is_empty() && !v.is_empty())
        .map(|(k, v)| (k.as_str(), v.as_str()))
}
pub fn alias_accepted(entry: &GlossaryEntry) -> Vec<&str> {
    match &entry.alias_targets {
        AliasTargets::Mapped(map) => map
            .values()
            .map(String::as_str)
            .filter(|s| !s.is_empty())
            .collect(),
        AliasTargets::Legacy(values) => values
            .iter()
            .map(String::as_str)
            .filter(|s| !s.is_empty())
            .collect(),
    }
}
pub fn canonical(entry: &GlossaryEntry) -> &str {
    if entry.later_form.is_empty() {
        entry.target.trim()
    } else {
        entry.later_form.trim()
    }
}

fn key(text: &str) -> String {
    text.nfkd()
        .filter(|&c| !is_combining_mark(c))
        .flat_map(char::to_lowercase)
        .map(|c| match c {
            'ي' | 'ى' => 'ی',
            'ك' => 'ک',
            _ => c,
        })
        .collect()
}

pub fn lookup<'a>(glossary: &'a Glossary, term: &str) -> impl Iterator<Item = &'a GlossaryEntry> {
    let term = key(term);
    glossary
        .entries
        .iter()
        .filter(move |entry| !term.is_empty() && surface_forms(entry).any(|form| key(form) == term))
}

const WORD: &str = r"[A-Z][A-Za-z'’]*(?:-[A-Z][A-Za-z'’]*)*";
const STOPWORDS: &str = "a an and are as at be been but by for from had has have he her hers him his how i if in is it its me my no nor not of on or our ours she so than that the their theirs them then there these they this those to too us was we were what when where which who whom why will with you your yours after again against all also am among any because before being below between both did do does doing down during each few further here into itself more most other over same some such through under until up very while mr mrs miss ms dr sir madam lord lady oh ah yes well now just only even still chapter part book volume prologue epilogue contents preface introduction january february march april may june july august september october november december monday tuesday wednesday thursday friday saturday sunday";

fn stop(word: &str) -> bool {
    STOPWORDS
        .split_ascii_whitespace()
        .any(|stop| stop.eq_ignore_ascii_case(word))
}

#[derive(Default)]
struct Candidate {
    total: usize,
    middle: usize,
    contexts: Vec<String>,
    first: String,
}

pub fn scan<'a>(
    blocks: impl IntoIterator<Item = (&'a str, &'a str)>,
    minimum: usize,
    limit: usize,
) -> Result<Vec<GlossaryEntry>, Error> {
    static NAMES: LazyLock<Result<Regex, regex::Error>> = LazyLock::new(|| {
        Regex::new(&format!(
            r"\b({WORD}(?:\s+(?:of|de|van|von|der|den|la|le|du|the)\s+{WORD})?(?:\s+{WORD})*)\b"
        ))
    });
    let names = NAMES.as_ref().map_err(Clone::clone)?;
    let contractions = Regex::new(r"(?i)^[A-Za-z]{1,3}['’](?:m|s|d|t|ll|ve|re|em)$")?;
    let sentence = Regex::new(r#"(?:^|[.!?…]\s+|[«"'“]\s*)$"#)?;
    let mut candidates = IndexMap::<String, Candidate>::new();
    for (id, raw) in blocks {
        let text = plain_text(raw);
        for m in names.find_iter(&text) {
            let mut words: Vec<_> = m.as_str().split_whitespace().collect();
            if let Some(word) = words.last_mut() {
                for ending in ["'s", "’s", "s'", "s’"] {
                    if let Some(stem) = word.strip_suffix(ending) {
                        *word = stem;
                        break;
                    }
                }
            }
            let first = words
                .iter()
                .position(|word| !stop(word))
                .unwrap_or(words.len());
            let last = words
                .iter()
                .rposition(|word| !stop(word))
                .map_or(first, |i| i + 1);
            let name = words[first..last].join(" ");
            if name.chars().count() < 3 || contractions.is_match(&name) {
                continue;
            }
            let cand = candidates.entry(name.clone()).or_default();
            cand.total += 1;
            if cand.first.is_empty() {
                cand.first = id.into();
            }
            let before_byte = text[..m.start()]
                .char_indices()
                .rev()
                .nth(39)
                .map_or(0, |(i, _)| i);
            if !m.as_str().starts_with(&name) || !sentence.is_match(&text[before_byte..m.start()]) {
                cand.middle += 1;
            }
            if cand.contexts.len() < 5 {
                let start = text[..m.start()]
                    .char_indices()
                    .rev()
                    .nth(59)
                    .map_or(0, |(i, _)| i);
                let end = text[m.end()..]
                    .char_indices()
                    .nth(60)
                    .map_or(text.len(), |(i, _)| i + m.end());
                cand.contexts.push(text[start..end].to_owned());
            }
        }
    }
    candidates.retain(|_, c| c.total >= minimum && c.middle >= (c.total / 4).max(1));
    let mut multi: Vec<_> = candidates
        .keys()
        .filter(|name| name.contains(' '))
        .map(String::as_str)
        .collect();
    multi.sort_by_key(|name| std::cmp::Reverse(name.chars().count()));
    let mut form_aliases = IndexMap::<&str, Vec<&str>>::new();
    let mut absorbed = HashSet::new();
    for name in candidates
        .keys()
        .map(String::as_str)
        .filter(|name| !name.contains(' '))
    {
        if let Some(&long) = multi
            .iter()
            .find(|long| !super::glossary::standalone_spans(long, name).is_empty())
        {
            form_aliases.entry(long).or_default().push(name);
            absorbed.insert(name);
        }
    }
    let mut ordered: Vec<_> = candidates
        .iter()
        .filter(|(n, _)| !absorbed.contains(n.as_str()))
        .collect();
    ordered.sort_by(|(a, x), (b, y)| y.total.cmp(&x.total).then(a.cmp(b)));
    ordered.into_iter().take(limit).enumerate().map(|(index, (name, c))| {
        let aliases = form_aliases.get(name.as_str()).into_iter().flatten().copied();
        let mut seen = aliases.clone().filter_map(|alias| candidates.get(alias));
        let frequency = c.total + seen.clone().map(|c| c.total).sum::<usize>();
        let first = seen.by_ref().map(|c| c.first.as_str()).chain([c.first.as_str()]).min().unwrap_or_default();
        let lower = regex::escape(&name.to_lowercase());
        let contexts = c.contexts.join(" ").to_lowercase();
        let category = if Regex::new(&format!(r"\b(?:in|at|to|from|near|towards?)\s+{lower}\b"))?.is_match(&contexts) { "place" }
            else if Regex::new(&format!(r"{lower}\s+(?:said|asked|replied|whispered|cried)|\b(?:mr|mrs|miss|ms|dr|lord|lady|sir)\.?\s+{lower}"))?.is_match(&contexts) { "person" }
            else { "unknown" };
        let mut entry = make_entry(index + 1, name, category, frequency, aliases.map(str::to_owned));
        entry.first_block_id = first.into();
        Ok(entry)
    }).collect()
}

pub fn count_frequencies<'a>(glossary: &mut Glossary, sources: impl IntoIterator<Item = &'a str>) {
    let corpus = sources
        .into_iter()
        .map(plain_text)
        .collect::<Vec<_>>()
        .join("\n");
    for entry in &mut glossary.entries {
        entry.frequency = surface_forms(entry)
            .map(|form| super::glossary::standalone_spans(&corpus, form).len())
            .sum();
    }
}

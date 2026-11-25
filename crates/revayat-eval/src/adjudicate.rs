use serde::de::{self, Deserialize, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fmt,
    fs::File,
    io::{self, Read},
    path::Path,
};

const SCHEMA: &str = "revayat-novel/adjudication@1";
const LIMIT: usize = 1024 * 1024;
const PENDING_REASON: &str = "Qualified independent human evidence has not been supplied";

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("{0}")]
    Invalid(String),
    #[error("could not read review or benchmark: {0}")]
    Io(#[from] io::Error),
    #[error("invalid JSON: {0}")]
    Json(#[from] serde_json::Error),
}

fn invalid(reason: impl Into<String>) -> Error {
    Error::Invalid(reason.into())
}

struct Strict(Value);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: de::Deserializer<'de>>(input: D) -> Result<Self, D::Error> {
        input.deserialize_any(StrictVisitor).map(Self)
    }
}
struct StrictVisitor;
impl<'de> Visitor<'de> for StrictVisitor {
    type Value = Value;
    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("a finite JSON value")
    }
    fn visit_unit<E: de::Error>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }
    fn visit_bool<E: de::Error>(self, value: bool) -> Result<Value, E> {
        Ok(Value::Bool(value))
    }
    fn visit_f64<E: de::Error>(self, value: f64) -> Result<Value, E> {
        serde_json::Number::from_f64(value)
            .map(Value::Number)
            .ok_or_else(|| E::custom("Nonfinite review value"))
    }
    fn visit_i64<E: de::Error>(self, value: i64) -> Result<Value, E> {
        Ok(json!(value))
    }
    fn visit_u64<E: de::Error>(self, value: u64) -> Result<Value, E> {
        Ok(json!(value))
    }
    fn visit_str<E: de::Error>(self, value: &str) -> Result<Value, E> {
        Ok(json!(value))
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
        let mut values = Vec::new();
        while let Some(Strict(value)) = seq.next_element()? {
            values.push(value);
        }
        Ok(Value::Array(values))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
        let mut values = Map::new();
        while let Some((key, Strict(value))) = map.next_entry::<String, Strict>()? {
            if values.insert(key, value).is_some() {
                return Err(de::Error::custom("Duplicate review JSON key"));
            }
        }
        Ok(Value::Object(values))
    }
}

pub fn parse_object(bytes: &[u8]) -> Result<Value, Error> {
    if bytes.len() > LIMIT {
        return Err(invalid("Review JSON exceeds 1 MiB"));
    }
    let Strict(value) = serde_json::from_slice(bytes).map_err(|e| {
        if e.to_string().starts_with("Duplicate review JSON key") {
            invalid("Duplicate review JSON key")
        } else {
            Error::Json(e)
        }
    })?;
    if !value.is_object() {
        return Err(invalid("Review must be an object"));
    }
    Ok(value)
}

pub fn object_file(path: &Path) -> Result<Value, Error> {
    Ok(read_object(Some(path))?.0)
}

fn read_object(path: Option<&Path>) -> Result<(Value, String), Error> {
    let mut file_bytes = Vec::new();
    let bytes = if let Some(path) = path {
        File::open(path)?
            .take(LIMIT as u64 + 1)
            .read_to_end(&mut file_bytes)?;
        file_bytes.as_slice()
    } else {
        super::CASES.as_bytes()
    };
    let value = parse_object(bytes)?;
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut digest = String::with_capacity(64);
    for byte in Sha256::digest(bytes) {
        digest.push(char::from(HEX[usize::from(byte >> 4)]));
        digest.push(char::from(HEX[usize::from(byte & 15)]));
    }
    Ok((value, digest))
}

fn text<'a>(value: &'a Value, label: &str, limit: usize) -> Result<&'a str, Error> {
    value
        .as_str()
        .filter(|s| {
            !s.trim_matches(|c: char| c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}'))
                .is_empty()
                && s.chars().count() <= limit
                && !s.contains('\0')
        })
        .ok_or_else(|| invalid(format!("Invalid {label}")))
}

fn source_language(case: &Value) -> &Value {
    static EN: std::sync::LazyLock<Value> = std::sync::LazyLock::new(|| json!("en"));
    case.get("source_language").unwrap_or(&EN)
}

fn controls<'a>(case: &'a Value, key: &str, reason: &str) -> Result<&'a [Value], Error> {
    case[key]
        .as_array()
        .filter(|a| !a.is_empty())
        .map(Vec::as_slice)
        .ok_or_else(|| invalid(reason))
}

fn benchmark(path: Option<&Path>) -> Result<(Vec<Value>, String), Error> {
    let (mut value, digest) = read_object(path)?;
    if value["schema"] != "revayat-novel/benchmark@1" {
        return Err(invalid("Invalid benchmark cases"));
    }
    let Value::Array(cases) = value["cases"].take() else {
        return Err(invalid("Invalid benchmark cases"));
    };
    if !(1..=1000).contains(&cases.len()) || cases.iter().any(|c| !c.is_object()) {
        return Err(invalid("Invalid benchmark cases"));
    }
    let mut ids = HashSet::new();
    for case in &cases {
        let id = case["id"].as_str().filter(|s| {
            (1..=80).contains(&s.len())
                && s.bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
        });
        if !id.is_some_and(|id| ids.insert(id)) {
            return Err(invalid("Invalid benchmark case identities"));
        }
    }
    for case in &cases {
        for field in ["axis", "source", "rationale"] {
            text(&case[field], &format!("benchmark {field}"), 10000)?;
        }
        text(source_language(case), "benchmark language", 20)?;
        for accepted in controls(case, "accepts", "Benchmark requires accepted controls")? {
            text(accepted, "accepted control", 10000)?;
        }
        for wrong in controls(case, "fails", "Benchmark requires wrong controls")? {
            if !wrong.is_object() {
                return Err(invalid("Invalid wrong control"));
            }
            text(&wrong["text"], "wrong control", 10000)?;
            text(&wrong["why"], "wrong rationale", 10000)?;
        }
    }
    Ok((cases, digest))
}

fn count(case: &Value, key: &str) -> usize {
    case[key].as_array().map_or(0, Vec::len)
}

pub fn prepare(path: Option<&Path>) -> Result<Value, Error> {
    let (cases, digest) = benchmark(path)?;
    let cases: Vec<_> = cases.iter().map(|c| json!({
        "id": c["id"], "source_language": source_language(c), "axis": c["axis"], "rubric": c["rationale"],
        "accepted_controls": count(c, "accepts"), "wrong_controls": count(c, "fails"),
        "status": "pending", "reviews": [], "pending_reason": PENDING_REASON
    })).collect();
    Ok(json!({"schema": SCHEMA, "benchmark_sha256": digest, "cases": cases}))
}

fn exact(value: &Value, fields: &[&str]) -> bool {
    value
        .as_object()
        .is_some_and(|map| map.len() == fields.len() && fields.iter().all(|k| map.contains_key(*k)))
}

fn utc_seconds(s: &str) -> Result<(), Error> {
    const MASK: &[u8] = b"####-##-##T##:##:##Z";
    if s.len() != MASK.len()
        || !s.bytes().zip(MASK).all(|(v, &p)| {
            if p == b'#' {
                v.is_ascii_digit()
            } else {
                v == p
            }
        })
    {
        return Err(invalid("Review time must be UTC ISO8601 seconds"));
    }
    let part = |i, j| {
        s.get(i..j)
            .and_then(|part| part.parse::<u32>().ok())
            .unwrap_or(0)
    };
    let year = part(0, 4);
    let days = match part(5, 7) {
        4 | 6 | 9 | 11 => 30,
        2 if year.is_multiple_of(4) && (!year.is_multiple_of(100) || year.is_multiple_of(400)) => {
            29
        }
        2 => 28,
        1..=12 => 31,
        _ => 0,
    };
    if year == 0
        || !(1..=days).contains(&part(8, 10))
        || part(11, 13) > 23
        || part(14, 16) > 59
        || part(17, 19) > 59
    {
        return Err(invalid("Invalid review time"));
    }
    Ok(())
}

fn status(row: &Value) -> Result<&'static str, Error> {
    const FIELDS: &[&str] = &[
        "reviewer_id",
        "qualification",
        "independent",
        "source_language",
        "reviewed_utc",
        "evidence_ref",
        "evidence_sha256",
        "verdict",
        "notes",
    ];
    let reviews = row["reviews"]
        .as_array()
        .filter(|a| a.len() <= 10)
        .ok_or_else(|| invalid("Invalid reviewer evidence list"))?;
    let mut seen = HashSet::new();
    let mut verdicts = HashSet::new();
    for entry in reviews {
        if !exact(entry, FIELDS) {
            return Err(invalid("Missing reviewer metadata"));
        }
        if !seen.insert(text(&entry["reviewer_id"], "reviewer identity", 120)?)
            || entry["independent"] != true
        {
            return Err(invalid("Duplicate or nonindependent reviewer"));
        }
        for key in ["qualification", "evidence_ref", "notes"] {
            text(&entry[key], key, 2000)?;
        }
        if entry["source_language"] != row["source_language"] {
            return Err(invalid("Reviewer source language differs"));
        }
        utc_seconds(text(&entry["reviewed_utc"], "review time", 20)?)?;
        let digest = text(&entry["evidence_sha256"], "evidence digest", 64)?;
        if digest.len() != 64
            || !digest
                .bytes()
                .all(|c| matches!(c, b'a'..=b'f' | b'0'..=b'9'))
        {
            return Err(invalid("Invalid evidence digest"));
        }
        match entry["verdict"].as_str() {
            Some(v @ ("accept" | "reject" | "unknown")) => {
                verdicts.insert(v);
            }
            _ => return Err(invalid("Invalid independent verdict")),
        }
    }
    Ok(
        match (
            verdicts.contains("accept"),
            verdicts.contains("reject"),
            verdicts.len(),
        ) {
            (true, true, _) => "disputed",
            (true, false, 1) => "accept",
            (false, true, 1) => "reject",
            _ => "pending",
        },
    )
}

pub fn validate(review: &Value, path: Option<&Path>) -> Result<(), Error> {
    const FIELDS: &[&str] = &[
        "id",
        "source_language",
        "axis",
        "rubric",
        "accepted_controls",
        "wrong_controls",
        "status",
        "reviews",
        "pending_reason",
    ];
    let (cases, digest) = benchmark(path)?;
    if !exact(review, &["schema", "benchmark_sha256", "cases"])
        || review["schema"] != SCHEMA
        || review["benchmark_sha256"] != digest
    {
        return Err(invalid("Review benchmark identity differs"));
    }
    let rows = review["cases"]
        .as_array()
        .filter(|a| a.len() == cases.len() && a.iter().all(Value::is_object))
        .ok_or_else(|| invalid("Review must cover every case exactly once"))?;
    if rows.iter().zip(&cases).any(|(r, c)| r["id"] != c["id"]) {
        return Err(invalid(
            "Missing, duplicate, foreign or reordered review case",
        ));
    }
    for (row, case) in rows.iter().zip(&cases) {
        if !exact(row, FIELDS) {
            return Err(invalid("Invalid review case fields"));
        }
        if row["source_language"] != *source_language(case) || row["axis"] != case["axis"] {
            return Err(invalid("Review case language or axis differs"));
        }
        if row["rubric"] != case["rationale"]
            || row["accepted_controls"].as_u64() != Some(count(case, "accepts") as u64)
            || row["wrong_controls"].as_u64() != Some(count(case, "fails") as u64)
        {
            return Err(invalid("Review rubric or control enumeration differs"));
        }
        if !matches!(
            row["status"].as_str(),
            Some("pending" | "accept" | "reject" | "disputed")
        ) {
            return Err(invalid("Invalid review status"));
        }
        let expected = status(row)?;
        if row["status"] != expected {
            return Err(invalid("Case status does not follow independent evidence"));
        }
        if expected == "pending" {
            text(&row["pending_reason"], "pending reason", 2000)?;
        } else if !row["pending_reason"].is_null() {
            return Err(invalid("Settled case must not claim pending evidence"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::utc_seconds;

    #[test]
    fn verifies_utc_calendar_dates() {
        assert!(utc_seconds("2000-02-29T23:59:59Z").is_ok());
        assert!(utc_seconds("1900-02-29T23:59:59Z").is_err());
    }
}

//! Standalone Persian text mechanics.
#![forbid(unsafe_code)]

use fancy_regex::{Captures, Regex, Replacer};
use indexmap::IndexMap;

pub type Stats = IndexMap<&'static str, usize>;
use std::sync::LazyLock;

const PL: &str = "ء-يٮ-ەۺ-ۼۿ";
const PLD: &str = "ء-يٮ-ەۺ-ۼۿ\u{064b}-\u{0652}\u{0670}";
const AR: &str = "ء-يٮ-ەۺ-ۼۿ۰-۹٠-٩‌";
const ZWNJ: char = '\u{200c}';
const HEH_STOPWORDS: &[&str] = &[
    "که",
    "چه",
    "نه",
    "به",
    "سه",
    "بله",
    "آنچه",
    "چنانچه",
    "اگرچه",
    "گرچه",
];

macro_rules! rx {
    ($pattern:expr) => {{
        static RX: LazyLock<Result<Regex, fancy_regex::Error>> =
            LazyLock::new(|| Regex::new(&$pattern));
        RX.as_ref().map_err(|e| Error(e.clone()))?
    }};
}

fn is_fa(c: char) -> bool {
    matches!(c, 'ء'..='ي' | 'ٮ'..='ە' | 'ۺ'..='ۼ' | 'ۿ')
}

fn fa_line(s: &str) -> bool {
    let (fa, latin) = s.chars().fold((0, 0), |(fa, la), c| {
        (
            fa + usize::from(is_fa(c)),
            la + usize::from(c.is_ascii_alphabetic()),
        )
    });
    fa > latin
}

fn fa_context(c: char) -> bool {
    is_fa(c) || matches!(c, '۰'..='۹' | '٠'..='٩' | ZWNJ)
}

fn mask(text: &str) -> Result<(String, Vec<&str>), Error> {
    let protected = rx!(
        r"(?sm)```.*?```|`[^`\n]*`|^(?: {4,}|\t)[^\s\x1c-\x1f][^\r\n]*$|(?:https?://|www\.)[^\s\x1c-\x1f]+|[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}|\bv\d+(?:\.\d+)+\b|\b\d+(?:\.\d+){2,}\b"
    );
    let mut spans = Vec::new();
    let mut out = String::new();
    let mut end = 0;
    for found in protected.find_iter(text) {
        let found = found?;
        out.push_str(&text[end..found.start()]);
        out.push('\0');
        spans.push(found.as_str());
        end = found.end();
    }
    out.push_str(&text[end..]);
    Ok((out, spans))
}

fn restore(text: &str, spans: Vec<&str>) -> String {
    let mut parts = text.split('\0');
    let mut out = parts.next().unwrap_or_default().to_owned();
    let mut spans = spans.into_iter();
    for part in parts {
        out.push_str(spans.next().unwrap_or_default());
        out.push_str(part);
    }
    out
}

fn bump(stats: &mut Stats, key: &'static str, n: usize) {
    if n != 0 {
        *stats.entry(key).or_default() += n;
    }
}

fn replace(
    text: &mut String,
    stats: &mut Stats,
    key: &'static str,
    pat: &Regex,
    rep: impl Replacer,
) -> Result<(), Error> {
    let mut count = 0;
    let mut rep = rep;
    let output = pat.try_replacen(text, 0, |c: &Captures<'_, str>| {
        count += 1;
        let mut next = String::new();
        rep.replace_append(c, &mut next);
        next
    })?;
    if count != 0 {
        *text = output.into_owned();
        bump(stats, key, count);
    }
    Ok(())
}

fn digit(c: char, from: char, to: char) -> char {
    char::from_u32(u32::from(c) - u32::from(from) + u32::from(to)).unwrap_or(c)
}

fn latin_digits(
    line: &mut String,
    doc_fa: bool,
    line_fa: bool,
    stats: &mut Stats,
) -> Result<(), Error> {
    if !(line_fa || doc_fa && !line.bytes().any(|b| b.is_ascii_alphabetic())) {
        return Ok(());
    }
    let head = rx!(r"^[\s\x1c-\x1f]*\d+[.)][\s\x1c-\x1f]")
        .find(line)?
        .map_or(0, |m| m.end());
    let mut out = String::from(&line[..head]);
    let rest = &line[head..];
    let mut end = 0;
    let mut count = 0;
    for token in rx!(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*").find_iter(rest) {
        let token = token?;
        out.push_str(&rest[end..token.start()]);
        if token.as_str().bytes().any(|b| b.is_ascii_alphabetic()) {
            out.push_str(token.as_str());
        } else {
            out.extend(token.as_str().chars().map(|c| {
                if c.is_ascii_digit() {
                    count += 1;
                    digit(c, '0', '۰')
                } else {
                    c
                }
            }));
        }
        end = token.end();
    }
    out.push_str(&rest[end..]);
    *line = out;
    bump(stats, "digits_to_persian", count);
    Ok(())
}

fn punctuation(line: &mut String, from: char, to: char, fa: bool, keep_number: bool) -> usize {
    let mut out = String::with_capacity(line.len());
    let mut left = None;
    let mut count = 0;
    for (at, c) in line.char_indices() {
        let right = &line[at + c.len_utf8()..];
        let is_digit = |c: char| c.is_ascii_digit() || matches!(c, '۰'..='۹');
        if c == from
            && !left.is_some_and(|c: char| c.is_ascii_alphabetic())
            && (fa
                || left.is_some_and(fa_context)
                    && right
                        .trim_start_matches([' ', '\t'])
                        .starts_with(fa_context))
            && !(keep_number && left.is_some_and(is_digit) && right.starts_with(is_digit))
        {
            count += 1;
            out.push(to);
        } else {
            out.push(c);
        }
        left = Some(c);
    }
    *line = out;
    count
}

fn conditional(
    text: &mut String,
    stats: &mut Stats,
    key: &'static str,
    regex: &Regex,
    mut replacement: impl FnMut(&Captures<'_, str>) -> Option<String>,
) -> Result<(), Error> {
    let mut n = 0;
    let updated = regex.try_replacen(text, 0, |caps: &Captures<'_, str>| {
        if let Some(s) = replacement(caps) {
            n += 1;
            s
        } else {
            caps[0].to_owned()
        }
    })?;
    if n != 0 {
        *text = updated.into_owned();
        bump(stats, key, n);
    }
    Ok(())
}

pub fn normalize(text: &str, digits: Digits) -> Result<(String, Stats), Error> {
    let (mut masked, spans) = mask(text)?;
    let mut stats = Stats::new();
    for (a, b) in [('ي', "ی"), ('ى', "ی"), ('ك', "ک"), ('ة', "ه")] {
        let n = masked.matches(a).count();
        if n != 0 {
            masked = masked.replace(a, b);
            bump(&mut stats, "arabic_to_persian_letters", n);
        }
    }
    replace(
        &mut masked,
        &mut stats,
        "kashida_dash_to_en_dash",
        rx!(r"(?m)(?:(?<=[\s\x1c-\x1f])|^)ـ+(?=[\s\x1c-\x1f]|$)"),
        "\u{2013}",
    )?;
    replace(&mut masked, &mut stats, "kashida_removed", rx!("ـ"), "")?;
    let doc_fa = fa_line(&masked);
    if digits == Digits::Persian {
        replace(
            &mut masked,
            &mut stats,
            "digits_to_persian",
            rx!("[٠-٩]"),
            |c: &Captures<'_, str>| c[0].chars().map(|c| digit(c, '٠', '۰')).collect::<String>(),
        )?;
    } else if digits == Digits::Latin {
        replace(
            &mut masked,
            &mut stats,
            "digits_to_latin",
            rx!("[۰-۹٠-٩]"),
            |c: &Captures<'_, str>| {
                c[0].chars()
                    .map(|c| digit(c, if c >= '۰' { '۰' } else { '٠' }, '0'))
                    .collect::<String>()
            },
        )?;
    }
    let mut lines = Vec::new();
    for line in masked.split('\n') {
        let fa = fa_line(line);
        let mut line = line.to_owned();
        if digits == Digits::Persian {
            latin_digits(&mut line, doc_fa, fa, &mut stats)?;
        }
        replace(
            &mut line,
            &mut stats,
            "percent_to_persian",
            rx!(r"(?<=[۰-۹])%"),
            "٪",
        )?;
        for (from, to, key) in [
            (',', '،', "comma_to_persian"),
            (';', '؛', "semicolon_to_persian"),
            ('?', '؟', "question_mark_to_persian"),
        ] {
            bump(
                &mut stats,
                key,
                punctuation(&mut line, from, to, fa, from == ','),
            );
        }
        conditional(
            &mut line,
            &mut stats,
            "quotes_to_guillemets",
            rx!(r#""([^"\n]*)"|“([^“”\n]*)”"#),
            |c| {
                let inner = c.get(1).or_else(|| c.get(2))?.as_str();
                (fa_line(inner) || fa && !inner.bytes().any(|b| b.is_ascii_alphabetic()))
                    .then(|| format!("«{inner}»"))
            },
        )?;
        if fa {
            replace(
                &mut line,
                &mut stats,
                "space_before_punct",
                rx!(r"(?<=[^\s\x1c-\x1f])[ \t]+([.!:،؛؟»])"),
                "$1",
            )?;
            replace(
                &mut line,
                &mut stats,
                "space_after_punct",
                rx!(r"(!)(?=[^\s\x1c-\x1f»)\]\x00،؛؟!.…])"),
                "$1 ",
            )?;
        }
        lines.push(line);
    }
    masked = lines.join("\n");
    replace(
        &mut masked,
        &mut stats,
        "ellipsis",
        rx!(format!(r"(?<=[{PL}‌])\.{{3,}}")),
        "…",
    )?;
    replace(
        &mut masked,
        &mut stats,
        "zwnj_mi_prefix",
        rx!(format!(r"(?<![{PL}‌])(نمی|می)[ \t]+(?=[{PL}])")),
        "${1}‌",
    )?;
    replace(
        &mut masked,
        &mut stats,
        "zwnj_plural_ha",
        rx!(format!(
            r"(?<=[{PLD}])[ \t]+(هایشان|هایتان|هایمان|هایی|هایم|هایت|هایش|های|ها)(?![{PL}])(?! و هوی)"
        )),
        "‌${1}",
    )?;
    replace(
        &mut masked,
        &mut stats,
        "zwnj_superlative",
        rx!(format!(r"(?<=[{PLD}])[ \t]+(ترین)(?![{PL}])")),
        "‌${1}",
    )?;
    conditional(
        &mut masked,
        &mut stats,
        "zwnj_heh_enclitic",
        rx!(format!(
            r"([{PL}]*ه)[ \t]+(اند|ایم|اید|ام|ات|اش|ای)(?![{PL}])"
        )),
        |c| {
            (!HEH_STOPWORDS.contains(&&c[1]) && c[1].chars().count() >= 2)
                .then(|| format!("{}‌{}", &c[1], &c[2]))
        },
    )?;
    replace(
        &mut masked,
        &mut stats,
        "space_before_punct",
        rx!(format!(r"(?<=[{AR}])[ \t]+([،؛؟»])")),
        "$1",
    )?;
    replace(
        &mut masked,
        &mut stats,
        "space_after_openquote",
        rx!(r"(«)[ \t]+"),
        "$1",
    )?;
    replace(
        &mut masked,
        &mut stats,
        "space_after_punct",
        rx!(r"([،؛؟])(?=[^\s\x1c-\x1f»)\]\x00،؛؟!.…])"),
        "$1 ",
    )?;
    replace(
        &mut masked,
        &mut stats,
        "collapsed_spaces",
        rx!(r"(?<=[^\s\x1c-\x1f])[ \t]{2,}(?=[^\s\x1c-\x1f])"),
        " ",
    )?;
    replace(
        &mut masked,
        &mut stats,
        "trailing_space",
        rx!(r"[ \t]+(\n|$)"),
        "$1",
    )?;
    Ok((restore(&masked, spans), stats))
}

const MI_WORDS: &str = "میز میزان میزبان میان میانه میانگین میانبر میدان میوه میهن میهمان میمون میل میلاد میلادی میلیون میلیارد میله میخ میت میثاق میراث میکروب میکرو مینا مینو میش میگو میکده میکس میثم میرزا میر میگرن میناکاری میخانه میخک میدانی میلی میکروفون میکروسکوپ میکروفر میلیمتر میلی‌متر میانی میدانها میوه‌ها میزها میلها میخها میسر میکائیل میناب میامی میگل میلان میکل میکی میترا میثاق‌نامه";
const STYLE: &[(&str, &str)] = &[
    (r"می‌باشد|می‌باشند|میباشد", "«می‌باشد» -> «است / هستند»"),
    (
        r"می‌گردد|می‌گردند",
        "«می‌گردد» as a passive helper -> «می‌شود» or an active verb",
    ),
    (
        r"مورد\s+\S+\s+قرار\s+(?:می‌گیرد|گرفت|گرفته|داد|می‌دهد)",
        "«مورد … قرار گرفتن» -> plain verb (استفاده می‌شود، بررسی کردند)",
    ),
    (r"\bدارای\b", "«دارای X است» -> «X دارد»"),
    (
        r"\bتوسط\b",
        "«توسط» agent passive -> active sentence (او نوشت)",
    ),
    (
        r"\bبه عنوان\b|\bبه‌عنوان\b",
        "«به عنوان» is often a calque of English 'as'; try dropping it",
    ),
    (
        r"\bبه منظور\b|\bبه‌منظور\b|\bدر راستای\b|\bدر جهت\b",
        "bureaucratic connector -> «برای»",
    ),
    (
        r"لازم به ذکر است|شایان ذکر است|قابل ذکر است|قابل توجه است که",
        "announcement filler; delete it and state the point",
    ),
    (
        r"نه تنها .{0,60}? بلکه",
        "«نه تنها … بلکه» is over-used by models; try a plain sentence",
    ),
    (
        r"\bدر نهایت\b|\bدر پایان\b|\bبه طور کلی\b|\bدر مجموع\b",
        "summary connector; usually delete",
    ),
    (
        r"\bاین امر\b|\bاین موضوع\b|\bاین مسئله\b",
        "abstract subject; name the concrete thing",
    ),
    (
        r"\bایفا می‌کند\b|\bنقش (?:مهم|کلیدی|حیاتی)",
        "«نقش مهمی ایفا می‌کند» is the Persian 'plays a crucial role'; say what it does",
    ),
    (
        r"\bیک\s+\S+ی?\s+است\b",
        "«یک X است» often calques English 'a'; Persian usually says «Xی است» or just «X است»",
    ),
];

fn suggest_text(text: &str, style: bool) -> Result<Vec<Suggestion>, Error> {
    let (masked, _) = mask(text)?;
    let context = |m: fancy_regex::Match<'_>, left: usize, right: usize| {
        let start = masked[..m.start()]
            .char_indices()
            .rev()
            .nth(left - 1)
            .map_or(0, |(i, _)| i);
        let end = masked[m.end()..]
            .char_indices()
            .nth(right)
            .map_or(masked.len(), |(i, _)| i + m.end());
        &masked[start..end]
    };
    let mut out = Vec::new();
    for m in rx!(format!(r"(?<=[{PLD}])[ \t]+تر(?![{PL}])")).find_iter(&masked) {
        let m = m?;
        out.push(Suggestion {
            kind: "maybe_comparative_tar",
            hint: "space+«تر»: join as «‌تر» if comparative; leave if it means 'wet'.",
            context: context(m, 12, 3).to_owned(),
        });
    }
    for m in rx!(format!(r"(?<![{PL}‌])(?:ن)?می[{PL}]+")).find_iter(&masked) {
        let m = m?;
        let word = m.as_str();
        let stem = word.strip_prefix('ن').unwrap_or(word);
        if MI_WORDS
            .split(' ')
            .any(|s| s == stem || s == stem.trim_end_matches('ی'))
        {
            continue;
        }
        out.push(Suggestion {
            kind: "maybe_glued_mi",
            hint: "«می» glued to verb: insert ZWNJ (می‌…); not auto-fixed (cf. «میمون», «میز»).",
            context: context(m, 3, 3).to_owned(),
        });
    }
    for m in rx!("\u{2014}").find_iter(&masked) {
        let m = m?;
        out.push(Suggestion { kind: "em_dash", hint: "em dash: Persian prose rarely uses it; restructure, or use a spaced en dash (\u{2013}) if a dash is really needed.", context: context(m, 12, 12).replace('\n', " ").to_owned() });
    }
    if style {
        static STYLES: LazyLock<Result<Vec<Regex>, fancy_regex::Error>> =
            LazyLock::new(|| STYLE.iter().map(|(pat, _)| Regex::new(pat)).collect());
        for (pat, (_, hint)) in STYLES
            .as_ref()
            .map_err(|e| Error(e.clone()))?
            .iter()
            .zip(STYLE)
        {
            for m in pat.find_iter(&masked) {
                let m = m?;
                out.push(Suggestion {
                    kind: "style",
                    hint,
                    context: context(m, 15, 15).replace('\n', " ").to_owned(),
                });
            }
        }
    }
    Ok(out)
}

pub fn suggestions(text: &str, style: bool) -> Result<Vec<Suggestion>, Error> {
    suggest_text(text, style)
}

pub fn line_changes<'a>(original: &'a str, fixed: &'a str) -> Vec<LineChange<'a>> {
    original
        .split('\n')
        .zip(fixed.split('\n'))
        .enumerate()
        .filter(|(_, (before, after))| before != after)
        .map(|(i, (before, after))| LineChange {
            line: i + 1,
            before,
            after,
        })
        .collect()
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum Digits {
    #[default]
    Persian,
    Latin,
    Keep,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct LineChange<'a> {
    pub line: usize,
    pub before: &'a str,
    pub after: &'a str,
}

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct Suggestion {
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub hint: &'static str,
    pub context: String,
}

#[derive(Debug, thiserror::Error)]
#[error("Persian mechanics pattern failed: {0}")]
pub struct Error(#[from] fancy_regex::Error);

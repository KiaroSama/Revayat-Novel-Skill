use std::{collections::BTreeMap, fs, thread};

use proptest::prelude::*;
use revayat_core::*;

fn document(text: String) -> BookDocument {
    let mut book = BookDocument::new(SourceFormat::Text, sha256_bytes(b"source"), "Book", "en");
    book.chapters.push(Chapter {
        id: "chapter-1".into(),
        title: "Chapter".into(),
        blocks: vec![Block::Paragraph {
            id: "p1".into(),
            runs: vec![TextRun {
                text,
                bold: true,
                italic: true,
                underline: true,
                strikethrough: true,
                superscript: true,
                hyperlink: Some("https://example.com/".into()),
                lang: Some("fa-IR".into()),
                ..TextRun::default()
            }],
            style: None,
            align: None,
            rtl: true,
        }],
        ..Chapter::default()
    });
    book
}

#[test]
fn invalid_transaction_preserves_bytes_and_releases_lock() -> Result<(), Box<dyn std::error::Error>>
{
    let dir = tempfile::tempdir()?;
    let path = dir.path().join(BOOK_FILENAME);
    save_book(&path, &document("original".into()))?;
    let prior = fs::read(&path)?;
    let failure = update_book(&path, |book| {
        book.chapters.push(book.chapters[0].clone());
        Ok(())
    });
    assert!(matches!(failure, Err(CoreError::InvalidBook(_))));
    assert_eq!(fs::read(&path)?, prior);
    let failure: Result<(), _> = update_book(&path, |book| {
        book.title = "uncommitted".into();
        Err(CoreError::InvalidBook("abort".into()))
    });
    assert!(failure.is_err());
    assert_eq!(fs::read(&path)?, prior);
    update_book(&path, |book| {
        book.title = "committed".into();
        Ok(())
    })?;
    assert_eq!(load_book(&path)?.title, "committed");
    assert_eq!(fs::read_dir(dir.path())?.count(), 2);
    Ok(())
}

#[test]
fn separate_file_handles_contend_for_the_same_os_lock() -> Result<(), Box<dyn std::error::Error>> {
    let dir = tempfile::tempdir()?;
    let path = dir.path().join(BOOK_FILENAME);
    let guard = BookLock::acquire(&path)?;
    let other = path.clone();
    let result = thread::spawn(move || BookLock::acquire(&other)).join();
    assert!(matches!(result, Ok(Err(CoreError::LockBusy(_)))));
    drop(guard);
    BookLock::acquire(&path)?;
    Ok(())
}

#[test]
fn graph_rejects_orphan_and_missing_notes() {
    let mut book = document("Body".into());
    book.footnotes.push(Footnote {
        id: "fn0001".into(),
        number: 1,
        ..Footnote::default()
    });
    let Block::Paragraph { runs, .. } = &mut book.chapters[0].blocks[0] else {
        unreachable!();
    };
    runs[0].footnote_ref = Some("fn0002".into());
    let problems = check_note_graph(&book);
    assert!(
        problems
            .iter()
            .any(|finding| finding.code == "orphan-footnote")
    );
    assert!(
        problems
            .iter()
            .any(|finding| finding.code == "missing-footnote")
    );
    assert!(book.validate().is_err());
}

#[test]
fn markup_preserves_formatted_footnotes_and_escaped_literals() {
    let input = r"***a\*[[fn:fn0007]]b*** `[[fn:fn9999]]` \[[fn:fn8888]]";
    let spans = parse_markup(input);
    assert_eq!(render_spans(&spans), input);
    assert_eq!(footnote_refs(input), ["fn0007"]);
    let refs = BTreeMap::from([("fn0007".into(), "fn0012".into())]);
    assert_eq!(
        footnote_refs(&rewrite_footnote_refs(input, &refs)),
        ["fn0012"]
    );
}

#[test]
fn naming_and_segment_edges() {
    assert!(!is_safe_filename("CON.txt"));
    assert!(!is_safe_filename("../book"));
    assert!(!is_safe_filename("Book. "));
    assert_eq!(safe_filename("CON.txt"), "_CON.txt");
    assert_eq!(segment_id("p0003", 2), "p0003#2");
    assert_eq!(base_of("p0003#2"), "p0003");
    assert_eq!(index_of("p0003#2"), 2);
    assert_eq!(index_of("p0003#2.x"), 0);
}

#[test]
fn report_limits_display_without_changing_verdict() {
    let mut report = Report::default();
    report.add(Severity::Warning, "advice", "a", "review this");
    report.add(Severity::Error, "broken", "b", "bad content");
    report.count("units", 7);
    let summary = report.summary(Some(1));
    assert!(!summary.ok);
    assert_eq!(
        (summary.errors, summary.warnings, summary.truncated),
        (1, 1, 1)
    );
    assert_eq!(summary.findings[0].code, "broken");
    assert_eq!(summary.counts["units"], 7);
}

#[test]
fn bidi_recognizes_unicode_ltr_and_preserves_persian_joiners() {
    let mixed = " 42: 漢字 فارسی\u{200c}متن";
    let isolated = isolate_persian_line(mixed);
    assert!(isolated.starts_with([RLM, RLI]));
    assert!(isolated.ends_with(PDI));
    assert_eq!(strip_bidi_controls(&isolated), mixed);
    assert!(check_bidi_isolation(&isolated).is_empty());
    assert!(!check_bidi_isolation(mixed).is_empty());
    assert_eq!(
        isolate_persian_line("123, English 漢字"),
        "123, English 漢字"
    );
    assert_eq!(isolate_persian_line("`print('فارسی')`"), "`print('فارسی')`");
}

proptest! {
    #[test]
    fn book_json_round_trips(text in any::<String>()) {
        let book = document(text);
        let encoded = book.to_json()?;
        prop_assert_eq!(BookDocument::from_json(&encoded)?, book);
    }

    #[test]
    fn plain_markup_escaping_round_trips(text in any::<String>()) {
        let text = format!("{text} * ** ` \\ [[fn:fn0007]]");
        let escaped = escape_markup(&text);
        prop_assert!(footnote_refs(&escaped).is_empty());
        prop_assert_eq!(plain_text(&escaped), text);
    }

    #[test]
    fn bidi_isolation_is_idempotent(prefix in "[a-z]{1,10}", suffix in "[a-z0-9 ]{0,12}") {
        let mixed = format!("  🧪 {prefix} 漢字 فارسی {suffix}");
        let isolated = isolate_persian_line(&mixed);
        prop_assert!(isolated.starts_with([RLM, RLI]));
        prop_assert!(isolated.ends_with(PDI));
        prop_assert_eq!(isolate_persian_line(&isolated), isolated.clone());
        prop_assert_eq!(strip_bidi_controls(&isolated), mixed);
    }
}

use revayat_core::{Block, BookDocument, Chapter, SourceFormat, TableCell, TableRow, TextRun};
use revayat_persian::falint::{Options, fix_book, lint_book};

#[test]
fn book_typography_visits_cells_and_keeps_run_styles_and_links()
-> Result<(), Box<dyn std::error::Error>> {
    let mut book = BookDocument::new(SourceFormat::Text, "0".repeat(64), "خانه", "fa");
    book.chapters.push(Chapter {
        id: "c1".into(),
        title: "كتاب".into(),
        blocks: vec![Block::Table {
            id: "table".into(),
            rows: vec![TableRow {
                cells: vec![TableCell {
                    blocks: vec![Block::Paragraph {
                        id: "cell-text".into(),
                        style: None,
                        align: None,
                        rtl: true,
                        runs: vec![
                            TextRun {
                                text: "PDF ".into(),
                                bold: true,
                                ..TextRun::default()
                            },
                            TextRun {
                                text: "كتاب 12 ".into(),
                                italic: true,
                                ..TextRun::default()
                            },
                            TextRun {
                                text: "پیوند".into(),
                                hyperlink: Some("https://example.test/كتاب/١".into()),
                                ..TextRun::default()
                            },
                        ],
                    }],
                    ..TableCell::default()
                }],
                ..TableRow::default()
            }],
        }],
        ..Chapter::default()
    });
    let report = fix_book(&mut book, &Options::default())?;
    assert_eq!(report.changed, ["c1", "cell-text"]);
    assert_eq!(book.chapters[0].title, "کتاب");
    let Block::Table { rows, .. } = &book.chapters[0].blocks[0] else {
        return Err("table layout changed".into());
    };
    let runs = rows[0].cells[0].blocks[0].runs();
    assert_eq!(runs[0].text, "\u{200f}\u{2067}PDF ");
    assert!(runs[0].bold);
    assert_eq!(runs[1].text, "کتاب ۱۲ ");
    assert!(runs[1].italic);
    assert_eq!(runs[2].text, "پیوند\u{2069}");
    assert_eq!(
        runs[2].hyperlink.as_deref(),
        Some("https://example.test/كتاب/١")
    );
    assert_eq!(fix_book(&mut book, &Options::default())?.changed_count, 0);
    assert_eq!(lint_book(&book)?.count, 0);
    Ok(())
}

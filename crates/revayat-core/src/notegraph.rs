use std::collections::BTreeMap;

use crate::{BookDocument, Finding, Severity, bookir::walk_blocks, markup::canonical_note_id};

pub fn check_note_graph(book: &BookDocument) -> Vec<Finding> {
    let mut found = Vec::new();
    let notes: BTreeMap<_, _> = book
        .footnotes
        .iter()
        .map(|note| (note.id.as_str(), note))
        .collect();
    let mut references = BTreeMap::<String, Vec<(String, String)>>::new();
    {
        let mut inspect = |blocks: &[_], chapter: &str, enclosing_note: Option<&str>| {
            walk_blocks(blocks, &mut |block| {
                for reference in block
                    .runs()
                    .iter()
                    .filter_map(|run| run.footnote_ref.as_deref())
                {
                    let problem = if enclosing_note == Some(reference) {
                        Some(("self-footnote", "A footnote cannot refer to itself"))
                    } else if enclosing_note.is_some() {
                        Some((
                            "nested-footnote",
                            "A footnote cannot contain a footnote reference",
                        ))
                    } else if !canonical_note_id(reference) {
                        Some((
                            "invalid-footnote-id",
                            "A book reference must use an allocated footnote ID",
                        ))
                    } else if !notes.contains_key(reference) {
                        Some(("missing-footnote", "The book does not define this footnote"))
                    } else {
                        None
                    };
                    if let Some((code, detail)) = problem {
                        found.push(Finding::new(
                            Severity::Error,
                            code,
                            block.id(),
                            format!("{reference}: {detail}"),
                        ));
                    } else {
                        references
                            .entry(reference.into())
                            .or_default()
                            .push((chapter.into(), block.id().into()));
                    }
                }
            });
        };
        for chapter in &book.chapters {
            inspect(&chapter.blocks, &chapter.id, None);
        }
        for note in &book.footnotes {
            inspect(&note.blocks, "", Some(&note.id));
        }
    }
    for note in &book.footnotes {
        let problem = match references.get(&note.id).map(Vec::as_slice) {
            Some([(chapter, block)]) => {
                if note.origin_chapter.as_ref().is_some_and(|id| id != chapter)
                    || note.anchor_block.as_ref().is_some_and(|id| id != block)
                {
                    Some((
                        "wrong-footnote-anchor",
                        "The note anchor does not match its reference",
                    ))
                } else {
                    None
                }
            }
            Some(_) => Some((
                "duplicate-footnote-reference",
                "A note can have only one anchor",
            )),
            None => Some(("orphan-footnote", "No body text refers to this note")),
        };
        if let Some((code, detail)) = problem {
            found.push(Finding::new(Severity::Error, code, &note.id, detail));
        }
    }
    found
}

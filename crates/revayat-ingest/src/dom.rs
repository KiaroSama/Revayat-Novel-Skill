use revayat_core::{Block, TableCell, TableRow, TextRun};
use scraper::{ElementRef, Node};

use crate::{IngestError, assets::paragraph};

pub(crate) fn selector(css: &str) -> Result<scraper::Selector, IngestError> {
    scraper::Selector::parse(css).map_err(|e| crate::error::invalid("HTML selector", e.to_string()))
}

pub(crate) fn text(element: ElementRef<'_>) -> String {
    element
        .text()
        .collect::<String>()
        .split_ascii_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

pub(crate) fn has_role(element: ElementRef<'_>, roles: &[&str]) -> bool {
    ["epub:type", "type", "role"]
        .iter()
        .filter_map(|name| element.attr(name))
        .any(|value| {
            value
                .split_ascii_whitespace()
                .any(|v| roles.contains(&v.strip_prefix("doc-").unwrap_or(v)))
        })
}

pub(crate) fn is_note(element: ElementRef<'_>) -> bool {
    has_role(element, &["footnote", "endnote", "rearnote"])
}

pub(crate) fn is_note_ref(element: ElementRef<'_>) -> bool {
    has_role(element, &["noteref"])
        || element.attr("href").is_some_and(|href| href.contains('#'))
            && element
                .parent()
                .and_then(ElementRef::wrap)
                .is_some_and(|parent| matches!(parent.value().name(), "sup" | "sub"))
}

fn excluded(element: ElementRef<'_>, web: bool) -> bool {
    if matches!(
        element.value().name(),
        "script" | "style" | "template" | "noscript" | "nav" | "iframe" | "form" | "button" | "svg"
    ) || element.attr("hidden").is_some()
        || element.attr("aria-hidden") == Some("true")
    {
        return true;
    }
    web && (matches!(element.value().name(), "footer" | "aside")
        || element
            .value()
            .classes()
            .chain(element.attr("id"))
            .any(|c| {
                matches!(
                    c.to_ascii_lowercase().as_str(),
                    "ad" | "ads"
                        | "advert"
                        | "advertisement"
                        | "advertisements"
                        | "sidebar"
                        | "social-share"
                        | "chapter-nav"
                )
            }))
}

fn heading(name: &str) -> Option<u8> {
    match name {
        "h1" => Some(1),
        "h2" => Some(2),
        "h3" => Some(3),
        "h4" => Some(4),
        "h5" => Some(5),
        "h6" => Some(6),
        _ => None,
    }
}

fn block_container(name: &str) -> bool {
    matches!(
        name,
        "p" | "div"
            | "section"
            | "article"
            | "main"
            | "header"
            | "blockquote"
            | "pre"
            | "li"
            | "ul"
            | "ol"
            | "dl"
            | "dt"
            | "dd"
            | "figure"
            | "figcaption"
            | "address"
            | "details"
            | "summary"
            | "aside"
    )
}

fn run_style(element: ElementRef<'_>, mut run: TextRun) -> TextRun {
    match element.value().name() {
        "strong" | "b" => run.bold = true,
        "em" | "i" | "cite" => run.italic = true,
        "u" => run.underline = true,
        "s" | "strike" | "del" => run.strikethrough = true,
        "sup" => run.superscript = true,
        "sub" => run.subscript = true,
        "a" if !has_role(element, &["backlink"]) => {
            run.hyperlink = element.attr("href").map(str::to_owned);
            if is_note_ref(element) {
                run.footnote_ref = Some(String::new());
            }
        }
        _ => {}
    }
    if let Some(lang) = element.attr("lang").or_else(|| element.attr("xml:lang")) {
        run.lang = Some(lang.to_owned());
    }
    for (key, value) in element
        .attr("style")
        .unwrap_or("")
        .split(';')
        .filter_map(|pair| pair.split_once(':'))
    {
        match (key.trim(), value.trim()) {
            ("font-weight", "bold" | "bolder" | "600" | "700" | "800" | "900") => run.bold = true,
            ("font-style", "italic" | "oblique") => run.italic = true,
            ("text-decoration" | "text-decoration-line", value) => {
                run.underline |= value.split_ascii_whitespace().any(|v| v == "underline");
                run.strikethrough |= value.split_ascii_whitespace().any(|v| v == "line-through");
            }
            _ => {}
        }
    }
    run
}

#[derive(Default)]
struct Runs {
    runs: Vec<TextRun>,
    pending_space: bool,
}

impl Runs {
    fn text(&mut self, text: &str, style: &TextRun, pre: bool) {
        let mut value = String::new();
        if pre {
            value.push_str(text);
        } else {
            for ch in text.chars() {
                if ch.is_ascii_whitespace() {
                    self.pending_space = true;
                } else {
                    if self.pending_space
                        && (!value.is_empty()
                            || self
                                .runs
                                .last()
                                .is_some_and(|run| !run.text.ends_with('\n')))
                    {
                        value.push(' ');
                    }
                    self.pending_space = false;
                    value.push(ch);
                }
            }
        }
        if !value.is_empty() {
            let mut run = style.clone();
            run.text = value;
            self.runs.push(run);
        }
    }
}

struct Dom<'a, F> {
    prefix: &'a str,
    counter: usize,
    web: bool,
    note_ids: &'a std::collections::HashSet<String>,
    image: F,
}

impl<F: FnMut(&str, &str, String) -> Result<Option<Block>, IngestError>> Dom<'_, F> {
    fn id(&mut self) -> String {
        self.counter += 1;
        format!("{}-b{:05}", self.prefix, self.counter)
    }

    fn flush(&mut self, out: &mut Vec<Block>, pending: &mut Runs, level: Option<u8>) {
        let runs = std::mem::take(pending).runs;
        if runs.iter().all(|run| run.text.trim().is_empty()) {
            return;
        }
        let id = self.id();
        out.push(if let Some(level) = level {
            Block::Heading { id, level, runs }
        } else {
            paragraph(id, runs)
        });
    }

    fn flow(&mut self, root: ElementRef<'_>, depth: usize) -> Result<Vec<Block>, IngestError> {
        let mut out = Vec::new();
        let mut pending = Runs::default();
        self.walk(
            root,
            &run_style(root, TextRun::default()),
            &mut pending,
            &mut out,
            depth,
            (heading(root.value().name()), root.value().name() == "pre"),
        )?;
        self.flush(&mut out, &mut pending, heading(root.value().name()));
        Ok(out)
    }

    fn table(&mut self, table: ElementRef<'_>, depth: usize) -> Result<Block, IngestError> {
        let mut rows = Vec::new();
        for child in table.child_elements() {
            let is_group = matches!(child.value().name(), "thead" | "tbody" | "tfoot");
            let candidates: Vec<_> = if is_group {
                child.child_elements().collect()
            } else {
                vec![child]
            };
            for row in candidates
                .into_iter()
                .filter(|el| el.value().name() == "tr")
            {
                let mut out = TableRow {
                    header: child.value().name() == "thead",
                    ..TableRow::default()
                };
                for cell in row
                    .child_elements()
                    .filter(|el| matches!(el.value().name(), "td" | "th"))
                {
                    out.header |= cell.value().name() == "th";
                    let span = |attribute| {
                        cell.attr(attribute)
                            .and_then(|n| n.parse::<u32>().ok())
                            .filter(|&n| n > 1)
                    };
                    out.cells.push(TableCell {
                        grid_span: span("colspan"),
                        row_span: span("rowspan"),
                        blocks: self.flow(cell, depth + 1)?,
                    });
                }
                if !out.cells.is_empty() {
                    rows.push(out);
                }
            }
        }
        Ok(Block::Table {
            id: self.id(),
            rows,
        })
    }

    fn walk(
        &mut self,
        root: ElementRef<'_>,
        style: &TextRun,
        pending: &mut Runs,
        out: &mut Vec<Block>,
        depth: usize,
        (level, pre): (Option<u8>, bool),
    ) -> Result<(), IngestError> {
        if depth > 96 {
            return Err(IngestError::Limit("HTML nesting depth"));
        }
        for node in root.children() {
            if let Node::Text(txt) = node.value() {
                pending.text(txt, style, pre);
                continue;
            }
            let Some(el) = ElementRef::wrap(node) else {
                continue;
            };
            if excluded(el, self.web)
                || is_note(el)
                || has_role(el, &["backlink"])
                || el.attr("id").is_some_and(|id| self.note_ids.contains(id))
            {
                continue;
            }
            let name = el.value().name();
            match name {
                "img" => {
                    let alt = el.attr("alt").unwrap_or("");
                    if let Some(src) = el.attr("src").filter(|s| !s.trim().is_empty()) {
                        let id = self.id();
                        if let Some(image) = (self.image)(src, alt, id)? {
                            self.flush(out, pending, level);
                            out.push(image);
                            continue;
                        }
                    }
                    pending.text(alt, style, pre);
                }
                "br" => {
                    pending.pending_space = false;
                    pending.text("\n", style, true);
                }
                "hr" => {
                    self.flush(out, pending, level);
                    out.push(Block::SceneBreak { id: self.id() });
                }
                "table" => {
                    self.flush(out, pending, level);
                    for caption in el
                        .child_elements()
                        .filter(|c| c.value().name() == "caption")
                    {
                        out.extend(self.flow(caption, depth + 1)?);
                    }
                    out.push(self.table(el, depth + 1)?);
                }
                name if heading(name).is_some() || block_container(name) => {
                    self.flush(out, pending, level);
                    let next_level = heading(name).or(level);
                    self.walk(
                        el,
                        &run_style(el, style.clone()),
                        pending,
                        out,
                        depth + 1,
                        (next_level, pre || name == "pre"),
                    )?;
                    self.flush(out, pending, next_level);
                }
                _ => self.walk(
                    el,
                    &run_style(el, style.clone()),
                    pending,
                    out,
                    depth + 1,
                    (level, pre),
                )?,
            }
        }
        Ok(())
    }
}

pub(crate) fn blocks(
    root: ElementRef<'_>,
    prefix: &str,
    web: bool,
    image: impl FnMut(&str, &str, String) -> Result<Option<Block>, IngestError>,
) -> Result<Vec<Block>, IngestError> {
    blocks_excluding_notes(root, prefix, web, &std::collections::HashSet::new(), image)
}

pub(crate) fn blocks_excluding_notes(
    root: ElementRef<'_>,
    prefix: &str,
    web: bool,
    note_ids: &std::collections::HashSet<String>,
    image: impl FnMut(&str, &str, String) -> Result<Option<Block>, IngestError>,
) -> Result<Vec<Block>, IngestError> {
    Dom {
        prefix,
        counter: 0,
        web,
        note_ids,
        image,
    }
    .flow(root, 0)
}

pub(crate) fn namespace_blocks(blocks: &mut [Block], prefix: &str) {
    for block in blocks {
        let id = match block {
            Block::Paragraph { id, .. }
            | Block::Heading { id, .. }
            | Block::Image { id, .. }
            | Block::PageBreak { id }
            | Block::SceneBreak { id } => id,
            Block::Table { id, rows } => {
                for cell in rows.iter_mut().flat_map(|row| &mut row.cells) {
                    namespace_blocks(&mut cell.blocks, prefix);
                }
                id
            }
        };
        id.insert_str(0, prefix);
    }
}

pub(crate) fn visit_runs(
    blocks: &mut [Block],
    f: &mut impl FnMut(&str, &mut TextRun) -> Result<(), IngestError>,
) -> Result<(), IngestError> {
    for block in blocks {
        match block {
            Block::Paragraph { id, runs, .. }
            | Block::Heading { id, runs, .. }
            | Block::Image {
                id, caption: runs, ..
            } => {
                for run in runs {
                    f(id, run)?;
                }
            }
            Block::Table { rows, .. } => {
                for cell in rows.iter_mut().flat_map(|r| &mut r.cells) {
                    visit_runs(&mut cell.blocks, f)?;
                }
            }
            Block::PageBreak { .. } | Block::SceneBreak { .. } => {}
        }
    }
    Ok(())
}

pub(crate) fn first_heading(blocks: &[Block]) -> Option<String> {
    blocks.iter().find_map(|b| match b {
        Block::Heading { runs, .. } => Some(runs.iter().map(|r| r.text.as_str()).collect()),
        _ => None,
    })
}

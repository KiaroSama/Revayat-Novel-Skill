use std::{collections::BTreeMap, fmt::Write as _};

use revayat_core::{Block, Orientation, PageGeometry, TableRow, TextRun, isolate_persian_line};

use crate::{
    OoxmlError,
    fonts::FONT_RUN,
    opc::{PR, R, escape},
};

#[derive(Default)]
pub(crate) struct Relationships(Vec<(String, String, bool)>);

impl Relationships {
    pub fn add(&mut self, kind: &str, target: &str, external: bool) -> String {
        let id = self
            .0
            .iter()
            .position(|(k, t, e)| k == kind && t == target && *e == external)
            .map_or_else(
                || {
                    self.0.push((kind.into(), target.into(), external));
                    self.0.len()
                },
                |i| i + 1,
            );
        format!("rId{id}")
    }

    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    pub fn xml(&self) -> String {
        let mut out = format!(r#"<Relationships xmlns="{PR}">"#);
        for (i, (kind, target, external)) in self.0.iter().enumerate() {
            let mode = if *external {
                r#" TargetMode="External""#
            } else {
                ""
            };
            let _ = write!(
                out,
                r#"<Relationship Id="rId{}" Type="{R}/{kind}" Target="{}"{mode}/>"#,
                i + 1,
                escape(target)
            );
        }
        out.push_str("</Relationships>");
        out
    }
}

pub(crate) struct Renderer<'a> {
    assets: &'a BTreeMap<&'a str, String>,
    notes: &'a BTreeMap<&'a str, usize>,
    drawing_id: usize,
    bookmark_id: usize,
    anchors: BTreeMap<&'a str, String>,
}

impl<'a> Renderer<'a> {
    pub fn new(assets: &'a BTreeMap<&'a str, String>, notes: &'a BTreeMap<&'a str, usize>) -> Self {
        Self {
            assets,
            notes,
            drawing_id: 0,
            bookmark_id: 0,
            anchors: BTreeMap::new(),
        }
    }

    pub fn set_anchors(&mut self, blocks: &'a [Block]) {
        for block in blocks {
            match block {
                Block::Heading { id, .. } | Block::Paragraph { id, .. } => {
                    self.anchors.insert(id, bookmark(id));
                }
                Block::Table { rows, .. } => {
                    for cell in rows.iter().flat_map(|r| &r.cells) {
                        self.set_anchors(&cell.blocks);
                    }
                }
                _ => {}
            }
        }
    }

    pub fn blocks(
        &mut self,
        out: &mut String,
        blocks: &[Block],
        rels: &mut Relationships,
    ) -> Result<(), OoxmlError> {
        for block in blocks {
            match block {
                Block::Paragraph { id, runs, .. } => {
                    let text = self.runs(runs, rels)?;
                    out.push_str(&paragraph(&self.bookmarked(id, &text), None));
                }
                Block::Heading { id, level, runs } => {
                    let text = self.runs(runs, rels)?;
                    out.push_str(&paragraph(
                        &self.bookmarked(id, &text),
                        Some(&format!("Heading{level}")),
                    ));
                }
                Block::Table { rows, .. } => self.table(out, rows, rels)?,
                Block::Image {
                    asset_id,
                    width_emu,
                    height_emu,
                    caption,
                    ..
                } => {
                    let target = self
                        .assets
                        .get(asset_id.as_str())
                        .ok_or_else(|| OoxmlError::Image(format!("missing asset {asset_id}")))?;
                    let rel = rels.add("image", target, false);
                    self.drawing_id += 1;
                    out.push_str(&paragraph(
                        &drawing(self.drawing_id, &rel, *width_emu, *height_emu),
                        None,
                    ));
                    if !caption.is_empty() {
                        out.push_str(&paragraph(&self.runs(caption, rels)?, Some("Caption")));
                    }
                }
                Block::PageBreak { .. } => {
                    out.push_str(&paragraph(r#"<w:r><w:br w:type="page"/></w:r>"#, None))
                }
                Block::SceneBreak { .. } => out.push_str(&paragraph(
                    "<w:r><w:t>***</w:t></w:r>",
                    Some("RevayatSceneBreak"),
                )),
            }
        }
        Ok(())
    }

    fn bookmarked(&mut self, id: &str, text: &str) -> String {
        self.bookmark_id += 1;
        let index = self.bookmark_id;
        let name = bookmark(id);
        format!(
            r#"<w:bookmarkStart w:id="{index}" w:name="{name}"/>{text}<w:bookmarkEnd w:id="{index}"/>"#
        )
    }

    fn runs(&self, runs: &[TextRun], rels: &mut Relationships) -> Result<String, OoxmlError> {
        let mut result = String::new();
        let mut line = Vec::new();
        for run in runs {
            for (i, text) in run.text.split('\n').enumerate() {
                if i > 0 {
                    self.line(&mut result, &line, rels)?;
                    line.clear();
                    result.push_str("<w:r><w:br/></w:r>");
                }
                if !text.is_empty() || (run.footnote_ref.is_some() && i == 0) {
                    line.push((run, text.strip_suffix('\r').unwrap_or(text), i == 0));
                }
            }
        }
        self.line(&mut result, &line, rels)?;
        Ok(result)
    }

    fn line(
        &self,
        out: &mut String,
        items: &[(&TextRun, &str, bool)],
        rels: &mut Relationships,
    ) -> Result<(), OoxmlError> {
        let joined: String = items.iter().map(|(_, t, _)| *t).collect();
        let isolated = isolate_persian_line(&joined);
        let already = isolated == joined;
        if !already {
            let inner = isolated
                .strip_prefix("\u{200f}\u{2067}")
                .and_then(|s| s.strip_suffix('\u{2069}'));
            if inner != Some(joined.as_str()) {
                return Err(OoxmlError::Package(
                    "cannot isolate malformed bidi controls without changing the text".into(),
                ));
            }
            out.push_str(&literal_run("\u{200f}\u{2067}"));
        }
        for (run, text, include_note) in items {
            self.run(out, run, text, *include_note, rels)?;
        }
        if !already {
            out.push_str(&literal_run("\u{2069}"));
        }
        Ok(())
    }

    fn run(
        &self,
        out: &mut String,
        run: &TextRun,
        text: &str,
        include_note: bool,
        rels: &mut Relationships,
    ) -> Result<(), OoxmlError> {
        if let Some(link) = &run.hyperlink {
            if let Some(name) = link.strip_prefix('#') {
                let anchor = self
                    .anchors
                    .get(name)
                    .ok_or_else(|| OoxmlError::Package(format!("missing bookmark {name}")))?;
                let _ = write!(out, r#"<w:hyperlink w:anchor="{}">"#, escape(anchor));
            } else {
                if !safe_hyperlink(link) {
                    return Err(OoxmlError::Unsupported(
                        "unsupported external hyperlink".into(),
                    ));
                }
                let id = rels.add("hyperlink", link, true);
                let _ = write!(out, r#"<w:hyperlink r:id="{id}">"#);
            }
        }
        out.push_str("<w:r><w:rPr>");
        out.push_str(FONT_RUN);
        if run.bold {
            out.push_str("<w:b/><w:bCs/>");
        }
        if run.italic {
            out.push_str("<w:i/><w:iCs/>");
        }
        if run.strikethrough {
            out.push_str("<w:strike/>");
        }
        if run.underline {
            out.push_str(r#"<w:u w:val="single"/>"#);
        }
        if run.superscript || run.subscript {
            let val = if run.superscript {
                "superscript"
            } else {
                "subscript"
            };
            let _ = write!(out, r#"<w:vertAlign w:val="{val}"/>"#);
        }
        if revayat_core::has_persian(text) || text.chars().all(|c| !c.is_alphabetic()) {
            out.push_str("<w:rtl/>");
        }
        if let Some(lang) = &run.lang {
            let _ = write!(out, r#"<w:lang w:val="{}"/>"#, escape(lang));
        }
        out.push_str("</w:rPr>");
        for (i, part) in text.split('\t').enumerate() {
            if i > 0 {
                out.push_str("<w:tab/>");
            }
            if !part.is_empty() {
                let _ = write!(out, r#"<w:t xml:space="preserve">{}</w:t>"#, escape(part));
            }
        }
        if include_note && let Some(note) = &run.footnote_ref {
            let id = self
                .notes
                .get(note.as_str())
                .ok_or_else(|| OoxmlError::Package(format!("missing note {note}")))?;
            let _ = write!(out, r#"<w:footnoteReference w:id="{id}"/>"#);
        }
        out.push_str("</w:r>");
        if run.hyperlink.is_some() {
            out.push_str("</w:hyperlink>");
        }
        Ok(())
    }

    fn table(
        &mut self,
        out: &mut String,
        rows: &[TableRow],
        rels: &mut Relationships,
    ) -> Result<(), OoxmlError> {
        let count = rows
            .iter()
            .map(|row| {
                row.cells.iter().fold(0_u32, |sum, cell| {
                    sum.saturating_add(cell.grid_span.unwrap_or(1))
                })
            })
            .max()
            .unwrap_or(0);
        if count == 0 || count > 256 {
            return Err(OoxmlError::Limit("empty or oversized table grid".into()));
        }
        out.push_str(r#"<w:tbl><w:tblPr><w:bidiVisual/><w:tblW w:w="0" w:type="auto"/></w:tblPr><w:tblGrid>"#);
        for _ in 0..count {
            out.push_str(r#"<w:gridCol w:w="1440"/>"#);
        }
        out.push_str("</w:tblGrid>");
        let mut active: BTreeMap<u32, (u32, u32)> = BTreeMap::new();
        for row in rows {
            out.push_str("<w:tr>");
            if row.header {
                out.push_str("<w:trPr><w:tblHeader/></w:trPr>");
            }
            let mut column = 0;
            let mut cell_iter = row.cells.iter().peekable();
            let mut next = BTreeMap::new();
            while cell_iter.peek().is_some() || active.range(column..).next().is_some() {
                if let Some((span, remaining)) = active.get(&column) {
                    cell_start(out, *span, Some("continue"));
                    out.push_str("<w:p/></w:tc>");
                    if *remaining > 1 {
                        next.insert(column, (*span, *remaining - 1));
                    }
                    column += span;
                    continue;
                }
                let cell = cell_iter
                    .next()
                    .ok_or_else(|| OoxmlError::Package("invalid table row span position".into()))?;
                let span = cell.grid_span.unwrap_or(1);
                let vertical = cell.row_span.unwrap_or(1);
                if column + span > 256 || active.range(column..column + span).next().is_some() {
                    return Err(OoxmlError::Package(
                        "overlapping or oversized table spans".into(),
                    ));
                }
                cell_start(out, span, (vertical > 1).then_some("restart"));
                if vertical > 1 {
                    next.insert(column, (span, vertical - 1));
                }
                self.blocks(out, &cell.blocks, rels)?;
                if !matches!(
                    cell.blocks.last(),
                    Some(Block::Paragraph { .. } | Block::Heading { .. })
                ) {
                    out.push_str("<w:p/>");
                }
                out.push_str("</w:tc>");
                column += span;
            }
            out.push_str("</w:tr>");
            active = next;
        }
        if !active.is_empty() {
            return Err(OoxmlError::Package("unfinished vertical table span".into()));
        }
        out.push_str("</w:tbl>");
        Ok(())
    }
}

fn cell_start(out: &mut String, span: u32, vertical: Option<&str>) {
    out.push_str("<w:tc><w:tcPr>");
    if span > 1 {
        let _ = write!(out, r#"<w:gridSpan w:val="{span}"/>"#);
    }
    if let Some(val) = vertical {
        let _ = write!(out, r#"<w:vMerge w:val="{val}"/>"#);
    }
    out.push_str("</w:tcPr>");
}

pub(crate) fn safe_hyperlink(value: &str) -> bool {
    ["https://", "http://", "mailto:"]
        .iter()
        .any(|p| value.starts_with(p) && value.len() > p.len())
        && !value.chars().any(char::is_control)
}

pub(crate) fn bookmark(id: &str) -> String {
    format!(
        "Revayat_{}",
        &revayat_core::sha256_bytes(id.as_bytes())[..30]
    )
}

fn literal_run(text: &str) -> String {
    format!(
        r#"<w:r><w:rPr>{FONT_RUN}<w:rtl/></w:rPr><w:t xml:space="preserve">{}</w:t></w:r>"#,
        escape(text)
    )
}

pub(crate) fn paragraph(payload: &str, style: Option<&str>) -> String {
    let style = style.map_or_else(String::new, |name| {
        format!(r#"<w:pStyle w:val="{}"/>"#, escape(name))
    });
    format!(r#"<w:p><w:pPr>{style}<w:bidi/><w:jc w:val="start"/></w:pPr>{payload}</w:p>"#)
}

pub(crate) fn geometry(g: &PageGeometry, headers: &str) -> String {
    let orientation = if g.orientation == Orientation::Landscape {
        r#" w:orient="landscape""#
    } else {
        ""
    };
    format!(
        r#"<w:sectPr>{headers}<w:pgSz w:w="{}" w:h="{}"{orientation}/><w:pgMar w:top="{}" w:right="{}" w:bottom="{}" w:left="{}" w:header="720" w:footer="720" w:gutter="0"/><w:bidi/></w:sectPr>"#,
        g.width_twips,
        g.height_twips,
        g.margin_top_twips,
        g.margin_inner_twips,
        g.margin_bottom_twips,
        g.margin_outer_twips
    )
}

fn drawing(id: usize, rel: &str, w: u64, h: u64) -> String {
    format!(
        r#"<w:r><w:drawing><wp:inline><wp:extent cx="{w}" cy="{h}"/><wp:docPr id="{id}" name="Image {id}"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="{id}" name="Image {id}"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="{rel}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{w}" cy="{h}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"#
    )
}

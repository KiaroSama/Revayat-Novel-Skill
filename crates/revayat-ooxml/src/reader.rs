use std::{collections::BTreeMap, fs, io::Write, path::Path};

use revayat_core::{
    AssetRecord, Block, BookDocument, Chapter, Footnote, FootnoteOrigin, Orientation, PageGeometry,
    SourceFormat, TextRun, sha256_file,
};
use roxmltree::Node;

use crate::{
    OoxmlError,
    media::{Media, resolve_drawing},
    opc::{self, Package, R, Relationship, W, child, related, required, text_of},
    render::safe_hyperlink,
};

pub fn read_docx(path: &Path, assets_dir: &Path) -> Result<BookDocument, OoxmlError> {
    let mut package = Package::open(path)?;
    let main = package.main_part()?;
    let xml = package.xml(&main)?;
    let doc = opc::parse(&xml, &main)?;
    let body = body(doc.root_element())?;
    let geometry = page_geometry(body)?;
    let rels = package.relationships(&main)?;
    let types = package.content_types()?;
    let (title, lang) = metadata(&mut package, path)?;
    let mut book = BookDocument::new(SourceFormat::Docx, sha256_file(path)?, title, lang);
    book.page_geometry = geometry;
    let mut anchors = BTreeMap::new();
    for n in body
        .descendants()
        .filter(|n| n.has_tag_name((W, "bookmarkStart")))
    {
        if let Ok(name) = required(n, Some(W), "name") {
            if name == "_GoBack" {
                continue;
            }
            let para = n
                .ancestors()
                .find(|a| a.has_tag_name((W, "p")))
                .ok_or_else(|| OoxmlError::Unsupported("bookmark outside paragraph".into()))?;
            if anchors.insert(name.to_owned(), block_id(para, 0)).is_some() {
                return Err(OoxmlError::Package(format!("duplicate bookmark {name}")));
            }
        }
    }
    let mut intake = Intake {
        package: &mut package,
        types,
        assets: BTreeMap::new(),
        anchors,
        notes: BTreeMap::new(),
    };
    let mut blocks = intake.blocks(body, &rels)?;
    if blocks.is_empty() {
        return Err(OoxmlError::Package("no supported book content".into()));
    }
    materialize_notes(
        book_partition(&mut blocks, &mut book)?,
        &mut intake,
        &rels,
        &mut book,
    )?;
    book.assets = intake
        .assets
        .values()
        .map(|(record, _)| record.clone())
        .collect();
    book.validate()?;
    if !intake.assets.is_empty() {
        fs::create_dir_all(assets_dir)?;
        if !fs::symlink_metadata(assets_dir)?.is_dir() {
            return Err(OoxmlError::Image("unsafe asset output directory".into()));
        }
        // Check all occupied destinations before publishing any of the extracted assets.
        for (record, _) in intake.assets.values() {
            let target = assets_dir.join(&record.rel_path);
            match fs::symlink_metadata(&target) {
                Ok(meta)
                    if meta.is_file()
                        && meta.len() <= opc::MAX_PART
                        && sha256_file(&target)? == record.sha256 => {}
                Ok(_) => {
                    return Err(OoxmlError::Image(format!(
                        "asset destination already differs: {}",
                        record.rel_path
                    )));
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => return Err(e.into()),
            }
        }
        for (record, bytes) in intake.assets.values() {
            let target = assets_dir.join(&record.rel_path);
            if target.exists() {
                continue;
            }
            let mut tmp = tempfile::NamedTempFile::new_in(assets_dir)?;
            tmp.write_all(bytes)?;
            tmp.persist_noclobber(&target)?;
        }
    }
    Ok(book)
}

pub(crate) fn body<'a, 'input>(root: Node<'a, 'input>) -> Result<Node<'a, 'input>, OoxmlError> {
    if !root.has_tag_name((W, "document")) {
        return Err(OoxmlError::Package("expected Word document".into()));
    }
    child(root, W, "body").ok_or_else(|| OoxmlError::Package("missing Word body".into()))
}

pub(crate) fn page_geometry(body: Node<'_, '_>) -> Result<PageGeometry, OoxmlError> {
    let mut found = None;
    for section in body.descendants().filter(|n| n.has_tag_name((W, "sectPr"))) {
        let mut page = PageGeometry::default();
        if let Some(sz) = child(section, W, "pgSz") {
            page.width_twips = measure(sz, "w")?;
            page.height_twips = measure(sz, "h")?;
            page.orientation = if sz.attribute((W, "orient")) == Some("landscape")
                || page.width_twips > page.height_twips
            {
                Orientation::Landscape
            } else {
                Orientation::Portrait
            };
        }
        if let Some(m) = child(section, W, "pgMar") {
            page.margin_top_twips = measure(m, "top")?;
            page.margin_bottom_twips = measure(m, "bottom")?;
            (page.margin_inner_twips, page.margin_outer_twips) =
                if child(section, W, "bidi").is_some_and(on) {
                    (measure(m, "right")?, measure(m, "left")?)
                } else {
                    (measure(m, "left")?, measure(m, "right")?)
                };
        }
        page.validate()?;
        if found.as_ref().is_some_and(|previous| previous != &page) {
            return Err(OoxmlError::Unsupported(
                "different section geometries cannot be represented in Book IR".into(),
            ));
        }
        found = Some(page);
    }
    Ok(found.unwrap_or_default())
}

pub(crate) fn measure(n: Node<'_, '_>, name: &str) -> Result<u32, OoxmlError> {
    required(n, Some(W), name)?.parse().map_err(|_| {
        OoxmlError::Unsupported(format!("non-integer or negative Word measure {name}"))
    })
}

pub(crate) fn on(n: Node<'_, '_>) -> bool {
    !matches!(n.attribute((W, "val")), Some("0" | "false" | "off"))
}

fn metadata(pkg: &mut Package, path: &Path) -> Result<(String, String), OoxmlError> {
    const DC: &str = "http://purl.org/dc/elements/1.1/";
    let default = || {
        path.file_stem()
            .map_or_else(|| "Untitled".into(), |p| p.to_string_lossy().into_owned())
    };
    let rels = pkg.relationships("")?;
    let kind = format!("{}/metadata/core-properties", opc::PR);
    let core = rels.values().find(|r| r.kind == kind && !r.external);
    let Some(core) = core else {
        return Ok((default(), "und".into()));
    };
    let xml = pkg.xml(&core.target)?;
    let doc = opc::parse(&xml, &core.target)?;
    let text = |tag| {
        child(doc.root_element(), DC, tag)
            .and_then(|n| n.text())
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
    };
    Ok((
        text("title").unwrap_or_else(default),
        text("language").unwrap_or_else(|| "und".into()),
    ))
}

type NoteAnchors = BTreeMap<String, (String, String)>;
fn book_partition(
    blocks: &mut Vec<Block>,
    book: &mut BookDocument,
) -> Result<NoteAnchors, OoxmlError> {
    let mut current = Chapter {
        id: "ch1".into(),
        index: 1,
        title: book.title.clone(),
        ..Chapter::default()
    };
    for block in blocks.drain(..) {
        if let Block::Heading { level: 1, runs, .. } = &block {
            let title = runs.iter().map(|r| r.text.as_str()).collect();
            if !current.blocks.is_empty() {
                let index = current.index + 1;
                book.chapters.push(current);
                current = Chapter {
                    id: format!("ch{index}"),
                    index,
                    ..Chapter::default()
                };
            }
            current.title = title;
        }
        current.blocks.push(block);
    }
    book.chapters.push(current);
    let mut out = BTreeMap::new();
    for chapter in &book.chapters {
        capture_notes(&chapter.blocks, &chapter.id, &mut out)?;
    }
    Ok(out)
}

fn capture_notes(blocks: &[Block], chapter: &str, out: &mut NoteAnchors) -> Result<(), OoxmlError> {
    for b in blocks {
        for reference in b.runs().iter().filter_map(|r| r.footnote_ref.as_ref()) {
            if out
                .insert(reference.clone(), (chapter.into(), b.id().into()))
                .is_some()
            {
                return Err(OoxmlError::Package(format!(
                    "duplicate note occurrence {reference}"
                )));
            }
        }
        if let Block::Table { rows, .. } = b {
            for cell in rows.iter().flat_map(|r| &r.cells) {
                capture_notes(&cell.blocks, chapter, out)?;
            }
        }
    }
    Ok(())
}

fn materialize_notes(
    anchors: NoteAnchors,
    intake: &mut Intake<'_>,
    rels: &BTreeMap<String, Relationship>,
    book: &mut BookDocument,
) -> Result<(), OoxmlError> {
    let Some(part) = related(rels, "footnotes")? else {
        return if anchors.is_empty() {
            Ok(())
        } else {
            Err(OoxmlError::Package("missing footnotes part".into()))
        };
    };
    let xml = intake.package.xml(part)?;
    let doc = opc::parse(&xml, part)?;
    if !doc.root_element().has_tag_name((W, "footnotes")) {
        return Err(OoxmlError::Package("expected footnotes root".into()));
    }
    let bodies = opc::footnote_bodies(doc.root_element())?;
    let note_rels = intake.package.relationships(part)?;
    let note_ids = std::mem::take(&mut intake.notes);
    for (original, canonical) in note_ids {
        let (chapter, block) = anchors
            .get(&canonical)
            .ok_or_else(|| OoxmlError::Package(format!("missing note anchor {canonical}")))?;
        let n = bodies
            .get(original.as_str())
            .ok_or_else(|| OoxmlError::Package(format!("missing note body {original}")))?;
        let blocks = intake.blocks(*n, &note_rels)?;
        let number = canonical
            .strip_prefix("fn")
            .and_then(|number| number.parse().ok())
            .ok_or_else(|| OoxmlError::Package("invalid allocated note ID".into()))?;
        book.footnotes.push(Footnote {
            id: canonical,
            number,
            blocks,
            origin_chapter: Some(chapter.clone()),
            anchor_block: Some(block.clone()),
            origin: FootnoteOrigin::Source,
        });
    }
    if !intake.notes.is_empty() {
        return Err(OoxmlError::Unsupported("nested footnote reference".into()));
    }
    book.footnotes.sort_by_key(|n| n.number);
    Ok(())
}

pub(crate) struct Intake<'a> {
    package: &'a mut Package,
    types: BTreeMap<String, String>,
    assets: BTreeMap<String, (AssetRecord, Vec<u8>)>,
    anchors: BTreeMap<String, String>,
    notes: BTreeMap<String, String>,
}

pub(crate) fn block_id(n: Node<'_, '_>, instance: usize) -> String {
    let lane = if n.ancestors().any(|a| a.has_tag_name((W, "footnotes"))) {
        "n"
    } else {
        "b"
    };
    format!("{lane}{}-{instance}", n.id().get())
}

impl Intake<'_> {
    pub(crate) fn blocks(
        &mut self,
        container: Node<'_, '_>,
        rels: &BTreeMap<String, Relationship>,
    ) -> Result<Vec<Block>, OoxmlError> {
        let mut blocks = Vec::new();
        for n in container.children().filter(Node::is_element) {
            match n.tag_name().name() {
                "p" if n.has_tag_name((W, "p")) => self.paragraph(n, rels, &mut blocks)?,
                "tbl" if n.has_tag_name((W, "tbl")) => blocks.push(self.table(n, rels)?),
                "sectPr" | "tcPr" if n.tag_name().namespace() == Some(W) => {}
                _ => {
                    return Err(OoxmlError::Unsupported(format!(
                        "unrepresented block {}",
                        n.tag_name().name()
                    )));
                }
            }
        }
        Ok(blocks)
    }

    fn paragraph(
        &mut self,
        n: Node<'_, '_>,
        rels: &BTreeMap<String, Relationship>,
        out: &mut Vec<Block>,
    ) -> Result<(), OoxmlError> {
        let ppr = child(n, W, "pPr");
        let style = ppr
            .and_then(|p| child(p, W, "pStyle"))
            .and_then(|p| p.attribute((W, "val")));
        if (style == Some("RevayatTOC")
            || n.descendants().any(|a| {
                a.has_tag_name((W, "instrText"))
                    && a.text().is_some_and(|t| t.trim_start().starts_with("TOC "))
            }))
            && n.descendants().any(|a| a.has_tag_name((W, "fldChar")))
        {
            return Ok(());
        }
        if style == Some("RevayatSceneBreak") && text_of(n) == "***" {
            out.push(Block::SceneBreak { id: block_id(n, 0) });
            return Ok(());
        }
        if style == Some("Caption")
            && let Some(Block::Image { caption, .. }) = out.last_mut()
            && caption.is_empty()
        {
            for run in n
                .children()
                .filter(|x| x.has_tag_name((W, "r")) || x.has_tag_name((W, "hyperlink")))
            {
                for e in self.events(run, rels)? {
                    if let Event::Text(t) = e {
                        caption.push(t);
                    } else {
                        return Err(OoxmlError::Unsupported("non-text image caption".into()));
                    }
                }
            }
            return Ok(());
        }
        let level = style
            .and_then(|s| s.strip_prefix("Heading"))
            .and_then(|n| n.parse::<u8>().ok())
            .filter(|n| (1..=9).contains(n))
            .or_else(|| {
                ppr.and_then(|p| child(p, W, "outlineLvl"))
                    .and_then(|p| p.attribute((W, "val")))
                    .and_then(|n| n.parse::<u8>().ok())
                    .filter(|n| *n < 9)
                    .map(|n| n + 1)
            });
        let mut runs = Vec::new();
        let mut sequence = 0;
        let mut recognizable = false;
        let mut flush = |out: &mut Vec<Block>, runs: &mut Vec<TextRun>, empty: bool| {
            if empty || !runs.is_empty() {
                let id = block_id(n, sequence);
                sequence += 1;
                let runs = std::mem::take(runs);
                if let Some(level) = level {
                    out.push(Block::Heading { id, level, runs });
                } else {
                    let align = ppr
                        .and_then(|p| child(p, W, "jc"))
                        .and_then(|p| p.attribute((W, "val")))
                        .map(str::to_owned);
                    out.push(Block::Paragraph {
                        id,
                        runs,
                        style: style.map(str::to_owned),
                        align,
                        rtl: ppr.and_then(|p| child(p, W, "bidi")).is_some_and(on),
                    });
                }
            }
        };
        for node in n.children().filter(Node::is_element) {
            match node.tag_name().name() {
                "r" | "hyperlink" if node.tag_name().namespace() == Some(W) => {}
                "pPr" | "bookmarkStart" | "bookmarkEnd" | "proofErr"
                    if node.tag_name().namespace() == Some(W) =>
                {
                    continue;
                }
                _ => {
                    return Err(OoxmlError::Unsupported(format!(
                        "paragraph element {}",
                        node.tag_name().name()
                    )));
                }
            }
            for event in self.events(node, rels)? {
                recognizable = true;
                match event {
                    Event::Text(t) => runs.push(t),
                    Event::Image(m) => {
                        flush(out, &mut runs, false);
                        out.push(self.image(&m, format!("{}-{}", block_id(n, 0), out.len()))?);
                    }
                    Event::Break => {
                        flush(out, &mut runs, false);
                        out.push(Block::PageBreak {
                            id: format!("{}-{}", block_id(n, 0), out.len()),
                        });
                    }
                }
            }
        }
        let marker = n.descendants().any(|a| a.has_tag_name((W, "footnoteRef")));
        flush(out, &mut runs, !recognizable && !marker);
        Ok(())
    }

    fn events(
        &mut self,
        n: Node<'_, '_>,
        rels: &BTreeMap<String, Relationship>,
    ) -> Result<Vec<Event>, OoxmlError> {
        let mut result = Vec::new();
        if n.has_tag_name((W, "hyperlink")) {
            let link = if let Some(anchor) = n.attribute((W, "anchor")) {
                format!(
                    "#{}",
                    self.anchors
                        .get(anchor)
                        .ok_or_else(|| OoxmlError::Package(format!(
                            "missing hyperlink bookmark {anchor}"
                        )))?
                )
            } else {
                let id = required(n, Some(R), "id")?;
                rels.get(id)
                    .filter(|r| {
                        r.external
                            && r.kind == format!("{R}/hyperlink")
                            && safe_hyperlink(&r.target)
                    })
                    .ok_or_else(|| OoxmlError::Unsupported(format!("invalid hyperlink {id}")))?
                    .target
                    .clone()
            };
            for run in n.children().filter(Node::is_element) {
                if !run.has_tag_name((W, "r")) {
                    return Err(OoxmlError::Unsupported("non-run hyperlink content".into()));
                }
                self.run_events(run, rels, Some(&link), &mut result)?;
            }
        } else {
            self.run_events(n, rels, None, &mut result)?;
        }
        Ok(result)
    }

    fn run_events(
        &mut self,
        n: Node<'_, '_>,
        rels: &BTreeMap<String, Relationship>,
        link: Option<&str>,
        out: &mut Vec<Event>,
    ) -> Result<(), OoxmlError> {
        let props = child(n, W, "rPr");
        let has = |tag| props.and_then(|p| child(p, W, tag)).is_some_and(on);
        let prop = |tag| {
            props
                .and_then(|p| child(p, W, tag))
                .and_then(|p| p.attribute((W, "val")))
        };
        let template = TextRun {
            bold: has("b") || has("bCs"),
            italic: has("i") || has("iCs"),
            underline: props.and_then(|p| child(p, W, "u")).is_some() && prop("u") != Some("none"),
            strikethrough: has("strike") || has("dstrike"),
            superscript: prop("vertAlign") == Some("superscript"),
            subscript: prop("vertAlign") == Some("subscript"),
            hyperlink: link.map(str::to_owned),
            lang: prop("lang").map(str::to_owned),
            ..TextRun::default()
        };
        for c in n.children().filter(Node::is_element) {
            if c.tag_name().namespace() != Some(W) {
                return Err(OoxmlError::Unsupported(format!(
                    "run element {}",
                    c.tag_name().name()
                )));
            }
            match c.tag_name().name() {
                "t" | "tab" | "cr" | "br" if c.attribute((W, "type")) != Some("page") => {
                    let text = match c.tag_name().name() {
                        "t" => c.text().unwrap_or_default(),
                        "tab" => "\t",
                        _ => "\n",
                    };
                    if !text.is_empty() {
                        out.push(Event::Text(TextRun {
                            text: text.into(),
                            ..template.clone()
                        }));
                    }
                }
                "br" => out.push(Event::Break),
                "drawing" => {
                    let items = resolve_drawing(self.package, rels, &self.types, c)?;
                    if items.is_empty() {
                        return Err(OoxmlError::Unsupported("unrepresented drawing".into()));
                    }
                    out.extend(items.into_iter().map(Event::Image));
                }
                "footnoteReference" => {
                    let id = required(c, Some(W), "id")?;
                    let next = self.notes.len() + 1;
                    let note = self
                        .notes
                        .entry(id.into())
                        .or_insert_with(|| format!("fn{next:04}"));
                    out.push(Event::Text(TextRun {
                        footnote_ref: Some(note.clone()),
                        ..template.clone()
                    }));
                }
                "rPr" | "footnoteRef" | "lastRenderedPageBreak" => {}
                _ => {
                    return Err(OoxmlError::Unsupported(format!(
                        "unrepresented run element {}",
                        c.tag_name().name()
                    )));
                }
            }
        }
        Ok(())
    }

    fn image(&mut self, m: &Media, id: String) -> Result<Block, OoxmlError> {
        if self.assets.len() >= 4096 {
            return Err(OoxmlError::Limit("embedded image count".into()));
        }
        let prefix = self.assets.len() + 1;
        let (record, _) = self.assets.entry(m.target.clone()).or_insert_with(|| {
            (
                AssetRecord {
                    id: format!("image-{prefix}"),
                    media_type: m.media_type.into(),
                    sha256: m.sha256.clone(),
                    width_px: m.width_px,
                    height_px: m.height_px,
                    rel_path: format!("image-{prefix}.{}", m.extension),
                    original_bytes_preserved: true,
                },
                m.bytes.clone(),
            )
        });
        Ok(Block::Image {
            id,
            asset_id: record.id.clone(),
            width_emu: m.width_emu,
            height_emu: m.height_emu,
            caption: Vec::new(),
        })
    }
}

enum Event {
    Text(TextRun),
    Image(Media),
    Break,
}

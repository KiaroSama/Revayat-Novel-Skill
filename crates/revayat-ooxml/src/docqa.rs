use crate::{
    OoxmlError,
    fonts::{PERSIAN_FONT, validate_fonts},
    media::resolve_drawing,
    opc::{self, Package, R, Relationship, W, child, related, required, text_of},
    reader::{body, page_geometry},
};
use revayat_core::{Block, BookDocument, Report, Severity, check_bidi_isolation, has_persian};
use roxmltree::Node;
use std::{
    collections::{BTreeMap, BTreeSet},
    path::Path,
};

pub fn check_docx(path: &Path, book: Option<&BookDocument>) -> Result<Report, OoxmlError> {
    if let Some(book) = book {
        book.validate()?;
    }
    let mut package = Package::open(path)?;
    let main = package.main_part()?;
    let types = package.content_types()?;
    for part in package
        .names()
        .filter(|n| opc::xml_part(n))
        .map(str::to_owned)
        .collect::<Vec<_>>()
    {
        package.xml(&part)?;
        if part != "[Content_Types].xml" && !types.contains_key(&part) {
            return Err(OoxmlError::Package(format!("undeclared XML part {part}")));
        }
        if let Some(owner) = rel_owner(&part) {
            package.relationships(&owner)?;
        }
    }
    let xml = package.xml(&main)?;
    let doc = opc::parse(&xml, &main)?;
    let body = body(doc.root_element())?;
    let rels = package.relationships(&main)?;
    let mut report = Report::default();
    let mut seen_images = Vec::new();
    verify_layout(body, &mut report, &main);
    check_styles(&mut package, &rels, &mut report)?;
    let bookmarks: BTreeSet<&str> = body
        .descendants()
        .filter(|n| n.has_tag_name((W, "bookmarkStart")))
        .filter_map(|n| n.attribute((W, "name")))
        .collect();
    verify_links(body, &rels, &bookmarks, &mut report);
    inspect_media(&mut package, &rels, &types, body, &mut seen_images)?;
    let refs = opc::footnote_references(body);
    let mut defined = BTreeSet::new();
    if let Some(part) = related(&rels, "footnotes")? {
        let notes = package.xml(part)?;
        let doc = opc::parse(&notes, part)?;
        let root = doc.root_element();
        if !root.has_tag_name((W, "footnotes")) {
            return Err(OoxmlError::Package("expected footnote root".into()));
        }
        let bodies = opc::footnote_bodies(root)?;
        defined.extend(bodies.keys().map(|s| (*s).to_owned()));
        let note_rels = package.relationships(part)?;
        for n in bodies.values().copied() {
            verify_layout(n, &mut report, part);
            verify_links(n, &note_rels, &bookmarks, &mut report);
            inspect_media(&mut package, &note_rels, &types, n, &mut seen_images)?;
            if !opc::footnote_references(n).is_empty() {
                problem(
                    &mut report,
                    "nested-footnote",
                    part,
                    "footnote contains another footnote reference",
                );
            }
        }
    }
    let mut seen_refs = BTreeSet::new();
    for id in refs {
        if !defined.contains(id) {
            problem(
                &mut report,
                "missing-footnote",
                id,
                "reference has no note body",
            );
        }
        if !seen_refs.insert(id) {
            problem(
                &mut report,
                "duplicate-footnote-reference",
                id,
                "note was referenced more than once",
            );
        }
    }
    if body
        .descendants()
        .any(|n| n.has_tag_name((W, "footnoteReference")) && required(n, Some(W), "id").is_err())
    {
        problem(
            &mut report,
            "invalid-footnote-id",
            &main,
            "note reference has no ID",
        );
    }
    for id in &defined {
        if !seen_refs.contains(id.as_str()) {
            problem(
                &mut report,
                "orphan-footnote",
                id,
                "note body has no reference",
            );
        }
    }
    for kind in ["header", "footer"] {
        for rel in rels
            .values()
            .filter(|r| r.kind == format!("{R}/{kind}") && !r.external)
        {
            let xml = package.xml(&rel.target)?;
            let doc = opc::parse(&xml, &rel.target)?;
            verify_layout(doc.root_element(), &mut report, &rel.target);
            verify_links(
                doc.root_element(),
                &package.relationships(&rel.target)?,
                &bookmarks,
                &mut report,
            );
        }
    }
    if let Some(book) = book {
        if book.page_geometry != page_geometry(body)? {
            problem(
                &mut report,
                "page-geometry",
                &main,
                "published geometry differs from Book IR",
            );
        }
        if book.footnotes.len() != defined.len() {
            problem(
                &mut report,
                "footnote-count",
                &main,
                "published note count differs from Book IR",
            );
        }
        compare_media(book, &mut seen_images, &mut report);
    }
    report.count("footnotes", defined.len());
    report.count("images", seen_images.len());
    Ok(report)
}

fn check_styles(
    pkg: &mut Package,
    rels: &BTreeMap<String, Relationship>,
    report: &mut Report,
) -> Result<(), OoxmlError> {
    if let Some(font) = related(rels, "fontTable")? {
        if let Err(e) = validate_fonts(pkg, font) {
            problem(report, "font-embedding", font, e.to_string());
        }
    } else {
        problem(
            report,
            "font-embedding",
            "fontTable",
            "missing font table relationship",
        );
    }
    if let Some(styles) = related(rels, "styles")? {
        let xml = pkg.xml(styles)?;
        let doc = opc::parse(&xml, styles)?;
        let fonts = child(doc.root_element(), W, "docDefaults")
            .and_then(|n| child(n, W, "rPrDefault"))
            .and_then(|n| child(n, W, "rPr"))
            .and_then(|n| child(n, W, "rFonts"));
        if !fonts.is_some_and(|f| {
            ["ascii", "hAnsi", "cs"]
                .into_iter()
                .all(|a| f.attribute((W, a)) == Some(PERSIAN_FONT))
        }) {
            problem(
                report,
                "font-default",
                styles,
                "default Persian face is not Vazirmatn",
            );
        }
    } else {
        problem(
            report,
            "font-default",
            "styles",
            "missing styles relationship",
        );
    }
    Ok(())
}

fn rel_owner(part: &str) -> Option<String> {
    let suffix = part.strip_suffix(".rels")?;
    if let Some(base) = suffix.strip_prefix("_rels/") {
        return Some(base.to_owned());
    }
    let (path, base) = suffix.rsplit_once("/_rels/")?;
    Some(format!("{path}/{base}"))
}

fn verify_layout(root: Node<'_, '_>, report: &mut Report, part: &str) {
    for n in root.descendants().filter(Node::is_element) {
        if n.has_tag_name((W, "p")) {
            let text = text_of(n);
            if has_persian(&text) {
                let properties = child(n, W, "pPr");
                if !properties.and_then(|n| child(n, W, "bidi")).is_some_and(on) {
                    problem(report, "no-rtl", part, "Persian paragraph lacks bidi");
                }
                if properties
                    .and_then(|n| child(n, W, "jc"))
                    .and_then(|n| n.attribute((W, "val")))
                    != Some("start")
                {
                    problem(
                        report,
                        "rtl-alignment",
                        part,
                        "Persian paragraph needs logical start alignment",
                    );
                }
            }
            for finding in check_bidi_isolation(&text) {
                problem(report, &finding.code, part, finding.detail);
            }
        }
        if n.has_tag_name((W, "r")) && has_persian(&text_of(n)) {
            let props = child(n, W, "rPr");
            if !props.and_then(|p| child(p, W, "rtl")).is_some_and(on) {
                problem(report, "no-rtl-run", part, "Persian run lacks rtl");
            }
            if props
                .and_then(|p| child(p, W, "rFonts"))
                .and_then(|f| f.attribute((W, "cs")))
                != Some(PERSIAN_FONT)
            {
                problem(
                    report,
                    "font-run",
                    part,
                    "Persian run lacks Vazirmatn complex-script font",
                );
            }
        }
        if n.has_tag_name((W, "sectPr")) && !child(n, W, "bidi").is_some_and(on) {
            problem(report, "no-section-rtl", part, "section lacks bidi");
        }
        if n.has_tag_name((W, "tbl"))
            && !child(n, W, "tblPr")
                .and_then(|p| child(p, W, "bidiVisual"))
                .is_some_and(on)
        {
            problem(report, "no-table-rtl", part, "table lacks bidiVisual");
        }
    }
}

fn on(n: Node<'_, '_>) -> bool {
    !matches!(n.attribute((W, "val")), Some("false" | "0" | "off"))
}

fn verify_links(
    root: Node<'_, '_>,
    rels: &BTreeMap<String, Relationship>,
    anchors: &BTreeSet<&str>,
    report: &mut Report,
) {
    for n in root
        .descendants()
        .filter(|n| n.has_tag_name((W, "hyperlink")))
    {
        if let Some(anchor) = n.attribute((W, "anchor")) {
            if !anchors.contains(anchor) {
                problem(
                    report,
                    "missing-bookmark",
                    anchor,
                    "internal link is unresolved",
                );
            }
        } else if !n
            .attribute((R, "id"))
            .and_then(|id| rels.get(id))
            .is_some_and(|r| {
                r.external
                    && r.kind == format!("{R}/hyperlink")
                    && crate::render::safe_hyperlink(&r.target)
            })
        {
            problem(
                report,
                "invalid-hyperlink",
                "link",
                "external hyperlink relationship is unresolved",
            );
        }
    }
}

type ImageSignature = (String, u32, u32, u64, u64);
fn inspect_media(
    pkg: &mut Package,
    rels: &BTreeMap<String, Relationship>,
    types: &BTreeMap<String, String>,
    root: Node<'_, '_>,
    out: &mut Vec<ImageSignature>,
) -> Result<(), OoxmlError> {
    for m in resolve_drawing(pkg, rels, types, root)? {
        out.push((m.sha256, m.width_px, m.height_px, m.width_emu, m.height_emu));
    }
    Ok(())
}
fn compare_media(book: &BookDocument, seen: &mut [ImageSignature], report: &mut Report) {
    fn collect(
        blocks: &[Block],
        assets: &BTreeMap<&str, &revayat_core::AssetRecord>,
        out: &mut Vec<ImageSignature>,
    ) {
        for block in blocks {
            match block {
                Block::Image {
                    asset_id,
                    width_emu,
                    height_emu,
                    ..
                } => {
                    if let Some(asset) = assets.get(asset_id.as_str()) {
                        out.push((
                            asset.sha256.clone(),
                            asset.width_px,
                            asset.height_px,
                            *width_emu,
                            *height_emu,
                        ));
                    }
                }
                Block::Table { rows, .. } => {
                    for cell in rows.iter().flat_map(|r| &r.cells) {
                        collect(&cell.blocks, assets, out);
                    }
                }
                _ => {}
            }
        }
    }
    let assets = book.assets.iter().map(|a| (a.id.as_str(), a)).collect();
    let mut expected = Vec::new();
    for chapter in &book.chapters {
        collect(&chapter.blocks, &assets, &mut expected);
    }
    for note in &book.footnotes {
        collect(&note.blocks, &assets, &mut expected);
    }
    expected.sort();
    seen.sort();
    if seen != expected {
        problem(
            report,
            "image-integrity",
            "media",
            "embedded image bytes, pixel dimensions, drawing geometry, or occurrences differ from Book IR",
        );
    }
}

fn problem(r: &mut Report, code: &str, unit: &str, detail: impl AsRef<str>) {
    r.add(Severity::Error, code, unit, detail);
}

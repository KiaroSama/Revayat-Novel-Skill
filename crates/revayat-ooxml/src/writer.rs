use crate::{
    OoxmlError,
    fonts::{self, FONT_RUN},
    media::image_info,
    opc::{self, A, CT, MAIN_TYPE, MAX_PART, PR, R, W, WP, escape},
    render::{Relationships, Renderer, geometry, paragraph},
};
use revayat_core::{BookDocument, sha256_bytes};
use serde::Serialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt::Write as _,
    fs::{self, File},
    io::Read,
    path::{Path, PathBuf},
};
use zip::ZipWriter;

#[derive(Debug, Clone)]
pub struct BuildOptions {
    pub assets_dir: PathBuf,
    pub toc: bool,
    pub page_numbers: bool,
    pub header: Option<String>,
    pub footer: Option<String>,
}
impl Default for BuildOptions {
    fn default() -> Self {
        Self {
            assets_dir: "assets".into(),
            toc: true,
            page_numbers: true,
            header: None,
            footer: None,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct BuildReport {
    pub path: PathBuf,
    pub sha256: String,
    pub images: usize,
    pub footnotes: usize,
}

const CORE: &str = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties";
const DC: &str = "http://purl.org/dc/elements/1.1/";
const WORD: &str = "application/vnd.openxmlformats-officedocument.wordprocessingml";

pub fn build_docx(
    book: &BookDocument,
    out_path: &Path,
    options: &BuildOptions,
) -> Result<BuildReport, OoxmlError> {
    book.validate()?;
    let parent = out_path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)?;
    let mut tmp = tempfile::NamedTempFile::new_in(parent)?;
    let mut zip = ZipWriter::new(tmp.as_file_mut());
    let mut types = BTreeMap::new();
    let mut targets = BTreeMap::new();
    let mut seen = BTreeSet::new();
    let mut total = 0;
    for asset in &book.assets {
        if !revayat_core::is_safe_filename(&asset.rel_path) {
            return Err(OoxmlError::Image("unsafe asset path".into()));
        }
        let path = options.assets_dir.join(&asset.rel_path);
        let meta = fs::symlink_metadata(&path)?;
        if !meta.is_file() || meta.len() > MAX_PART {
            return Err(OoxmlError::Image(format!(
                "invalid input asset {}",
                asset.id
            )));
        }
        let mut bytes = Vec::new();
        File::open(path)?
            .take(MAX_PART + 1)
            .read_to_end(&mut bytes)?;
        total += bytes.len() as u64;
        if total > 400 * 1024 * 1024 {
            return Err(OoxmlError::Limit("output assets".into()));
        }
        let (mime, _, width, height) = image_info(&bytes)?;
        if sha256_bytes(&bytes) != asset.sha256
            || (mime, width, height) != (asset.media_type.as_str(), asset.width_px, asset.height_px)
        {
            return Err(OoxmlError::Image(format!(
                "asset {} differs from Book IR",
                asset.id
            )));
        }
        let name = format!("word/media/{}", asset.rel_path);
        if !seen.insert(name.to_ascii_lowercase()) {
            return Err(OoxmlError::Image(format!(
                "duplicate asset name {}",
                asset.rel_path
            )));
        }
        opc::write_part(&mut zip, &name, &bytes)?;
        types.insert(name, mime.to_owned());
        targets.insert(asset.id.as_str(), format!("media/{}", asset.rel_path));
    }
    let note_ids = BTreeMap::from_iter(
        book.footnotes
            .iter()
            .enumerate()
            .map(|(i, n)| (n.id.as_str(), i + 1)),
    );
    let mut writer = Renderer::new(&targets, &note_ids);
    for chapter in &book.chapters {
        writer.set_anchors(&chapter.blocks);
    }
    let mut main = Relationships::default();
    main.add("styles", "styles.xml", false);
    main.add("fontTable", "fontTable.xml", false);
    let mut headers = String::new();
    if let Some(text) = &options.header {
        let id = main.add("header", "header1.xml", false);
        headers.push_str(&format!(
            r#"<w:headerReference w:type="default" r:id="{id}"/>"#
        ));
        xml_part(
            &mut zip,
            &mut types,
            "word/header1.xml",
            "header",
            furniture("hdr", text, false),
        )?;
    }
    if options.footer.is_some() || options.page_numbers {
        let id = main.add("footer", "footer1.xml", false);
        headers.push_str(&format!(
            r#"<w:footerReference w:type="default" r:id="{id}"/>"#
        ));
        xml_part(
            &mut zip,
            &mut types,
            "word/footer1.xml",
            "footer",
            furniture(
                "ftr",
                options.footer.as_deref().unwrap_or_default(),
                options.page_numbers,
            ),
        )?;
    }
    let mut body = String::new();
    if options.toc {
        body.push_str(&toc(book));
    }
    for chapter in &book.chapters {
        writer.blocks(&mut body, &chapter.blocks, &mut main)?;
    }
    body.push_str(&geometry(&book.page_geometry, &headers));
    if !book.footnotes.is_empty() {
        main.add("footnotes", "footnotes.xml", false);
        let mut notes = Relationships::default();
        let mut text = format!("<w:footnotes {}>", namespaces());
        text.push_str(r#"<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote><w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>"#);
        for (i, note) in book.footnotes.iter().enumerate() {
            text.push_str(&format!(r#"<w:footnote w:id="{}"><w:p><w:pPr><w:bidi/><w:jc w:val="start"/></w:pPr><w:r><w:rPr>{FONT_RUN}<w:rtl/></w:rPr><w:footnoteRef/></w:r></w:p>"#,i+1));
            writer.blocks(&mut text, &note.blocks, &mut notes)?;
            text.push_str("</w:footnote>");
        }
        text.push_str("</w:footnotes>");
        xml_part(
            &mut zip,
            &mut types,
            "word/footnotes.xml",
            "footnotes",
            text,
        )?;
        if !notes.is_empty() {
            opc::write_part(&mut zip, "word/_rels/footnotes.xml.rels", notes.xml())?;
        }
    }
    let document = format!(
        "<w:document {}><w:body>{body}</w:body></w:document>",
        namespaces()
    );
    opc::parse(&document, "word/document.xml")?;
    xml_part(
        &mut zip,
        &mut types,
        "word/document.xml",
        "document.main",
        document,
    )?;
    xml_part(&mut zip, &mut types, "word/styles.xml", "styles", styles())?;
    for (name, bytes) in fonts::font_parts() {
        if name.ends_with(".ttf") {
            types.insert(name.clone(), fonts::TTF_CONTENT_TYPE.to_owned());
        }
        if name == "word/fontTable.xml" {
            types.insert(name.clone(), format!("{WORD}.fontTable+xml"));
        }
        opc::write_part(&mut zip, &name, bytes)?;
    }
    let core = format!(
        r#"<cp:coreProperties xmlns:cp="{CORE}" xmlns:dc="{DC}"><dc:title>{}</dc:title><dc:language>{}</dc:language></cp:coreProperties>"#,
        escape(&book.title),
        escape(&book.language)
    );
    opc::parse(&core, "docProps/core.xml")?;
    opc::write_part(&mut zip, "docProps/core.xml", core)?;
    types.insert(
        "docProps/core.xml".into(),
        "application/vnd.openxmlformats-package.core-properties+xml".into(),
    );
    opc::write_part(&mut zip, "word/_rels/document.xml.rels", main.xml())?;
    opc::write_part(
        &mut zip,
        "_rels/.rels",
        format!(
            r#"<Relationships xmlns="{PR}"><Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="{PR}/metadata/core-properties" Target="docProps/core.xml"/></Relationships>"#
        ),
    )?;
    let mut manifest = format!(
        r#"<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>"#
    );
    for (name, mime) in types {
        manifest.push_str(&format!(
            r#"<Override PartName="/{}" ContentType="{}"/>"#,
            escape(&name),
            escape(&mime)
        ));
    }
    manifest.push_str("</Types>");
    opc::write_part(&mut zip, "[Content_Types].xml", manifest)?;
    zip.finish()?;
    tmp.as_file().sync_all()?;
    let sha256 = revayat_core::sha256_file(tmp.path())?;
    tmp.persist(out_path)?;
    Ok(BuildReport {
        path: out_path.to_owned(),
        sha256,
        images: book.assets.len(),
        footnotes: book.footnotes.len(),
    })
}

fn xml_part<T: std::io::Write + std::io::Seek>(
    zip: &mut ZipWriter<T>,
    types: &mut BTreeMap<String, String>,
    name: &str,
    kind: &str,
    body: String,
) -> Result<(), OoxmlError> {
    let mime = if kind == "document.main" {
        MAIN_TYPE.to_owned()
    } else {
        format!("{WORD}.{kind}+xml")
    };
    types.insert(name.to_owned(), mime);
    opc::parse(&body, name)?;
    opc::write_part(zip, name, body)
}

fn namespaces() -> String {
    format!(
        r#"xmlns:w="{W}" xmlns:r="{R}" xmlns:wp="{WP}" xmlns:a="{A}" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture""#
    )
}

fn styles() -> String {
    let mut out = format!(
        r#"<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr>{FONT_RUN}<w:sz w:val="23"/><w:szCs w:val="23"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:bidi/><w:jc w:val="start"/></w:pPr></w:pPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>"#
    );
    for i in 1..=9 {
        let _ = write!(
            out,
            r#"<w:style w:type="paragraph" w:styleId="Heading{i}"><w:name w:val="heading {i}"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:outlineLvl w:val="{}"/></w:pPr><w:rPr><w:b/><w:bCs/></w:rPr></w:style>"#,
            i - 1
        );
    }
    out.push_str("</w:styles>");
    out
}

fn furniture(tag: &str, text: &str, page: bool) -> String {
    let text = revayat_core::isolate_persian_line(text);
    let mut payload = format!(
        r#"<w:r><w:rPr>{FONT_RUN}<w:rtl/></w:rPr><w:t xml:space="preserve">{}</w:t></w:r>"#,
        escape(&text)
    );
    if page {
        payload.push_str(r#"<w:fldSimple w:instr=" PAGE "><w:r><w:rPr><w:rtl/></w:rPr><w:t>1</w:t></w:r></w:fldSimple>"#);
    }
    format!(
        "<w:{tag} {}>{}</w:{tag}>",
        namespaces(),
        paragraph(&payload, None)
    )
}

fn toc(book: &BookDocument) -> String {
    let mut content = String::from(
        r#"<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> TOC \o "1-3" \h \z \u </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>"#,
    );
    for block in book.chapters.iter().flat_map(|c| &c.blocks) {
        if let revayat_core::Block::Heading { id, level, runs } = block {
            let anchor = crate::render::bookmark(id);
            if *level <= 3 {
                let text = runs.iter().map(|r| r.text.as_str()).collect::<String>();
                let text = revayat_core::isolate_persian_line(&text);
                let _ = write!(
                    content,
                    r#"<w:hyperlink w:anchor="{anchor}"><w:r><w:rPr>{FONT_RUN}<w:rtl/></w:rPr><w:t xml:space="preserve">{}</w:t></w:r></w:hyperlink><w:r><w:br/></w:r>"#,
                    escape(&text)
                );
            }
        }
    }
    content.push_str(r#"<w:r><w:fldChar w:fldCharType="end"/></w:r>"#);
    paragraph(&content, Some("RevayatTOC"))
}

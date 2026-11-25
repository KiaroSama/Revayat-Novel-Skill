use std::{
    collections::{BTreeMap, HashSet},
    fs::File,
    io::Read,
    path::Path,
};

use revayat_core::{
    AssetRecord, BookDocument, Chapter, ChapterProvenance, Footnote, SourceFormat, sha256_bytes,
    sha256_file,
};
use roxmltree::Document;
use scraper::Html;
use zip::ZipArchive;

use crate::{
    IngestError,
    assets::{MAX_IMAGE_BYTES, MAX_TEXT_BYTES, image_block, source_title, store_image, uri_part},
    dom,
    error::{invalid, io},
};

const MAX_ENTRIES: usize = 20_000;
const MAX_ARCHIVE_BYTES: u64 = 2 * 1024 * 1024 * 1024;

fn archive_name(name: &str) -> Result<(), IngestError> {
    if name.is_empty()
        || name.trim_end_matches('/').split('/').any(|part| {
            matches!(part, "" | "." | "..")
                || part.contains(['\\', ':'])
                || part.chars().any(char::is_control)
        })
    {
        return Err(IngestError::UnsafePath(name.into()));
    }
    Ok(())
}

fn archive_path(base: &str, href: &str) -> Result<String, IngestError> {
    if href.is_empty() {
        return Ok(base.into());
    }
    if href.starts_with('/') || href.contains(['\\', ':', '?']) {
        return Err(IngestError::UnsafePath(href.into()));
    }
    let parent = base.rsplit_once('/').map_or("", |(parent, _)| parent);
    let mut parts: Vec<String> = parent
        .split('/')
        .filter(|p| !p.is_empty())
        .map(str::to_owned)
        .collect();
    for part in href.split('/') {
        match uri_part(part)?.as_str() {
            "." => {}
            ".." if parts.pop().is_some() => {}
            "" | ".." => return Err(IngestError::UnsafePath(href.into())),
            part => parts.push(part.into()),
        }
    }
    let name = parts.join("/");
    archive_name(&name)?;
    Ok(name)
}

fn open(path: &Path) -> Result<ZipArchive<File>, IngestError> {
    let file = File::open(path).map_err(|e| io("open", path, e))?;
    let metadata = file.metadata().map_err(|e| io("inspect", path, e))?;
    if !metadata.is_file() || metadata.len() > MAX_ARCHIVE_BYTES {
        return Err(IngestError::Limit("EPUB archive size"));
    }
    let mut zip = ZipArchive::new(file)?;
    if zip.len() > MAX_ENTRIES {
        return Err(IngestError::Limit("EPUB member count"));
    }
    let mut names = HashSet::new();
    let mut total = 0u64;
    for i in 0..zip.len() {
        let entry = zip.by_index_raw(i)?;
        let name = entry.name()?;
        archive_name(&name)?;
        if entry.is_symlink() || entry.enclosed_name().is_none() || !names.insert(name.to_string())
        {
            return Err(IngestError::UnsafePath(name.into_owned()));
        }
        total = total.saturating_add(entry.size());
        if total > MAX_ARCHIVE_BYTES
            || entry.size() > MAX_IMAGE_BYTES
            || (entry.size() >= 8 * 1024 * 1024
                && entry.size() > entry.compressed_size().saturating_mul(400))
        {
            return Err(IngestError::Limit("EPUB expansion"));
        }
    }
    Ok(zip)
}

fn member(zip: &mut ZipArchive<File>, name: &str, limit: u64) -> Result<Vec<u8>, IngestError> {
    let entry = zip.by_name(name)?;
    if entry.is_dir() || entry.size() > limit {
        return Err(IngestError::Limit("EPUB member size"));
    }
    let expected = entry.size();
    let mut bytes = Vec::new();
    entry
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| io("extract EPUB member", Path::new(name), e))?;
    if bytes.len() as u64 != expected {
        return Err(invalid("EPUB", "member size does not match ZIP directory"));
    }
    Ok(bytes)
}

fn xml_bytes(zip: &mut ZipArchive<File>, path: &str) -> Result<String, IngestError> {
    let bytes = member(zip, path, MAX_TEXT_BYTES)?;
    let (encoding, skip) =
        encoding_rs::Encoding::for_bom(&bytes).unwrap_or((encoding_rs::UTF_8, 0));
    let (value, malformed) = encoding.decode_without_bom_handling(&bytes[skip..]);
    if malformed {
        return Err(invalid("EPUB", "invalid XHTML/XML encoding"));
    }
    Ok(value.into_owned())
}

fn xml(text: &str) -> Result<Document<'_>, IngestError> {
    Ok(Document::parse_with_options(
        text,
        roxmltree::ParsingOptions {
            nodes_limit: 200_000,
            ..Default::default()
        },
    )?)
}

fn named<'a, 'input>(node: roxmltree::Node<'a, 'input>, name: &str) -> bool {
    node.is_element() && node.tag_name().name() == name
}

struct ManifestItem<'a> {
    path: String,
    media: &'a str,
    props: &'a str,
}

fn locator(base: &str, href: &str) -> Result<(String, String), IngestError> {
    let (path, fragment) = href.split_once('#').unwrap_or((href, ""));
    Ok((archive_path(base, path)?, uri_part(fragment)?))
}

fn toc(
    zip: &mut ZipArchive<File>,
    manifest: &BTreeMap<&str, ManifestItem<'_>>,
    ncx: Option<&str>,
) -> Result<BTreeMap<String, String>, IngestError> {
    let mut titles = BTreeMap::new();
    if let Some(nav) = manifest
        .values()
        .find(|item| item.props.split_ascii_whitespace().any(|p| p == "nav"))
    {
        let html = Html::parse_document(&xml_bytes(zip, &nav.path)?);
        for root in html
            .select(&dom::selector("nav")?)
            .filter(|&el| dom::has_role(el, &["toc"]))
        {
            for link in root.select(&dom::selector("a[href]")?) {
                if let Some(href) = link.attr("href").filter(|h| !h.contains(':')) {
                    let (path, _) = locator(&nav.path, href)?;
                    titles.entry(path).or_insert_with(|| dom::text(link));
                }
            }
        }
    }
    if let Some(ncx) = ncx.and_then(|id| manifest.get(id)).or_else(|| {
        manifest
            .values()
            .find(|item| item.media == "application/x-dtbncx+xml")
    }) {
        let raw = xml_bytes(zip, &ncx.path)?;
        for point in xml(&raw)?.descendants().filter(|&el| named(el, "navPoint")) {
            let label = point
                .children()
                .find(|&el| named(el, "navLabel"))
                .and_then(|label| label.children().find(|&el| named(el, "text")))
                .and_then(|t| t.text());
            let href = point
                .children()
                .find(|&el| named(el, "content"))
                .and_then(|el| el.attribute("src"));
            if let (Some(label), Some(href)) = (label, href) {
                let (path, _) = locator(&ncx.path, href)?;
                titles.entry(path).or_insert_with(|| label.trim().into());
            }
        }
    }
    Ok(titles)
}

fn allowed_link(link: &str) -> bool {
    if link.chars().any(char::is_control) || link.starts_with("//") {
        return false;
    }
    url::Url::parse(link).map_or(!link.contains(':'), |u| {
        matches!(u.scheme(), "http" | "https" | "mailto")
            && u.username().is_empty()
            && u.password().is_none()
    })
}

pub fn read_epub(path: &Path, assets_dir: &Path) -> Result<BookDocument, IngestError> {
    let mut zip = open(path)?;
    let container = xml_bytes(&mut zip, "META-INF/container.xml")?;
    let container = xml(&container)?;
    let opf_path = container
        .descendants()
        .find(|&el| named(el, "rootfile"))
        .and_then(|node| node.attribute("full-path"))
        .ok_or_else(|| invalid("EPUB", "container has no rootfile"))?;
    archive_name(opf_path)?;
    let opf = xml_bytes(&mut zip, opf_path)?;
    let package = xml(&opf)?;
    let metadata = |tag| {
        package
            .descendants()
            .find(|&el| named(el, tag))
            .and_then(|el| el.text())
            .map(str::trim)
            .filter(|v| !v.is_empty())
    };
    let mut book = BookDocument::new(
        SourceFormat::Epub,
        sha256_file(path)?,
        metadata("title").map_or_else(|| source_title(path), str::to_owned),
        metadata("language").unwrap_or("und"),
    );
    let mut manifest = BTreeMap::new();
    for item in package
        .descendants()
        .filter(|&el| named(el, "item") && el.parent().is_some_and(|p| named(p, "manifest")))
    {
        let id = item
            .attribute("id")
            .ok_or_else(|| invalid("EPUB", "manifest entry has no ID"))?;
        let href = item
            .attribute("href")
            .ok_or_else(|| invalid("EPUB", "manifest entry has no href"))?;
        let (path, _) = locator(opf_path, href)?;
        let entry = ManifestItem {
            path,
            media: item.attribute("media-type").unwrap_or(""),
            props: item.attribute("properties").unwrap_or(""),
        };
        if manifest.insert(id, entry).is_some() {
            return Err(invalid("EPUB", "duplicate manifest ID"));
        }
    }
    let spine = package
        .descendants()
        .find(|&el| named(el, "spine"))
        .ok_or_else(|| invalid("EPUB", "missing spine"))?;
    let titles = toc(&mut zip, &manifest, spine.attribute("toc"))?;
    let mut paths = Vec::new();
    let mut seen = HashSet::new();
    for item in spine.children().filter(|&el| named(el, "itemref")) {
        let entry = item
            .attribute("idref")
            .and_then(|id| manifest.get(id))
            .ok_or_else(|| invalid("EPUB", "spine refers to an unknown manifest item"))?;
        if !matches!(entry.media, "application/xhtml+xml" | "text/html") {
            return Err(invalid("EPUB", "unsupported spine media type"));
        }
        seen.insert(entry.path.as_str());
        paths.push((entry.path.as_str(), true));
    }
    if paths.is_empty() {
        return Err(invalid("EPUB", "empty spine"));
    }
    paths.extend(
        manifest
            .values()
            .filter(|item| {
                matches!(item.media, "application/xhtml+xml" | "text/html")
                    && !seen.contains(item.path.as_str())
            })
            .map(|item| (item.path.as_str(), false)),
    );
    let mut assets = BTreeMap::new();
    let mut notes = BTreeMap::new();
    let body = dom::selector("body")?;
    let identified = dom::selector("[id]")?;
    let links = dom::selector("a")?;
    let mut targets: BTreeMap<String, HashSet<String>> = BTreeMap::new();
    for &(chapter_path, in_spine) in &paths {
        if !in_spine {
            continue;
        }
        let html = Html::parse_document(&xml_bytes(&mut zip, chapter_path)?);
        for link in html.select(&links).filter(|&el| dom::is_note_ref(el)) {
            let href = link
                .attr("href")
                .ok_or_else(|| invalid("EPUB", "note reference has no target"))?;
            let (path, fragment) = locator(chapter_path, href)?;
            if fragment.is_empty() {
                return Err(invalid("EPUB", "note reference has no fragment"));
            }
            targets.entry(path).or_default().insert(fragment);
        }
    }
    let empty = HashSet::new();
    for (index, (chapter_path, in_spine)) in paths.into_iter().enumerate() {
        let raw = xml_bytes(&mut zip, chapter_path)?;
        let html = Html::parse_document(&raw);
        let mut image = |src: &str, alt: &str, id| {
            let (image_path, _) = locator(chapter_path, src)?;
            let bytes = member(&mut zip, &image_path, MAX_IMAGE_BYTES)?;
            let asset = store_image(assets_dir, &mut assets, &bytes, true)?;
            Ok(Some(image_block(id, asset, alt)))
        };
        let chapter_id = format!("ch{:04}", index + 1);
        let note_ids = targets.get(chapter_path).unwrap_or(&empty);
        for (i, note) in html.select(&identified).enumerate() {
            let Some(id) = note.attr("id") else {
                continue;
            };
            if !dom::is_note(note) && !note_ids.contains(id) {
                continue;
            }
            let blocks = dom::blocks(note, &format!("{chapter_id}-n{i}"), false, &mut image)?;
            if notes
                .insert((chapter_path.to_owned(), id.to_owned()), (blocks, false))
                .is_some()
            {
                return Err(invalid("EPUB", "ambiguous note target"));
            }
        }
        if in_spine {
            let root = html
                .select(&body)
                .next()
                .unwrap_or_else(|| html.root_element());
            let blocks =
                dom::blocks_excluding_notes(root, &chapter_id, false, note_ids, &mut image)?;
            if blocks.is_empty() {
                continue;
            }
            let title = titles
                .get(chapter_path)
                .filter(|v| !v.is_empty())
                .cloned()
                .or_else(|| dom::first_heading(&blocks))
                .unwrap_or_else(|| format!("Chapter {}", index + 1));
            book.chapters.push(Chapter {
                id: chapter_id,
                title,
                index: (index + 1) as u32,
                blocks,
                provenance: Some(ChapterProvenance {
                    source_sha256: sha256_bytes(raw.as_bytes()),
                    source_locator: Some(chapter_path.into()),
                    pages: vec![],
                }),
            });
        }
    }
    for chapter in &mut book.chapters {
        let chapter_path = chapter
            .provenance
            .as_ref()
            .and_then(|p| p.source_locator.as_deref())
            .unwrap_or("");
        dom::visit_runs(&mut chapter.blocks, &mut |anchor, run| {
            let required = run.footnote_ref.take().is_some();
            let Some(link) = run.hyperlink.as_deref() else {
                return if required {
                    Err(invalid("EPUB", "note reference has no link"))
                } else {
                    Ok(())
                };
            };
            if !allowed_link(link) {
                run.hyperlink = None;
                return Ok(());
            }
            if link.contains('#') && url::Url::parse(link).is_err() {
                let key = locator(chapter_path, link)?;
                if let Some((body, referenced)) = notes.get_mut(&key) {
                    *referenced = true;
                    let mut blocks = body.clone();
                    if blocks.is_empty() {
                        return Err(invalid("EPUB", "referenced note is empty"));
                    }
                    dom::visit_runs(&mut blocks, &mut |_, r| {
                        if r.footnote_ref.take().is_some() {
                            return Err(invalid("EPUB", "nested note reference"));
                        }
                        if r.hyperlink
                            .as_deref()
                            .is_some_and(|link| !allowed_link(link))
                        {
                            r.hyperlink = None;
                        }
                        Ok(())
                    })?;
                    let number = (book.footnotes.len() + 1) as u32;
                    let id = format!("fn{number:04}");
                    dom::namespace_blocks(&mut blocks, &format!("{id}-"));
                    run.footnote_ref = Some(id.clone());
                    run.hyperlink = None;
                    book.footnotes.push(Footnote {
                        id,
                        number,
                        blocks,
                        origin_chapter: Some(chapter.id.clone()),
                        anchor_block: Some(anchor.into()),
                        ..Footnote::default()
                    });
                } else if required {
                    return Err(invalid("EPUB", "note target is missing"));
                }
            }
            Ok(())
        })?;
    }
    notes.retain(|_, (_, referenced)| !*referenced);
    // Orphan notes still carry source text; keep them as an appendix instead of dropping them.
    if !notes.is_empty() {
        book.chapters.push(Chapter {
            id: "unreferenced-notes".into(),
            title: "Unreferenced notes".into(),
            index: (book.chapters.len() + 1) as u32,
            blocks: notes.into_values().flat_map(|(blocks, _)| blocks).collect(),
            provenance: None,
        });
    }
    book.assets = assets.into_values().collect::<Vec<AssetRecord>>();
    if book.chapters.is_empty() {
        return Err(invalid("EPUB", "no readable content"));
    }
    book.validate()?;
    Ok(book)
}

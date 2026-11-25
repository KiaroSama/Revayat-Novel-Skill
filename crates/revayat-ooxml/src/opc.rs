use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::{Read, Seek, Write},
    path::Path,
};

use roxmltree::{Document, Node};
use zip::{CompressionMethod, ZipArchive, ZipWriter, write::SimpleFileOptions};

use crate::OoxmlError;

pub(crate) const W: &str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
pub(crate) const R: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
pub(crate) const A: &str = "http://schemas.openxmlformats.org/drawingml/2006/main";
pub(crate) const WP: &str =
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing";
pub(crate) const CT: &str = "http://schemas.openxmlformats.org/package/2006/content-types";
pub(crate) const PR: &str = "http://schemas.openxmlformats.org/package/2006/relationships";
pub(crate) const MAIN_TYPE: &str =
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml";
pub(crate) const MAX_PART: u64 = 128 * 1024 * 1024;
const MAX_TOTAL: u64 = 512 * 1024 * 1024;
const MAX_ENTRIES: usize = 16_384;
const MAX_XML: u64 = 32 * 1024 * 1024;

pub fn qname(prefix: &str, tag: &str) -> Result<String, OoxmlError> {
    let ns = match prefix {
        "w" => W,
        "r" => R,
        "a" => A,
        "wp" => WP,
        "ct" => CT,
        "pr" => PR,
        _ => return Err(OoxmlError::Package(format!("unknown namespace {prefix}"))),
    };
    Ok(format!("{{{ns}}}{tag}"))
}

pub(crate) fn safe_part(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 1024
        && !name.contains(['\\', ':', '%', '?', '#'])
        && name.split('/').all(|c| {
            c != "."
                && c != ".."
                && revayat_core::is_safe_filename(c)
                && !c.chars().any(char::is_control)
        })
}

pub struct Package {
    zip: ZipArchive<File>,
    names: BTreeSet<String>,
}

#[derive(Debug, Clone)]
pub struct Relationship {
    pub kind: String,
    pub target: String,
    pub external: bool,
}

impl Package {
    pub fn open(path: &Path) -> Result<Self, OoxmlError> {
        let file = File::open(path)?;
        if file.metadata()?.len() > MAX_TOTAL {
            return Err(OoxmlError::Limit("compressed package size".into()));
        }
        let mut zip = ZipArchive::new(file)?;
        if zip.len() > MAX_ENTRIES {
            return Err(OoxmlError::Limit("package entry count".into()));
        }
        let mut names = BTreeSet::new();
        let mut seen = BTreeSet::new();
        let mut total = 0_u64;
        for i in 0..zip.len() {
            let entry = zip.by_index(i)?;
            let filename = entry.name()?;
            let name = filename.trim_end_matches('/');
            if !safe_part(name) || entry.is_symlink() || !seen.insert(name.to_ascii_lowercase()) {
                return Err(OoxmlError::Package(format!(
                    "unsafe or duplicate part {name}"
                )));
            }
            let size = entry.size();
            total = total.saturating_add(size);
            let limit = if xml_part(name) { MAX_XML } else { MAX_PART };
            if size > limit
                || total > MAX_TOTAL
                || (size > 1024 * 1024 && size > entry.compressed_size().saturating_mul(200))
            {
                return Err(OoxmlError::Limit(format!("uncompressed part {name}")));
            }
            if !entry.is_dir() {
                names.insert(name.to_owned());
            }
        }
        Ok(Self { zip, names })
    }

    pub fn names(&self) -> impl Iterator<Item = &str> {
        self.names.iter().map(String::as_str)
    }
    pub fn contains(&self, name: &str) -> bool {
        self.names.contains(name)
    }

    pub fn read(&mut self, name: &str) -> Result<Vec<u8>, OoxmlError> {
        if !self.contains(name) {
            return Err(OoxmlError::Package(format!("missing part {name}")));
        }
        let entry = self.zip.by_name(name)?;
        let size = entry.size();
        let mut bytes = Vec::new();
        entry.take(size + 1).read_to_end(&mut bytes)?;
        if bytes.len() as u64 != size {
            return Err(OoxmlError::Package(format!("size mismatch for {name}")));
        }
        Ok(bytes)
    }

    pub fn xml(&mut self, name: &str) -> Result<String, OoxmlError> {
        let bytes = self.read(name)?;
        if bytes.len() as u64 > MAX_XML {
            return Err(OoxmlError::Limit(format!("XML part {name}")));
        }
        let text = String::from_utf8(bytes).map_err(|e| xml_error(name, e.to_string()))?;
        parse(&text, name)?;
        Ok(text)
    }

    pub fn content_types(&mut self) -> Result<BTreeMap<String, String>, OoxmlError> {
        let part = "[Content_Types].xml";
        let source = self.xml(part)?;
        let doc = parse(&source, part)?;
        let root = doc.root_element();
        if !root.has_tag_name((CT, "Types")) {
            return Err(xml_error(part, "expected Types"));
        }
        let mut defaults = BTreeMap::new();
        let mut overrides = BTreeMap::new();
        for n in root.children().filter(Node::is_element) {
            let kind = required(n, None, "ContentType")?.to_owned();
            let old = if n.has_tag_name((CT, "Default")) {
                defaults.insert(required(n, None, "Extension")?.to_ascii_lowercase(), kind)
            } else if n.has_tag_name((CT, "Override")) {
                let name = required(n, None, "PartName")?
                    .strip_prefix('/')
                    .filter(|p| safe_part(p))
                    .ok_or_else(|| xml_error(part, "invalid override name"))?;
                if !self.contains(name) {
                    return Err(xml_error(part, format!("missing overridden part {name}")));
                }
                overrides.insert(name.to_owned(), kind)
            } else {
                return Err(xml_error(part, "unknown content type declaration"));
            };
            if old.is_some() {
                return Err(xml_error(part, "duplicate content type declaration"));
            }
        }
        for name in self.names() {
            if let Some(kind) = name
                .rsplit_once('.')
                .and_then(|(_, ext)| defaults.get(&ext.to_ascii_lowercase()))
            {
                overrides
                    .entry(name.to_owned())
                    .or_insert_with(|| kind.clone());
            }
        }
        Ok(overrides)
    }

    pub fn relationships(
        &mut self,
        owner: &str,
    ) -> Result<BTreeMap<String, Relationship>, OoxmlError> {
        let name = if owner.is_empty() {
            "_rels/.rels".into()
        } else if let Some((dir, base)) = owner.rsplit_once('/') {
            format!("{dir}/_rels/{base}.rels")
        } else {
            format!("_rels/{owner}.rels")
        };
        if !self.contains(&name) {
            return Ok(BTreeMap::new());
        }
        let xml = self.xml(&name)?;
        let doc = parse(&xml, &name)?;
        let root = doc.root_element();
        if !root.has_tag_name((PR, "Relationships")) {
            return Err(xml_error(&name, "expected Relationships"));
        }
        let mut found = BTreeMap::new();
        for node in root.children().filter(Node::is_element) {
            if !node.has_tag_name((PR, "Relationship")) {
                return Err(xml_error(&name, "unexpected relationship record"));
            }
            let id = required(node, None, "Id")?;
            let kind = required(node, None, "Type")?;
            let target = required(node, None, "Target")?;
            let external = match node.attribute("TargetMode") {
                None | Some("Internal") => false,
                Some("External") => true,
                _ => return Err(xml_error(&name, "invalid relationship mode")),
            };
            let target = if external {
                target.to_owned()
            } else {
                resolve_target(owner, target)?
            };
            if !external && !self.contains(&target) {
                return Err(OoxmlError::Package(format!(
                    "missing relationship target {target}"
                )));
            }
            if found
                .insert(
                    id.to_owned(),
                    Relationship {
                        kind: kind.to_owned(),
                        target,
                        external,
                    },
                )
                .is_some()
            {
                return Err(xml_error(&name, "duplicate relationship ID"));
            }
        }
        Ok(found)
    }

    pub(crate) fn main_part(&mut self) -> Result<String, OoxmlError> {
        let parts = self.relationships("")?;
        let name = related(&parts, "officeDocument")?
            .ok_or_else(|| OoxmlError::Package("missing officeDocument relationship".into()))?;
        if self.content_types()?.get(name).map(String::as_str) != Some(MAIN_TYPE) {
            return Err(OoxmlError::Package("invalid main part content type".into()));
        }
        Ok(name.to_owned())
    }
}

pub(crate) fn related<'a>(
    rels: &'a BTreeMap<String, Relationship>,
    kind: &str,
) -> Result<Option<&'a str>, OoxmlError> {
    let expected = format!("{R}/{kind}");
    let mut parts = rels.values().filter(|r| r.kind == expected);
    let Some(rel) = parts.next() else {
        return Ok(None);
    };
    if rel.external || parts.next().is_some() {
        return Err(OoxmlError::Package(format!("invalid {kind} relationship")));
    }
    Ok(Some(&rel.target))
}

pub(crate) fn xml_part(name: &str) -> bool {
    name.ends_with(".xml") || name.ends_with(".rels")
}

fn resolve_target(owner: &str, target: &str) -> Result<String, OoxmlError> {
    let mut segments: Vec<&str> = if target.starts_with('/') {
        Vec::new()
    } else {
        owner
            .rsplit_once('/')
            .map_or_else(Vec::new, |(p, _)| p.split('/').collect())
    };
    for part in target.strip_prefix('/').unwrap_or(target).split('/') {
        match part {
            ".." if segments.pop().is_none() => {
                return Err(OoxmlError::Package(format!(
                    "target escapes package: {target}"
                )));
            }
            ".." | "." => {}
            p if safe_part(p) => segments.push(p),
            _ => {
                return Err(OoxmlError::Package(format!(
                    "invalid relationship target {target}"
                )));
            }
        }
    }
    if segments.is_empty() {
        return Err(OoxmlError::Package("empty relationship target".into()));
    }
    Ok(segments.join("/"))
}

pub(crate) fn parse<'a>(text: &'a str, part: &str) -> Result<Document<'a>, OoxmlError> {
    Document::parse_with_options(
        text,
        roxmltree::ParsingOptions {
            nodes_limit: 750_000,
            ..Default::default()
        },
    )
    .map_err(|e| xml_error(part, e.to_string()))
}

pub(crate) fn xml_error(part: &str, detail: impl Into<String>) -> OoxmlError {
    OoxmlError::Xml {
        part: part.into(),
        detail: detail.into(),
    }
}

pub(crate) fn child<'a, 'input>(
    n: Node<'a, 'input>,
    ns: &str,
    name: &str,
) -> Option<Node<'a, 'input>> {
    n.children().find(|c| c.has_tag_name((ns, name)))
}

pub(crate) fn required<'a>(
    n: Node<'a, '_>,
    ns: Option<&str>,
    key: &str,
) -> Result<&'a str, OoxmlError> {
    let value = ns.map_or_else(|| n.attribute(key), |ns| n.attribute((ns, key)));
    value
        .filter(|s| !s.is_empty())
        .ok_or_else(|| OoxmlError::Package(format!("missing {key} on {}", n.tag_name().name())))
}

pub fn text_of(node: Node<'_, '_>) -> String {
    node.descendants()
        .filter_map(|n| {
            if n.has_tag_name((W, "t")) {
                n.text()
            } else if n.has_tag_name((W, "tab")) {
                Some("\t")
            } else if n.has_tag_name((W, "br")) || n.has_tag_name((W, "cr")) {
                Some("\n")
            } else {
                None
            }
        })
        .collect()
}

pub fn footnote_references<'a>(node: Node<'a, '_>) -> Vec<&'a str> {
    node.descendants()
        .filter(|n| n.has_tag_name((W, "footnoteReference")))
        .filter_map(|n| n.attribute((W, "id")))
        .collect()
}

pub fn footnote_bodies<'a>(
    node: Node<'a, '_>,
) -> Result<BTreeMap<&'a str, Node<'a, 'a>>, OoxmlError> {
    let mut notes = BTreeMap::new();
    for n in node.children().filter(|n| {
        n.has_tag_name((W, "footnote")) && n.attribute((W, "type")).is_none_or(|t| t == "normal")
    }) {
        let id = required(n, Some(W), "id")?;
        if notes.insert(id, n).is_some() {
            return Err(OoxmlError::Package(format!("duplicate note {id}")));
        }
    }
    Ok(notes)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DrawingExtent<'a> {
    pub id: &'a str,
    pub width_emu: u64,
    pub height_emu: u64,
}

pub fn drawing_extents<'a>(node: Node<'a, '_>) -> Result<Vec<DrawingExtent<'a>>, OoxmlError> {
    node.descendants()
        .filter(|n| n.has_tag_name((WP, "inline")) || n.has_tag_name((WP, "anchor")))
        .map(|n| {
            let extent = child(n, WP, "extent")
                .ok_or_else(|| OoxmlError::Package("missing drawing extent".into()))?;
            let blip = n
                .descendants()
                .find(|b| b.has_tag_name((A, "blip")))
                .ok_or_else(|| OoxmlError::Unsupported("non-image drawing".into()))?;
            let measure = |key| {
                required(extent, None, key)?
                    .parse::<u64>()
                    .ok()
                    .filter(|v| *v > 0)
                    .ok_or_else(|| OoxmlError::Package(format!("invalid drawing {key}")))
            };
            Ok(DrawingExtent {
                id: required(blip, Some(R), "embed")?,
                width_emu: measure("cx")?,
                height_emu: measure("cy")?,
            })
        })
        .collect()
}

pub(crate) fn escape(text: &str) -> std::borrow::Cow<'_, str> {
    quick_xml::escape::escape(text)
}

pub(crate) fn write_part<Wtr: Write + Seek>(
    zip: &mut ZipWriter<Wtr>,
    name: &str,
    bytes: impl AsRef<[u8]>,
) -> Result<(), OoxmlError> {
    let bytes = bytes.as_ref();
    let limit = if xml_part(name) { MAX_XML } else { MAX_PART };
    if !safe_part(name) || bytes.len() as u64 > limit {
        return Err(OoxmlError::Limit(format!("output part {name}")));
    }
    zip.start_file(
        name,
        SimpleFileOptions::default().compression_method(CompressionMethod::Stored),
    )?;
    zip.write_all(bytes)?;
    Ok(())
}

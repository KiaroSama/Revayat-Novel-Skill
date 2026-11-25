use crate::{
    OoxmlError,
    opc::{PR, Package, R, W, child, escape, parse, required},
};

pub const PERSIAN_FONT: &str = "Vazirmatn";
pub(crate) const TTF_CONTENT_TYPE: &str = "application/x-font-ttf";
const FONT_TYPE: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font";
const FONTS: [(&str, &str, &[u8]); 4] = [
    (
        "Regular",
        "embedRegular",
        include_bytes!(
            "../../../skills/revayat-novel/assets/fonts/vazirmatn/Vazirmatn-Regular.ttf"
        ),
    ),
    (
        "Bold",
        "embedBold",
        include_bytes!("../../../skills/revayat-novel/assets/fonts/vazirmatn/Vazirmatn-Bold.ttf"),
    ),
    (
        "Medium",
        "embedRegular",
        include_bytes!("../../../skills/revayat-novel/assets/fonts/vazirmatn/Vazirmatn-Medium.ttf"),
    ),
    (
        "Light",
        "embedRegular",
        include_bytes!("../../../skills/revayat-novel/assets/fonts/vazirmatn/Vazirmatn-Light.ttf"),
    ),
];
pub(crate) const FONT_RUN: &str = r#"<w:rFonts w:ascii="Vazirmatn" w:hAnsi="Vazirmatn" w:cs="Vazirmatn" w:eastAsia="Vazirmatn"/>"#;

pub(crate) fn font_parts() -> Vec<(String, Vec<u8>)> {
    let mut parts = Vec::new();
    let mut table = format!(
        r#"<w:fonts xmlns:w="{W}" xmlns:r="{R}"><w:font w:name="{PERSIAN_FONT}"><w:family w:val="swiss"/>"#
    );
    let mut rels = format!(r#"<Relationships xmlns="{PR}">"#);
    for (index, (variant, tag, bytes)) in FONTS.iter().enumerate() {
        let file = format!("Vazirmatn-{variant}.ttf");
        let id = index + 1;
        if index >= 2 {
            table.push_str(&format!(
                r#"</w:font><w:font w:name="{PERSIAN_FONT} {variant}"><w:family w:val="swiss"/>"#
            ));
        }
        table.push_str(&format!(r#"<w:{tag} r:id="rId{id}"/>"#));
        rels.push_str(&format!(
            r#"<Relationship Id="rId{id}" Type="{FONT_TYPE}" Target="fonts/{file}"/>"#
        ));
        parts.push((format!("word/fonts/{file}"), bytes.to_vec()));
    }
    table.push_str("</w:font></w:fonts>");
    rels.push_str("</Relationships>");
    parts.push(("word/fontTable.xml".into(), table.into_bytes()));
    parts.push(("word/_rels/fontTable.xml.rels".into(), rels.into_bytes()));
    parts
}

pub(crate) fn validate_fonts(pkg: &mut Package, part: &str) -> Result<(), OoxmlError> {
    let types = pkg.content_types()?;
    let rels = pkg.relationships(part)?;
    let xml = pkg.xml(part)?;
    let doc = parse(&xml, part)?;
    for (i, (style, tag, expected)) in FONTS.iter().enumerate() {
        let family = if i < 2 {
            PERSIAN_FONT.to_owned()
        } else {
            format!("{PERSIAN_FONT} {style}")
        };
        let font = doc.root_element().children().find(|n| {
            n.has_tag_name((W, "font")) && n.attribute((W, "name")) == Some(family.as_str())
        });
        let embed = font.and_then(|n| child(n, W, tag)).ok_or_else(|| {
            OoxmlError::Package(format!(
                "missing {} {style} font embedding",
                escape(&family)
            ))
        })?;
        let id = required(embed, Some(R), "id")?;
        let rel = rels
            .get(id)
            .filter(|r| !r.external && r.kind == FONT_TYPE)
            .ok_or_else(|| OoxmlError::Package(format!("missing font relationship {id}")))?;
        if types.get(&rel.target).map(String::as_str) != Some(TTF_CONTENT_TYPE)
            || pkg.read(&rel.target)?.as_slice() != *expected
        {
            return Err(OoxmlError::Package(format!(
                "font bytes or content type differ for {style}"
            )));
        }
    }
    Ok(())
}

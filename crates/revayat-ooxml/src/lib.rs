#![forbid(unsafe_code)]

mod docqa;
mod error;
mod fonts;
mod media;
mod opc;
mod reader;
mod render;
mod table;
mod writer;

pub use docqa::check_docx;
pub use error::OoxmlError;
pub use fonts::PERSIAN_FONT;
pub use opc::{
    DrawingExtent, Package, Relationship, drawing_extents, footnote_bodies, footnote_references,
    qname, text_of,
};
pub use reader::read_docx;
pub use writer::{BuildOptions, BuildReport, build_docx};

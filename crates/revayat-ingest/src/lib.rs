#![forbid(unsafe_code)]

mod assets;
mod dom;
mod epub;
mod error;
mod ocr;
mod pdf;
mod scan_clean;
mod web;

pub use epub::read_epub;
pub use error::IngestError;
pub use ocr::{
    OcrPage, OcrToken, import_mineru, import_mineru_with_assets, locate_tool, parse_tesseract_tsv,
    run_tesseract_tsv,
};
pub use pdf::{PdfPageSummary, PdfSummary, inspect_pdf, read_pdf, render_pdf_pages};
pub use scan_clean::{ScanSurvey, clean, preview, survey};
pub use web::{FetchedPage, fetch_url, import_html_files, import_url, parse_html_chapter};

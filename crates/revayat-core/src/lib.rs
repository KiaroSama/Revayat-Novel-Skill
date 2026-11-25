#![forbid(unsafe_code)]

pub mod bidi;
pub mod bookir;
pub mod error;
pub mod findings;
pub mod layout;
pub mod markup;
pub mod naming;
pub mod notegraph;
pub mod provenance;
pub mod runlog;
pub mod runstate;
pub mod segments;
pub mod transaction;

pub use bidi::{
    PDI, RLI, RLM, check_bidi_isolation, has_persian, isolate_persian_line,
    starts_with_latin_in_persian_line, strip_bidi_controls,
};
pub use bookir::{
    AssetRecord, BOOK_SCHEMA_VERSION, Block, BookDocument, Chapter, Footnote, FootnoteOrigin,
    SourceFormat, TableCell, TableRow, TextRun,
};
pub use error::CoreError;
pub use findings::{Finding, Report, ReportSummary, Severity};
pub use layout::{
    Orientation, PageGeometry, TranslationEligibility, points_to_twips, translation_eligibility,
    twips_to_points,
};
pub use markup::{
    InlineSpan, emphasis_signature, escape_markup, footnote_refs, footnote_refs_with_local,
    normalise_source, parse_markup, plain_text, prose, render_markup, render_spans,
    rewrite_footnote_refs, verbatim_spans,
};
pub use naming::{
    BOOK_FILENAME, QA_REPORT_FILENAME, RUN_STATE_FILENAME, checkpoint_filename, flatten_in_prose,
    introduce_in_prose, introduction_count, introduction_spans, is_safe_filename, lock_path,
    output_filename, placement_spans, report_filename, safe_filename, work_dir_name,
};
pub use notegraph::check_note_graph;
pub use provenance::{
    ChapterProvenance, PageProvenance, PublishedKind, PublishedUnit, published_digest,
    published_units, sha256_bytes, sha256_file,
};
pub use runlog::{LogLevel, RunLog, format_log_line, log_stage};
pub use runstate::{
    MAX_REPAIR_ATTEMPTS, PageRecord, PageState, RepairAttempt, RepairEpisode, RepairLedger,
    RepairRefusal, RunState, SignoffState, StageRecord, StageStatus, TranslationStage,
};
pub use segments::{
    SegmentRecord, base_of, coverage_problems, cut_points, index_of, owners, rejoin, segment_id,
    segments_by_owner, source_separators, split_text,
};
pub use transaction::{BookLock, load_book, replace_book, save_book, update_book};

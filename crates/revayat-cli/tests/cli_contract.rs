use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::{Command, Stdio},
};

use revayat_core::{
    BOOK_SCHEMA_VERSION, Block, BookDocument, Chapter, PageGeometry, SourceFormat, TextRun,
    save_book,
};
use serde_json::Value;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_revayat-novel")
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

#[test]
fn doctor_json_reports_zero_python_bundled_fonts_and_all_five_manifests() {
    let root = repo_root();
    let out = Command::new(bin())
        .args(["doctor", "--json", "--root"])
        .arg(&root)
        .output();
    assert!(out.is_ok(), "failed to execute doctor: {out:?}");
    let out = match out {
        Ok(v) => v,
        Err(_) => return,
    };
    assert!(
        out.status.success(),
        "doctor failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    let report: Value = match serde_json::from_slice(&out.stdout) {
        Ok(v) => v,
        Err(e) => panic!("invalid doctor json: {e}"),
    };
    assert_eq!(report["version"], "3.0.0");
    assert_eq!(report["requires_python"], false);
    for font in [
        "Vazirmatn-Regular.ttf",
        "Vazirmatn-Bold.ttf",
        "Vazirmatn-Medium.ttf",
        "Vazirmatn-Light.ttf",
        "OFL.txt",
    ] {
        assert_eq!(report["fonts"][font], true, "missing bundled font {font}");
    }
    for manifest in ["claude", "codex", "cursor", "antigravity", "universal"] {
        assert_eq!(
            report["manifests"][manifest], true,
            "missing plugin manifest {manifest}"
        );
    }
}

#[test]
fn persian_lint_fixes_arabic_letters_and_zwnj_via_stdin() {
    let mut child = match Command::new(bin())
        .args(["persian-lint", "--fix"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => panic!("spawn persian-lint: {e}"),
    };
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all("كتاب ها مي رود".as_bytes());
    }
    let out = match child.wait_with_output() {
        Ok(o) => o,
        Err(e) => panic!("wait persian-lint: {e}"),
    };
    assert!(out.status.success());
    let fixed = String::from_utf8_lossy(&out.stdout);
    assert!(fixed.contains("کتاب\u{200c}ها"), "got: {fixed}");
    assert!(fixed.contains("می\u{200c}رود"), "got: {fixed}");
}

#[test]
fn eval_score_passes_reference_answers_and_rejects_wrong_answers() {
    let cases_path = repo_root().join("evaluation").join("cases.json");
    let raw = match fs::read_to_string(&cases_path) {
        Ok(s) => s,
        Err(e) => panic!("read cases.json: {e}"),
    };
    let parsed: Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(e) => panic!("parse cases.json: {e}"),
    };
    let cases = match parsed["cases"].as_array() {
        Some(a) => a,
        None => panic!("cases array missing"),
    };
    assert_eq!(cases.len(), 28, "expected 28 benchmark cases");

    let mut passing = serde_json::Map::new();
    let mut failing = serde_json::Map::new();
    for case in cases {
        let id = case["id"].as_str().unwrap_or_default().to_string();
        let good = case["accepts"]
            .as_array()
            .and_then(|a| a.first())
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let bad = case["fails"]
            .as_array()
            .and_then(|a| a.first())
            .and_then(|f| f["text"].as_str())
            .unwrap_or("wrong")
            .to_string();
        passing.insert(id.clone(), Value::String(good));
        failing.insert(id, Value::String(bad));
    }

    let tmp = match tempfile::tempdir() {
        Ok(t) => t,
        Err(e) => panic!("tempdir: {e}"),
    };
    let good_file = tmp.path().join("good.json");
    let bad_file = tmp.path().join("bad.json");
    let _ = fs::write(&good_file, Value::Object(passing).to_string());
    let _ = fs::write(&bad_file, Value::Object(failing).to_string());

    let ok_run = Command::new(bin())
        .args(["eval-score", "--answers"])
        .arg(&good_file)
        .output();
    assert!(ok_run.as_ref().is_ok_and(|o| o.status.success()));

    let fail_run = Command::new(bin())
        .args(["eval-score", "--answers"])
        .arg(&bad_file)
        .output();
    assert!(fail_run.as_ref().is_ok_and(|o| !o.status.success()));
}

#[test]
fn all_five_plugin_manifests_conform_to_v3_contract() {
    let root = repo_root();
    let files = [
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        ".codex-plugin/plugin.json",
        ".agents/plugins/marketplace.json",
        ".cursor-plugin/plugin.json",
        ".cursor-plugin/marketplace.json",
        ".antigravity-plugin/plugin.json",
        "plugin.json",
    ];
    for rel in files {
        let path = root.join(rel);
        let raw = match fs::read_to_string(&path) {
            Ok(s) => s,
            Err(e) => panic!("read {rel}: {e}"),
        };
        let doc: Value = match serde_json::from_str(&raw) {
            Ok(v) => v,
            Err(e) => panic!("parse {rel}: {e}"),
        };
        if let Some(version) = doc.get("version").and_then(Value::as_str) {
            assert_eq!(version, "3.0.0", "manifest {rel} version mismatch");
        }
        if let Some(plugins) = doc.get("plugins").and_then(Value::as_array) {
            assert!(!plugins.is_empty(), "marketplace {rel} has no plugins");
        }
    }
}

#[test]
fn end_to_end_build_and_doc_qa_with_embedded_vazirmatn_and_log_file() {
    let tmp = match tempfile::tempdir() {
        Ok(t) => t,
        Err(e) => panic!("tempdir: {e}"),
    };
    let work = tmp.path().join("work");
    let _ = fs::create_dir_all(work.join("assets"));
    let book = BookDocument {
        schema_version: BOOK_SCHEMA_VERSION,
        source_format: SourceFormat::Text,
        source_sha256: "a".repeat(64),
        title: "فصل آزمایشی".into(),
        language: "fa".into(),
        page_geometry: PageGeometry::default(),
        chapters: vec![Chapter {
            id: "ch01".into(),
            index: 1,
            title: "فصل اول".into(),
            blocks: vec![
                Block::Heading {
                    id: "h1".into(),
                    level: 1,
                    runs: vec![TextRun {
                        text: "فصل اول".into(),
                        ..TextRun::default()
                    }],
                },
                Block::Paragraph {
                    id: "p1".into(),
                    runs: vec![TextRun {
                        text: "این یک متن آزمایشی با فونت وزیرمتن است.".into(),
                        ..TextRun::default()
                    }],
                    style: None,
                    align: None,
                    rtl: true,
                },
            ],
            provenance: None,
        }],
        footnotes: Vec::new(),
        assets: Vec::new(),
    };
    let book_path = work.join("book.json");
    assert!(save_book(&book_path, &book).is_ok());

    let docx_out = tmp.path().join("book_fa.docx");
    let build = Command::new(bin())
        .arg("--log-dir")
        .arg(tmp.path())
        .args(["build"])
        .arg(&work)
        .args(["--out"])
        .arg(&docx_out)
        .output();
    assert!(
        build.as_ref().is_ok_and(|o| o.status.success()),
        "build failed: {build:?}"
    );
    assert!(docx_out.is_file());

    let qa = Command::new(bin())
        .args(["doc-qa", "check"])
        .arg(&docx_out)
        .args(["--work"])
        .arg(&work)
        .output();
    assert!(
        qa.as_ref().is_ok_and(|o| o.status.success()),
        "doc-qa failed: {qa:?}"
    );

    let logs_dir = tmp.path().join("logs");
    let has_utc_log = fs::read_dir(&logs_dir)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .any(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            name.starts_with("revayat-novel_") && name.ends_with("_UTC.log")
        });
    assert!(has_utc_log, "expected structured UTC operational log file");
}

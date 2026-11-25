use std::{
    collections::BTreeMap,
    env,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::render;

const FONT_DIR: &str = "assets/fonts/vazirmatn";
const FONTS: [&str; 5] = [
    "Vazirmatn-Regular.ttf",
    "Vazirmatn-Bold.ttf",
    "Vazirmatn-Medium.ttf",
    "Vazirmatn-Light.ttf",
    "OFL.txt",
];
const MANIFESTS: [(&str, &[&str]); 5] = [
    (
        "claude",
        &[
            ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
        ],
    ),
    (
        "codex",
        &[
            ".codex-plugin/plugin.json",
            ".agents/plugins/marketplace.json",
        ],
    ),
    (
        "cursor",
        &[
            ".cursor-plugin/plugin.json",
            ".cursor-plugin/marketplace.json",
        ],
    ),
    ("antigravity", &[".antigravity-plugin/plugin.json"]),
    ("universal", &["plugin.json"]),
];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DoctorReport {
    pub version: &'static str,
    pub build_target: &'static str,
    pub requires_python: bool,
    pub skill_root: Option<PathBuf>,
    pub fonts: BTreeMap<&'static str, bool>,
    pub manifests: BTreeMap<&'static str, bool>,
    pub optional_tools: BTreeMap<&'static str, Option<PathBuf>>,
}

fn resolve_root(root: &Path) -> (Option<PathBuf>, Option<PathBuf>) {
    let nested = root.join("skills/revayat-novel");
    if nested.is_dir() {
        return (Some(nested), Some(root.into()));
    }
    if root.join("SKILL.md").is_file() || root.join(FONT_DIR).is_dir() {
        let repo = root
            .parent()
            .and_then(Path::parent)
            .filter(|path| path.join("skills/revayat-novel").is_dir())
            .map(Path::to_owned);
        return (Some(root.into()), repo);
    }
    (None, Some(root.into()))
}

pub fn run_doctor(repo_or_skill_root: Option<&Path>) -> DoctorReport {
    let (skill_root, repo) = if let Some(root) = repo_or_skill_root {
        resolve_root(root)
    } else {
        env::current_exe()
            .ok()
            .and_then(|file| file.parent().map(Path::to_owned))
            .into_iter()
            .chain(env::current_dir().ok())
            .flat_map(|path| path.ancestors().map(Path::to_owned).collect::<Vec<_>>())
            .find_map(|root| {
                let resolved = resolve_root(&root);
                resolved.0.is_some().then_some(resolved)
            })
            .unwrap_or_default()
    };
    let fonts = FONTS
        .into_iter()
        .map(|name| {
            (
                name,
                skill_root
                    .as_ref()
                    .is_some_and(|root| root.join(FONT_DIR).join(name).is_file()),
            )
        })
        .collect();
    let manifests = MANIFESTS
        .into_iter()
        .map(|(name, paths)| {
            (
                name,
                repo.as_ref()
                    .is_some_and(|root| paths.iter().all(|path| root.join(path).is_file())),
            )
        })
        .collect();
    let tesseract = if cfg!(windows) {
        "tesseract.exe"
    } else {
        "tesseract"
    };
    let optional_tools = BTreeMap::from([
        ("tesseract", render::locate(&[tesseract])),
        ("libreoffice", render::libreoffice()),
        ("word", render::word()),
    ]);
    DoctorReport {
        version: env!("CARGO_PKG_VERSION"),
        build_target: env!("REVAYAT_BUILD_TARGET"),
        requires_python: false,
        skill_root,
        fonts,
        manifests,
        optional_tools,
    }
}

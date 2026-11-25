use revayat_installer::{
    Agent, InstallError, Options, Scope, Status, inspect, install, recover, run, run_with_bin,
};
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};

struct Fixture {
    _temp: tempfile::TempDir,
    base: PathBuf,
    source: PathBuf,
}

impl Fixture {
    fn new() -> Result<Self, Box<dyn Error>> {
        let temp = tempfile::tempdir()?;
        let root = temp.path().canonicalize()?;
        let base = root.join("project with spaces");
        let source = root.join("payload");
        fs::create_dir(&base)?;
        fs::create_dir_all(source.join("references"))?;
        fs::write(source.join("SKILL.md"), b"first")?;
        fs::write(
            source.join("references/multi-agent-workflow.md"),
            b"workflow",
        )?;
        Ok(Self {
            _temp: temp,
            base,
            source,
        })
    }

    fn options(&self, agent: Agent, scope: Scope, force: bool) -> Options<'_> {
        Options {
            base: &self.base,
            payload: &self.source,
            agent,
            scope,
            force,
        }
    }

    fn state(&self) -> PathBuf {
        self.base.join(".revayat-novel-installer")
    }
}

#[test]
fn all_installs_each_agent_in_its_scope() -> Result<(), Box<dyn Error>> {
    for scope in [Scope::User, Scope::Project] {
        let f = Fixture::new()?;
        let opencode = if scope == Scope::User {
            ".config/opencode"
        } else {
            ".opencode"
        };
        let homes = [
            ".claude", ".kiro", ".codex", ".cursor", ".cline", ".hermes", opencode, ".agents",
        ];
        for home in homes {
            fs::create_dir_all(f.base.join(home))?;
        }
        let original = b"\xef\xbb\xbfExisting instructions\r\n";
        fs::write(f.base.join("AGENTS.md"), original)?;
        let receipt = install(f.options(Agent::All, scope, false))?;
        assert!(receipt.committed);
        assert_eq!(receipt.destinations.len(), 8);
        for home in homes {
            assert_eq!(
                fs::read(f.base.join(home).join("skills/revayat-novel/SKILL.md"))?,
                b"first"
            );
        }
        let pointer = fs::read(f.base.join("AGENTS.md"))?;
        assert!(pointer.starts_with(original));
        let text = std::str::from_utf8(&pointer)?;
        assert!(text.contains(&format!("{opencode}/skills/revayat-novel/SKILL.md")));
        assert!(text.contains(".agents/skills/revayat-novel/SKILL.md"));
        let status = inspect(&f.base, Agent::All, scope);
        assert_eq!(
            (status.status, status.installed, status.participants),
            (Status::Committed, 8, 9)
        );
    }
    Ok(())
}

#[test]
fn all_does_not_create_missing_agent_homes() -> Result<(), Box<dyn Error>> {
    let f = Fixture::new()?;
    fs::create_dir(f.base.join(".kiro"))?;
    let receipt = install(f.options(Agent::All, Scope::Project, false))?;
    assert_eq!(receipt.destinations.len(), 1);
    assert!(!f.base.join(".claude").exists());
    Ok(())
}

#[test]
fn force_is_required_and_preserves_exact_previous_tree() -> Result<(), Box<dyn Error>> {
    let f = Fixture::new()?;
    let first = install(f.options(Agent::Claude, Scope::Project, false))?;
    let destination = &first.destinations[0];
    fs::write(destination.join("personal-notes.md"), b"private")?;
    fs::write(f.source.join("SKILL.md"), b"second")?;
    assert!(matches!(
        install(f.options(Agent::Claude, Scope::Project, false)),
        Err(InstallError::OverwriteRequired(_))
    ));
    assert_eq!(fs::read(destination.join("SKILL.md"))?, b"first");
    let updated = install(f.options(Agent::Claude, Scope::Project, true))?;
    let backup = &updated.backups[0];
    assert_eq!(fs::read(backup.join("personal-notes.md"))?, b"private");
    assert_eq!(fs::read(backup.join("SKILL.md"))?, b"first");
    assert_eq!(fs::read(destination.join("SKILL.md"))?, b"second");
    assert!(!destination.join("personal-notes.md").exists());
    assert_eq!(inspect(&f.base, Agent::Claude, Scope::Project).backups, 1);
    Ok(())
}

#[test]
fn native_binaries_and_vendored_fonts_are_included() -> Result<(), Box<dyn Error>> {
    let f = Fixture::new()?;
    let fonts = f.source.join("assets/fonts/vazirmatn");
    let vendored = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../skills/revayat-novel/assets/fonts/vazirmatn");
    fs::create_dir_all(&fonts)?;
    for name in ["Vazirmatn-Regular.ttf", "OFL.txt"] {
        fs::copy(vendored.join(name), fonts.join(name))?;
    }
    fs::create_dir_all(f.source.join("bin"))?;
    // Minimal format headers keep the fixture portable. No payload is ever executed.
    fs::write(f.source.join("bin/revayat-novel.exe"), b"MZ fixture PE")?;
    let elf = f.source.join("bin/revayat-novel");
    fs::write(&elf, b"\x7fELF fixture ELF")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&elf, fs::Permissions::from_mode(0o755))?;
    }
    fs::write(f.source.join("bin/local-secret.txt"), b"excluded")?;
    fs::write(f.source.join("references/.env"), b"excluded")?;
    let result = install(f.options(Agent::Cursor, Scope::Project, false))?;
    let dest = &result.destinations[0];
    for file in [
        "bin/revayat-novel.exe",
        "bin/revayat-novel",
        "assets/fonts/vazirmatn/Vazirmatn-Regular.ttf",
        "assets/fonts/vazirmatn/OFL.txt",
        "references/multi-agent-workflow.md",
    ] {
        assert_eq!(
            fs::read(dest.join(file))?,
            fs::read(f.source.join(file))?,
            "{file}"
        );
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(dest.join("bin/revayat-novel"))?
                .permissions()
                .mode()
                & 0o777,
            0o755
        );
    }
    assert!(!dest.join("bin/local-secret.txt").exists());
    assert!(!dest.join("references/.env").exists());
    Ok(())
}

#[test]
fn cli_stages_an_external_bin_directory_or_executable() -> Result<(), Box<dyn Error>> {
    for directory in [false, true] {
        let f = Fixture::new()?;
        let shipped = f._temp.path().canonicalize()?.join("published binaries");
        fs::create_dir(&shipped)?;
        let exe = shipped.join(if directory {
            "revayat-novel.exe"
        } else {
            "unpacked.exe"
        });
        fs::write(&exe, b"MZ fixture PE")?;
        let args = [
            "--scope".into(),
            "project".into(),
            "--agent".into(),
            "claude".into(),
        ];
        let source = if directory { &shipped } else { &exe };
        let output = run_with_bin(&args, &f.base, &f.base, &f.source, Some(source))?;
        assert_eq!(output.exit_code, 0);
        assert_eq!(
            fs::read(
                f.base
                    .join(".claude/skills/revayat-novel/bin/revayat-novel.exe")
            )?,
            b"MZ fixture PE"
        );
    }
    Ok(())
}

#[test]
fn force_does_not_override_a_git_tracked_pointer() -> Result<(), Box<dyn Error>> {
    let f = Fixture::new()?;
    for args in [&["init", "-q"][..], &["add", "--", "AGENTS.md"][..]] {
        if args[0] == "add" {
            fs::write(f.base.join("AGENTS.md"), b"tracked\n")?;
        }
        assert!(
            std::process::Command::new("git")
                .arg("-C")
                .arg(&f.base)
                .args(args)
                .status()?
                .success()
        );
    }
    assert!(matches!(
        install(f.options(Agent::Antigravity, Scope::Project, true)),
        Err(InstallError::Conflict(_))
    ));
    assert_eq!(fs::read(f.base.join("AGENTS.md"))?, b"tracked\n");
    assert!(!f.base.join(".agents").exists());
    Ok(())
}

#[test]
fn status_is_read_only_and_conflicts_on_modified_install() -> Result<(), Box<dyn Error>> {
    let f = Fixture::new()?;
    let status = run(
        &[
            "--status".into(),
            "--scope".into(),
            "project".into(),
            "--agent".into(),
            "claude".into(),
        ],
        &f.base,
        &f.base,
        &f.source,
    )?;
    assert_eq!(status.exit_code, 0);
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&status.json)?["status"],
        "absent"
    );
    assert!(!f.state().exists());
    let result = install(f.options(Agent::Claude, Scope::Project, false))?;
    fs::write(result.destinations[0].join("SKILL.md"), b"edited")?;
    assert_eq!(
        inspect(&f.base, Agent::Claude, Scope::Project).status,
        Status::Conflict
    );
    Ok(())
}

#[test]
fn status_uses_the_receipt_for_the_selected_agent() -> Result<(), Box<dyn Error>> {
    let f = Fixture::new()?;
    assert!(install(f.options(Agent::Claude, Scope::Project, false))?.committed);
    assert!(install(f.options(Agent::Kiro, Scope::Project, false))?.committed);
    fs::write(
        f.base.join(".claude/skills/revayat-novel/SKILL.md"),
        b"edited",
    )?;
    assert_eq!(
        inspect(&f.base, Agent::Claude, Scope::Project).status,
        Status::Conflict
    );
    assert_eq!(
        inspect(&f.base, Agent::Kiro, Scope::Project).status,
        Status::Committed
    );
    assert_eq!(
        inspect(&f.base, Agent::Codex, Scope::Project).status,
        Status::Absent
    );
    Ok(())
}

#[test]
fn recovery_validates_every_participant_before_rollback() -> Result<(), Box<dyn Error>> {
    let f = Fixture::new()?;
    fs::create_dir_all(f.base.join(".agents/skills/revayat-novel"))?;
    fs::write(f.base.join(".agents/skills/revayat-novel/SKILL.md"), b"old")?;
    fs::write(f.base.join("AGENTS.md"), b"original\n")?;
    let result = install(f.options(Agent::Antigravity, Scope::Project, true))?;
    let receipt = result.receipt.ok_or("missing receipt")?;
    let mut record: serde_json::Value = serde_json::from_slice(&fs::read(&receipt)?)?;
    record["phase"] = "prepared".into();
    fs::remove_file(receipt)?;
    fs::write(f.state().join("journal.json"), serde_json::to_vec(&record)?)?;
    assert_eq!(
        inspect(&f.base, Agent::Antigravity, Scope::Project).status,
        Status::Prepared
    );
    let pointer = fs::read(f.base.join("AGENTS.md"))?;
    fs::write(f.base.join("AGENTS.md"), b"unexpected change")?;
    assert!(matches!(recover(&f.base), Err(InstallError::Conflict(_))));
    assert_eq!(
        fs::read(f.base.join(".agents/skills/revayat-novel/SKILL.md"))?,
        b"first"
    );
    assert!(f.state().join("journal.json").is_file());
    fs::write(f.base.join("AGENTS.md"), pointer)?;
    let rolled_back = recover(&f.base)?.ok_or("expected recovery")?;
    assert!(!rolled_back.committed);
    assert!(!f.state().join("journal.json").exists());
    assert_eq!(
        fs::read(f.base.join(".agents/skills/revayat-novel/SKILL.md"))?,
        b"old"
    );
    assert_eq!(fs::read(f.base.join("AGENTS.md"))?, b"original\n");
    Ok(())
}

#[test]
fn committed_interruption_retains_backups_and_finishes_receipt() -> Result<(), Box<dyn Error>> {
    let f = Fixture::new()?;
    let initial = install(f.options(Agent::Kiro, Scope::Project, false))?;
    fs::write(f.source.join("SKILL.md"), b"new")?;
    let next = install(f.options(Agent::Kiro, Scope::Project, true))?;
    let receipt = next.receipt.ok_or("missing receipt")?;
    let journal = f.state().join("journal.json");
    fs::copy(&receipt, &journal)?;
    assert_eq!(
        inspect(&f.base, Agent::Kiro, Scope::Project).status,
        Status::CleanupPending
    );
    assert!(recover(&f.base)?.is_some_and(|r| r.committed && !r.cleanup_pending));
    assert!(!journal.exists());
    assert_eq!(fs::read(initial.destinations[0].join("SKILL.md"))?, b"new");
    assert_eq!(fs::read(next.backups[0].join("SKILL.md"))?, b"first");
    assert!(receipt.is_file());
    Ok(())
}

#[test]
fn refuses_a_symlink_or_reparse_ancestor() -> Result<(), Box<dyn Error>> {
    let f = Fixture::new()?;
    let outside = f._temp.path().canonicalize()?.join("outside");
    fs::create_dir(&outside)?;
    let link = f.base.join(".claude");
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, &link)?;
    #[cfg(windows)]
    {
        let status = std::process::Command::new("cmd.exe")
            .args(["/d", "/c", "mklink", "/J"])
            .arg(&link)
            .arg(&outside)
            .stdout(std::process::Stdio::null())
            .status()?;
        assert!(status.success(), "could not create junction");
    }
    let outcome = install(f.options(Agent::Claude, Scope::Project, true));
    #[cfg(windows)]
    fs::remove_dir(link)?;
    assert!(matches!(outcome, Err(InstallError::UnsafePath(_))));
    assert!(!outside.join("skills").exists());
    Ok(())
}

#[test]
fn installer_does_not_mutate_when_os_lock_is_held() -> Result<(), Box<dyn Error>> {
    let f = Fixture::new()?;
    assert!(recover(&f.base)?.is_none());
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(f.state().join("lock"))?;
    fs2::FileExt::try_lock_exclusive(&lock)?;
    assert!(matches!(
        install(f.options(Agent::Claude, Scope::Project, true)),
        Err(InstallError::Busy)
    ));
    assert!(!f.base.join(".claude").exists());
    drop(lock);
    assert!(install(f.options(Agent::Claude, Scope::Project, false))?.committed);
    Ok(())
}

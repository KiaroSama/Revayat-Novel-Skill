use std::{
    fs::{self, File, OpenOptions},
    io::{ErrorKind, Write},
    path::{Path, PathBuf},
};

use chrono::{DateTime, Utc};

use crate::{CoreError, error::io_error};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogLevel {
    Debug,
    Info,
    Warning,
    Error,
    Critical,
}

impl LogLevel {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Debug => "DEBUG",
            Self::Info => "INFO",
            Self::Warning => "WARNING",
            Self::Error => "ERROR",
            Self::Critical => "CRITICAL",
        }
    }
}

#[derive(Debug)]
pub struct RunLog {
    path: PathBuf,
    file: File,
    component: String,
}

impl RunLog {
    pub fn create(work_dir: impl AsRef<Path>, component: &str) -> Result<Self, CoreError> {
        if !crate::is_safe_filename(component)
            || component.len() > 64
            || !component
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
        {
            return Err(CoreError::InvalidLogComponent(component.into()));
        }
        let directory = work_dir.as_ref().join("logs");
        fs::create_dir_all(&directory)
            .map_err(|error| io_error("create log directory", &directory, error))?;
        let stamp = Utc::now().format("%Y-%m-%d_%H-%M-%S").to_string();
        for number in 0..1000 {
            let suffix = if number == 0 {
                String::new()
            } else {
                format!("_{number}")
            };
            let path = directory.join(format!("{component}_{stamp}_UTC{suffix}.log"));
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(file) => {
                    return Ok(Self {
                        path,
                        file,
                        component: component.into(),
                    });
                }
                Err(error) if error.kind() == ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(io_error("create log", &path, error)),
            }
        }
        Err(io_error(
            "create log",
            &directory,
            ErrorKind::AlreadyExists.into(),
        ))
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    // Only pass pre-sanitized operational labels, never raw source or reply text.
    pub fn log(&mut self, level: LogLevel, message: &str) -> Result<(), CoreError> {
        let line = format_log_line(Utc::now(), level, &self.component, message);
        self.file
            .write_all(line.as_bytes())
            .and_then(|()| self.file.flush())
            .map_err(|error| io_error("write log", &self.path, error))
    }

    pub fn stage(&mut self, name: &str) -> Result<(), CoreError> {
        self.log(LogLevel::Info, &format!("stage stage={}", log_stage(name)))
    }
}

pub fn log_stage(value: &str) -> &str {
    match value {
        "extract" | "web-import" | "clean-scan" | "ocr-sidecar" | "glossary" | "chunk"
        | "pages" | "render-qa" | "doc-qa" | "merge" | "meaning" | "fluency" | "falint" | "qa"
        | "build" | "doctor" => value,
        _ => "unknown",
    }
}

pub fn format_log_line(
    time: DateTime<Utc>,
    level: LogLevel,
    component: &str,
    message: &str,
) -> String {
    let component: String = component
        .chars()
        .map(|c| {
            if c.is_control() || matches!(c, '[' | ']') {
                '_'
            } else {
                c
            }
        })
        .collect();
    let message: String = message
        .chars()
        .map(|c| {
            if c.is_control() || crate::bidi::is_bidi_control(c) {
                ' '
            } else {
                c
            }
        })
        .collect();
    format!(
        "[{} UTC] [{}] [{}] {}\n",
        time.format("%Y-%m-%d %H:%M:%S"),
        level.as_str(),
        component,
        message
    )
}

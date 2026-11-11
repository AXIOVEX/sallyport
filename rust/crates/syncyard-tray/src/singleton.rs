//! Singleton by install path (FR-035-17) — the fix for the
//! field defect `laptop-two-tray-fight-2026-11-06`, in which
//! two tray copies (the canonical install and a build-folder
//! copy) ran at once on the laptop, fought over the fleet's
//! session, and generated the popup storm.
//!
//! The lock is keyed on the **canonical install path**: one
//! instance per install path, enforced by construction — a
//! second launch of the same install is refused and the
//! refusal is written to the install's log, so it says so in
//! its records. A copy installed at a *different* path is a
//! foreign instance: it is not blocked from its own path, but
//! it is detected through the shared heartbeat directory
//! (see `heartbeat::scan_heartbeats`) and surfaced in the
//! app's state rather than silently coexisting.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use fs2::FileExt;

/// Why singleton acquisition failed.
#[derive(Debug)]
pub enum SingletonError {
    /// An instance from this install path is already running;
    /// carries the canonical path.
    AlreadyRunning(PathBuf),
    Io(std::io::Error),
}

impl std::fmt::Display for SingletonError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SingletonError::AlreadyRunning(p) => write!(
                f,
                "a Syncyard tray instance from this install path is already running: {}",
                p.display()
            ),
            SingletonError::Io(e) => write!(f, "singleton lock error: {e}"),
        }
    }
}

impl std::error::Error for SingletonError {}

/// Whether a `try_lock_exclusive` error means another instance
/// already holds the lock, rather than a real I/O failure.
/// Same Windows defect as the agent singleton: `LockFileEx`
/// (via fs2) reports contention as `ERROR_LOCK_VIOLATION`
/// (raw OS error 33), whose `ErrorKind` is not `WouldBlock`,
/// so a WouldBlock-only check misreports a second instance
/// on Windows as a generic I/O error instead of the named
/// `AlreadyRunning` refusal.
fn is_lock_contention(e: &std::io::Error) -> bool {
    // The cfg gate keeps the Windows literal (33) from ever
    // matching a Unix raw error, where 33 means something else.
    e.kind() == std::io::ErrorKind::WouldBlock || (cfg!(windows) && e.raw_os_error() == Some(33))
}

/// The held per-install-path lock. Dropping it releases the
/// path for a later launch.
pub struct TrayInstance {
    _lock_file: File,
    /// The canonical install path this instance owns.
    pub install_path: PathBuf,
}

/// The lock file inside the install directory.
pub fn lock_path(install_path: &Path) -> PathBuf {
    install_path.join("tray-instance.lock")
}

/// The refusal log inside the install directory.
pub fn refusal_log_path(install_path: &Path) -> PathBuf {
    install_path.join("tray-singleton-refusals.log")
}

fn canonical(path: &Path) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

/// Acquire the singleton for `install_path`. On refusal the
/// refusal itself is appended to the install's refusal log
/// (FR-035-17: the second instance "is refused and says so in
/// its log") — a failed log write never masks the refusal.
pub fn acquire(install_path: &Path) -> Result<TrayInstance, SingletonError> {
    let install_path = canonical(install_path);
    std::fs::create_dir_all(&install_path).map_err(SingletonError::Io)?;
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(lock_path(&install_path))
        .map_err(SingletonError::Io)?;
    match file.try_lock_exclusive() {
        Ok(()) => {
            let _ = file.set_len(0);
            let _ = writeln!(file, "{}", std::process::id());
            Ok(TrayInstance {
                _lock_file: file,
                install_path,
            })
        }
        Err(e) if is_lock_contention(&e) => {
            log_refusal(&install_path);
            Err(SingletonError::AlreadyRunning(install_path))
        }
        Err(e) => Err(SingletonError::Io(e)),
    }
}

/// Append the refusal line to the install's log.
pub fn log_refusal(install_path: &Path) {
    let line = format!(
        "{} tray launch refused: an instance from this install path is already running (pid {})\n",
        syncyard_contract::isotime::now_iso(),
        std::process::id()
    );
    if let Ok(mut f) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(refusal_log_path(install_path))
    {
        let _ = f.write_all(line.as_bytes());
    }
}

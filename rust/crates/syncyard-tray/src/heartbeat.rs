//! Tray heartbeat (FR-035-16) and foreign-instance detection
//! (FR-035-17's surface half).
//!
//! The heartbeat file is written at startup and on the app's
//! tick (~30 s), atomically (temp file + replace). Its name is
//! keyed by the install path, so two installs never overwrite
//! each other's heartbeat — which is exactly what lets a scan
//! of the shared heartbeat directory notice a *foreign*
//! instance from a different path. A failed write is returned
//! as an error value; it must never stall the app.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use syncyard_contract::isotime;

/// Per-lane running/faulted state as the heartbeat carries it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LaneBeat {
    pub name: String,
    pub running: bool,
    pub faulted: bool,
}

/// The heartbeat document.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TrayHeartbeat {
    pub host: String,
    /// Build stamp of the writing instance.
    pub build: String,
    /// Train of the writing instance.
    pub train: String,
    pub pid: u32,
    /// Non-empty only for interactive sessions.
    pub session: String,
    pub started_at: String,
    pub ts: String,
    pub tick: u64,
    /// Canonical install path of the writing instance — the
    /// field foreign detection compares.
    pub install_path: String,
    #[serde(default)]
    pub lanes: Vec<LaneBeat>,
}

/// The heartbeat filename for an install path: distinct per
/// path, stable for a path.
pub fn heartbeat_filename(install_path: &Path) -> String {
    let canon = std::fs::canonicalize(install_path).unwrap_or_else(|_| install_path.to_path_buf());
    let mut hasher = Sha256::new();
    hasher.update(canon.to_string_lossy().as_bytes());
    let digest = hasher.finalize();
    let short: String = digest.iter().take(6).map(|b| format!("{b:02x}")).collect();
    format!("tray-heartbeat-{short}.json")
}

/// Writes one install's heartbeat into a shared directory.
pub struct HeartbeatWriter {
    pub dir: PathBuf,
    pub install_path: PathBuf,
}

impl HeartbeatWriter {
    pub fn new(dir: PathBuf, install_path: PathBuf) -> Self {
        HeartbeatWriter { dir, install_path }
    }

    /// This install's heartbeat path in the shared directory.
    pub fn path(&self) -> PathBuf {
        self.dir.join(heartbeat_filename(&self.install_path))
    }

    /// Atomic write (temp + replace). Failure is a returned
    /// error, never a stall or a panic.
    pub fn write(&self, hb: &TrayHeartbeat) -> std::io::Result<PathBuf> {
        std::fs::create_dir_all(&self.dir)?;
        let target = self.path();
        let tmp = self.dir.join(format!(
            ".{}.tmp-{}",
            heartbeat_filename(&self.install_path),
            std::process::id()
        ));
        let text = serde_json::to_string_pretty(hb)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        std::fs::write(&tmp, text)?;
        std::fs::rename(&tmp, &target)?;
        Ok(target)
    }
}

/// A foreign tray instance: a *fresh* heartbeat in the shared
/// directory from a different install path.
#[derive(Debug, Clone, PartialEq)]
pub struct ForeignInstance {
    pub install_path: String,
    pub age_s: i64,
    pub pid: Option<u32>,
    pub build: Option<String>,
}

/// Scan the shared heartbeat directory for a fresh heartbeat
/// from a different install path. Stale foreign heartbeats
/// (past `fresh_bound_s`) are history, not a live conflict.
pub fn scan_heartbeats(
    dir: &Path,
    own_install_path: &Path,
    now_unix_s: i64,
    fresh_bound_s: i64,
) -> Option<ForeignInstance> {
    let own =
        std::fs::canonicalize(own_install_path).unwrap_or_else(|_| own_install_path.to_path_buf());
    let own = own.to_string_lossy().to_string();
    let entries = std::fs::read_dir(dir).ok()?;
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !name.starts_with("tray-heartbeat-") || !name.ends_with(".json") {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(entry.path()) else {
            continue;
        };
        let Ok(hb) = serde_json::from_str::<TrayHeartbeat>(&text) else {
            continue;
        };
        if hb.install_path == own {
            continue;
        }
        let Some(ts) = isotime::unix_from_iso(&hb.ts) else {
            continue;
        };
        let age = now_unix_s - ts;
        if age >= 0 && age <= fresh_bound_s {
            return Some(ForeignInstance {
                install_path: hb.install_path,
                age_s: age,
                pid: Some(hb.pid),
                build: Some(hb.build),
            });
        }
    }
    None
}

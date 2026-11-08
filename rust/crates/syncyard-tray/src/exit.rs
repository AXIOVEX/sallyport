//! Exit behavior (FR-035-15): Exit writes a stopped-by-user
//! marker before the app ends, and the watchdog honors it —
//! the app stays ended until the user starts it again. The
//! Exit row's label states its effect on supervised things
//! (built in `render::build_menu` from the effect string the
//! app supplies).

use std::io::Write;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// The stopped-by-user marker document.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StoppedByUserMarker {
    pub host: String,
    pub pid: u32,
    pub install_path: String,
    pub stopped_at: String,
    pub build: String,
}

/// The marker's filename in the app state directory.
pub fn marker_path(state_dir: &Path) -> PathBuf {
    state_dir.join("tray-stopped-by-user.json")
}

/// The app's log file in the state directory.
pub fn log_path(state_dir: &Path) -> PathBuf {
    state_dir.join("tray.log")
}

fn write_atomic(path: &Path, text: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text)?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

/// Write the marker (atomic).
pub fn write_stopped_marker(state_dir: &Path, marker: &StoppedByUserMarker) -> std::io::Result<()> {
    let text = serde_json::to_string_pretty(marker)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    write_atomic(&marker_path(state_dir), &text)
}

/// Read the marker, if one exists.
pub fn read_stopped_marker(state_dir: &Path) -> Option<StoppedByUserMarker> {
    let text = std::fs::read_to_string(marker_path(state_dir)).ok()?;
    serde_json::from_str(&text).ok()
}

/// The watchdog's decision after the app ends.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WatchdogDecision {
    /// A stopped-by-user marker exists: stay down until the
    /// user starts the app again.
    StayDown,
    Relaunch,
}

/// The watchdog honors the stopped-by-user marker
/// unconditionally: while it exists the app is never
/// relaunched, at any minute mark.
pub fn watchdog_relaunch_decision(marker: Option<&StoppedByUserMarker>) -> WatchdogDecision {
    match marker {
        Some(_) => WatchdogDecision::StayDown,
        None => WatchdogDecision::Relaunch,
    }
}

/// Perform the exit: marker first, then the log line, both
/// before the app ends. Returns the paths written.
pub fn perform_exit(
    state_dir: &Path,
    marker: &StoppedByUserMarker,
) -> std::io::Result<(PathBuf, PathBuf)> {
    write_stopped_marker(state_dir, marker)?;
    let mut log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path(state_dir))?;
    writeln!(
        log,
        "{} tray exited by user (pid {}, install {}) — stopped-by-user marker written; \
         supervised services unaffected",
        marker.stopped_at, marker.pid, marker.install_path
    )?;
    Ok((marker_path(state_dir), log_path(state_dir)))
}

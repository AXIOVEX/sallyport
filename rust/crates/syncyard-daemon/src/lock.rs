//! Locks: the single-writer state lock and named owner locks.
//!
//! Single writer (spec 038 FR-038-03, spec 032 section 2): the
//! daemon holds an exclusive `flock` on `gk.lock` in the state home
//! for its whole lifetime. The CLI's direct-file fallback takes the
//! same lock, so the two writers can never interleave; if the daemon
//! holds it, the fallback is refused (never worse than today: today
//! there is no daemon and the fallback always runs).
//!
//! Owner locks (spec 038 FR-038-26, ERR-05): a lock file carrying its
//! owner's pid is taken over automatically when the owner is
//! provably dead on this host, regardless of the lock's age, and
//! never while the owner lives. This is the cured form of the
//! 2026-11-06 stranded-lock defect, whose owner died and whose lock
//! was only taken over after a 600 s age timeout.

use std::fs::{File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};

use fs2::FileExt;
use serde::{Deserialize, Serialize};

use crate::time::now_iso;
use crate::vwrite::{FsWriter, StateWriter};

/// The daemon/fallback single-writer lock. Dropping it releases the
/// flock (the file itself stays behind as the lock anchor).
pub struct StateLock {
    _file: File,
    pub path: PathBuf,
}

/// Try to take the state lock without blocking. `Ok(None)` means a
/// live writer already holds it.
#[cfg(unix)]
pub fn try_state_lock(path: &Path) -> io::Result<Option<StateLock>> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    // truncate(false) is explicit: the lock file's content is
    // never used, only its flock.
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(path)?;
    match file.try_lock_exclusive() {
        Ok(()) => Ok(Some(StateLock {
            _file: file,
            path: path.to_path_buf(),
        })),
        Err(e) if e.raw_os_error() == Some(libc::EWOULDBLOCK) => Ok(None),
        Err(e) => Err(e),
    }
}

#[cfg(not(unix))]
pub fn try_state_lock(path: &Path) -> io::Result<Option<StateLock>> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(path)?;
    match file.try_lock_exclusive() {
        Ok(()) => Ok(Some(StateLock {
            _file: file,
            path: path.to_path_buf(),
        })),
        Err(_) => Ok(None),
    }
}

/// A named owner lock file: `{"pid": N, "holder": "...", "ts": ...}`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct OwnerLock {
    pub pid: u32,
    pub holder: String,
    pub ts: String,
}

/// Is `pid` alive on this host? (Signal 0 probe; permission-denied
/// also means alive — a live owner we cannot signal is still live.)
#[cfg(unix)]
pub fn pid_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    let rc = unsafe { libc::kill(pid as libc::pid_t, 0) };
    if rc == 0 {
        return true;
    }
    io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(not(unix))]
pub fn pid_alive(_pid: u32) -> bool {
    // No portable probe off-unix; treat unknown as alive so a lock
    // is never taken from a possibly-live owner.
    true
}

/// Outcome of a takeover attempt.
#[derive(Debug, PartialEq)]
pub enum Takeover {
    /// We now hold the lock.
    Acquired,
    /// The recorded owner is alive; the lock was not touched.
    OwnerAlive(OwnerLock),
}

/// Take the owner lock at `path` for `holder`.
///
/// - No lock file: create ours.
/// - Lock file whose owner is provably dead: take over, regardless
///   of the lock's age (ERR-05).
/// - Lock file whose owner lives (or whose contents cannot be
///   parsed — an unparseable lock is treated as owned, never
///   stolen): refuse.
pub fn acquire_owner_lock(
    path: &Path,
    holder: &str,
    writer: &dyn StateWriter,
) -> Result<Takeover, crate::vwrite::WriteFailure> {
    if let Ok(text) = std::fs::read_to_string(path) {
        match serde_json::from_str::<OwnerLock>(&text) {
            Ok(existing) if pid_alive(existing.pid) => {
                return Ok(Takeover::OwnerAlive(existing));
            }
            Ok(_) => { /* owner provably dead: fall through to takeover */ }
            Err(_) => {
                // Unparseable: cannot prove the owner dead.
                return Ok(Takeover::OwnerAlive(OwnerLock {
                    pid: 0,
                    holder: "<unparseable lock file>".to_string(),
                    ts: String::new(),
                }));
            }
        }
    }
    let ours = OwnerLock {
        pid: std::process::id(),
        holder: holder.to_string(),
        ts: now_iso(),
    };
    let bytes = serde_json::to_vec_pretty(&ours).expect("lock serializes");
    writer.write_verified(path, &bytes)?;
    Ok(Takeover::Acquired)
}

/// Release an owner lock we hold (only removes the file when the
/// recorded holder/pid are ours).
pub fn release_owner_lock(path: &Path, holder: &str) {
    if let Ok(text) = std::fs::read_to_string(path) {
        if let Ok(existing) = serde_json::from_str::<OwnerLock>(&text) {
            if existing.holder == holder && existing.pid == std::process::id() {
                let _ = std::fs::remove_file(path);
            }
        }
    }
}

pub fn fs_writer() -> FsWriter {
    FsWriter::default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sy-lock-{}-{}", std::process::id(), tag));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn state_lock_is_exclusive_until_dropped() {
        let dir = scratch("state");
        let p = dir.join("gk.lock");
        let first = try_state_lock(&p).unwrap();
        assert!(first.is_some());
        // A second writer is refused while the first holds it.
        assert!(try_state_lock(&p).unwrap().is_none());
        drop(first);
        // After release, the lock can be taken again.
        assert!(try_state_lock(&p).unwrap().is_some());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn live_owner_lock_is_never_taken() {
        let dir = scratch("live");
        let p = dir.join("job.lock");
        let w = fs_writer();
        // Our own pid is alive by construction.
        let lock = OwnerLock {
            pid: std::process::id(),
            holder: "someone-live".to_string(),
            ts: now_iso(),
        };
        w.write_verified(&p, &serde_json::to_vec(&lock).unwrap())
            .unwrap();
        match acquire_owner_lock(&p, "us", &w).unwrap() {
            Takeover::OwnerAlive(existing) => assert_eq!(existing.holder, "someone-live"),
            other => panic!("expected OwnerAlive, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn dead_owner_lock_is_taken_regardless_of_age() {
        let dir = scratch("dead");
        let p = dir.join("job.lock");
        let w = fs_writer();
        // pid 1 is init (alive) — use an implausible pid instead:
        // the maximum pid value is never a live process here.
        let lock = OwnerLock {
            pid: 99_999_999,
            holder: "dead-owner".to_string(),
            ts: now_iso(), // brand-new lock: age must not matter
        };
        w.write_verified(&p, &serde_json::to_vec(&lock).unwrap())
            .unwrap();
        assert!(!pid_alive(99_999_999));
        match acquire_owner_lock(&p, "us", &w).unwrap() {
            Takeover::Acquired => {}
            other => panic!("expected Acquired, got {other:?}"),
        }
        let now: OwnerLock = serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
        assert_eq!(now.holder, "us");
        let _ = std::fs::remove_dir_all(&dir);
    }
}

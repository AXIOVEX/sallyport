//! Singleton by install path.
//!
//! Exactly one agent instance may run per install path. The
//! guard is an exclusive flock on a lock file inside the
//! (canonicalized) install path, held for the process lifetime:
//! a second instance started from the same path is refused by
//! name, while an instance from a different path has its own
//! lock (fleet hosts run one install per host; the path is the
//! identity).
//!
//! Defect class: two live instances from one install path
//! fighting over the same jobs and state — the agent-side
//! analogue of the tray's laptop-two-tray-fight-2026-11-06
//! (spec 036 section 3's defect-to-test list). The P3 suite
//! carries the defect-named regression test for this guard.

use std::fs::OpenOptions;
use std::path::{Path, PathBuf};

use fs2::FileExt;

use crate::channel::AgentError;

/// The held singleton guard. Drop releases the flock.
#[derive(Debug)]
pub struct InstanceGuard {
    #[allow(dead_code)]
    file: std::fs::File,
    /// The canonical install path this guard holds.
    pub install_path: PathBuf,
    /// The lock file inside it.
    pub lock_path: PathBuf,
}

/// Acquire the singleton guard for `install_path`.
///
/// The install path is canonicalized first, so the same
/// directory reached through a symlink or a relative path is
/// the same singleton.
pub fn acquire(install_path: &Path) -> Result<InstanceGuard, AgentError> {
    let canon = std::fs::canonicalize(install_path).unwrap_or_else(|_| install_path.to_path_buf());
    std::fs::create_dir_all(&canon)
        .map_err(|e| AgentError::Local(format!("install path {}: {e}", canon.display())))?;
    let lock_path = canon.join("agent-instance.lock");
    let mut file = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(false)
        .open(&lock_path)
        .map_err(|e| AgentError::Local(format!("lock file {}: {e}", lock_path.display())))?;
    match file.try_lock_exclusive() {
        Ok(()) => {
            use std::io::{Seek, SeekFrom, Write};
            let _ = file.seek(SeekFrom::Start(0));
            let _ = writeln!(file, "{}", std::process::id());
            Ok(InstanceGuard {
                file,
                install_path: canon,
                lock_path,
            })
        }
        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
            Err(AgentError::AlreadyRunning(canon))
        }
        Err(e) => Err(AgentError::Local(format!(
            "lock {}: {e}",
            lock_path.display()
        ))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sy-agent-singleton-{}-{tag}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    // Defect-named regression test (defect class:
    // two-instances-one-install-path, the agent analogue of
    // laptop-two-tray-fight-2026-11-06): a second acquire on the
    // same install path is refused while the first guard lives;
    // a different install path is a different singleton; and a
    // dropped guard frees the path for the next instance.
    #[test]
    fn two_instances_one_install_path_refused() {
        let a = scratch("a");
        let b = scratch("b");
        let first = acquire(&a).expect("first instance takes the path");
        match acquire(&a) {
            Err(AgentError::AlreadyRunning(p)) => {
                assert_eq!(p, first.install_path);
            }
            other => panic!("second instance must be refused, got {other:?}"),
        }
        let _other_path = acquire(&b).expect("a different install path is a different singleton");
        drop(first);
        let _again = acquire(&a).expect("a dropped guard frees the install path");
        let _ = std::fs::remove_dir_all(&a);
        let _ = std::fs::remove_dir_all(&b);
    }
}

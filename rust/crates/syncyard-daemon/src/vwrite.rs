//! Verified writes and the wave-aware writer abstraction.
//!
//! The verified-write defense from the Python gatekeeper is kept
//! verbatim in spirit (spec 032 section 6): a disk write is only
//! believed after read-back verification. On top of it, spec 038
//! FR-038-36 (ERR-08) requires wave behavior: when writes are being
//! denied (EPERM on write-family calls), writers switch to fail-fast
//! with a named outcome and bounded retries instead of burning the
//! full retry budget on every pass, and no caller dies on an
//! uncaught write error — failures come back as typed values.

use std::fs;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

/// Why a state write failed.
#[derive(Debug)]
pub enum WriteFailure {
    /// The write was denied (EPERM/EACCES) — the wave signature.
    /// Callers treat this as "state stays dirty, retry next cycle",
    /// never as corruption and never as a panic.
    Denied(io::Error),
    /// Any other I/O failure, including read-back mismatch.
    Io(io::Error),
}

impl WriteFailure {
    /// True when this failure has the write-wave signature.
    pub fn is_denial(&self) -> bool {
        matches!(self, WriteFailure::Denied(_))
    }
}

impl std::fmt::Display for WriteFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WriteFailure::Denied(e) => write!(f, "write denied (wave signature): {e}"),
            WriteFailure::Io(e) => write!(f, "write failed: {e}"),
        }
    }
}

impl std::error::Error for WriteFailure {}

fn classify(e: io::Error) -> WriteFailure {
    match e.raw_os_error() {
        // EPERM (1) and EACCES (13): the wave denies write-family
        // syscalls with EPERM (spec 032 measured shape).
        Some(1) | Some(13) => WriteFailure::Denied(e),
        _ => WriteFailure::Io(e),
    }
}

/// The state-writing surface. The daemon's flush goes through this
/// trait so tests can inject a wave (a writer that denies everything)
/// without touching the real filesystem policy.
pub trait StateWriter: Send + Sync {
    /// Write `bytes` to `path` atomically and verified: the write is
    /// believed only after a read-back compares byte-equal.
    fn write_verified(&self, path: &Path, bytes: &[u8]) -> Result<(), WriteFailure>;
    /// Append one line to `path` and verify the tail reads back.
    fn append_verified(&self, path: &Path, line: &[u8]) -> Result<(), WriteFailure>;
}

/// The production writer: temp file in the same directory, fsync,
/// rename over the target, fsync the directory, read back and
/// compare. Mirrors `gk.py`'s `_write_verified_bytes` /
/// `_append_verified`, with the retry count bounded by the caller
/// (the flush loop owns retries; a single pass stays cheap so wave
/// fail-fast is possible).
pub struct FsWriter {
    /// Attempts per write before giving up (gk.py uses 8; the flush
    /// loop retries next cycle, so the default here is 3).
    pub attempts: u32,
}

impl Default for FsWriter {
    fn default() -> Self {
        FsWriter { attempts: 3 }
    }
}

impl FsWriter {
    fn write_once(&self, path: &Path, bytes: &[u8]) -> Result<(), WriteFailure> {
        let dir = path.parent().unwrap_or_else(|| Path::new("."));
        let tmp = tmp_sibling(path);
        let attempt = (|| -> io::Result<()> {
            {
                let mut f = fs::File::create(&tmp)?;
                f.write_all(bytes)?;
                f.sync_all()?;
            }
            fs::rename(&tmp, path)?;
            sync_dir(dir);
            // Read-back verification: the write is not believed
            // until the bytes on disk compare equal.
            let back = fs::read(path)?;
            if back != bytes {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "read-back mismatch after rename",
                ));
            }
            Ok(())
        })();
        if attempt.is_err() {
            let _ = fs::remove_file(&tmp);
        }
        attempt.map_err(classify)
    }
}

impl StateWriter for FsWriter {
    fn write_verified(&self, path: &Path, bytes: &[u8]) -> Result<(), WriteFailure> {
        let mut last = None;
        for _ in 0..self.attempts.max(1) {
            match self.write_once(path, bytes) {
                Ok(()) => return Ok(()),
                Err(e) => {
                    // Fail fast on denial: retrying a denied write
                    // inside one pass is the pre-ERR-08 behavior.
                    if e.is_denial() {
                        return Err(e);
                    }
                    last = Some(e);
                }
            }
        }
        Err(last.expect("at least one attempt ran"))
    }

    fn append_verified(&self, path: &Path, line: &[u8]) -> Result<(), WriteFailure> {
        let attempt = (|| -> io::Result<()> {
            let mut f = fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(path)?;
            f.write_all(line)?;
            f.sync_all()?;
            // Verify the tail: the last line.len() bytes must equal
            // what was appended.
            let mut f = fs::File::open(path)?;
            let len = f.metadata()?.len();
            if len < line.len() as u64 {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "append read-back shorter than the appended line",
                ));
            }
            f.seek(SeekFrom::End(-(line.len() as i64)))?;
            let mut back = vec![0u8; line.len()];
            f.read_exact(&mut back)?;
            if back != line {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "append read-back mismatch",
                ));
            }
            Ok(())
        })();
        attempt.map_err(classify)
    }
}

fn tmp_sibling(path: &Path) -> PathBuf {
    let mut s = path.as_os_str().to_owned();
    s.push(format!(".tmp-{}", std::process::id()));
    PathBuf::from(s)
}

fn sync_dir(dir: &Path) {
    #[cfg(unix)]
    {
        if let Ok(f) = fs::File::open(dir) {
            let _ = f.sync_all();
        }
    }
    #[cfg(not(unix))]
    {
        let _ = dir;
    }
}

/// SHA-256 hex digest of bytes (manifests, idempotency keys, CAS).
pub fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(bytes);
    let out = h.finalize();
    let mut s = String::with_capacity(64);
    for b in out {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// Writers for tests: the wave, simulated two ways. Available to
/// the crate's own tests and to integration tests via the
/// `test-support` feature.
#[cfg(any(test, feature = "test-support"))]
pub mod test_support {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};

    /// A writer that denies every write (the wave, simulated).
    pub struct DenyWriter;
    impl StateWriter for DenyWriter {
        fn write_verified(&self, _path: &Path, _bytes: &[u8]) -> Result<(), WriteFailure> {
            Err(WriteFailure::Denied(io::Error::from_raw_os_error(1)))
        }
        fn append_verified(&self, _path: &Path, _line: &[u8]) -> Result<(), WriteFailure> {
            Err(WriteFailure::Denied(io::Error::from_raw_os_error(1)))
        }
    }

    /// A writer that denies while `deny` is set, then delegates to
    /// the real filesystem writer (a wave that clears).
    pub struct GateWriter {
        pub deny: AtomicBool,
        pub inner: FsWriter,
    }

    impl GateWriter {
        pub fn new(deny: bool) -> Self {
            GateWriter {
                deny: AtomicBool::new(deny),
                inner: FsWriter::default(),
            }
        }
        pub fn set_deny(&self, deny: bool) {
            self.deny.store(deny, Ordering::SeqCst);
        }
    }

    impl StateWriter for GateWriter {
        fn write_verified(&self, path: &Path, bytes: &[u8]) -> Result<(), WriteFailure> {
            if self.deny.load(Ordering::SeqCst) {
                return Err(WriteFailure::Denied(io::Error::from_raw_os_error(1)));
            }
            self.inner.write_verified(path, bytes)
        }
        fn append_verified(&self, path: &Path, line: &[u8]) -> Result<(), WriteFailure> {
            if self.deny.load(Ordering::SeqCst) {
                return Err(WriteFailure::Denied(io::Error::from_raw_os_error(1)));
            }
            self.inner.append_verified(path, line)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::test_support::DenyWriter;
    use super::*;

    #[test]
    fn fs_writer_round_trip_and_append() {
        let dir = std::env::temp_dir().join(format!("sy-vwrite-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let w = FsWriter::default();
        let p = dir.join("rec.json");
        w.write_verified(&p, b"{\"a\":1}").unwrap();
        assert_eq!(fs::read(&p).unwrap(), b"{\"a\":1}");
        w.write_verified(&p, b"{\"a\":2}").unwrap();
        assert_eq!(fs::read(&p).unwrap(), b"{\"a\":2}");
        let log = dir.join("ledger.jsonl");
        w.append_verified(&log, b"{\"n\":1}\n").unwrap();
        w.append_verified(&log, b"{\"n\":2}\n").unwrap();
        assert_eq!(fs::read(&log).unwrap(), b"{\"n\":1}\n{\"n\":2}\n");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn deny_writer_reports_denial_not_io() {
        let w = DenyWriter;
        let err = w.write_verified(Path::new("/nowhere/x"), b"y").unwrap_err();
        assert!(err.is_denial());
    }

    #[test]
    fn sha256_matches_known_vector() {
        // SHA-256("abc") — FIPS 180-4 example.
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}

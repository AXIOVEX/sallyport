//! The off-VM record and the fencing epoch (spec 038 section 2).
//!
//! The one write with an outside witness — the pre-dispatch record —
//! lives off-VM (in production: a `gk-state` branch in the door repo,
//! written through the GitHub Contents API, spec 032 section 4). This
//! module defines the store trait and a directory-backed
//! implementation whose epoch record is guarded by compare-and-swap
//! on the record's content hash — the same CAS shape the Contents
//! API gives (write with `sha` = current blob sha or be refused).
//! The Contents API transport slots behind this trait at cutover
//! wiring; P2 implements and tests the protocol against the
//! directory store.
//!
//! Fencing (red-team H1, FR-038-09): the off-VM record carries a
//! monotonic epoch. A daemon acquires the epoch by CAS before it may
//! dispatch, and re-validates it before every dispatch. Two daemons,
//! one record: exactly one holds the current epoch; the other's
//! dispatches are refused.

use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use syncyard_contract::QueueRecord;

use crate::time::now_iso;
use crate::vwrite::{sha256_hex, FsWriter, StateWriter, WriteFailure};

/// The fencing epoch record, stored off-VM.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EpochRecord {
    /// Monotonic epoch; bumped on every successful acquisition.
    pub epoch: u64,
    /// Identity of the daemon instance holding the epoch.
    pub holder: String,
    /// Wall-clock time of acquisition (display only, spec 038 §9).
    pub ts: String,
}

/// Errors from the off-VM store.
#[derive(Debug)]
pub enum OffVmError {
    /// Compare-and-swap failed: the record changed under us (or the
    /// epoch moved). Whoever holds the newer record wins.
    Conflict,
    /// The store itself failed.
    Write(WriteFailure),
    /// Stored content did not parse.
    Corrupt(String),
}

impl std::fmt::Display for OffVmError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OffVmError::Conflict => write!(f, "off-VM compare-and-swap conflict"),
            OffVmError::Write(e) => write!(f, "off-VM write failed: {e}"),
            OffVmError::Corrupt(s) => write!(f, "off-VM record corrupt: {s}"),
        }
    }
}

impl std::error::Error for OffVmError {}

impl From<WriteFailure> for OffVmError {
    fn from(e: WriteFailure) -> Self {
        OffVmError::Write(e)
    }
}

/// A held fencing epoch. Dispatch is permitted only while the
/// off-VM record still shows this epoch and holder.
#[derive(Debug, Clone, PartialEq)]
pub struct FencingToken {
    pub epoch: u64,
    pub holder: String,
}

/// The off-VM store surface.
pub trait OffVmStore: Send + Sync {
    /// Read the current epoch record and its content hash (the CAS
    /// token). `None` when no epoch record exists yet.
    fn read_epoch(&self) -> Result<Option<(EpochRecord, String)>, OffVmError>;

    /// Write `next` only if the current content hash equals
    /// `expected_sha` (`None` = the record must not exist yet).
    /// This is the Contents API's `sha` precondition, exactly.
    fn cas_epoch(
        &self,
        expected_sha: Option<&str>,
        next: &EpochRecord,
    ) -> Result<String, OffVmError>;

    /// Write a per-job record (submit acknowledgement or
    /// pre-dispatch record), verified by read-back.
    fn put_record(&self, kind: &str, job_id: &str, bytes: &[u8]) -> Result<(), OffVmError>;

    /// Read a per-job record, if present.
    fn get_record(&self, kind: &str, job_id: &str) -> Result<Option<Vec<u8>>, OffVmError>;

    /// List job ids with a record of `kind`.
    fn list_records(&self, kind: &str) -> Result<Vec<String>, OffVmError>;
}

/// Record-kind names (directories in the directory store; paths on
/// the state branch in the Contents API store).
pub const KIND_SUBMITS: &str = "submits";
pub const KIND_PREDISPATCH: &str = "predispatch";

/// Directory-backed off-VM store. In tests this is a scratch dir;
/// in the field it is a local mirror used to develop and prove the
/// protocol — the witness property comes from the Contents API
/// transport at cutover, which is a named P2 gap.
pub struct DirOffVmStore {
    root: PathBuf,
    writer: FsWriter,
}

impl DirOffVmStore {
    pub fn new(root: PathBuf) -> Self {
        DirOffVmStore {
            root,
            writer: FsWriter::default(),
        }
    }

    fn epoch_path(&self) -> PathBuf {
        self.root.join("epoch.json")
    }

    fn record_path(&self, kind: &str, job_id: &str) -> PathBuf {
        // Job ids are timestamps + slugs + hex (no path separators);
        // refuse anything else rather than escape the store root.
        let safe: String = job_id
            .chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.') {
                    c
                } else {
                    '_'
                }
            })
            .collect();
        self.root.join(kind).join(format!("{safe}.json"))
    }
}

impl OffVmStore for DirOffVmStore {
    fn read_epoch(&self) -> Result<Option<(EpochRecord, String)>, OffVmError> {
        match std::fs::read(self.epoch_path()) {
            Ok(bytes) => {
                let sha = sha256_hex(&bytes);
                let rec: EpochRecord = serde_json::from_slice(&bytes)
                    .map_err(|e| OffVmError::Corrupt(e.to_string()))?;
                Ok(Some((rec, sha)))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(OffVmError::Write(WriteFailure::Io(e))),
        }
    }

    fn cas_epoch(
        &self,
        expected_sha: Option<&str>,
        next: &EpochRecord,
    ) -> Result<String, OffVmError> {
        // Check-then-write. Against the directory store this is the
        // faithful model of the Contents API precondition; the API
        // transport enforces it server-side at cutover.
        let current = self.read_epoch()?;
        let current_sha = current.as_ref().map(|(_, s)| s.as_str());
        if current_sha != expected_sha {
            return Err(OffVmError::Conflict);
        }
        if let Some((cur, _)) = &current {
            if next.epoch <= cur.epoch {
                return Err(OffVmError::Conflict);
            }
        }
        let bytes = serde_json::to_vec_pretty(next).expect("epoch serializes");
        if let Some(parent) = self.epoch_path().parent() {
            std::fs::create_dir_all(parent).map_err(WriteFailure::Io)?;
        }
        self.writer.write_verified(&self.epoch_path(), &bytes)?;
        // Read back: the returned sha is the new CAS token.
        match self.read_epoch()? {
            Some((_, sha)) => Ok(sha),
            None => Err(OffVmError::Corrupt("epoch vanished after write".into())),
        }
    }

    fn put_record(&self, kind: &str, job_id: &str, bytes: &[u8]) -> Result<(), OffVmError> {
        let path = self.record_path(kind, job_id);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(WriteFailure::Io)?;
        }
        self.writer.write_verified(&path, bytes)?;
        Ok(())
    }

    fn get_record(&self, kind: &str, job_id: &str) -> Result<Option<Vec<u8>>, OffVmError> {
        match std::fs::read(self.record_path(kind, job_id)) {
            Ok(b) => Ok(Some(b)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(OffVmError::Write(WriteFailure::Io(e))),
        }
    }

    fn list_records(&self, kind: &str) -> Result<Vec<String>, OffVmError> {
        let dir = self.root.join(kind);
        let mut ids = Vec::new();
        match std::fs::read_dir(&dir) {
            Ok(rd) => {
                for e in rd.flatten() {
                    let name = e.file_name().to_string_lossy().to_string();
                    if let Some(id) = name.strip_suffix(".json") {
                        ids.push(id.to_string());
                    }
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(OffVmError::Write(WriteFailure::Io(e))),
        }
        ids.sort();
        Ok(ids)
    }
}

/// Acquire the fencing epoch for `holder`: read the current record,
/// CAS to epoch+1 held by us. Bounded retries on conflict; the
/// loser of a two-daemon race gets `Conflict`, never a shared epoch.
pub fn acquire_epoch(store: &dyn OffVmStore, holder: &str) -> Result<FencingToken, OffVmError> {
    for _ in 0..8 {
        let current = store.read_epoch()?;
        let (next_epoch, expected) = match &current {
            Some((rec, sha)) => (rec.epoch + 1, Some(sha.as_str())),
            None => (1, None),
        };
        let next = EpochRecord {
            epoch: next_epoch,
            holder: holder.to_string(),
            ts: now_iso(),
        };
        match store.cas_epoch(expected, &next) {
            Ok(_) => {
                return Ok(FencingToken {
                    epoch: next_epoch,
                    holder: holder.to_string(),
                })
            }
            Err(OffVmError::Conflict) => continue,
            Err(e) => return Err(e),
        }
    }
    Err(OffVmError::Conflict)
}

/// Validate that `token` is still the current fencing epoch. A
/// daemon must call this before dispatching; a stale token (another
/// daemon took over) refuses the dispatch (FR-038-09).
pub fn assert_epoch(store: &dyn OffVmStore, token: &FencingToken) -> Result<(), OffVmError> {
    match store.read_epoch()? {
        Some((rec, _)) if rec.epoch == token.epoch && rec.holder == token.holder => Ok(()),
        _ => Err(OffVmError::Conflict),
    }
}

/// Parse every stored record of a kind into queue records (used by
/// boot reconciliation to recover off-VM state).
pub fn load_records(
    store: &dyn OffVmStore,
    kind: &str,
) -> Result<BTreeMap<String, QueueRecord>, OffVmError> {
    let mut out = BTreeMap::new();
    for id in store.list_records(kind)? {
        if let Some(bytes) = store.get_record(kind, &id)? {
            let rec: QueueRecord = serde_json::from_slice(&bytes)
                .map_err(|e| OffVmError::Corrupt(format!("{kind}/{id}: {e}")))?;
            out.insert(rec.id.clone(), rec);
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sy-offvm-{}-{}", std::process::id(), tag));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn epoch_acquire_bumps_and_validates() {
        let store = DirOffVmStore::new(scratch("epoch"));
        let t1 = acquire_epoch(&store, "daemon-a").unwrap();
        assert_eq!(t1.epoch, 1);
        assert_epoch(&store, &t1).unwrap();
        // A second daemon taking over invalidates the first token.
        let t2 = acquire_epoch(&store, "daemon-b").unwrap();
        assert_eq!(t2.epoch, 2);
        assert_epoch(&store, &t2).unwrap();
        assert!(matches!(
            assert_epoch(&store, &t1),
            Err(OffVmError::Conflict)
        ));
    }

    #[test]
    fn cas_rejects_stale_sha_and_nonmonotonic_epoch() {
        let store = DirOffVmStore::new(scratch("cas"));
        let t1 = acquire_epoch(&store, "daemon-a").unwrap();
        let (_, sha) = store.read_epoch().unwrap().unwrap();
        // Stale expectation (pretending the record does not exist).
        let next = EpochRecord {
            epoch: t1.epoch + 1,
            holder: "daemon-b".into(),
            ts: now_iso(),
        };
        assert!(matches!(
            store.cas_epoch(None, &next),
            Err(OffVmError::Conflict)
        ));
        // Correct sha but a non-increasing epoch.
        let flat = EpochRecord {
            epoch: t1.epoch,
            holder: "daemon-b".into(),
            ts: now_iso(),
        };
        assert!(matches!(
            store.cas_epoch(Some(&sha), &flat),
            Err(OffVmError::Conflict)
        ));
    }

    #[test]
    fn record_round_trip_by_kind() {
        let store = DirOffVmStore::new(scratch("records"));
        store
            .put_record(KIND_PREDISPATCH, "job-1", b"{\"id\":\"job-1\"}")
            .unwrap();
        store
            .put_record(KIND_SUBMITS, "job-2", b"{\"id\":\"job-2\"}")
            .unwrap();
        assert_eq!(
            store.get_record(KIND_PREDISPATCH, "job-1").unwrap(),
            Some(b"{\"id\":\"job-1\"}".to_vec())
        );
        assert_eq!(store.list_records(KIND_SUBMITS).unwrap(), vec!["job-2"]);
        assert_eq!(store.get_record(KIND_SUBMITS, "nope").unwrap(), None);
    }
}

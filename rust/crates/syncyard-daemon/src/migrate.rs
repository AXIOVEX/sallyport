//! `migrate-history`: compact file-backed history into an archive
//! with a SHA-256 manifest (spec 038 section 2 + FR-038-35/ERR-06,
//! spec 036 FR-036-13).
//!
//! History (completed records + the ledger) stays file-backed and
//! read-only in steady state. Migration reads a Python gatekeeper
//! directory **read-only** and writes, into the state home:
//!
//! - `history/records.jsonl` — every queue record's raw bytes
//!   (raw bytes, not re-serialized, so the round trip is byte-exact
//!   by construction), framed as `SYREC1 <id> <len> <sha256>` header
//!   lines followed by exactly `<len>` bytes — records are
//!   pretty-printed JSON and span lines, so line framing alone
//!   would corrupt them;
//! - `history/ledger.jsonl` — the ledger, byte-copied;
//! - `history/manifest.json` — per-record SHA-256 over the raw
//!   bytes, record/ledger counts, and a hash chain over the ledger:
//!   `chain[i] = sha256(chain[i-1] || line[i])`, seeded with the
//!   SHA-256 of the empty string. Chain verification is a release
//!   test (FR-036-13).
//!
//! Any verification failure blocks cutover (ERR-06): `verify`
//! recomputes every hash and the full chain and reports the first
//! mismatch by name. Compaction is forbidden during a detected
//! write wave (FR-036-13): the caller supplies a wave probe; when
//! it reports denial, migration refuses before writing anything.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::state::StateHome;
use crate::vwrite::{sha256_hex, FsWriter, StateWriter};

/// The archive manifest.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Manifest {
    /// Migration format version.
    pub format: u32,
    /// Source directory the history was read from.
    pub source: String,
    /// Job id -> SHA-256 of the record's raw source bytes.
    pub records: BTreeMap<String, String>,
    /// Number of ledger lines compacted.
    pub ledger_lines: usize,
    /// Final ledger hash-chain value (hex).
    pub ledger_chain: String,
    /// SHA-256 of the whole ledger file bytes.
    pub ledger_sha256: String,
}

/// Why a migration or verification did not complete.
#[derive(Debug)]
pub enum MigrateError {
    /// A write wave is detected: compaction is forbidden (FR-036-13).
    WaveDetected,
    Io(String),
    /// Verification found a mismatch; the string names the record.
    Mismatch(String),
}

impl std::fmt::Display for MigrateError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MigrateError::WaveDetected => {
                write!(
                    f,
                    "write wave detected: history compaction is forbidden during a wave"
                )
            }
            MigrateError::Io(s) => write!(f, "migration I/O: {s}"),
            MigrateError::Mismatch(s) => write!(f, "history verification mismatch: {s}"),
        }
    }
}

impl std::error::Error for MigrateError {}

/// The ledger hash chain: fold each line into the running hash.
pub fn ledger_chain(lines: &[&[u8]]) -> String {
    let mut chain = sha256_hex(b"");
    for line in lines {
        let mut material = chain.clone().into_bytes();
        material.extend_from_slice(line);
        chain = sha256_hex(&material);
    }
    chain
}

/// What a source read yields: record id -> raw bytes, plus the
/// ledger file's raw bytes.
type SourceData = (BTreeMap<String, Vec<u8>>, Vec<u8>);

fn read_source(source: &Path) -> Result<SourceData, MigrateError> {
    let mut records = BTreeMap::new();
    let qd = source.join("queue");
    let rd = std::fs::read_dir(&qd)
        .map_err(|e| MigrateError::Io(format!("cannot read {}: {e}", qd.display())))?;
    for entry in rd.flatten() {
        let p = entry.path();
        if p.extension().map(|x| x == "json") != Some(true) {
            continue;
        }
        let Some(stem) = p.file_stem().and_then(|s| s.to_str()) else {
            continue;
        };
        let bytes = std::fs::read(&p)
            .map_err(|e| MigrateError::Io(format!("cannot read {}: {e}", p.display())))?;
        // The record must parse and its id must match the file name;
        // anything else is a corrupt source, named, not absorbed.
        let rec: syncyard_contract::QueueRecord = serde_json::from_slice(&bytes)
            .map_err(|e| MigrateError::Mismatch(format!("{}: unparseable: {e}", p.display())))?;
        if rec.id != stem {
            return Err(MigrateError::Mismatch(format!(
                "{}: record id {} does not match file name",
                p.display(),
                rec.id
            )));
        }
        records.insert(rec.id.clone(), bytes);
    }
    let ledger = std::fs::read(source.join("ledger.jsonl")).unwrap_or_default();
    Ok((records, ledger))
}

/// Run the migration from `source` (a Python gatekeeper directory,
/// read-only) into `home`'s history archive. `wave_detected` is the
/// caller's write-wave probe (spec 036 FR-036-13).
pub fn migrate_history(
    source: &Path,
    home: &StateHome,
    wave_detected: &dyn Fn() -> bool,
) -> Result<Manifest, MigrateError> {
    if wave_detected() {
        return Err(MigrateError::WaveDetected);
    }
    let (records, ledger) = read_source(source)?;
    let hist = home.history_dir();
    std::fs::create_dir_all(&hist)
        .map_err(|e| MigrateError::Io(format!("cannot create {}: {e}", hist.display())))?;
    let writer = FsWriter::default();

    // records.jsonl: framed raw bytes, id order.
    let mut records_jsonl = Vec::new();
    let mut hashes = BTreeMap::new();
    for (id, bytes) in &records {
        let sha = sha256_hex(bytes);
        hashes.insert(id.clone(), sha.clone());
        records_jsonl.extend_from_slice(format!("SYREC1 {id} {} {sha}\n", bytes.len()).as_bytes());
        records_jsonl.extend_from_slice(bytes);
        if !bytes.ends_with(b"\n") {
            records_jsonl.push(b'\n');
        }
    }
    writer
        .write_verified(&hist.join("records.jsonl"), &records_jsonl)
        .map_err(|e| MigrateError::Io(e.to_string()))?;
    writer
        .write_verified(&hist.join("ledger.jsonl"), &ledger)
        .map_err(|e| MigrateError::Io(e.to_string()))?;

    let lines: Vec<&[u8]> = ledger
        .split(|b| *b == b'\n')
        .filter(|l| !l.is_empty())
        .collect();
    let manifest = Manifest {
        format: 1,
        source: source.display().to_string(),
        records: hashes,
        ledger_lines: lines.len(),
        ledger_chain: ledger_chain(&lines),
        ledger_sha256: sha256_hex(&ledger),
    };
    let bytes = serde_json::to_vec_pretty(&manifest).expect("manifest serializes");
    writer
        .write_verified(&hist.join("manifest.json"), &bytes)
        .map_err(|e| MigrateError::Io(e.to_string()))?;
    Ok(manifest)
}

/// Walk the framed records archive. Each frame's header carries
/// the record id, byte length and SHA-256; the frame hash is
/// checked during the walk, so a flipped byte anywhere is caught
/// here before the manifest comparison.
pub fn parse_frames(data: &[u8]) -> Result<Vec<(String, Vec<u8>)>, MigrateError> {
    let mut out = Vec::new();
    let mut cur = 0usize;
    while cur < data.len() {
        let nl = data[cur..]
            .iter()
            .position(|b| *b == b'\n')
            .ok_or_else(|| MigrateError::Mismatch("frame header unterminated".into()))?;
        let header = std::str::from_utf8(&data[cur..cur + nl])
            .map_err(|_| MigrateError::Mismatch("frame header not UTF-8".into()))?;
        let parts: Vec<&str> = header.split(' ').collect();
        if parts.len() != 4 || parts[0] != "SYREC1" {
            return Err(MigrateError::Mismatch(format!(
                "bad frame header: {header}"
            )));
        }
        let id = parts[1].to_string();
        let len: usize = parts[2]
            .parse()
            .map_err(|_| MigrateError::Mismatch(format!("frame {id}: bad length")))?;
        let start = cur + nl + 1;
        let end = start + len;
        if end > data.len() {
            return Err(MigrateError::Mismatch(format!(
                "frame {id}: runs past end of archive"
            )));
        }
        let bytes = data[start..end].to_vec();
        if sha256_hex(&bytes) != parts[3] {
            return Err(MigrateError::Mismatch(format!("record {id} frame hash")));
        }
        out.push((id, bytes));
        cur = end;
        if cur < data.len() && data[cur] == b'\n' {
            cur += 1;
        }
    }
    Ok(out)
}

/// Verify an archive against its manifest: every record hash, the
/// ledger hash, the chain, and the counts. The first mismatch is
/// named (ERR-06: any failure blocks cutover).
pub fn verify_history(home: &StateHome) -> Result<Manifest, MigrateError> {
    let hist = home.history_dir();
    let manifest_bytes = std::fs::read(hist.join("manifest.json"))
        .map_err(|e| MigrateError::Io(format!("manifest unreadable: {e}")))?;
    let manifest: Manifest = serde_json::from_slice(&manifest_bytes)
        .map_err(|e| MigrateError::Mismatch(format!("manifest unparseable: {e}")))?;
    let records_jsonl = std::fs::read(hist.join("records.jsonl"))
        .map_err(|e| MigrateError::Io(format!("records.jsonl unreadable: {e}")))?;
    let frames = parse_frames(&records_jsonl)?;
    for (id, bytes) in &frames {
        let rec: syncyard_contract::QueueRecord = serde_json::from_slice(bytes).map_err(|e| {
            MigrateError::Mismatch(format!("archived record {id} unparseable: {e}"))
        })?;
        if rec.id != *id {
            return Err(MigrateError::Mismatch(format!(
                "frame id {id} but record id {}",
                rec.id
            )));
        }
        match manifest.records.get(id) {
            Some(want) if *want == sha256_hex(bytes) => {}
            Some(_) => return Err(MigrateError::Mismatch(format!("record {id} hash"))),
            None => {
                return Err(MigrateError::Mismatch(format!(
                    "record {id} in archive but not in manifest"
                )))
            }
        }
    }
    if frames.len() != manifest.records.len() {
        return Err(MigrateError::Mismatch(format!(
            "record count: archive {}, manifest {}",
            frames.len(),
            manifest.records.len()
        )));
    }
    let ledger = std::fs::read(hist.join("ledger.jsonl"))
        .map_err(|e| MigrateError::Io(format!("ledger unreadable: {e}")))?;
    if sha256_hex(&ledger) != manifest.ledger_sha256 {
        return Err(MigrateError::Mismatch("ledger sha256".into()));
    }
    let lines: Vec<&[u8]> = ledger
        .split(|b| *b == b'\n')
        .filter(|l| !l.is_empty())
        .collect();
    if lines.len() != manifest.ledger_lines {
        return Err(MigrateError::Mismatch("ledger line count".into()));
    }
    if ledger_chain(&lines) != manifest.ledger_chain {
        return Err(MigrateError::Mismatch("ledger hash chain".into()));
    }
    Ok(manifest)
}

/// Export the archive back to per-record files (the ERR-06 round
/// trip's return leg). Exported files are byte-identical to the
/// migration sources by construction (raw bytes were archived).
pub fn export_history(home: &StateHome, out_dir: &Path) -> Result<usize, MigrateError> {
    let manifest = verify_history(home)?;
    let hist = home.history_dir();
    let records_jsonl = std::fs::read(hist.join("records.jsonl"))
        .map_err(|e| MigrateError::Io(format!("records.jsonl unreadable: {e}")))?;
    let queue_out = out_dir.join("queue");
    std::fs::create_dir_all(&queue_out)
        .map_err(|e| MigrateError::Io(format!("cannot create {}: {e}", queue_out.display())))?;
    let writer = FsWriter::default();
    let mut count = 0usize;
    for (id, bytes) in parse_frames(&records_jsonl)? {
        debug_assert!(manifest.records.contains_key(&id));
        writer
            .write_verified(&queue_out.join(format!("{id}.json")), &bytes)
            .map_err(|e| MigrateError::Io(e.to_string()))?;
        count += 1;
    }
    let ledger = std::fs::read(hist.join("ledger.jsonl"))
        .map_err(|e| MigrateError::Io(format!("ledger unreadable: {e}")))?;
    writer
        .write_verified(&out_dir.join("ledger.jsonl"), &ledger)
        .map_err(|e| MigrateError::Io(e.to_string()))?;
    Ok(count)
}

pub fn history_paths(home: &StateHome) -> (PathBuf, PathBuf, PathBuf) {
    let hist = home.history_dir();
    (
        hist.join("records.jsonl"),
        hist.join("ledger.jsonl"),
        hist.join("manifest.json"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::tests::sample_job;
    use crate::state::{new_queue_record, StateHome};

    fn fixture_source(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sy-mig-src-{}-{}", std::process::id(), tag));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("queue")).unwrap();
        for id in ["job-a", "job-b"] {
            let rec = new_queue_record(&sample_job(id));
            let bytes = serde_json::to_vec_pretty(&rec).unwrap();
            std::fs::write(dir.join("queue").join(format!("{id}.json")), bytes).unwrap();
        }
        std::fs::write(
            dir.join("ledger.jsonl"),
            b"{\"event\":\"submitted\",\"job_id\":\"job-a\"}\n{\"event\":\"dispatched\",\"job_id\":\"job-a\"}\n",
        )
        .unwrap();
        dir
    }

    #[test]
    fn migrate_verify_export_round_trip_is_byte_exact() {
        let src = fixture_source("rt");
        let home_dir = std::env::temp_dir().join(format!("sy-mig-home-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home_dir);
        let home = StateHome::new(home_dir.clone());
        let manifest = migrate_history(&src, &home, &|| false).unwrap();
        assert_eq!(manifest.records.len(), 2);
        assert_eq!(manifest.ledger_lines, 2);
        let verified = verify_history(&home).unwrap();
        assert_eq!(verified, manifest);
        // Export and compare byte-for-byte with the sources.
        let out = std::env::temp_dir().join(format!("sy-mig-out-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&out);
        let n = export_history(&home, &out).unwrap();
        assert_eq!(n, 2);
        for id in ["job-a", "job-b"] {
            let a = std::fs::read(src.join("queue").join(format!("{id}.json"))).unwrap();
            let b = std::fs::read(out.join("queue").join(format!("{id}.json"))).unwrap();
            assert_eq!(a, b, "record {id} differs after round trip");
        }
        let _ = std::fs::remove_dir_all(&src);
        let _ = std::fs::remove_dir_all(&home_dir);
        let _ = std::fs::remove_dir_all(&out);
    }

    #[test]
    fn tampered_archive_fails_verification_by_name() {
        let src = fixture_source("tamper");
        let home_dir = std::env::temp_dir().join(format!("sy-mig-home2-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home_dir);
        let home = StateHome::new(home_dir.clone());
        migrate_history(&src, &home, &|| false).unwrap();
        // Flip bytes inside the archived records.
        let (records_path, _, _) = history_paths(&home);
        let mut bytes = std::fs::read(&records_path).unwrap();
        let mid = bytes.len() / 2;
        bytes[mid] ^= 0x01;
        std::fs::write(&records_path, bytes).unwrap();
        match verify_history(&home) {
            Err(MigrateError::Mismatch(_)) | Err(MigrateError::Io(_)) => {}
            other => panic!("expected a named failure, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&src);
        let _ = std::fs::remove_dir_all(&home_dir);
    }

    #[test]
    fn migration_refused_during_wave() {
        let src = fixture_source("wave");
        let home = StateHome::new(
            std::env::temp_dir().join(format!("sy-mig-home3-{}", std::process::id())),
        );
        match migrate_history(&src, &home, &|| true) {
            Err(MigrateError::WaveDetected) => {}
            other => panic!("expected WaveDetected, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&src);
    }
}

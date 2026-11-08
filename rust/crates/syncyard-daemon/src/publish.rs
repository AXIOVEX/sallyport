//! Per-destination verified publishing (spec 038 section 7,
//! ERR-04).
//!
//! Every multi-destination publish writes **every** destination and
//! verifies each by reading it back and matching content identity
//! (SHA-256). Success is reported only when all destinations
//! verify. The 2026-11-06 defect — a publisher given two paths that
//! wrote only one and still reported success — is impossible in
//! this shape: the per-destination results are the return value,
//! and any failure makes the whole publish a failure.

use std::path::PathBuf;

use crate::vwrite::{sha256_hex, StateWriter};

/// The outcome for one destination.
#[derive(Debug, Clone, PartialEq)]
pub struct DestinationResult {
    pub path: PathBuf,
    pub ok: bool,
    pub sha256: Option<String>,
    pub error: Option<String>,
}

/// The outcome of a publish: per-destination results, in order.
#[derive(Debug)]
pub struct PublishReport {
    pub results: Vec<DestinationResult>,
}

impl PublishReport {
    /// True only when every destination verified (ERR-04).
    pub fn all_verified(&self) -> bool {
        !self.results.is_empty() && self.results.iter().all(|r| r.ok)
    }
}

/// Publish `bytes` to every destination, verifying each by
/// read-back content identity. Never short-circuits: a failed
/// destination does not excuse the others from being attempted
/// (the report must show the full picture), and the aggregate is a
/// failure unless all verify.
pub fn publish_verified(
    writer: &dyn StateWriter,
    bytes: &[u8],
    destinations: &[PathBuf],
) -> PublishReport {
    let want = sha256_hex(bytes);
    let mut results = Vec::new();
    for dest in destinations {
        let mut result = DestinationResult {
            path: dest.clone(),
            ok: false,
            sha256: None,
            error: None,
        };
        match writer.write_verified(dest, bytes) {
            Ok(()) => match std::fs::read(dest) {
                Ok(back) => {
                    let got = sha256_hex(&back);
                    if got == want {
                        result.ok = true;
                        result.sha256 = Some(got);
                    } else {
                        result.error = Some("read-back content identity mismatch".to_string());
                    }
                }
                Err(e) => result.error = Some(format!("read-back failed: {e}")),
            },
            Err(e) => result.error = Some(e.to_string()),
        }
        results.push(result);
    }
    PublishReport { results }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::vwrite::FsWriter;

    #[test]
    fn both_destinations_written_and_verified() {
        let dir = std::env::temp_dir().join(format!("sy-pub-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let a = dir.join("a.json");
        let b = dir.join("b.json");
        let report = publish_verified(&FsWriter::default(), b"{\"x\":1}", &[a.clone(), b.clone()]);
        assert!(report.all_verified());
        assert_eq!(std::fs::read(&a).unwrap(), b"{\"x\":1}");
        assert_eq!(std::fs::read(&b).unwrap(), b"{\"x\":1}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn one_bad_destination_fails_the_publish() {
        let dir = std::env::temp_dir().join(format!("sy-pub2-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let a = dir.join("a.json");
        let bad = dir.join("missing-dir").join("b.json");
        let report = publish_verified(&FsWriter::default(), b"data", &[a.clone(), bad]);
        assert!(!report.all_verified());
        assert!(report.results[0].ok);
        assert!(!report.results[1].ok);
        assert!(report.results[1].error.is_some());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
